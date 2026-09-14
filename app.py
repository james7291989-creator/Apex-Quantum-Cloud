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
        data = request.get_json() or {}
        asset = data.get('asset', {}) or {}

        raw_input = str(asset.get('address', '')).strip().upper()

        try:
            fee = float(asset.get('fee', 15000))
        except (TypeError, ValueError):
            fee = 15000.0

        property_type = str(asset.get('type') or asset.get('property_type') or 'SFR').upper()
        if property_type not in TYPE_MULT:
            property_type = 'SFR'

        condition = str(asset.get('condition') or 'Standard').upper()
        if condition not in COND_RATE:
            condition = 'STANDARD'

        try:
            manual_sqft = float(data.get('sqft') or asset.get('sqft') or 0)
        except (TypeError, ValueError):
            manual_sqft = 0.0

        try:
            manual_year = int(data.get('year_built') or asset.get('year_built') or 0)
        except (TypeError, ValueError):
            manual_year = 0

        if not raw_input:
            return jsonify({"error": "No address provided"}), 400

        house_num, street_name, extracted_zip = extract_address_anchors(raw_input)

        sqft = manual_sqft
        year_built = manual_year
        zip_code = extracted_zip
        zip_price = 0.0
        parcel_hit = False
        data_source = None
        siphon = None
        siphon_summary = None
        parcel_lat = None
        parcel_lon = None

        # 1) LOCAL DATA LAKE (fuzzy parcel match) - specs + PostGIS geometry seed
        if supabase and sqft == 0:
            query = supabase.table('county_parcels').select('*')
            if house_num:
                query = query.ilike('address', '%' + house_num + '%')
            if street_name:
                query = query.ilike('address', '%' + street_name.replace(" ", "%") + '%')
            parcel_res = query.execute()
            if parcel_res.data:
                parcel = parcel_res.data[0]
                parcel_hit = True
                data_source = "COUNTY_PARCELS"
                sqft = float(parcel.get('heated_sqft', 0) or 0)
                year_built = int(parcel.get('year_built', 0) or 0)
                if not zip_code:
                    zip_code = parcel.get('zip_code', '')
                parcel_lat = parcel.get('lat') or parcel.get('latitude')
                parcel_lon = parcel.get('lon') or parcel.get('longitude')

        # 2) FEDERAL CENSUS CATCH-NET - now harvests coordinates (Phase 1 PostGIS prep)
        census_zip = ""
        geo_lat = None
        geo_lon = None
        if not zip_code:
            census_zip, geo_lat, geo_lon = fetch_census_geodata(raw_input)
            if census_zip:
                zip_code = census_zip

        # 2.5) ARCGIS REST SIPHON - ground-truth specs when the local lake misses
        #      (public county GIS, synchronous requests, zero-cost; never raises)
        if (not sqft or not year_built) and property_engine is not None:
            siphon = property_engine.get_property_data_sync(
                raw_input,
                county_hint=asset.get('county'),
                zip_code=zip_code or '',
                timeout=8,
            ) or None
            if siphon:
                if siphon.get("sqft"):
                    sqft = float(siphon["sqft"])
                if siphon.get("year_built"):
                    year_built = int(siphon["year_built"])
                if (siphon.get("sqft") or siphon.get("year_built")) and siphon.get("data_source"):
                    data_source = siphon["data_source"]
                if siphon.get("county") and supabase:
                    try:
                        supabase.table('parcel_siphon_cache').upsert({
                            "address_normalized": re.sub(r"\s+", " ",
                                                         raw_input.strip().upper()),
                            "sqft": siphon.get("sqft"),
                            "year_built": siphon.get("year_built"),
                            "county": siphon.get("county"),
                            "data_source": siphon.get("data_source"),
                        }, on_conflict="address_normalized").execute()
                    except Exception:
                        pass
            siphon_summary = None
            if siphon:
                siphon_summary = {
                    "county": siphon.get("county"),
                    "parcel_id": siphon.get("parcel_id"),
                    "owner": siphon.get("owner"),
                    "situs": siphon.get("situs"),
                    "data_source": siphon.get("data_source"),
                    "supports_specs": bool(siphon.get("sqft")),
                }

        # 3) ZIP MARKET RATE - median baseline first, fallback index second
        if supabase and zip_code:
            zip_res = supabase.table('zip_market_rates').select('*').eq('zip_code', zip_code).execute()
            if zip_res.data:
                try:
                    zip_price = float(zip_res.data[0].get('median_price_per_sqft', 0))
                except (TypeError, ValueError):
                    zip_price = 0.0

        type_mult = TYPE_MULT[property_type]
        arv_multiplier = (zip_price if zip_price else FALLBACK_PRICE_PER_SQFT) * type_mult

        lat = geo_lat if geo_lat is not None else parcel_lat
        lon = geo_lon if geo_lon is not None else parcel_lon
        if parcel_hit:
            geo_source = "COUNTY_DATA_LAKE"
        elif census_zip or (geo_lat is not None):
            geo_source = "FEDERAL_CENSUS"
        elif extracted_zip:
            geo_source = "ZIP_INPUT_ONLY"
        else:
            geo_source = "NO_GEO_ANCHOR"

        geocode = {
            "zip": zip_code,
            "lat": lat,
            "lon": lon,
            "source": geo_source,
            "postgis_radius_ready": bool(lat is not None and lon is not None),
        }

        seller_intel = {
            "absentee": None,
            "tenure_years": None,
            "equity": None,
            "source": "PENDING_COUNTY_RECORDER_SCHEMA",
            "note": "MOCK HOOK ACTIVE - no county-recorder schema provisioned yet.",
        }

        if sqft == 0:
            zip_display = (" " + zip_code) if zip_code else ""
            base_label = "Local index" if zip_price else "Fallback index"
            lock_line = "{} locked at ${:,.2f}/SqFt ({}).".format(
                base_label, arv_multiplier / type_mult, property_type)
            return jsonify({
                "analysis": "\n".join([
                    ">>> [SYSTEM VERIFICATION]: Asset Federal Geolocation Locked" + zip_display + ".",
                    ">>> [MARKET BASELINE]: " + lock_line,
                    ">>> ACTION REQUIRED: Input physical specs (SqFt & Year Built) to execute Tri-Engine.",
                ]),
                "estimated_arv": 0,
                "mao": 0,
                "repair_estimates": 0,
                "requires_manual_specs": True,
                "inputs": {
                    "address": raw_input, "zip_code": zip_code, "property_type": property_type,
                    "condition": condition, "fee": round(fee, 2), "sqft": sqft, "year_built": year_built,
                    "arv_multiplier": round(arv_multiplier, 2), "rehab_rate": COND_RATE[condition],
                    "data_source": data_source,
                },
                "exits": None,
                "geocode": geocode,
                "seller_intel": seller_intel,
                "siphon": siphon_summary,
            }), 200

        # ------------------------------------------------------------------
        # CORE MATH
        # ------------------------------------------------------------------
        arv = sqft * arv_multiplier

        rehab_rate = COND_RATE[condition]
        if condition == "STANDARD" and year_built and year_built < 1980:
            rehab_rate += STANDARD_YEAR_BUMP
        rehab = sqft * rehab_rate

        # ---- EXIT A :: CASH MAO (wholesale standard) ----
        cash_mao = max(0.0, (arv * CASH_ACQ_MULT) - rehab - fee)

        # ---- EXIT B :: DSCR rental (cap-rate back-solved rent) ----
        noi = arv * DSCR_CAP_RATE
        gross_monthly_rent = (noi / (1.0 - DSCR_OPEX_RATIO)) / 12.0
        loan_amount = arv * DSCR_LTV
        dscr_monthly = monthly_debt_service(loan_amount, DSCR_RATE, DSCR_TERM_YEARS)
        annual_debt_service = dscr_monthly * 12.0
        dscr_ratio = (noi / annual_debt_service) if annual_debt_service > 0 else 0.0
        if dscr_ratio >= DSCR_FUNDABLE:
            dscr_verdict = "FUNDABLE"
        elif dscr_ratio >= DSCR_WEAK:
            dscr_verdict = "WEAK - CO-SIGNER REQUIRED"
        else:
            dscr_verdict = "REJECT - RAISE RENT / CUT PRICE"

        # ---- EXIT C :: CREATIVE finance (seller carry) ----
        entry_fee = arv * CREATIVE_ENTRY
        seller_note = arv * CREATIVE_NOTE
        creative_monthly = monthly_debt_service(seller_note, CREATIVE_RATE, CREATIVE_TERM_YEARS)
        creative_offer = max(0.0, (arv * CREATIVE_ACQ_MULT) - rehab)
        gap_amount = arv * (1.0 - CREATIVE_ENTRY - CREATIVE_NOTE)
        cash_on_cash_y1 = ((noi - (creative_monthly * 12.0)) / entry_fee) if entry_fee > 0 else 0.0
        spread_vs_cash = (arv * CASH_ACQ_MULT) - creative_offer

        exits = {
            "cash_mao": {
                "offer": round(cash_mao, 2),
                "formula": "ARV x 0.70 - Rehab - Fee",
                "acquisition_multiplier": CASH_ACQ_MULT,
                "rehab": round(rehab, 2),
                "fee": round(fee, 2),
            },
            "dscr": {
                "monthly_gross_rent": round(gross_monthly_rent, 2),
                "noi": round(noi, 2),
                "loan_amount": round(loan_amount, 2),
                "interest_rate": DSCR_RATE,
                "term_years": DSCR_TERM_YEARS,
                "monthly_debt_service": round(dscr_monthly, 2),
                "dscr_ratio": round(dscr_ratio, 3),
                "verdict": dscr_verdict,
            },
            "creative": {
                "entry_fee": round(entry_fee, 2),
                "seller_note_amount": round(seller_note, 2),
                "seller_note_rate": CREATIVE_RATE,
                "note_term_years": CREATIVE_TERM_YEARS,
                "monthly_debt_service": round(creative_monthly, 2),
                "gap_amount": round(gap_amount, 2),
                "offer": round(creative_offer, 2),
                "cash_on_cash_y1": round(cash_on_cash_y1, 4),
                "spread_vs_cash": round(spread_vs_cash, 2),
            },
        }

        inputs = {
            "address": raw_input, "zip_code": zip_code, "property_type": property_type,
            "condition": condition, "fee": round(fee, 2), "sqft": sqft, "year_built": year_built,
            "arv_multiplier": round(arv_multiplier, 2), "rehab_rate": rehab_rate,
            "arv": round(arv, 2), "rehab": round(rehab, 2),
            "data_source": data_source,
        }

        # ------------------------------------------------------------------
        # AI EXECUTIVE MEMO (optional - only when GROQ key present)
        # ------------------------------------------------------------------
        ai_narrative = ""
        if GROQ_API_KEY and Groq:
            client = Groq(api_key=GROQ_API_KEY)
            prompt = (
                "You are a ruthless real estate hedge fund AI. Write a strict 5-line underwriting memo for {}. "
                "Specs: {} sqft, Built {}. Type: {}. Condition: {}. ARV: ${:,.2f}. Repairs: ${:,.2f}.\n"
                "EXIT A Cash MAO: ${:,.2f} | EXIT B DSCR: {} (verdict: {}) | "
                "EXIT C Creative entry: ${:,.2f} COC {}.\n"
                "State clearly GO or NO-GO per exit. No fluff."
            ).format(
                raw_input, int(sqft), int(year_built), property_type, condition.capitalize(),
                arv, rehab, cash_mao, dscr_ratio, dscr_verdict, entry_fee, cash_on_cash_y1,
            )
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=180,
            )
            ai_narrative = completion.choices[0].message.content
        else:
            ai_narrative = ">>> AI NARRATOR OFFLINE. GROQ KEY MISSING."

        # ------------------------------------------------------------------
        # TERMINAL READOUT
        # ------------------------------------------------------------------
        vector_source = "LOCAL DATA LAKE"
        if parcel_hit and census_zip:
            vector_source = "LOCAL DATA LAKE + CENSUS GEO"
        elif census_zip:
            vector_source = "FEDERAL CENSUS"
        if siphon and (siphon.get("sqft") or siphon.get("year_built")):
            vector_source += " + ARCGIS SIPHON"

        readout = "\n".join([
            ">>> INITIATING QUANTUM UPLINK...",
            ">>> [SYSTEM]: Neural Underwriter initialized.",
            ">>> ANALYZING ASSET: " + raw_input,
            ">>> [SYSTEM]: SMART VAULT / FEDERAL GEO-MATCH SUCCESSFUL",
            "=== QUANTITATIVE UNDERWRITING DOSSIER ===",
            "| VECTOR SOURCE | " + vector_source + " |",
            "|---------------|-----------------|",
            "| Footprint     | {} SqFt |".format(int(sqft)),
            "| Year Built    | {} |".format(int(year_built)),
            "| Type x Rate   | {} x ${}/SqFt |".format(property_type, round(arv_multiplier, 2)),
            "| Condition     | {} @ ${:.0f}/SqFt |".format(condition.capitalize(), rehab_rate),
            "| ARV           | ${:,.2f} |".format(arv),
            "=== EXIT A :: CASH MAO ===",
            "** True Market ARV      : ${:,.2f}".format(arv),
            "** Parametric Rehab     : ${:,.2f}".format(rehab),
            "** Assignment Fee       : ${:,.2f}".format(fee),
            "** MAX ALLOWABLE OFFER  : ${:,.2f}".format(cash_mao),
            "=== EXIT B :: DSCR RENTAL YIELD ===",
            "** Est. Gross Rent      : ${:,.2f}/mo (cap {:.1f}%, opex {:.0f}%)".format(
                gross_monthly_rent, DSCR_CAP_RATE * 100, DSCR_OPEX_RATIO * 100),
            "** Est. NOI             : ${:,.2f}/yr".format(noi),
            "** Loan ({}% LTV)      : ${:,.2f}".format(int(DSCR_LTV * 100), loan_amount),
            "** Debt Service         : ${:,.2f}/mo".format(dscr_monthly),
            "** DSCR                 : {:.2f} :: {}".format(dscr_ratio, dscr_verdict),
            "=== EXIT C :: CREATIVE FINANCE ===",
            "** Entry Fee ({}% down): ${:,.2f}".format(int(CREATIVE_ENTRY * 100), entry_fee),
            "** Seller Note ({}%)  : ${:,.2f} @ {:.1f}% / {}yr".format(
                int(CREATIVE_NOTE * 100), seller_note, CREATIVE_RATE * 100, CREATIVE_TERM_YEARS),
            "** Note Payment         : ${:,.2f}/mo".format(creative_monthly),
            "** Cash-on-Cash Yr 1    : {:.1f}%".format(cash_on_cash_y1 * 100),
            "** Gap to Structure     : ${:,.2f} (flag for wrap/sub-to)".format(gap_amount),
            "-------------------------------",
            "=== AI EXECUTIVE MEMO ===",
            ai_narrative,
        ])

        return jsonify({
            "analysis": readout,
            "estimated_arv": round(arv, 2),
            "mao": round(cash_mao, 2),
            "repair_estimates": round(rehab, 2),
            "requires_manual_specs": False,
            "inputs": inputs,
            "exits": exits,
            "geocode": geocode,
            "seller_intel": seller_intel,
            "siphon": siphon_summary,
        }), 200

    except Exception as e:
        return jsonify({
            "analysis": ">>> [FATAL ERROR]: " + str(e),
            "estimated_arv": 0,
            "mao": 0,
            "repair_estimates": 0,
            "requires_manual_specs": False,
            "exits": None,
            "geocode": None,
            "seller_intel": None,
        }), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
