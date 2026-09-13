import os
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

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    try:
        data = request.get_json() or {}
        asset = data.get('asset', {})
        raw_address = asset.get('address', '').strip().upper()
        fee = float(asset.get('fee', 15000))
        
        if not raw_address:
            return jsonify({"error": "No address provided"}), 400

        sqft = 0
        year_built = 0
        arv_multiplier = 0
        
        # /// 1. QUERY THE DATA LAKE (SUPABASE) ///
        if supabase:
            parcel_res = supabase.table('county_parcels').select('*').ilike('address', f'%{raw_address}%').execute()
            
            if parcel_res.data:
                parcel = parcel_res.data[0]
                sqft = parcel.get('heated_sqft', 0)
                year_built = parcel.get('year_built', 0)
                zip_code = parcel.get('zip_code', '')
                
                zip_res = supabase.table('zip_market_rates').select('*').eq('zip_code', zip_code).execute()
                if zip_res.data:
                    arv_multiplier = float(zip_res.data[0].get('median_price_per_sqft', 0))

        # FALLBACK: If Address is not in database
        if sqft == 0:
            return jsonify({
                "analysis": ">>> [SYSTEM WARNING]: Asset not found in Data Lake. \n>>> ACTION REQUIRED: Input physical specs manually.",
                "estimated_arv": 0,
                "mao": 0,
                "requires_manual_specs": True
            }), 200

        # /// 2. QUANT ENGINE (DETERMINISTIC MATH) ///
        arv = sqft * arv_multiplier
        rehab_sqft_cost = 45 if year_built < 1980 else 25
        rehab = sqft * rehab_sqft_cost
        mao = max(0, (arv * 0.70) - rehab - fee)

        # /// 3. AI NARRATOR ///
        ai_narrative = ""
        if GROQ_API_KEY and Groq:
            client = Groq(api_key=GROQ_API_KEY)
            prompt = f"You are a ruthless real estate hedge fund AI. Write a strict 4-line underwriting memo for {raw_address}. Specs: {sqft} sqft, Built {year_built}. ARV: ${arv:,.2f}. Repairs: ${rehab:,.2f}. MAO: ${mao:,.2f}. State clearly if the deal is a GO or NO-GO. No fluff."
            
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=150
            )
            ai_narrative = completion.choices[0].message.content
        else:
            ai_narrative = ">>> AI NARRATOR OFFLINE. GROQ KEY MISSING."

        # /// 4. TERMINAL READOUT ///
        readout = (
            f">>> INITIATING QUANTUM UPLINK...\n"
            f">>> [SYSTEM]: Neural Underwriter initialized.\n"
            f">>> ANALYZING ASSET: {raw_address}\n"
            f">>> [SYSTEM]: TIER-1 DATA LAKE CONNECTED (SUPABASE)\n"
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