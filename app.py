import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

app = Flask(__name__)
CORS(app)

def get_clean_groq_key():
    key = os.environ.get('GROQ_API_KEY', '')
    if not key: return None
    key = key.strip().strip('"').strip("'")
    if key.startswith("Bearer "): key = key[7:].strip()
    return key if key else None

# ─────────────────────────────────────────────
# GOVERNMENT DATA EXTRACTION PIPELINE (ZERO COST)
# ─────────────────────────────────────────────

def geocode_address(address):
    url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
    params = {"address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=10)
        match = r.json()["result"]["addressMatches"][0]
        geos = match["geographies"]
        return {
            "lat": match["coordinates"]["y"], "lon": match["coordinates"]["x"],
            "state_fips": geos.get("2020 Census Blocks", [{}])[0].get("STATE"),
            "county_fips": geos.get("2020 Census Blocks", [{}])[0].get("COUNTY"),
            "tract": geos.get("Census Tracts", [{}])[0].get("TRACT"),
            "zip": match["addressComponents"].get("zip"), "state": match["addressComponents"].get("state")
        }
    except Exception:
        return None

def get_census_data(geo):
    if not geo: return {}
    variables = "B25077_001E,B25064_001E,B19013_001E,B25001_001E,B25002_003E,B25035_001E"
    url = f"https://api.census.gov/data/2022/acs/acs5"
    params = {"get": variables, "for": f"tract:{geo['tract']}", "in": f"state:{geo['state_fips']} county:{geo['county_fips']}"}
    try:
        r = requests.get(url, params=params, timeout=10)
        raw = dict(zip(r.json()[0], r.json()[1]))
        return {
            "median_value": int(raw.get("B25077_001E", 0) or 0),
            "median_rent": int(raw.get("B25064_001E", 0) or 0),
            "median_income": int(raw.get("B19013_001E", 0) or 0),
            "vacancy_rate": round(int(raw.get("B25002_003E", 0) or 0) / max(int(raw.get("B25001_001E", 1) or 1), 1) * 100),
            "median_year": int(raw.get("B25035_001E", 0) or 0)
        }
    except Exception:
        return {}

def get_flood_data(lat, lon):
    url = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
    params = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "f": "json"}
    try:
        r = requests.get(url, params=params, timeout=8)
        zone = r.json().get("features", [{}])[0].get("attributes", {}).get("FLD_ZONE", "X")
        return {"zone": zone, "high_risk": zone in ["A","AE","AH","AO","V","VE"]}
    except Exception:
        return {"zone": "X", "high_risk": False}

# ─────────────────────────────────────────────
# SV-1500 NEURAL INFERENCE ENGINE
# ─────────────────────────────────────────────

def execute_groq_inference(address, base_arv, base_rehab, fee, user_query, avm_triggered):
    groq_key = get_clean_groq_key()
    if not groq_key: return "[FATAL ERROR]: GROQ_API_KEY missing."

    system_prompt = f"SYSTEM DIRECTIVE: You are the SV-1500 Neural Underwriter.\n"

    # If AVM triggered, pull live government data to calculate numbers dynamically
    if avm_triggered:
        geo = geocode_address(address)
        census = get_census_data(geo)
        flood = get_flood_data(geo.get('lat'), geo.get('lon')) if geo else {}
        
        system_prompt += f"""
        LIVE TELEMETRY GATHERED FOR {address}:
        - Census Tract Median Home Value: ${census.get('median_value', 0):,}
        - Census Tract Median Rent: ${census.get('median_rent', 0):,}/mo
        - Neighborhood Vacancy Rate: {census.get('vacancy_rate', 0)}%
        - Median Year Built: {census.get('median_year', 'Unknown')}
        - Flood Zone: {flood.get('zone', 'Unknown')} (High Risk: {flood.get('high_risk', False)})
        
        YOUR DIRECTIVE:
        1. Base your ARV strictly on the Census Tract Median Home Value provided above.
        2. Calculate Rehab Cost based on Median Year Built (Pre-1980 = Heavy Rehab $60k+, Post-1980 = Medium $35k+).
        3. Calculate MAO: (ARV * 0.70) - Rehab - {fee}.
        4. Output a line-item readout of the numbers and a strict risk analysis based on the vacancy rate and flood data.
        """
    else:
        # User provided exact numbers; lock the AI to the database
        mao_calculated = (base_arv * 0.70) - base_rehab - fee
        system_prompt += f"""
        Absolute Truth Grounding (DO NOT RECALCULATE):
        - Asset: {address}
        - Database ARV: ${base_arv:,.2f}
        - Database Rehab Cost: ${base_rehab:,.2f}
        - Assignment Fee: ${fee:,.2f}
        - SYSTEM MAO: ${mao_calculated:,.2f}
        
        RULE: You must reflect the exact SYSTEM MAO provided above. Do not invent numbers.
        """

    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
        "temperature": 0.0,
        "max_tokens": 1024
    }

    try:
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=20)
        return response.json().get('choices', [{}])[0].get('message', {}).get('content', '[SYSTEM]: AI response invalid.')
    except Exception as e:
        return f"[SYSTEM ERROR]: {str(e)}"

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    data = request.json or {}
    asset_data = data.get('asset', {}) if data.get('asset') else data
    
    address = str(asset_data.get('address', 'UNKNOWN ASSET'))
    fee = float(asset_data.get('fee', 15000.0))
    arv = float(asset_data.get('arv', 0.0))
    rehab = float(asset_data.get('rehab_estimate', asset_data.get('rehab', 0.0)))
    
    avm_triggered = (arv == 0.0 or rehab == 0.0)

    result = execute_groq_inference(address, arv, rehab, fee, "Provide line-item underwriting readout.", avm_triggered)
    return jsonify({"analysis": result}), 200

if __name__ == '__main__':
    app.run(port=5000, debug=True, use_reloader=False)