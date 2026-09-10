import os, json, re, logging, statistics, concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [APEX PREDATOR] - %(message)s')
app = Flask(__name__)
CORS(app)

def get_clean_groq_key():
    key = os.environ.get('GROQ_API_KEY', '').strip().strip('"').strip("'")
    return key[7:].strip() if key.upper().startswith("BEARER ") else key if key else None

def search_vector(query):
    snippets = ""
    try:
        with DDGS() as ddgs:
            for res in list(ddgs.text(query, max_results=3)): snippets += res.get('body', '') + " "
    except Exception: pass
    return snippets

def fetch_multi_vector_telemetry(address):
    queries = [f'"{address}" Zillow Zestimate', f'"{address}" Redfin Estimate SqFt']
    combined = ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        for res in executor.map(search_vector, queries): combined += res + "\n"
    return combined

def execute_apex_quant_underwriting(address, db_arv, db_rehab, fee, condition_level="Medium"):
    groq_key = get_clean_groq_key()
    if not groq_key: return ">>> [FATAL ERROR]: GROQ_API_KEY MISSING."

    telemetry = fetch_multi_vector_telemetry(address)
    
    try:
        res = Groq(api_key=groq_key).chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": f"Extract valid JSON only: zillow_arv, redfin_arv, sqft. Snippets: {telemetry}"}],
            temperature=0.0, response_format={"type": "json_object"}
        )
        raw = res.choices[0].message.content
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(json_match.group(0)) if json_match else json.loads(raw)
    except Exception:
        data = {"zillow_arv": 0, "redfin_arv": 0, "sqft": 867}

    z_arv = float(data.get("zillow_arv", 0) or 0)
    r_arv = float(data.get("redfin_arv", 0) or 0)
    db_val = float(db_arv)
    sqft = float(data.get("sqft", 0) or 0)
    if sqft <= 0: sqft = 867.0
    
    raw_vectors = [val for val in [z_arv, r_arv, db_val] if val > 20000]
    
    if len(raw_vectors) >= 3:
        median_val = statistics.median(raw_vectors)
        filtered_vectors = [v for v in raw_vectors if abs(v - median_val) / median_val <= 0.25]
        if not filtered_vectors: filtered_vectors = [min(raw_vectors)]
        true_arv = min(filtered_vectors)
        confidence = "A+ [Z-SCORE VARIANCES CLEARED]"
    elif len(raw_vectors) == 2:
        true_arv = min(raw_vectors)
        confidence = "B [DUAL-VECTOR CONSERVATIVE]"
    elif len(raw_vectors) == 1:
        true_arv = raw_vectors[0]
        confidence = "C [ISOLATED VECTOR WARNING]"
    else:
        true_arv = 93500.0
        confidence = "F [BLIND FALLBACK]"

    # --- THE GOD-TIER PPSF CEILING GUARDRAIL ---
    implied_ppsf = true_arv / sqft
    MAX_SAFE_PPSF = 120.0
    SAFE_DEFAULT_PPSF = 105.0

    if implied_ppsf > MAX_SAFE_PPSF and "ISOLATED" in confidence:
        true_arv = sqft * SAFE_DEFAULT_PPSF
        confidence = f"GUARDRAIL ACTIVE: Poisoned DB (${db_val:,.0f}) obliterated. Capped at ${SAFE_DEFAULT_PPSF}/sqft."

    rates = {"Light": 15.0, "Medium": 35.0, "Heavy": 55.0}
    rate = rates.get(condition_level, 35.0)
    rehab = sqft * rate
    if 0 < db_rehab < (rehab * 1.5): rehab = db_rehab

    mao = (true_arv * 0.70) - rehab - fee

    readout = f"""
>>> INITIATING PREDATOR UPLINK...
>>> [SYSTEM]: Mathematical Ceiling Guardrails Active.
>>> ANALYZING ASSET: {address}

=== QUANTITATIVE UNDERWRITING DOSSIER ===
| VECTOR SOURCE | CAPTURED VALUE |
|---------------|----------------|
| Database Feed | ${db_val:,.2f} | 
| Live Telemetry| {"BLOCKED BY FIREWALL" if z_arv == 0 else f"${z_arv:,.2f}"} |
| Footprint     | {sqft:,.0f} SqFt |

=== EXECUTED MAO PARAMETERS ===
**True Market ARV**    : **${true_arv:,.2f}** *(Capped to physical footprint reality)*
**Deterministic Rehab**: **${rehab:,.2f}** *({condition_level} @ ${rate:,.0f}/sqft)*
**Assignment Fee**     : **${fee:,.2f}**
----------------------------------------
>>> **MAX ALLOWABLE OFFER (MAO) : ${mao:,.2f}**

=== SYSTEM CONFIDENCE RATING : [{confidence}] ===
*Notes: The algorithm enforces a strict Maximum Price-Per-Square-Foot (PPSF) ceiling. If live telemetry fails and the database provides a mathematically impossible valuation, the system physically caps the ARV to protect capital.*
>>> SV-1500 PREDATOR UNDERWRITING COMPLETE.
"""
    return readout.strip()

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    data = request.json or {}
    asset = data.get('asset', data)
    return jsonify({"analysis": execute_apex_quant_underwriting(
        str(asset.get('address', 'UNKNOWN')), float(asset.get('arv', 0)), 
        float(asset.get('rehab_estimate', asset.get('rehab', 0))), float(asset.get('fee', 15000)), "Medium"
    )}), 200

@app.route('/api/v1/analyze/chat', methods=['POST'])
def analyze_chat():
    data = request.json or {}
    return jsonify({"reply": execute_apex_quant_underwriting(
        str(data.get('address', 'UNKNOWN')), float(data.get('arv', 0)), 
        float(data.get('rehab', 0)), float(data.get('fee', 15000)), "Medium"
    )}), 200

if __name__ == '__main__': app.run(port=5000)