from titan_quant import TitanQuantEngine
import os
import re
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import property_engine
except ImportError:
    property_engine = None

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# ENVIRONMENT
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# TRI-ENGINE ASSUMPTION MATRIX :: single source of truth for all three exits
# ---------------------------------------------------------------------------
CASH_ACQ_MULT       = 0.700   # Exit A: wholesale acquisition multiplier (ARV x 0.70)
DSCR_CAP_RATE       = 0.070   # Exit B: baseline cap rate -> back-solves gross rent
DSCR_OPEX_RATIO     = 0.400   # Exit B: opex share of gross rent (NOI = rent x 0.60)
DSCR_LTV            = 0.750   # Exit B: conventional loan amount as share of ARV
DSCR_RATE           = 0.06875 # Exit B: annual note rate
DSCR_TERM_YEARS     = 30      # Exit B: amortization years
CREATIVE_ENTRY      = 0.100   # Exit C: cash-to-close entry (down) as share of ARV
CREATIVE_NOTE       = 0.700   # Exit C: seller-carried note as share of ARV
CREATIVE_RATE       = 0.050   # Exit C: seller note annual rate
CREATIVE_TERM_YEARS = 30      # Exit C: seller note amortization years
CREATIVE_ACQ_MULT   = 0.850   # Exit C: all-in creative acquisition multiplier off ARV

TYPE_MULT  = {"SFR": 1.00, "MULTI": 0.90, "MOBILE": 0.55}
COND_RATE  = {"COSMETIC": 25.0, "STANDARD": 45.0, "GUT": 70.0}
STANDARD_YEAR_BUMP  = 10.0   # extra $/sqft on Standard-condition pre-1980 builds
FALLBACK_PRICE_PER_SQFT = 145.00
DSCR_FUNDABLE = 1.25
DSCR_WEAK     = 1.00

def extract_address_anchors(raw_string):
    """Normalizer regex. Returns (house_num, street_name, zip_code)."""
    raw = raw_string.upper()
    zip_match = re.search(r'\b(\d{5})\b', raw)
    zip_code = zip_match.group(1) if zip_match else ""

    num_match = re.search(r'^(\d+)', raw.strip())
    house_num = num_match.group(1) if num_match else ""

    street_name = ""
    if house_num:
        parts = raw.replace(house_num, "").strip().split()
        noise = ['RD', 'ST', 'AVE', 'LN', 'DR', 'BLVD', 'CT', 'MO', 'MISSOURI', zip_code]
        clean_parts = [p for p in parts if p not in noise and len(p) > 1]
        street_name = " ".join(clean_parts[:2])

    return house_num, street_name, zip_code


def fetch_census_geodata(raw_address):
    """Federal Census catch-net. Returns (zip, lat, lon) or ("", None, None).
    Census coordinate contract: coordinates.x = longitude, coordinates.y = latitude."""
    try:
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        params = {
            "address": raw_address,
            "benchmark": "Public_AR_Current",
            "format": "json"
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            components = matches[0].get("addressComponents", {})
            coords = matches[0].get("coordinates", {}) or {}
            zip_code = str(components.get("zip", ""))
            lat = coords.get("y")
            lon = coords.get("x")
            if zip_code and lat is not None and lon is not None:
                return zip_code, float(lat), float(lon)
            if zip_code:
                return zip_code, None, None
    except Exception:
        return "", None, None
    return "", None, None


def monthly_debt_service(principal, annual_rate, years):
    """Fully-amortizing standard payment. Returns monthly payment (0 on invalid input)."""
    if principal <= 0 or annual_rate <= 0 or years <= 0:
        return 0.0
    r = annual_rate / 12.0
    n = years * 12
    denom = (1 + r) ** n
    if denom <= 1 or (denom - 1) == 0:
        return principal / n
    return principal * (r * denom) / (denom - 1)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "APEX SV-1500 CORE ONLINE", "engine": "tri-exit"}), 200


@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():

    try:
        from titan_quant import TitanQuantEngine
        try:
            from ai_agent import process_chat_query
        except ImportError:
            process_chat_query = None
        
        data = request.get_json() or {}
        address = data.get("address", "").strip()
        raw_query = data.get("raw_query", "").strip()
        manual_sqft = data.get("sqft", 0)
        manual_year = data.get("year_built", 0)
        
        raw_combined = f"{address} {manual_sqft} {manual_year} {raw_query}".strip()
        
        # --- APEX INTENT ROUTER ---
        # If the query starts with conversational words or question marks, route to Groq AI
        lower_query = raw_combined.lower()
        is_question = any(lower_query.startswith(q) for q in ["how", "what", "where", "why", "who", "can", "is", "show", "tell"]) or "?" in lower_query or "/chat" in lower_query
        
        if is_question and process_chat_query is not None:
            # Route to AI Chatbot
            clean_query = raw_combined.replace("/chat", "").strip()
            ai_response = process_chat_query(clean_query, supabase)
            return jsonify({
                "status": "success",
                "mao": 0,
                "arv": 0,
                "analysis": f">>> [SV-1500 AI]: {ai_response.get('response', 'AI Offline')}"
            }), 200

        # --- ROUTE TO UNDERWRITING ---
        params = TitanQuantEngine.parse_raw_telemetry(raw_combined)
        
        if manual_sqft and float(manual_sqft) > 0:
            params["sqft"] = float(manual_sqft)
            params["is_synthetic_sqft"] = False
        if manual_year and int(manual_year) > 0:
            params["year_built"] = int(manual_year)
            
        result = TitanQuantEngine.execute_underwrite(params)
        
        return jsonify({
            "status": "success",
            "mao": result["base_mao"],
            "arv": result["arv"],
            "analysis": result["analysis"]
        }), 200

    except Exception as e:
        err_msg = str(e)
        return jsonify({
            "status": "error",
            "mao": 0,
            "arv": 0,
            "analysis": f">>> [SYSTEM FATAL]: Backend Failure. ERROR: {err_msg}"
        }), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
