import os
from flask import Blueprint, request, jsonify
from groq import Groq

quantum_bp = Blueprint('quantum', __name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@quantum_bp.route('/api/v1/analyze/quantum', methods=['POST'])
def analyze_asset():
    data = request.get_json()
    
    # 1. Strict Type Casting & Fallback Zeroing
    try:
        arv = float(data.get('arv', 0.0))
        rehab = float(data.get('rehab_estimate', 0.0))
        fee = float(data.get('fee', 0.0))
        address = str(data.get('address', 'UNKNOWN ASSET'))
    except ValueError:
        return jsonify({"error": "CRITICAL: Malformed float payload from Supabase."}), 400

    # 2. Server-Side MAO Calculation (Immutable)
    mao_calculated = (arv * 0.70) - rehab - fee

    # 3. System Prompt Hardening (Zero-Hallucination Matrix)
    system_prompt = f"""
    SYSTEM DIRECTIVE: You are the SV-1500 Neural Underwriter.
    Absolute Truth Grounding (DO NOT RECALCULATE):
    - Asset: {address}
    - Database ARV: ${arv:,.2f}
    - Database Rehab Cost: ${rehab:,.2f}
    - Assignment Fee: ${fee:,.2f}
    - SYSTEM MAO (Maximum Allowable Offer): ${mao_calculated:,.2f}
    
    RULE 1: You are strictly prohibited from generating your own MAO or "Break-even" prices.
    RULE 2: Your output must reflect the exact SYSTEM MAO provided above.
    RULE 3: Do not offer financial advice outside the parameters of the (ARV * 0.70) - Rehab - Fee formula.
    """

    user_query = data.get('query', 'Provide line-item underwriting readout.')

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query}
            ],
            temperature=0.0, # DOM-Locking creativity to ZERO
            max_tokens=500
        )
        
        return jsonify({
            "status": "success",
            "mao_enforced": mao_calculated,
            "readout": completion.choices[0].message.content
        }), 200

    except Exception as e:
        return jsonify({"error": f"INFERENCE FAULT: {str(e)}"}), 500
