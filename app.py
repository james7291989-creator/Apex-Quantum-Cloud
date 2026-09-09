import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

def get_clean_groq_key():
    key = os.environ.get('GROQ_API_KEY', '')
    if not key: return None
    key = key.strip().strip('"').strip("'")
    if key.startswith("Bearer "): key = key[7:].strip()
    return key if key else None

def execute_groq_inference(address, arv, rehab, fee, user_query):
    groq_key = get_clean_groq_key()
    if not groq_key: return "[FATAL ERROR]: GROQ_API_KEY missing."

    # SERVER-SIDE MATH: The AI cannot hallucinate what it does not calculate.
    mao_calculated = (arv * 0.70) - rehab - fee

    system_prompt = f"""
    SYSTEM DIRECTIVE: You are the SV-1500 Neural Underwriter for Rodney & Sons.
    Absolute Truth Grounding (DO NOT RECALCULATE):
    - Asset: {address}
    - Database ARV: ${arv:,.2f}
    - Database Rehab Cost: ${rehab:,.2f}
    - Assignment Fee: ${fee:,.2f}
    - SYSTEM MAO (Maximum Allowable Offer): ${mao_calculated:,.2f}
    
    RULE 1: You are strictly prohibited from generating your own MAO or Break-even prices.
    RULE 2: Your output must reflect the exact SYSTEM MAO provided above.
    RULE 3: Do not offer financial advice outside the parameters of the (ARV * 0.70) - Rehab - Fee formula.
    """

    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
        "temperature": 0.0, # DOM-Locking creativity to ZERO
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
    
    arv = float(asset_data.get('arv', 0.0))
    rehab = float(asset_data.get('rehab_estimate', asset_data.get('rehab', 0.0)))
    fee = float(asset_data.get('fee', 15000.0)) # Fallback to target assignment fee
    address = str(asset_data.get('address', 'UNKNOWN ASSET'))

    result = execute_groq_inference(address, arv, rehab, fee, "Provide line-item underwriting readout and concise risk analysis.")
    return jsonify({"analysis": result}), 200

@app.route('/api/v1/analyze/chat', methods=['POST'])
def analyze_chat():
    data = request.json or {}
    arv = float(data.get('arv', 0.0))
    rehab = float(data.get('rehab', 0.0))
    fee = float(data.get('fee', 15000.0))
    address = str(data.get('address', 'UNKNOWN ASSET'))
    user_query = data.get('query', '')

    if not user_query: return jsonify({"reply": "[SYSTEM]: Query payload empty."}), 400
    
    result = execute_groq_inference(address, arv, rehab, fee, user_query)
    return jsonify({"reply": result}), 200

if __name__ == '__main__':
    app.run(port=5000, debug=True, use_reloader=False)