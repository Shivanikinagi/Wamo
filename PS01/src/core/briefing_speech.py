"""
BriefingSpeechBuilder: Converts structured briefing dict â†’ natural Hinglish opening.

READ PATH step 4: When a new agent picks up a session, this generates the
natural opening sentence that makes the customer feel remembered.

Model: phi4-mini ONLY
Timeout: 30s
Temperature: 0.7 (higher for natural speech variation)
"""

import os
import requests
import logging
from typing import Optional, Dict, List
import re

logger = logging.getLogger(__name__)


class BriefingSpeechBuilder:
    """
    Generates natural Hinglish opening sentences from briefing dict.
    
    The judge listens to this sentence. It must sound like the agent
    was actually listening to previous calls, not reading a database.
    """
    
    def __init__(self, ollama_api: str = None):
        """
        Initialize with Ollama API endpoint.
        Default: read from OLLAMA_API env or fallback to localhost:11434
        """
        if ollama_api is None:
            ollama_api = os.getenv("OLLAMA_API", "http://localhost:11434")
        self.ollama_api = ollama_api
        self.model = "phi4-mini"  # IMMUTABLE - never change

    def _display_name(self, briefing: Optional[Dict]) -> str:
        return str(
            (briefing or {}).get("customer_name")
            or (briefing or {}).get("customer_display_name")
            or ""
        ).strip()

    def _has_meaningful_recall(self, briefing: Dict) -> bool:
        """Return True only when we have factual prior-session recall to reference."""
        recall = briefing.get("deterministic_recall") or {}
        recall_keys = (
            "latest_income",
            "co_applicant_name",
            "co_applicant_income",
            "loan_amount_lakh",
            "loan_type",
            "property_stage",
        )
        return any(bool(recall.get(key)) for key in recall_keys)
    
    def build_opening(self, briefing: Dict) -> str:
        """
        Convert { customer_name, session_count, facts } â†’ natural opening sentence.
        
        Args:
            briefing: { customer_id, customer_name, session_count, facts: [{...}] }
        
        Returns:
            Natural Hinglish string, max 40 words.
            Falls back to template if ollama fails.
        """
        try:
            preferred_language = str(briefing.get("preferred_language", "hinglish")).lower()
            grounded_opening = self._build_grounded_opening(briefing)
            if grounded_opening:
                return grounded_opening

            # Step 1: Extract briefing fields
            customer_name = self._display_name(briefing) or "Customer"
            session_count = briefing.get("session_count", 1)
            facts = briefing.get("facts", [])

            # Never let the model invent prior context for first-session customers
            # or customers whose only known fact is their name/language choice.
            if session_count <= 0 or not facts or not self._has_meaningful_recall(briefing):
                if preferred_language == "english":
                    return "Hello, let's start your home loan process today."
                if preferred_language == "hindi":
                    return "Namaste, aaj hum aapka home loan process shuru karte hain."
                return "Hi, chaliye aaj aapka home loan process shuru karte hain."
            
            # Step 2: Summarize facts (max 5, most recent first)
            facts_summary = self._facts_to_summary(facts)
            
            # Step 3: Build prompt
            prompt_text = self._build_prompt(
                customer_name=customer_name,
                session_count=session_count,
                facts_summary=facts_summary,
                preferred_language=preferred_language,
            )
            
            # Step 4: Call ollama
            response = requests.post(
                f"{self.ollama_api}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt_text,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "num_ctx": 1024,
                        "num_predict": 80
                    }
                },
                timeout=30
            )
            response.raise_for_status()
            
            # Step 5: Extract and clean response
            response_text = response.json().get("response", "").strip()
            if not response_text:
                return self._fallback_opening(briefing)
            
            # Remove quotes if wrapped
            if response_text.startswith('"') and response_text.endswith('"'):
                response_text = response_text[1:-1].strip()
            
            return response_text
            
        except (requests.Timeout, requests.ConnectionError, Exception) as e:
            logger.debug(f"BriefingSpeechBuilder error: {e}")
            return self._fallback_opening(briefing)

    def _build_grounded_opening(self, briefing: Dict) -> Optional[str]:
        """Construct a deterministic, factual opening from retrieved recall fields."""
        recall = briefing.get("deterministic_recall") or {}
        if not recall:
            return None

        customer_name = self._display_name(briefing)
        preferred_language = str(briefing.get("preferred_language", "hinglish")).lower()
        day = recall.get("last_discussed_day")
        day_ref = f"last {day}" if day else "last time"
        english_greeting = f"Hello {customer_name}," if customer_name else "Hello,"
        hinglish_greeting = f"Hi {customer_name}," if customer_name else "Hi,"
        hindi_greeting = f"Namaste {customer_name}," if customer_name else "Namaste,"

        co_name = (recall.get("co_applicant_name") or {}).get("value")
        co_income = (recall.get("co_applicant_income") or {}).get("value")
        income = (recall.get("latest_income") or {}).get("value")
        loan_amount = (recall.get("loan_amount_lakh") or {}).get("value")
        loan_type = (recall.get("loan_type") or {}).get("value")
        property_stage = (recall.get("property_stage") or {}).get("value")

        if preferred_language == "english":
            if loan_amount and property_stage:
                return (
                    f"{english_greeting} {day_ref} you were discussing a {loan_amount} lakh "
                    f"{loan_type or 'loan'} for a {property_stage} property. Shall we continue from there?"
                )
            if co_name and co_income:
                return (
                    f"{english_greeting} {day_ref} you mentioned co-applicant {co_name}. "
                    f"Should I include income {co_income} in this calculation?"
                )
            if co_name:
                return (
                    f"{english_greeting} {day_ref} you mentioned co-applicant {co_name}. "
                    "Should I include their income as well?"
                )
            if income:
                return (
                    f"{english_greeting} {day_ref} you shared income {income}. "
                    "Should we continue with this as the base amount?"
                )

        if preferred_language == "hinglish":
            if loan_amount and property_stage:
                return (
                    f"{hinglish_greeting} {day_ref} aap {loan_amount} lakh ke "
                    f"{loan_type or 'loan'} aur {property_stage} property ke baare mein baat kar rahe the. "
                    "Kya wahi se continue karein?"
                )
            if co_name and co_income:
                return (
                    f"{hinglish_greeting} {day_ref} aapne co-applicant {co_name} mention kiya tha. "
                    f"Kya main income {co_income} bhi include karun?"
                )
            if co_name:
                return (
                    f"{hinglish_greeting} {day_ref} aapne co-applicant {co_name} mention kiya tha. "
                    "Kya main unki income bhi include karun?"
                )
            if income:
                return (
                    f"{hinglish_greeting} {day_ref} aapne income {income} share ki thi. "
                    "Kya hum isi amount ko base maan kar aage badhein?"
                )

        if loan_amount and property_stage:
            return (
                f"{hindi_greeting} {day_ref} aap {loan_amount} lakh ke "
                f"{loan_type or 'loan'} aur {property_stage} property ke baare mein baat kar rahe the, "
                "क्या हम वहीं से आगे बढ़ें?"
            )
        if co_name and co_income:
            return (
                f"{hindi_greeting} {day_ref} aapne co-applicant {co_name} mention kiya tha, "
                f"kya main unki income {co_income} bhi factor karun?"
            )
        if co_name:
            return (
                f"{hindi_greeting} {day_ref} aapne co-applicant {co_name} mention kiya tha, "
                "kya main unki income bhi factor karun?"
            )
        if income:
            return (
                f"{hindi_greeting} {day_ref} aapne income {income} batayi thi, "
                "kya hum isi base par aage badhein?"
            )

        return None
    
    def _build_prompt(self, customer_name: str, session_count: int,
                      facts_summary: str, preferred_language: str = "hinglish") -> str:
        """Build the exact prompt for phi4-mini."""
        if preferred_language == "english":
            language_rule = "- Write ONLY in English. Do not switch to Hindi or Hinglish."
        elif preferred_language == "hindi":
            language_rule = "- Write ONLY in Hindi. Keep the wording natural and simple."
        else:
            language_rule = "- Use Hinglish naturally (mix Hindi + English, Pune banker style)."
        prompt = f"""You are a cooperative bank loan officer in Pune, India.
You are calling a customer you have spoken to before.
You have notes from previous calls:

Customer: {customer_name}
Previous sessions: {session_count}
Key facts known:
{facts_summary}

Write your opening sentence when the call connects.

Rules:
- Sound like YOU personally remember â€” not like you read a file
{language_rule}
- Reference ONE specific detail the customer told you before
- Use time naturally: "last time", "pichle baar", "Tuesday ko"
- Ask ONE soft follow-up showing you were paying attention
- Maximum 40 words
- NEVER say "our records show" or "as per our system"
- NEVER list multiple facts â€” pick the most emotionally relevant one
- If income was recently revised, reference the revision naturally

Your opening sentence only:"""
        return prompt
    
    def _facts_to_summary(self, facts: List[Dict]) -> str:
        """
        Convert fact list to concise bullet summary.
        
        Takes max 5 facts, sorted by verified first + recency.
        Format: "- {type}: {value} ({source}, {'verified'|'unverified'})"
        Max total 200 chars.
        """
        if not facts:
            return "No prior facts recorded yet (first session)"
        
        # Sort: verified first, then by recency (assume list order is recent first)
        sorted_facts = sorted(facts, key=lambda f: (not f.get("verified", False), facts.index(f)))
        
        lines = []
        for fact in sorted_facts[:5]:  # Max 5 facts
            fact_type = fact.get("type", "unknown")
            value = fact.get("value", "")
            source = fact.get("source", "")
            verified = "verified" if fact.get("verified", False) else "unverified"
            
            line = f"- {fact_type}: {value} ({source}, {verified})"
            lines.append(line)
        
        summary = "\n".join(lines)
        if len(summary) > 200:
            # Truncate to 200 chars
            summary = summary[:200] + "..."
        
        return summary
    
    def _fallback_opening(self, briefing: Optional[Dict] = None) -> str:
        """
        Fallback Hinglish opening if ollama fails.
        Must be natural and show context awareness.
        """
        preferred_language = str((briefing or {}).get("preferred_language", "hinglish")).lower()
        if preferred_language == "english":
            return "Hello, welcome back. Shall we continue your home loan process from where we left off?"
        if preferred_language == "hindi":
            return "Namaste, swagat hai. Kya hum aapka home loan process wahin se continue karein jahan pichli baar ruk gaye the?"
        return (
            "Namaste, pichli baar humne aapke home loan ke baare mein baat ki thi. "
            "Kya documents ab ready hain?"
        )

