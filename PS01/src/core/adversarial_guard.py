# src/core/adversarial_guard.py

SUSPICIOUS_THRESHOLDS = {
    "income": 0.5,         # >50% change = suspicious
    "emi_outgoing": 0.3,   # >30% change = suspicious
    "loan_amount": 1.0,    # >100% change = suspicious
}


class AdversarialGuard:
    def check(self, fact_type: str, old_value: float, new_value: float) -> dict:
        """
        Returns: {"suspicious": bool, "reason": str, "pct_change": float}
        """
        if fact_type not in SUSPICIOUS_THRESHOLDS:
            return {"suspicious": False, "reason": "no threshold defined", "pct_change": 0.0}
        if old_value == 0:
            return {"suspicious": False, "reason": "baseline zero", "pct_change": 0.0}
        pct_change = abs(new_value - old_value) / abs(old_value)
        threshold = SUSPICIOUS_THRESHOLDS[fact_type]
        suspicious = pct_change > threshold
        reason = (
            f"{fact_type} changed by {pct_change:.1%} (threshold {threshold:.0%})"
            if suspicious
            else "within threshold"
        )
        return {"suspicious": suspicious, "reason": reason, "pct_change": pct_change}
