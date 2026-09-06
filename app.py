import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app) # Unlocks the React cross-origin firewall

@app.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_quantum():
    try:
        data = request.json or {}
        address = data.get('address', 'Target Asset')
        print(f">>> [SV-1500] INGESTING ASSET: {address}")

        groq_key = os.environ.get('GROQ_API_KEY')
        if not groq_key:
            return jsonify({"error": "GROQ_API_KEY not configured"}), 500

        system_prompt = "You are SV-1500, a merciless real estate AI underwriter. Provide a JSON-only response with estimated ARV, rehab cost, and a brief 1-sentence risk analysis."
        
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this property: {address}"}
            ],
            "temperature": 0.2
        }

        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        
        ai_text = response.json().get('choices', [{}])[0].get('message', {}).get('content', '')
        return jsonify({"analysis": ai_text}), 200

    except Exception as e:
        print(f">>> [QUANTUM ERROR]: {str(e)}")
        return jsonify({"error": "Quantum uplink failed."}), 503

@app.route('/api/v1/analyze/chat', methods=['POST'])
def analyze_chat():
    try:
        data = request.json or {}
        address = data.get('address', 'Unknown Asset')
        user_query = data.get('query', '')

        if not user_query:
            return jsonify({"reply": "[SYSTEM]: Query payload empty."}), 400

        groq_key = os.environ.get('GROQ_API_KEY')
        if not groq_key:
            return jsonify({"reply": "[FATAL ERROR]: GROQ_API_KEY missing on edge server."}), 500

        system_prompt = (
            "You are SV-1500, an elite AI Underwriter for Rodney & Sons. "
            "Strictly adhere to Missouri real estate law and anti-fraud compliance. "
            "Provide concise, institutional-grade answers. Zero conversational filler. "
            f"Active Asset Context: {address}"
        )

        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": 0.2
        }
        
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        
        ai_text = response.json().get('choices', [{}])[0].get('message', {}).get('content', '[SYSTEM]: AI response invalid.')
        return jsonify({"reply": ai_text}), 200

    except requests.exceptions.RequestException as e:
        print(f">>> [GROQ FATAL]: {str(e)}")
        return jsonify({"reply": "[FATAL UPLINK ERROR]: Groq Cloud API unreachable."}), 503
    except Exception as e:
        print(f">>> [SYSTEM ERROR]: {str(e)}")
        return jsonify({"reply": "[SYSTEM ERROR]: Internal processing failure."}), 500

if __name__ == '__main__':
    print("==========================================================")
    print(" APEX QUANTUM CORE : FLASK SERVER ONLINE (PORT 5000)")
    print("==========================================================")
    app.run(port=5000, debug=True, use_reloader=False)
