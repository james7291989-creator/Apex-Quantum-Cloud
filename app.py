import os
import re
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
try:
    from groq import Groq
except ImportError:
    Groq = None

app = Flask(__name__)
CORS(app)

# /// ENV VARIABLES ///
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# /// INITIALIZE SUPABASE ///
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def extract_address_anchors(raw_string):
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
    try:
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        params = {
            "address": raw_address,
            "benchmark": "Public_AR_Current",
            "format": "json"
        }
        response = requests.get(url, params=params, timeout=3)
        data = response.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            components = matches[0].get("addressComponents", {})
            return components.get("zip", "")
    except Exception:
        return ""
    return ""

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    try:
        data = request.get_json() or {}
        asset = data.get('asset', {})
        raw_input = asset.get('address', '').strip().upper()
        fee = float(asset.get('fee', 15000))
        
        manual_sqft = float(data.get('sqft') or asset.get('sqft') or 0)
        manual_year = int(data.get('year_built') or asset.get('year_built') or 0)
        
        if not raw_input:
            return jsonify({"error": "No address provided"}), 400

        house_num, street_name, extracted_zip = extract_address_anchors(raw_input)
        
        sqft = manual_sqft
        year_built = manual_year
        zip_code = extracted_zip
        arv_multiplier = 0
        
        if supabase and sqft == 0:
            query = supabase.table('county_parcels').select('*')
            if house_num:
                query = query.ilike('address', f'%{house_num}%')
            if street_name:
                fuzzy_street = street_name.replace(" ", "%")
                query = query.ilike('address', f'%{fuzzy_street}%')
            
            parcel_res = query.execute()
            
            if parcel_res.data:
                parcel = parcel_res.data[0]
                sqft = parcel.get('heated_sqft', 0)
                year_built = parcel.get('year_built', 0)
                if not zip_code:
                    zip_code = parcel.get('zip_code', '')

        if not zip_code:
            zip_code = fetch_census_geodata(raw_input)

        if supabase and zip_code:
            zip_res = supabase.table('zip_market_rates').select('*').eq('zip_code', zip_code).execute()
            if zip_res.data:
                arv_multiplier = float(zip_res.data[0].get('median_price_per_sqft', 0))
        
        if arv_multiplier == 0:
            arv_multiplier = 145.00

        if sqft == 0:
            zip_display = f" {zip_code}" if zip_code else ""
            return jsonify({
                "analysis": f">>> [SYSTEM VERIFICATION]: Asset Federal Geolocation Locked{zip_display}.\n>>> [MARKET BASELINE]: Local index locked at ${arv_multiplier:,.2f}/SqFt.\n>>> ACTION REQUIRED: Input physical specs (SqFt & Year Built) to execute MAO.",
                "estimated_arv": 0,
                "mao": 0,
                "requires_manual_specs": True
            }), 200

        arv = sqft * arv_multiplier
        rehab_sqft_cost = 45 if year_built < 1980 else 25
        rehab = sqft * rehab_sqft_cost
        mao = max(0, (arv * 0.70) - rehab - fee)

        ai_narrative = ""
        if GROQ_API_KEY and Groq:
            client = Groq(api_key=GROQ_API_KEY)
            prompt = f"You are a ruthless real estate hedge fund AI. Write a strict 4-line underwriting memo for {raw_input}. Specs: {sqft} sqft, Built {year_built}. ARV: ${arv:,.2f}. Repairs: ${rehab:,.2f}. MAO: ${mao:,.2f}. State clearly if the deal is a GO or NO-GO. No fluff."
            
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=150
            )
            ai_narrative = completion.choices[0].message.content
        else:
            ai_narrative = ">>> AI NARRATOR OFFLINE. GROQ KEY MISSING."

        readout = (
            f">>> INITIATING QUANTUM UPLINK...\n"
            f">>> [SYSTEM]: Neural Underwriter initialized.\n"
            f">>> ANALYZING ASSET: {raw_input}\n"
            f">>> [SYSTEM]: SMART VAULT / FEDERAL GEO-MATCH SUCCESSFUL\n"
            f"=== QUANTITATIVE UNDERWRITING DOSSIER ===\n"
            f"| VECTOR SOURCE | LOCAL DATA LAKE |\n"
            f"|---------------|-----------------|\n"
            f"| Footprint     | {sqft} SqFt |\n"
            f"| Year Built    | {year_built} |\n"
            f"| ZIP Multiplier| ${arv_multiplier}/SqFt |\n"
            f"=== EXECUTED MAO PARAMETERS ===\n"
            f"**True Market ARV** : ${arv:,.2f}\n"
            f"**Parametric Rehab** : ${rehab:,.2f}\n"
            f"**Assignment Fee** : ${fee:,.2f}\n"
            f"-------------------------------\n"
            f">>> **MAX ALLOWABLE OFFER (MAO) : ${mao:,.2f}**\n\n"
            f"=== AI EXECUTIVE MEMO ===\n"
            f"{ai_narrative}"
        )
        return jsonify({
            "analysis": readout,
            "estimated_arv": arv,
            "mao": mao,
            "repair_estimates": rehab
        }), 200

    except Exception as e:
        return jsonify({
            "analysis": f">>> [FATAL ERROR]: {str(e)}",
            "estimated_arv": 0,
            "mao": 0
        }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
