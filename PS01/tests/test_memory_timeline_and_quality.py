import json
from pathlib import Path

import pytest

from src.core.briefing_speech import BriefingSpeechBuilder
from src.core.conversation_agent import ConversationAgent
from src.core.memory_timeline import MemoryTimeline
from src.core.wal import WALLogger


def test_briefing_opening_prefers_name_over_customer_id():
    builder = BriefingSpeechBuilder()
    opening = builder.build_opening(
        {
            "customer_id": "phone:9999999999",
            "customer_name": "Rajesh",
            "preferred_language": "hinglish",
            "deterministic_recall": {
                "latest_income": {"value": "62000_INR_MONTHLY"},
                "co_applicant_name": None,
                "co_applicant_income": None,
                "loan_amount_lakh": {"value": "30"},
                "loan_type": {"value": "home loan"},
                "property_stage": {"value": "under construction"},
                "last_discussed_day": "Sunday",
            },
        }
    )

    assert "Rajesh" in opening
    assert "phone:9999999999" not in opening
    assert "30 lakh" in opening or "62000" in opening


def test_conversation_agent_grounded_hinglish_reply_uses_bank_ready_copy():
    agent = ConversationAgent()
    result = agent.respond(
        session_id="sess_quality_1",
        customer_id="phone:9999999999",
        agent_id="AGENT_A",
        customer_message="Mujhe 30 lakh ka home loan chahiye. Income 62000 hai aur wife Sunita co-applicant ho sakti hain.",
        briefing={"customer_name": "Rajesh", "facts": []},
        preferred_language="hinglish",
    )

    reply = result["agent_response"].lower()
    assert "30 lakh" in reply
    assert "62000" in reply or "62,000" in reply
    assert "sunita" in reply
    assert "emi" in reply or "salary slips" in reply or "bank statements" in reply


@pytest.mark.asyncio
async def test_memory_timeline_returns_highlights(tmp_path: Path):
    wal_path = tmp_path / "timeline.jsonl"
    wal = WALLogger(wal_path=str(wal_path))
    wal.append(
        session_id="sess_001",
        customer_id="C001",
        agent_id="AGENT_A",
        bank_id="cooperative_bank_01",
        facts=[
            {"type": "preferred_language", "value": "hinglish", "verified": True},
            {"type": "income", "value": "62000_INR_MONTHLY", "relationship": "updates", "verified": False},
            {"type": "document_ready", "value": "salary slips", "relationship": "new", "verified": False},
        ],
    )

    timeline = MemoryTimeline(wal=wal)
    result = await timeline.get_timeline("C001")

    assert len(result) == 1
    assert result[0]["fact_types"] == ["document_ready", "income", "preferred_language"]
    assert any("Language locked to hinglish" == item for item in result[0]["highlights"])
    assert any("Income noted: 62000_INR_MONTHLY" == item for item in result[0]["highlights"])
