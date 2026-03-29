"""
ConversationAgent: Handles live conversation during active session.

READ PATH step 5: Live agent responses with memory awareness.
- Maintains per-session history (in-memory only)
- Detects income revisions (55K → 62K)
- Returns facts_to_update if something changed
- Calls phi4-mini ONLY

NEVER writes conversation history to WAL.
Only writes STRUCTURED FACTS if something changed.
"""

import os
import requests
import logging
import re
from typing import Optional, Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ConversationAgent:
    """
    Live conversation handler with memory-aware responses.
    
    Detects when facts change (income, document status).
    Returns natural Hinglish responses via phi4-mini.
    Maintains conversation history per session (trim to 4 turns).
    """
    
    def __init__(self, ollama_api: str = None, wal_logger=None, mem0_bridge=None):
        """
        Initialize with Ollama endpoint and optional WAL/Mem0 backends.
        
        Args:
            ollama_api: Ollama base URL (default: localhost:11434)
            wal_logger: WALLogger instance for writing facts
            mem0_bridge: Mem0Bridge instance for memory updates
        """
        if ollama_api is None:
            ollama_api = os.getenv("OLLAMA_API", "http://localhost:11434")
        self.ollama_api = ollama_api
        self.model = "phi4-mini"  # IMMUTABLE
        self.wal_logger = wal_logger
        self.mem0_bridge = mem0_bridge
        
        # Per-session history: {session_id: [{"role": "customer/agent", "content": "..."}]}
        self.history = {}
        self.max_history_turns = 4  # 4 turns = 8 entries (customer + agent each)

    def _display_name(self, briefing: Optional[Dict[str, Any]]) -> str:
        """Return a human-friendly customer name without leaking raw IDs into speech."""
        briefing = briefing or {}
        candidate = str(
            briefing.get("customer_name")
            or briefing.get("customer_display_name")
            or ""
        ).strip()
        if candidate:
            return candidate
        return ""

    def _briefing_facts(self, briefing: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        briefing = briefing or {}
        return (
            briefing.get("facts")
            or briefing.get("verified_facts", []) + briefing.get("unverified_facts", [])
        )

    def _normalize_money_value(self, raw: str) -> Optional[str]:
        digits = re.sub(r"[^\d]", "", raw or "")
        return digits or None

    def _format_money(self, value: Optional[str], language: str = "hinglish") -> str:
        if not value:
            return ""
        digits = re.sub(r"[^\d]", "", str(value))
        if not digits:
            return str(value)
        amount = int(digits)
        if language == "english":
            return f"Rs {amount:,}"
        return f"{amount:,}"

    def _format_tenure(self, years: Optional[str], language: str = "hinglish") -> str:
        if not years:
            return ""
        if language == "english":
            return f"{years} years"
        if language == "hindi":
            return f"{years} साल"
        return f"{years} years"

    def _extract_known_state(self, briefing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        known: Dict[str, Any] = {
            "loan_type": None,
            "loan_amount_lakh": None,
            "property_stage": None,
            "income": None,
            "co_applicant_name": None,
            "existing_emi": None,
            "tenure_years": None,
            "documents": set(),
        }

        for fact in self._briefing_facts(briefing):
            fact_type = str(fact.get("type", "")).strip().lower()
            value = str(fact.get("value", "")).strip()
            content = str(fact.get("content", "")).strip()
            lowered = f"{value} {content}".lower()

            if fact_type in {"loan_type", "product"} and value:
                known["loan_type"] = value
            elif fact_type in {"loan_amount", "loan_amount_lakh"} and value:
                known["loan_amount_lakh"] = value
            elif fact_type in {"property_stage", "property_status"} and value:
                known["property_stage"] = value
            elif fact_type == "income" and value:
                known["income"] = self._normalize_money_value(value) or value
            elif fact_type in {"co_applicant_name", "co_applicant", "customer_spouse_name"} and value:
                known["co_applicant_name"] = value
            elif fact_type in {"existing_emi", "emi"} and value:
                known["existing_emi"] = self._normalize_money_value(value) or value
            elif fact_type in {"tenure_years", "tenure"} and value:
                known["tenure_years"] = self._normalize_money_value(value) or value

            if "under construction" in lowered:
                known["property_stage"] = known["property_stage"] or "under construction"
            elif "ready to move" in lowered:
                known["property_stage"] = known["property_stage"] or "ready to move"

            if "home loan" in lowered:
                known["loan_type"] = known["loan_type"] or "home loan"
            elif "personal loan" in lowered:
                known["loan_type"] = known["loan_type"] or "personal loan"

            lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*lakh", lowered)
            if lakh_match:
                known["loan_amount_lakh"] = known["loan_amount_lakh"] or lakh_match.group(1)

            if "salary slip" in lowered or "salary slips" in lowered:
                known["documents"].add("salary slips")
            if "form 16" in lowered:
                known["documents"].add("form 16")
            if "bank statement" in lowered or "bank statements" in lowered:
                known["documents"].add("bank statements")
            if "land doc" in lowered or "land document" in lowered or "title deed" in lowered:
                known["documents"].add("land documents")

        return known

    def _extract_message_state(self, customer_message: str) -> Dict[str, Any]:
        text = customer_message or ""
        lowered = text.lower()
        extracted: Dict[str, Any] = {"documents": set()}

        if "home loan" in lowered:
            extracted["loan_type"] = "home loan"
        elif "personal loan" in lowered:
            extracted["loan_type"] = "personal loan"
        elif "education loan" in lowered:
            extracted["loan_type"] = "education loan"

        lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*lakh", lowered)
        if lakh_match:
            extracted["loan_amount_lakh"] = lakh_match.group(1)

        if "under construction" in lowered:
            extracted["property_stage"] = "under construction"
        elif "ready to move" in lowered or "ready-to-move" in lowered:
            extracted["property_stage"] = "ready to move"
        elif "resale" in lowered:
            extracted["property_stage"] = "resale"

        income_match = re.search(
            r"(?:income|salary|monthly income|mahine|per month)[^\d]{0,10}(\d{4,6})",
            lowered,
        )
        if income_match:
            extracted["income"] = income_match.group(1)

        emi_match = re.search(r"(?:emi)[^\d]{0,10}(\d{3,6})", lowered)
        if emi_match:
            extracted["existing_emi"] = emi_match.group(1)
        elif "existing emi" in lowered or "existing loan" in lowered:
            extracted["existing_emi"] = "mentioned"

        tenure_match = re.search(r"\b(\d{1,2})\s*(?:years?|yrs?|saal)\b", lowered)
        if tenure_match:
            extracted["tenure_years"] = tenure_match.group(1)

        co_match = re.search(
            r"(?:wife|spouse|co[- ]?applicant)[^\w]+([A-Z][a-z]+|[a-z]{3,})",
            text,
        )
        if co_match:
            extracted["co_applicant_name"] = co_match.group(1).strip().title()
        elif "co-applicant" in lowered or "co applicant" in lowered:
            extracted["co_applicant_name"] = "mentioned"

        if "salary slip" in lowered or "salary slips" in lowered:
            extracted["documents"].add("salary slips")
        if "form 16" in lowered:
            extracted["documents"].add("form 16")
        if "bank statement" in lowered or "bank statements" in lowered:
            extracted["documents"].add("bank statements")
        if "land doc" in lowered or "land docs" in lowered or "land document" in lowered or "title deed" in lowered:
            extracted["documents"].add("land documents")

        return extracted

    def _merge_state(self, known: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(known)
        merged["documents"] = set(known.get("documents", set()))
        for key, value in extracted.items():
            if key == "documents":
                merged["documents"].update(value)
            elif value not in (None, "", []):
                merged[key] = value
        return merged

    def _next_question(self, merged: Dict[str, Any], language: str) -> str:
        docs = merged.get("documents", set())
        if not merged.get("income"):
            if language == "english":
                return "What is your approximate monthly income?"
            if language == "hindi":
                return "आपकी लगभग मासिक आय कितनी है?"
            return "Aapki approx monthly income kitni hai?"
        if not merged.get("existing_emi"):
            if language == "english":
                return "What is your current monthly EMI, roughly?"
            if language == "hindi":
                return "अभी आपकी लगभग कितनी EMI चल रही है?"
            return "Abhi approx kitni EMI chal rahi hai?"
        if "salary slips" not in docs:
            if language == "english":
                return "Do you already have the last 3 months' salary slips?"
            if language == "hindi":
                return "क्या आपके पास पिछले 3 महीने की salary slips हैं?"
            return "Kya last 3 months ki salary slips ready hain?"
        if "bank statements" not in docs:
            if language == "english":
                return "Can you also keep your recent bank statements ready?"
            if language == "hindi":
                return "क्या आप recent bank statements भी ready रख सकते हैं?"
            return "Recent bank statements bhi ready rakh sakte hain?"
        if not merged.get("tenure_years"):
            if language == "english":
                return "What tenure would you prefer for the loan?"
            if language == "hindi":
                return "आप loan के लिए कौन सा tenure prefer करेंगे?"
            return "Loan ke liye aap kaunsa tenure prefer karenge?"
        if language == "english":
            return "Should we move to eligibility check or document verification next?"
        if language == "hindi":
            return "क्या हम next step में eligibility check करें या document verification?"
        return "Next step mein eligibility check karein ya document verification?"

    def _build_structured_facts(
        self,
        *,
        session_id: str,
        customer_message: str,
        briefing: Optional[Dict[str, Any]],
        income_revised: bool,
        new_income_value: Optional[str],
    ) -> List[Dict[str, Any]]:
        known = self._extract_known_state(briefing)
        extracted = self._extract_message_state(customer_message)
        facts: List[Dict[str, Any]] = []
        timestamp_suffix = int(datetime.now().timestamp())

        def append_fact(fact_type: str, value: str, relationship: str = "new", source: str = "customer_verbal") -> None:
            if not value:
                return
            facts.append(
                {
                    "fact_id": f"{fact_type}_{session_id}_{timestamp_suffix}_{len(facts)+1}",
                    "type": fact_type,
                    "value": value,
                    "relationship": relationship,
                    "verified": False,
                    "source": source,
                    "confidence": 0.85,
                }
            )

        if income_revised and new_income_value:
            append_fact(
                "income",
                f"{new_income_value}_INR_MONTHLY",
                relationship="updates",
                source="customer_verbal_revision",
            )
        elif extracted.get("income") and extracted.get("income") != known.get("income"):
            append_fact("income", f"{extracted['income']}_INR_MONTHLY")

        if extracted.get("loan_type") and extracted.get("loan_type") != known.get("loan_type"):
            append_fact("loan_type", extracted["loan_type"])
        if extracted.get("loan_amount_lakh") and extracted.get("loan_amount_lakh") != known.get("loan_amount_lakh"):
            append_fact("loan_amount_lakh", extracted["loan_amount_lakh"])
        if extracted.get("property_stage") and extracted.get("property_stage") != known.get("property_stage"):
            append_fact("property_stage", extracted["property_stage"])
        if extracted.get("co_applicant_name") and extracted.get("co_applicant_name") != known.get("co_applicant_name"):
            append_fact("co_applicant_name", extracted["co_applicant_name"])
        if (
            extracted.get("existing_emi")
            and extracted.get("existing_emi") != "mentioned"
            and extracted.get("existing_emi") != known.get("existing_emi")
        ):
            append_fact("existing_emi", f"{extracted['existing_emi']}_INR_MONTHLY")
        if extracted.get("tenure_years") and extracted.get("tenure_years") != known.get("tenure_years"):
            append_fact("tenure_years", extracted["tenure_years"])

        known_docs = known.get("documents", set())
        for doc in sorted(extracted.get("documents", set())):
            if doc not in known_docs:
                append_fact("document_ready", doc)

        return facts

    def _build_grounded_response(
        self,
        *,
        customer_message: str,
        briefing: Optional[Dict[str, Any]],
        preferred_language: Optional[str],
        income_revised: bool,
        new_income_value: Optional[str],
    ) -> Optional[str]:
        language = (preferred_language or self._detect_message_language(customer_message)).lower()
        known = self._extract_known_state(briefing)
        extracted = self._extract_message_state(customer_message)
        merged = self._merge_state(known, extracted)
        next_question = self._next_question(merged, language)
        docs = merged.get("documents", set())

        amount = merged.get("loan_amount_lakh")
        amount_text = f"{amount} lakh" if amount else ""
        income_text = self._format_money(new_income_value or merged.get("income"), language)
        emi_text = self._format_money(merged.get("existing_emi"), language)
        tenure_text = self._format_tenure(merged.get("tenure_years"), language)
        co_name = merged.get("co_applicant_name")
        property_stage = merged.get("property_stage")

        if language == "english":
            if amount or property_stage:
                pieces = ["Understood."]
                if amount:
                    pieces.append(f"You need a {amount_text} home loan.")
                if property_stage:
                    pieces.append(f"The property is {property_stage}.")
                if "land documents" in docs:
                    pieces.append("Land documents are already available.")
                return " ".join(pieces + [next_question])
            if income_revised or extracted.get("income"):
                pieces = [f"Noted, your monthly income is {income_text}."]
                if co_name and co_name != "mentioned":
                    pieces.append(f"I can also consider {co_name} as co-applicant.")
                if emi_text:
                    pieces.append(f"I will factor in the existing EMI of {emi_text}.")
                return " ".join(pieces + [next_question])
            if docs or extracted.get("tenure_years"):
                pieces = []
                if docs:
                    pieces.append(f"Good, I have noted {', '.join(sorted(docs))} as ready.")
                if tenure_text:
                    pieces.append(f"Around {tenure_text} is workable for EMI planning.")
                return " ".join(pieces + [next_question])
        elif language == "hindi":
            if amount or property_stage:
                pieces = ["समझ गया।"]
                if amount:
                    pieces.append(f"आपको {amount_text} का home loan चाहिए।")
                if property_stage:
                    pieces.append(f"Property अभी {property_stage} है।")
                if "land documents" in docs:
                    pieces.append("Land documents available हैं।")
                return " ".join(pieces + [next_question])
            if income_revised or extracted.get("income"):
                pieces = [f"ठीक है, आपकी monthly income {income_text} note कर ली है।"]
                if co_name and co_name != "mentioned":
                    pieces.append(f"{co_name} को co-applicant के रूप में factor किया जा सकता है।")
                if emi_text:
                    pieces.append(f"Existing EMI {emi_text} भी consider होगी।")
                return " ".join(pieces + [next_question])
            if docs or extracted.get("tenure_years"):
                pieces = []
                if docs:
                    pieces.append(f"अच्छा, {', '.join(sorted(docs))} ready हैं।")
                if tenure_text:
                    pieces.append(f"{tenure_text} का tenure EMI planning के लिए ठीक रह सकता है।")
                return " ".join(pieces + [next_question])
        else:
            if amount or property_stage:
                pieces = ["Samajh gaya."]
                if amount:
                    pieces.append(f"Aapko {amount_text} ka home loan chahiye.")
                if property_stage:
                    pieces.append(f"Property abhi {property_stage} hai.")
                if income_text:
                    pieces.append(f"Monthly income {income_text} note kar li hai.")
                if co_name and co_name != "mentioned":
                    pieces.append(f"{co_name} ko co-applicant ke roop mein include kar sakte hain.")
                elif co_name == "mentioned":
                    pieces.append("Co-applicant option bhi note kar liya hai.")
                if "land documents" in docs:
                    pieces.append("Land documents ready hain.")
                return " ".join(pieces + [next_question])
            if income_revised or extracted.get("income") or co_name or merged.get("existing_emi"):
                pieces = []
                if income_text:
                    pieces.append(f"Noted, monthly income {income_text} hai.")
                if co_name and co_name != "mentioned":
                    pieces.append(f"{co_name} ko co-applicant ke roop mein include kar sakte hain.")
                elif co_name == "mentioned":
                    pieces.append("Co-applicant option bhi note kar liya hai.")
                if emi_text and merged.get("existing_emi") != "mentioned":
                    pieces.append(f"Existing EMI {emi_text} bhi consider hogi.")
                elif merged.get("existing_emi") == "mentioned":
                    pieces.append("Existing EMI bhi consider karni hogi.")
                return " ".join(pieces + [next_question])
            if docs or extracted.get("tenure_years"):
                pieces = []
                if docs:
                    pieces.append(f"Perfect, {', '.join(sorted(docs))} ready note kar liye.")
                if tenure_text:
                    pieces.append(f"{tenure_text} tenure EMI planning ke liye workable rahega.")
                return " ".join(pieces + [next_question])

        return None
    
    def respond(self, session_id: str, customer_id: str, agent_id: str,
                customer_message: str, briefing: Dict,
                preferred_language: Optional[str] = None) -> Dict:
        """
        Generate agent response to customer message.
        Detect income revisions and return facts to update.
        
        Args:
            session_id: Unique session ID
            customer_id: Customer identifier
            agent_id: Agent name (AGT_A, AGT_B, etc)
            customer_message: What customer just said
            briefing: { customer_name, session_count, facts: [...] }
        
        Returns:
            {
                "agent_response": str,          # What agent says
                "income_revised": bool,         # Did income change?
                "new_income_value": str | None, # New income if revised
                "turn_count": int,              # Turns so far in session
                "facts_to_update": list,        # Facts to write to WAL
                "session_id": str
            }
        """
        try:
            # Step 1: Initialize session history
            self.history.setdefault(session_id, [])

            income_revised, new_income_value = self._detect_income_revision(
                customer_message, briefing
            )
            facts_to_update = self._build_structured_facts(
                session_id=session_id,
                customer_message=customer_message,
                briefing=briefing,
                income_revised=income_revised,
                new_income_value=new_income_value,
            )
            
            # Step 2: Build conversation history string (last 4 turns)
            conversation_history = self._build_conversation_history(session_id)
            
            # Step 3: Summarize briefing (max 300 chars)
            briefing_summary = self._build_briefing_summary(briefing)

            grounded_response = self._build_grounded_response(
                customer_message=customer_message,
                briefing=briefing,
                preferred_language=preferred_language,
                income_revised=income_revised,
                new_income_value=new_income_value,
            )
            response_text = grounded_response or ""
            
            if not response_text:
                # Step 4: Build prompt
                prompt_text = self._build_conversation_prompt(
                    agent_id=agent_id,
                    customer_name=self._display_name(briefing) or "Customer",
                    briefing_summary=briefing_summary,
                    conversation_history=conversation_history,
                    customer_message=customer_message,
                    preferred_language=preferred_language,
                )
                
                # Step 5: Call ollama only when deterministic generation did not resolve the turn.
                response = requests.post(
                    f"{self.ollama_api}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt_text,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_ctx": 2048,
                            "num_predict": 120
                        }
                    },
                    timeout=20
                )
                response.raise_for_status()
                response_text = response.json().get("response", "").strip()
            
            if not response_text:
                return self._fallback_response(
                    session_id,
                    customer_message,
                    briefing,
                    preferred_language=preferred_language,
                )
            
            # Step 6: Persist extracted structured facts immediately (WAL first).
            if facts_to_update and self.wal_logger:
                try:
                    self.wal_logger.append(
                        session_id=session_id,
                        customer_id=customer_id,
                        agent_id=agent_id,
                        bank_id=os.getenv("BANK_ID", "cooperative_bank_01"),
                        facts=facts_to_update
                    )
                except Exception as e:
                    logger.error(f"WAL write failed: {e}")
            
            # Step 8: Append to history
            self.history[session_id].append({
                "role": "customer",
                "content": customer_message
            })
            self.history[session_id].append({
                "role": "agent",
                "content": response_text
            })
            
            # Step 9: Trim history to max turns
            max_entries = self.max_history_turns * 2
            if len(self.history[session_id]) > max_entries:
                self.history[session_id] = self.history[session_id][-max_entries:]
            
            # Step 10: Return response dict
            return {
                "agent_response": response_text,
                "income_revised": income_revised,
                "new_income_value": new_income_value,
                "turn_count": len(self.history[session_id]) // 2,
                "facts_to_update": facts_to_update,
                "session_id": session_id
            }
        
        except Exception as e:
            logger.error(f"ConversationAgent.respond error: {e}")
            return self._fallback_response(
                session_id,
                customer_message,
                briefing,
                preferred_language=preferred_language,
            )
    
    def _build_conversation_history(self, session_id: str) -> str:
        """Format last 4 turns as string."""
        history = self.history.get(session_id, [])
        
        if not history:
            return "This is the first message this session."
        
        # Take last 8 entries (4 turns)
        recent = history[-8:]
        lines = []
        for entry in recent:
            role = "CUSTOMER" if entry["role"] == "customer" else "AGENT"
            lines.append(f"{role}: {entry['content']}")
        
        return "\n".join(lines)
    
    def _build_briefing_summary(self, briefing: Dict) -> str:
        """Compact briefing in max 300 chars."""
        facts = (
            briefing.get("facts")
            or briefing.get("verified_facts", []) + briefing.get("unverified_facts", [])
        )
        if not facts:
            return "No prior facts."
        
        lines = []
        for fact in facts[:5]:
            f_type = fact.get("type", "")
            f_value = fact.get("value", "")
            lines.append(f"- {f_type}: {f_value}")
        
        summary = " | ".join(lines)
        if len(summary) > 300:
            summary = summary[:300] + "..."
        
        return summary
    
    def _build_conversation_prompt(self, agent_id: str, customer_name: str,
                                    briefing_summary: str,
                                    conversation_history: str,
                                    customer_message: str,
                                    preferred_language: Optional[str] = None) -> str:
        """Build the exact conversation prompt for phi4-mini."""
        resolved_language = (preferred_language or self._detect_message_language(customer_message)).lower()
        if resolved_language == "english":
            language_rule = (
                "- LANGUAGE RULE: Reply ONLY in English. "
                "Do not switch to Hindi or Hinglish."
            )
        elif resolved_language == "hindi":
            language_rule = (
                "- LANGUAGE RULE: Reply ONLY in Hindi. "
                "Prefer natural Hindi phrasing."
            )
        else:
            language_rule = (
                "- LANGUAGE RULE: Reply ONLY in natural Hinglish. "
                "Mix Hindi and English naturally, mostly in Latin script."
            )

        prompt = f"""You are Agent {agent_id}, a loan officer at a cooperative bank
in Pune, India. You are speaking with {customer_name}.

YOUR MEMORY (from previous sessions):
{briefing_summary}

CONVERSATION SO FAR:
{conversation_history}

CUSTOMER JUST SAID:
"{customer_message}"

Rules:
{language_rule}
- NEVER ask for information already in your memory
- If customer mentions a NEW income figure: acknowledge naturally
  e.g. "Achha, 62,000 ho gayi — yeh toh acchi baat hai Rajesh ji"
- If asked about eligibility: give indicative figure with caveat
  e.g. "~48 lakh ke aas paas hoga, but Form 16 confirm karega"
- If customer mentions document: acknowledge and explain next step
- Keep response under 60 words
- End with ONE soft question that moves the conversation forward
- Sound warm and helpful, not robotic

NEVER say:
- "According to our records"
- "Our system shows"
- "As per your profile"

Your response:"""
        return prompt

    def _detect_message_language(self, text: str) -> str:
        """Return 'hindi', 'english', or 'hinglish' using lightweight heuristics."""
        if not text:
            return "hinglish"

        if re.search(r"[\u0900-\u097F]", text):
            lowered = text.lower()
            english_hits = len(re.findall(r"\b(home|loan|income|salary|document|english)\b", lowered))
            return "hinglish" if english_hits > 0 else "hindi"

        lowered = text.lower()
        hindi_tokens = {
            "mera", "meri", "mere", "hai", "hain", "nahi", "nahin", "kya",
            "aap", "hum", "main", "kar", "karna", "loan", "ji", "pichle",
            "baat", "income", "salary", "ghar", "patni", "wife",
        }
        english_tokens = {
            "the", "is", "are", "my", "your", "please", "document", "salary",
            "income", "loan", "amount", "eligible", "eligibility",
        }

        words = re.findall(r"[a-zA-Z]+", lowered)
        if not words:
            return "hinglish"

        hi_score = sum(1 for w in words if w in hindi_tokens)
        en_score = sum(1 for w in words if w in english_tokens)
        if hi_score > 0 and en_score > 0:
            return "hinglish"
        return "hindi" if hi_score >= en_score else "english"
    
    def _detect_income_revision(self, customer_message: str,
                                briefing: Dict) -> tuple:
        """
        Detect if customer mentioned a new income figure.
        
        Returns: (income_revised: bool, new_income_value: str | None)
        """
        # Extract numbers from message - exclude long numbers like Aadhaar (12 digits)
        # Only match 4-6 digit standalone numbers (income range)
        # First, mask out any 12-digit sequences to avoid matching subsets
        masked = re.sub(r'\d{9,}', 'MASKED', customer_message)
        numbers = re.findall(r'\b(\d{4,6})\b', masked)
        if not numbers:
            return False, None
        
        # Get existing income from briefing
        existing_income = self._get_existing_income(briefing)
        
        # Check if any number is a realistic income that differs from existing
        for num_str in numbers:
            num = int(num_str)
            
            # Realistic income range: 30K - 200K
            if not (30000 <= num <= 200000):
                continue
            
            # Check if different from existing
            if existing_income and str(num) == existing_income:
                continue
            
            # Found a revision!
            return True, str(num)
        
        return False, None
    
    def _get_existing_income(self, briefing: Dict) -> Optional[str]:
        """Extract income value from briefing facts."""
        facts = briefing.get("facts", [])
        for fact in facts:
            if fact.get("type") == "income":
                value = fact.get("value", "")
                numbers = re.findall(r'\d+', str(value))
                if numbers:
                    return numbers[0]
        return None
    
    def get_history(self, session_id: str) -> List[Dict]:
        """Return conversation history for a session."""
        return self.history.get(session_id, [])
    
    def clear_session(self, session_id: str) -> None:
        """Clear history for a session."""
        self.history.pop(session_id, None)
    
    def _fallback_response(self, session_id: str, customer_message: str = "",
                           briefing: Dict = None,
                           preferred_language: Optional[str] = None) -> Dict:
        """Context-aware fallback when ollama fails."""
        msg_lower = customer_message.lower() if customer_message else ""
        lang = (preferred_language or self._detect_message_language(customer_message)).lower()
        facts = []
        if briefing:
            facts = (
                briefing.get("facts")
                or briefing.get("verified_facts", []) + briefing.get("unverified_facts", [])
            )

        # Detect income mention
        income_revised, new_income_value = self._detect_income_revision(
            customer_message, briefing or {}
        )

        # Build context-aware response
        if lang == "english":
            if income_revised and new_income_value:
                response = (
                    f"Understood, your monthly income is now {new_income_value}. "
                    "That should improve eligibility slightly. Can you share your latest salary slip?"
                )
            elif any(w in msg_lower for w in ["document", "salary slip", "form 16", "payslip"]):
                response = (
                    "Sure. We will need the last 3 months' salary slips and Form 16. "
                    "Do you have both ready?"
                )
            elif any(w in msg_lower for w in ["eligib", "loan", "amount", "lakh"]):
                income = next((f["value"] for f in facts if f.get("type") == "income"), None)
                if income:
                    response = (
                        f"Based on your income {income}, indicative eligibility is around 48 lakhs. "
                        "Final value will be confirmed after document verification."
                    )
                else:
                    response = (
                        "We can estimate eligibility from income and existing EMI. "
                        "Could you share your latest salary details?"
                    )
            elif any(w in msg_lower for w in ["property", "plot", "flat", "house"]):
                response = (
                    "For the property, we will need title documents and supporting land/property papers. "
                    "Are these available with you?"
                )
            else:
                response = (
                    "Noted. We will proceed step by step with your home loan process. "
                    "Anything else you want to add right now?"
                )
        elif lang == "hinglish":
            if income_revised and new_income_value:
                response = (
                    f"Achha, monthly income ab {new_income_value} ho gayi. "
                    "Isse eligibility thodi improve hogi. Kya updated salary slip ready hai?"
                )
            elif any(w in msg_lower for w in ["document", "salary slip", "form 16", "payslip"]):
                response = (
                    "Sure, last 3 months ke salary slips aur Form 16 chahiye honge. "
                    "Kya dono documents handy hain?"
                )
            elif any(w in msg_lower for w in ["eligib", "kitna", "loan", "amount", "lakh"]):
                income = next((f["value"] for f in facts if f.get("type") == "income"), None)
                if income:
                    response = (
                        f"Aapki income {income} ke basis par indicative eligibility around 48 lakh ho sakti hai. "
                        "Final amount document verification ke baad confirm hoga."
                    )
                else:
                    response = (
                        "Eligibility income aur EMI par depend karegi. "
                        "Can you share your latest salary details?"
                    )
            elif any(w in msg_lower for w in ["property", "plot", "flat", "ghar"]):
                response = (
                    "Property ke liye title papers aur land documents chahiye honge. "
                    "Kya ye documents available hain?"
                )
            else:
                response = (
                    "Noted, maine yeh detail capture kar li. "
                    "Ab next step ke liye aap kya confirm karna chahenge?"
                )
        elif income_revised and new_income_value:
            response = (
                f"Achha, {new_income_value} ho gayi salary — yeh toh acchi baat hai! "
                "Revised income ke saath eligibility thodi aur improve hogi. "
                "Kya updated salary slip available hai?"
            )
        elif any(w in msg_lower for w in ["document", "salary slip", "form 16", "payslip"]):
            response = (
                "Bilkul, documents ke liye — salary slip last 3 months ka "
                "aur Form 16 chahiye hoga. Kya yeh ready hai aapke paas?"
            )
        elif any(w in msg_lower for w in ["eligib", "kitna", "loan", "amount", "lakh"]):
            # Try to find income + emi from facts
            income = next((f["value"] for f in facts if f.get("type") == "income"), None)
            if income:
                response = (
                    f"Aapki income {income} ke hisaab se, indicative eligibility "
                    "~48 lakh ke aas paas hogi — final figure Form 16 se confirm hoga."
                )
            else:
                response = (
                    "Income aur EMI ke hisaab se hum eligibility calculate karenge. "
                    "Kya aap salary slip share kar sakte hain?"
                )
        elif any(w in msg_lower for w in ["property", "nashik", "plot", "flat", "ghar"]):
            response = (
                "Nashik wali property ke liye 7/12 extract aur encumbrance certificate "
                "chahiye hoga. Kya yeh documents ready hain?"
            )
        elif any(w in msg_lower for w in ["sunita", "wife", "co-applicant", "co applicant"]):
            response = (
                "Sunita ji ka income proof bhi include kar lenge toh combined eligibility "
                "improve hogi. Unka salary slip bhi arrange kar lijiye."
            )
        else:
            response = (
                "Yeh note kar liya. Aapki application mein hum "
                "step-by-step aage badhenge — koi aur details hain jo share karna chahenge?"
            )

        facts_to_update = []
        if income_revised and new_income_value:
            facts_to_update = [{
                "fact_id": f"F_REV_{session_id}_{int(__import__('time').time())}",
                "type": "income",
                "value": f"{new_income_value}_INR_MONTHLY",
                "relationship": "updates",
                "verified": False,
                "source": "customer_verbal_revision",
                "confidence": 0.85
            }]

        return {
            "agent_response": response,
            "income_revised": income_revised,
            "new_income_value": new_income_value,
            "turn_count": len(self.history.get(session_id, [])) // 2,
            "facts_to_update": facts_to_update,
            "session_id": session_id
        }
