import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

def get_clean_groq_key():
    key = os.environ.get('GROQ_API_KEY', '')
    if not key:
        return None
    key = key.strip().strip('"').strip("'")
    if key.startswith("Bearer "):
        key = key[7:].strip()
    return key if key else None

@app.route('/', methods=['GET', 'HEAD'])
def root():
    return jsonify({"status": "APEX QUANTUM CORE ONLINE", "model": "mixtral-8x7b-32768"}), 200

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    try:
        data = request.json or {}
        address = data.get('address', 'Target Asset')
        print(f">>> [SV-1500] INGESTING ASSET: {address}")

        groq_key = get_clean_groq_key()
        if not groq_key:
            print(">>> [AUTH ERROR]: GROQ_API_KEY not configured in environment")
            return jsonify({"error": "GROQ_API_KEY not configured in Render environment."}), 500

        system_prompt = (
            "You are SV-1500, an institutional real estate AI underwriter. "
            "Provide a concise analysis with estimated ARV, rehab cost, and a brief 1-sentence risk analysis."
        )

        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "mixtral-8x7b-32768",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this property: {address}"}
            ],
            "temperature": 0.2,
            "max_tokens": 1024
        }

        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=20)
        
        if response.status_code != 200:
            error_detail = response.text
            print(f">>> [GROQ REJECTED {response.status_code}]: {error_detail}")
            return jsonify({"error": f"Groq API {response.status_code}: {error_detail}"}), 503

        ai_text = response.json().get('choices', [{}])[0].get('message', {}).get('content', '')
        print(">>> [SV-1500] ANALYSIS STREAMED SUCCESSFULLY")
        return jsonify({"analysis": ai_text}), 200

    except Exception as e:
        print(f">>> [QUANTUM ERROR]: {str(e)}")
        return jsonify({"error": f"Quantum uplink failed: {str(e)}"}), 503

@app.route('/api/v1/analyze/chat', methods=['POST'])
def analyze_chat():
    try:
        data = request.json or {}
        address = data.get('address', 'Unknown Asset')
        user_query = data.get('query', '')

        if not user_query:
            return jsonify({"reply": "[SYSTEM]: Query payload empty."}), 400

        groq_key = get_clean_groq_key()
        if not groq_key:
            return jsonify({"reply": "[FATAL ERROR]: GROQ_API_KEY missing in Render environment."}), 500

        system_prompt = (
            "You are SV-1500, an elite AI Underwriter for Rodney & Sons. "
            "Strictly adhere to Missouri real estate law and anti-fraud compliance. "
            "Provide concise, institutional-grade answers. Zero conversational filler. "
            f"Active Asset Context: {address}"
        )

        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "mixtral-8x7b-32768",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": 0.2,
            "max_tokens": 1024
        }

        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=20)
        
        if response.status_code != 200:
            error_detail = response.text
            print(f">>> [GROQ CHAT REJECTED {response.status_code}]: {error_detail}")
            return jsonify({"reply": f"[FATAL UPLINK ERROR]: Groq API {response.status_code}: {error_detail}"}), 503

        ai_text = response.json().get('choices', [{}])[0].get('message', {}).get('content', '[SYSTEM]: AI response invalid.')
        return jsonify({"reply": ai_text}), 200

    except Exception as e:
        print(f">>> [SYSTEM ERROR]: {str(e)}")
        return jsonify({"reply": f"[SYSTEM ERROR]: Internal processing failure: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True, use_reloader=False)