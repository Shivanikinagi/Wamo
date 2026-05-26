# src/preprocessing/banking_rules.py
from typing import List, Dict, Any

class BankingRules:
    @staticmethod
    def calculate_disposable_income(primary_income: float, co_income: float, emi_outgoing: float) -> Dict[str, Any]:
        """Derive disposable income from facts"""
        combined = primary_income + co_income
        disposable = combined - emi_outgoing
        return {
            "type": "disposable_income",
            "value": disposable,
            "verified": False,
            "source": "derived",
            "confidence": 0.94,  # High if all inputs verified
            "formula": f"{primary_income} + {co_income} - {emi_outgoing}"
        }
