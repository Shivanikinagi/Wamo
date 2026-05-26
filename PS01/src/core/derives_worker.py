# src/core/derives_worker.py
from typing import List, Dict


class DerivesWorker:
    def calculate(self, facts: List[Dict]) -> Dict:
        """
        From a list of facts, derive:
        - total_emi_burden: sum of all emi_outgoing facts
        - net_income: income - total_emi_burden
        - loan_eligibility: net_income * 60 (rough 5-year EMI rule)
        Returns dict with derived values (or empty dict if facts insufficient)
        """
        income = None
        total_emi_burden = 0.0

        for fact in facts:
            fact_type = fact.get("type")
            value = fact.get("value")
            if fact_type == "income" and value is not None:
                income = float(value)
            elif fact_type == "emi_outgoing" and value is not None:
                total_emi_burden += float(value)

        if income is None:
            return {}

        net_income = income - total_emi_burden
        loan_eligibility = net_income * 60

        return {
            "total_emi_burden": total_emi_burden,
            "net_income": net_income,
            "loan_eligibility": loan_eligibility,
        }
