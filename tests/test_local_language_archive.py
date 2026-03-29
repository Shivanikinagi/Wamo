import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks

from src.api.models import SessionConverseRequest, SessionEndRequest, SessionStartRequest
from src.api.session import session_converse, session_end, session_start
from src.core.transcript_archive import TranscriptArchive


class _NoOpTokenizer:
    def tokenize(self, text: str):
        return text, {}


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl: int):
        self.store[key] = value

    async def delete(self, key: str):
        self.store.pop(key, None)


def _archive() -> TranscriptArchive:
    return TranscriptArchive(db_path=os.path.join(tempfile.mkdtemp(), "archive.db"))


@pytest.mark.asyncio
async def test_session_start_returns_language_selector_and_archives_first_turn():
    archive = _archive()

    resp = await session_start(
        req=SessionStartRequest(
            customer_id="phone:9876543210",
            session_type="home_loan_processing",
            agent_id="AGENT_A",
            consent_id="demo_consent",
        ),
        background_tasks=BackgroundTasks(),
        wal_logger=MagicMock(),
        mem0_bridge=MagicMock(),
        consent_db=MagicMock(verify_consent=MagicMock(return_value=True)),
        cbs_preseeder=MagicMock(preseed=AsyncMock(return_value=[])),
        briefing_builder=MagicMock(
            build=AsyncMock(
                return_value={
                    "context_summary": "Income 62000, co-applicant discussed last Tuesday",
                    "preferred_language": "hinglish",
                    "has_prior_context": True,
                }
            )
        ),
        briefing_speech_builder=MagicMock(),
        redis_cache=_FakeRedis(),
        redpanda_producer=None,
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=MagicMock(is_enabled=MagicMock(return_value=False)),
        transcript_archive=archive,
    )

    assert resp.awaiting_language_selection is True
    assert "Hindi, English, or Hinglish" in resp.greeting_message
    turns = archive.get_turns(resp.session_id)
    assert turns[0]["role"] == "assistant"
    assert "Hinglish" in turns[0]["text"]


@pytest.mark.asyncio
async def test_first_customer_turn_locks_hinglish_and_persists_choice():
    archive = _archive()
    redis = _FakeRedis()

    start_resp = await session_start(
        req=SessionStartRequest(
            customer_id="RAJESH_001",
            session_type="home_loan_processing",
            agent_id="AGENT_B",
            consent_id="demo_consent",
        ),
        background_tasks=BackgroundTasks(),
        wal_logger=MagicMock(),
        mem0_bridge=MagicMock(),
        consent_db=MagicMock(verify_consent=MagicMock(return_value=True)),
        cbs_preseeder=MagicMock(preseed=AsyncMock(return_value=[])),
        briefing_builder=MagicMock(build=AsyncMock(return_value={"preferred_language": "english"})),
        briefing_speech_builder=MagicMock(),
        redis_cache=redis,
        redpanda_producer=None,
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=MagicMock(is_enabled=MagicMock(return_value=False)),
        transcript_archive=archive,
    )

    resp = await session_converse(
        req=SessionConverseRequest(
            session_id=start_resp.session_id,
            customer_id="RAJESH_001",
            customer_message="Hinglish please",
        ),
        wal_logger=MagicMock(append=MagicMock()),
        tokenizer=_NoOpTokenizer(),
        briefing_builder=MagicMock(build=AsyncMock(return_value={"preferred_language": "english"})),
        redis_cache=redis,
        theme_memory_client=MagicMock(is_enabled=MagicMock(return_value=False)),
        transcript_archive=archive,
        briefing_speech_builder=MagicMock(
            build_opening=MagicMock(return_value="Hi Rajesh, pichli baar aapne co-applicant mention kiya tha. Shall I include that income too?")
        ),
        conversation_agent=MagicMock(),
    )

    assert resp.language_locked is True
    assert resp.preferred_language == "hinglish"
    stored = archive.get_session(start_resp.session_id)
    assert stored["preferred_language"] == "hinglish"


@pytest.mark.asyncio
async def test_session_end_uses_archived_turns_when_transcript_missing():
    archive = _archive()
    archive.start_session(
        session_id="sess_local_end",
        customer_id="RAJESH_001",
        agent_id="AGENT_C",
        preferred_language="hinglish",
        metadata={"awaiting_language_selection": False},
    )
    archive.append_turn("sess_local_end", "assistant", "Hindi, English, or Hinglish?")
    archive.append_turn("sess_local_end", "user", "Hindi")
    archive.append_turn("sess_local_end", "assistant", "Namaste, aaj hum home loan details continue karte hain.")

    resp = await session_end(
        req=SessionEndRequest(session_id="sess_local_end", transcript=None),
        background_tasks=BackgroundTasks(),
        redis_cache=None,
        wal_logger=MagicMock(
            append=MagicMock(),
            replay=MagicMock(return_value=[{"type": "preferred_language", "value": "hinglish"}]),
        ),
        mem0_bridge=MagicMock(add_after_wal=AsyncMock(return_value={"status": "ok"})),
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=MagicMock(is_enabled=MagicMock(return_value=False)),
        transcript_archive=archive,
    )

    assert resp.transcript_archived is True
    stored = archive.get_session("sess_local_end")
    assert "USER: Hindi" in stored["full_transcript"]
    assert "AI: Namaste" in stored["full_transcript"]
