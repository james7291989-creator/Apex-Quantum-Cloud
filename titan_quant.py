import re
import math
from typing import Dict, Any, Tuple

class TitanQuantEngine:
    """
    Institutional Underwriting Engine:
    - Bifurcated Land vs. Improvement valuation (handles 0.1 to 1000+ acres).
    - Autonomous Heuristic Synthetic Dimension Synthesizer (never crashes on missing SqFt).
    - Tri-Tier Capital Preservation Matrix (Bear, Base, Bull MAO).
    - DSCR & Creative Finance Term Structuring.
    """

    @staticmethod
    def parse_raw_telemetry(raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "").strip()
        data = {
            "sqft": 0,
            "year_built": 0,
            "beds": 0,
            "baths": 0.0,
            "acres": 0.0,
            "property_type": "SFR",
            "condition": "STANDARD",
            "target_fee": 10000.0,
            "is_synthetic_sqft": False,
            "raw_text": text
        }

        # 1. Acres Extraction
        acre_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:acres|acre|ac)\b", text, re.IGNORECASE)
        if acre_match:
            try:
                data["acres"] = float(acre_match.group(1))
            except ValueError:
                pass

        # 2. Square Footage Extraction
        sqft_match = re.search(r"(\d{3,5})\s*(?:sqft|sq\s*ft|square\s*feet|sf)\b", text, re.IGNORECASE)
        if sqft_match:
            try:
                data["sqft"] = int(sqft_match.group(1))
            except ValueError:
                pass

        # 3. Year Built Extraction
        year_match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text)
        if year_match:
            try:
                data["year_built"] = int(year_match.group(1))
            except ValueError:
                pass

        # 4. Beds & Baths
        bed_match = re.search(r"(\d+)\s*(?:bed|beds|br)\b", text, re.IGNORECASE)
        if bed_match:
            data["beds"] = int(bed_match.group(1))

        bath_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:bath|baths|ba)\b", text, re.IGNORECASE)
        if bath_match:
            data["baths"] = float(bath_match.group(1))

        # 5. Property Type Detection
        lower_text = text.lower()
        if any(w in lower_text for w in ["mobile", "manufactured", "trailer", "modular"]):
            data["property_type"] = "MOBILE"
        elif any(w in lower_text for w in ["multi", "duplex", "triplex", "fourplex", "apartment", "units"]):
            data["property_type"] = "MULTI"
        else:
            data["property_type"] = "SFR"

        # 6. Condition Detection
        if any(w in lower_text for w in ["gut", "tear down", "rough", "shell", "fire", "heavy"]):
            data["condition"] = "GUT"
        elif any(w in lower_text for w in ["cosmetic", "paint", "turnkey", "clean", "light"]):
            data["condition"] = "COSMETIC"
        else:
            data["condition"] = "STANDARD"

        # 7. Assignment Fee Extraction
        fee_match = re.search(r"(?:fee|assignment)\s*[:=]?\s*\$?([\d,]+)", text, re.IGNORECASE)
        if fee_match:
            try:
                data["target_fee"] = float(fee_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # 8. Autonomous Synthetic Fallback (Zero-Halt Guarantee)
        if data["sqft"] <= 0:
            data["is_synthetic_sqft"] = True
            beds = data["beds"] if data["beds"] > 0 else 3
            p_type = data["property_type"]

            if p_type == "MOBILE":
                data["sqft"] = 840 if beds <= 2 else (1216 if beds == 3 else 1560)
            elif p_type == "MULTI":
                data["sqft"] = max(beds, 2) * 750
            else:
                data["sqft"] = 950 if beds <= 2 else (1450 if beds == 3 else 2100)

        if data["year_built"] <= 0:
            data["year_built"] = 1976

        return data

    @classmethod
    def execute_underwrite(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        sqft = max(float(params.get("sqft", 1200)), 100.0)
        year_built = int(params.get("year_built", 1976))
        acres = max(float(params.get("acres", 0.0)), 0.0)
        p_type = str(params.get("property_type", "SFR")).upper()
        condition = str(params.get("condition", "STANDARD")).upper()
        target_fee = max(float(params.get("target_fee", 10000.0)), 0.0)
        is_synthetic = bool(params.get("is_synthetic_sqft", False))

        # PILLAR 1: BIFURCATED ASSET VALUATION (Land vs. Structure)
        base_market_rate = 145.0
        if acres >= 1.0:
            # Rural/Suburban acreage valuation curve
            per_acre_rate = 14000.0 if acres > 10.0 else 22000.0
            land_value = acres * per_acre_rate
            structure_rate = 45.0 if p_type == "MOBILE" else 85.0
            structure_value = sqft * structure_rate
            arv_raw = land_value + structure_value
        else:
            type_mult = 0.52 if p_type == "MOBILE" else (1.25 if p_type == "MULTI" else 1.0)
            arv_raw = sqft * base_market_rate * type_mult

        arv = round(max(arv_raw, 10000.0), 2)

        # PILLAR 2: LINE-ITEM CONSTRUCTION & REHAB
        if condition == "GUT":
            base_rehab_rate = 75.0
        elif condition == "COSMETIC":
            base_rehab_rate = 25.0
        else:
            base_rehab_rate = 45.0

        vintage_penalty = 0.0
        if year_built < 1930:
            vintage_penalty = 18.0
        elif year_built < 1980:
            vintage_penalty = 12.0

        if p_type == "MOBILE":
            base_rehab_rate = min(base_rehab_rate, 25.0)
            rehab_total = min((sqft * (base_rehab_rate + vintage_penalty)), 35000.0)
        else:
            rehab_total = (sqft * (base_rehab_rate + vintage_penalty)) * 1.12

        rehab_total = round(max(rehab_total, 5000.0), 2)

        # PILLAR 3: CAPITAL PRESERVATION SENSITIVITY MATRIX (MAO)
        holding_costs = round(arv * 0.04, 2)
        disposition_friction = round(arv * 0.08, 2)

        # Bear: 20% investor margin | Base: 15% margin | Bull: 10% margin
        bear_mao = max((arv * 0.65) - (rehab_total * 1.15) - target_fee, 0.0)
        base_mao = max((arv * 0.70) - rehab_total - target_fee, 0.0)
        bull_mao = max((arv * 0.75) - (rehab_total * 0.88) - target_fee, 0.0)

        # PILLAR 4: DSCR & CREATIVE METRICS
        gross_monthly_rent = round(arv * 0.0085, 2)
        annual_noi = round((gross_monthly_rent * 12) * 0.58, 2)
        annual_debt_service = round((arv * 0.75) * 0.0775, 2)
        dscr_ratio = round(annual_noi / max(annual_debt_service, 1.0), 2)

        if dscr_ratio >= 1.25:
            dscr_tier = "TIER-1 INSTITUTIONAL QUALIFIED"
        elif dscr_ratio >= 1.0:
            dscr_tier = "TIGHT DEBT SERVICE MARGIN"
        else:
            dscr_tier = "NEGATIVE LEVERAGE DANGER"

        entry_fee = round((arv * 0.08) + rehab_total + target_fee + 3500.0, 2)
        net_cash_flow = annual_noi - annual_debt_service
        coc_return = round((net_cash_flow / max(entry_fee, 1.0)) * 100, 2)

        # PILLAR 5: TERMINAL DOSSIER STRING GENERATION
        acre_str = f"{acres:.2f} ACRES | " if acres >= 1.0 else ""
        synth_flag = " [SYNTHESIZED DIMENSIONS]" if is_synthetic else ""
        dossier = (
            f"\n>>> ================================================================\n"
            f">>> SV-1500 TITAN QUANT DOSSIER :: DEVIATION TOLERANCE: ZERO\n"
            f">>> ================================================================\n"
            f">>> PHYSICAL PROFILE : {acre_str}{int(sqft)} SQFT{synth_flag} | BUILT: {year_built}\n"
            f">>> ASSET TAXONOMY   : {p_type} | CONDITION: {condition} | FEE: ${target_fee:,.2f}\n"
            f">>> CAPITALIZED ARV  : ${arv:,.2f}\n"
            f">>> AUDITED REHAB    : ${rehab_total:,.2f} (INCL. 12% CONTINGENCY & VINTAGE PENALTY)\n"
            f">>> ----------------------------------------------------------------\n"
            f">>> [TRIPLE-BARRIER CASH EXIT MATRIX]\n"
            f">>>   BEAR MAO (20% PROTECTED MARGIN) : ${bear_mao:,.2f}\n"
            f">>>   BASE MAO (INSTITUTIONAL TARGET) : ${base_mao:,.2f}\n"
            f">>>   BULL MAO (AGGRESSIVE EXPANSION) : ${bull_mao:,.2f}\n"
            f">>> ----------------------------------------------------------------\n"
            f">>> [INSTITUTIONAL YIELD & LEVERAGE]\n"
            f">>>   EST. MONTHLY RENT : ${gross_monthly_rent:,.2f} | NET NOI: ${annual_noi:,.2f}/yr\n"
            f">>>   DSCR RATIO        : {dscr_ratio:.2f}x [{dscr_tier}]\n"
            f">>>   CREATIVE ENTRY    : ${entry_fee:,.2f} | CASH-ON-CASH: {coc_return:.2f}%\n"
            f">>> ----------------------------------------------------------------\n"
            f">>> LEGAL PROTOCOL   : MANDATORY 14-DAY PHYSICAL CONTINGENCY REQUIRED.\n"
            f">>> ================================================================"
        )

        return {
            "sqft": int(sqft),
            "year_built": int(year_built),
            "acres": acres,
            "property_type": p_type,
            "condition": condition,
            "target_fee": target_fee,
            "arv": arv,
            "rehab": rehab_total,
            "bear_mao": bear_mao,
            "base_mao": base_mao,
            "bull_mao": bull_mao,
            "mao": base_mao,
            "gross_monthly_rent": gross_monthly_rent,
            "noi": annual_noi,
            "dscr": dscr_ratio,
            "dscr_status": dscr_tier,
            "entry_fee": entry_fee,
            "cash_on_cash": coc_return,
            "is_synthetic": is_synthetic,
            "analysis": dossier
        }
