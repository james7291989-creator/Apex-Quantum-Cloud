"""
=============================================================================
 ApexQuantumCore - Render Python Backend

 Endpoint : POST/GET /api/v1/analyze/quantum
 Data     : Official RentCast Tier-1 Data API  (https://api.rentcast.io)
-----------------------------------------------------------------------------
 REPLACES the legacy `duckduckgo_search` / blind Zillow scraping layer.

 HARD RULES ENFORCED IN THIS FILE:
   1. Zero usage of duckduckgo_search - no imports, no scraping, no selenium.
   2. Exact property data comes ONLY from the RentCast REST API:
        GET /v1/properties?address=...   -> squareFootage, yearBuilt,
                                             lastSalePrice, assessedValue
        GET /v1/avm/value?address=...    -> price (current market value / ARV),
                                             priceRangeLow/High, comparables
   3. If RentCast returns a BLANK/missing/absurd estimated value, the
      ABSOLUTE PHYSICAL GUARDRAIL always engages:
          max_arv = squareFootage * ARV_MAX_PER_SQFT  (default $105/sqft)
      so the MAO can never be inflated by bad data.
   4. The endpoint NEVER returns HTTP 500. Every failure mode degrades into
      the guardrail fallback so the React frontend always receives
      { "estimated_arv": int, "mao": int }.
=============================================================================
"""

import logging
import math
import os
import re
import time
import urllib.parse

import requests
from flask import Flask, jsonify, request

# flask-cors is optional at runtime; the endpoint works without it
try:
    from flask_cors import CORS

    _HAS_CORS = True
except Exception:  # pragma: no cover - never let an optional dep kill the app
    _HAS_CORS = False

app = Flask(__name__)
if _HAS_CORS:
    CORS(app)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("apex.quantum.v2")

# ============================================================================
# CONFIGURATION - override every value below from the Render "Environment" tab
# ============================================================================
RENTCAST_BASE_URL = os.environ.get(
    "RENTCAST_BASE_URL", "https://api.rentcast.io/v1"
).rstrip("/")
RENTCAST_API_KEY = os.environ.get("RENTCAST_API_KEY", "").strip()
RENTCAST_TIMEOUT_SEC = float(os.environ.get("RENTCAST_TIMEOUT_SEC", "10"))
RENTCAST_RETRIES = int(os.environ.get("RENTCAST_RETRIES", "2"))
RENTCAST_RETRY_BACKOFF_SEC = float(os.environ.get("RENTCAST_RETRY_BACKOFF_SEC", "0.4"))

# --- Absolute physical guardrail ---------------------------------------------
# Hard ceiling: ARV may NEVER exceed squareFootage * ARV_MAX_PER_SQFT.
ARV_MAX_PER_SQFT = float(os.environ.get("ARV_MAX_PER_SQFT", "105"))
# Only used when neither the API nor the caller supplies a square footage.
DEFAULT_SQFT = float(os.environ.get("DEFAULT_SQFT", "1200"))

# --- MAO engine ("70% rule" with repair reserve) -----------------------------
MAO_AFTER_REPAIR_VALUE_RATIO = float(os.environ.get("MAO_AFTER_REPAIR_VALUE_RATIO", "0.70"))
MAO_REPAIR_RESERVE_RATE = float(os.environ.get("MAO_REPAIR_RESERVE_RATE", "0.15"))


# ============================================================================
#  TOLERANT HELPERS - these never raise
# ============================================================================
def _to_float(value):
    """Coerce any value to float or None. Never raises."""
    try:
        if value is None or value == "":
            return None
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _rentcast_headers():
    return {"Accept": "application/json", "X-Api-Key": RENTCAST_API_KEY}


def rentcast_get(path, params):
    """GET a RentCast endpoint with retry / backoff.

    Returns (payload, http_status):
      - payload     : parsed JSON (dict/list) or None on any failure
      - http_status : real HTTP code, or 0 for network-level failure
    NEVER raises. A None payload is a contractual signal to callers that the
    guardrail fallback must be used.
    """
    url = "{}/{}".format(RENTCAST_BASE_URL, path.lstrip("/"))
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    )
    last_error = None
    for attempt in range(RENTCAST_RETRIES + 1):
        try:
            resp = requests.get(
                url, params=query, headers=_rentcast_headers(), timeout=RENTCAST_TIMEOUT_SEC
            )
            if resp.status_code == 200:
                try:
                    return resp.json(), 200
                except ValueError:
                    logger.warning("RentCast %s returned non-JSON body", path)
                    return None, resp.status_code

            # Transient errors: backoff and retry. Auth/limit/4xx are final.
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < RENTCAST_RETRIES:
                time.sleep(RENTCAST_RETRY_BACKOFF_SEC * (attempt + 1))
                continue

            logger.warning("RentCast %s HTTP %s: %s", path, resp.status_code, resp.text[:300])
            return None, resp.status_code
        except requests.RequestException as exc:
            last_error = exc
            if attempt < RENTCAST_RETRIES:
                time.sleep(RENTCAST_RETRY_BACKOFF_SEC * (attempt + 1))
                continue

    if last_error is not None:
        logger.warning("RentCast %s network failure: %r", path, last_error)
    return None, 0
# ============================================================================
# GROQ DYNAMIC REHAB ENGINE  (llama3-8b-8192 · medium cosmetic rehab)
# -----------------------------------------------------------------------------
# 1. RentCast squareFootage + yearBuilt are forwarded to Groq after RentCast
#    returns. The LLM replies with a single raw integer (no text/commas/$).
# 2. ANY failure (missing key, missing lib, network error, bad reply) degrades
#    the dynamic engine back to REHAB_FALLBACK_COST ($30,000) - the pipeline is
#    UNBREAKABLE and the response always contains estimated_arv + mao.
# ============================================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama3-8b-8192").strip()
GROQ_TIMEOUT_SEC = float(os.environ.get("GROQ_TIMEOUT_SEC", "12"))
REHAB_FALLBACK_COST = float(os.environ.get("REHAB_FALLBACK_COST", "30000"))
REHAB_MIN_COST = float(os.environ.get("REHAB_MIN_COST", "5000"))
REHAB_MAX_COST = float(os.environ.get("REHAB_MAX_COST", "200000"))
MAO_WHOLESALE_FEE = float(os.environ.get("MAO_WHOLESALE_FEE", "5000"))

# `groq` is an optional dependency: the app must boot even if it is absent.
try:  # pragma: no cover - optional import guard
    from groq import Groq

    _GROQ_AVAILABLE = True
except Exception:
    Groq = None
    _GROQ_AVAILABLE = False


def _parse_rehab_integer(raw_text):
    """Extract the single integer from the LLM reply. NEVER raises."""
    try:
        if not raw_text:
            return None
        cleaned = str(raw_text).replace(",", "").replace("$", "").strip()
        # Anchored token match: reject negatives, ignore decimal tails.
        match = re.search(r"(?<![\d.])-?(\d+)", cleaned)
        if not match:
            return None
        if match.group(0).startswith("-"):
            return None
        value = int(match.group(1))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def estimate_dynamic_rehab(year_built, sqft):
    """Institutional-grade AI rehab estimate via Groq llama3-8b-8192.

    Returns (rehab_cost:int, source:str):
      - ("groq_ai", <int>)                          when the LLM replied validly.
      - (REHAB_FALLBACK_COST, "default_fallback")   on ANY failure.
    NEVER raises, NEVER blocks the pipeline.
    """
    try:
        yb = int(_to_float(year_built) or 0)
        sq = int(_to_float(sqft) or DEFAULT_SQFT)
    except Exception:  # pragma: no cover - paranoia net
        yb, sq = 0, int(DEFAULT_SQFT)

    if not GROQ_API_KEY or not _GROQ_AVAILABLE:
        logger.warning(
            "Rehab engine DEGRADED -> fallback %s (key=%s, lib=%s)",
            int(REHAB_FALLBACK_COST), bool(GROQ_API_KEY), _GROQ_AVAILABLE,
        )
        return int(REHAB_FALLBACK_COST), "default_fallback"

    prompt = (
        "You are a Missouri real estate investor. Estimate rehab costs for a "
        "property built in {yb} with {sq} sqft. Assume a medium cosmetic rehab. "
        "Return ONLY a single raw integer representing the total dollar cost. "
        "No text. No commas. No dollar signs. Example: 35000"
    ).format(yb=yb, sq=sq)

    try:
        # SDK versions differ on constructor arguments; fall back gracefully.
        try:
            client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT_SEC)
        except TypeError:
            client = Groq(api_key=GROQ_API_KEY)

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=16,
        )
        raw = (completion.choices[0].message.content or "").strip()
        value = _parse_rehab_integer(raw)
        if value is None:
            logger.warning("Groq returned non-integer rehab payload: %r", raw[:120])
            return int(REHAB_FALLBACK_COST), "default_fallback"

        # Clamp hallucinated numbers into the sanity band.
        value = max(int(REHAB_MIN_COST), min(value, int(REHAB_MAX_COST)))
        return value, "groq_ai"
    except Exception as exc:  # noqa: BLE001 - unbreakable by contract
        logger.warning(
            "Groq rehab engine DEGRADED -> fallback %s (%r)",
            int(REHAB_FALLBACK_COST), exc,
        )
        return int(REHAB_FALLBACK_COST), "default_fallback"


# ============================================================================
# RENTCAST FACT GATHERER - uses the exact `rentcast_get` helper above (untouched)
# ============================================================================
def rentcast_facts(address):
    """Fetch property facts (/properties) + AVM value (/avm/value).

    Returns a dict (keys below) that is always present; fields may be None.
    NEVER raises.
    """
    facts = {
        "sqft": None,
        "year_built": None,
        "owner_name": None,
        "arv": None,
        "price_low": None,
        "price_high": None,
    }
    props, _ = rentcast_get("properties", {"address": address, "limit": "1"})
    record = None
    if isinstance(props, list) and props:
        record = props[0]
    elif isinstance(props, dict):
        record = props
    if isinstance(record, dict):
        facts["sqft"] = _to_float(record.get("squareFootage"))
        facts["year_built"] = _to_float(record.get("yearBuilt"))
        facts["owner_name"] = record.get("ownerName") or record.get("lastOwnerName") or None

    avm, _ = rentcast_get("avm/value", {"address": address})
    if isinstance(avm, dict):
        facts["arv"] = _to_float(avm.get("price"))
        facts["price_low"] = _to_float(avm.get("priceRangeLow"))
        facts["price_high"] = _to_float(avm.get("priceRangeHigh"))
    return facts
# ============================================================================
# QUANTUM UNDERWRITING ROUTE - the only public entry point for the frontend
# ============================================================================
@app.route("/api/v1/analyze/quantum", methods=["GET", "POST"])
def analyze_quantum():
    """POST/GET /api/v1/analyze/quantum   body: { "address": "..." }

    Pipeline :: RentCast (sqft + yearBuilt + ARV) -> Groq dynamic rehab -> MAO.
    MAO       :: (ARV * MAO_AFTER_REPAIR_VALUE_RATIO) - dynamic_rehab - fee
    NEVER 500s: every failure degrades into the guardrail so the Vercel
    frontend always receives { "estimated_arv": int, "mao": int } plus the
    terminal readout dossier (dynamic rehab, fee, facts, sources).
    """
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:  # pragma: no cover - malformed body can never 500 us
        payload = {}

    if request.method == "GET":
        address = (request.args.get("address") or "").strip()
    else:
        address = (str(payload.get("address") or "")).strip()

    if not address:
        return jsonify({"error": "address is required"}), 400

    try:
        facts = rentcast_facts(address)
    except Exception as exc:  # pragma: no cover - last-hope safety net
        logger.warning("Quantum facts engine DEGRADED: %r", exc)
        facts = {}

    # --- Property facts (guardrailed) ----------------------------------------
    sqft = _to_float(facts.get("sqft")) or DEFAULT_SQFT
    year_built = _to_float(facts.get("year_built"))
    if year_built is None or year_built <= 0:
        year_built = int(time.strftime("%Y"))

    # --- ARV with the ABSOLUTE PHYSICAL GUARDRAIL ------------------------------
    max_arv = int(sqft * ARV_MAX_PER_SQFT)
    raw_arv = _to_float(facts.get("arv"))
    if raw_arv is None or raw_arv <= 0:
        estimated_arv = max_arv
        arv_source = "guardrail"
    else:
        estimated_arv = int(min(raw_arv, max_arv))
        arv_source = "rentcast-avm"

    # --- DYNAMIC AI REHAB (Groq llama3-8b-8192) -------------------------------
    dynamic_rehab, rehab_source = estimate_dynamic_rehab(year_built, sqft)

    # --- MAO :: (ARV * 0.70) - dynamic_rehab - fee ----------------------------
    fee = int(MAO_WHOLESALE_FEE)
    mao = int(round((estimated_arv * MAO_AFTER_REPAIR_VALUE_RATIO) - dynamic_rehab - fee))

    return jsonify({
        # Contract keys consumed by the Vercel frontend (AdminDashboard/ApexTerminal).
        "estimated_arv": estimated_arv,
        "mao": mao,
        # Terminal readout dossier - full transparency for the operator.
        "address": address,
        "sqft": int(sqft),
        "year_built": int(year_built),
        "arv_source": arv_source,
        "dynamic_rehab": dynamic_rehab,
        "repair_estimates": dynamic_rehab,
        "rehab_source": rehab_source,
        "rehab_model": GROQ_MODEL,
        "fee": fee,
        "max_allowable_offer": mao,
        "registered_agent": facts.get("owner_name"),
    }), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "ApexQuantumCore",
        "status": "online",
        "endpoint": "/api/v1/analyze/quantum",
    })


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=False)