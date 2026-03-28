"""Tests for PS01 integration with Theme long-context memory service."""

import json
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from src.api.models import SessionConverseRequest, SessionEndRequest, SessionStartRequest
from src.api.session import session_converse, session_end, session_start


class _NoOpTokenizer:
    def tokenize(self, text: str):
        return text, {}


class _FakeRedis:
    def __init__(self, session_payload: dict | None = None):
        self._payload = session_payload
        self._store = {}

    async def get(self, key: str):
        if key.startswith("session:") and self._payload is not None:
            return json.dumps(self._payload)
        return self._store.get(key)

    async def set(self, key: str, value: str, ttl: int):
        self._store[key] = value

    async def delete(self, key: str):
        self._store.pop(key, None)


@pytest.mark.asyncio
async def test_session_start_fetches_theme_briefing_and_merges_summary():
    req = SessionStartRequest(
        customer_id="C001",
        session_type="home_loan_processing",
        agent_id="AGENT_1",
        consent_id="consent_123",
    )

    fake_theme = MagicMock()
    fake_theme.is_enabled.return_value = True
    fake_theme.send_call_start = AsyncMock(return_value=True)
    fake_theme.get_briefing = AsyncMock(
        return_value={
            "phone_number": "C001",
            "total_calls_found": 2,
            "highlights": [
                {"customer_highlights": ["Need home loan", "Income revised to 65k"]}
            ],
        }
    )

    resp = await session_start(
        req=req,
        background_tasks=BackgroundTasks(),
        wal_logger=MagicMock(),
        mem0_bridge=MagicMock(),
        consent_db=MagicMock(verify_consent=MagicMock(return_value=True)),
        cbs_preseeder=MagicMock(preseed=AsyncMock(return_value=[])),
        briefing_builder=MagicMock(build=AsyncMock(return_value={"context_summary": "Local summary"})),
        briefing_speech_builder=MagicMock(build_opening=MagicMock(return_value="Welcome")),
        redis_cache=None,
        redpanda_producer=None,
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=fake_theme,
    )

    assert resp.status == "ready"
    assert "External memory" in resp.context_summary
    fake_theme.send_call_start.assert_awaited_once()
    fake_theme.get_briefing.assert_awaited_once_with("C001")


@pytest.mark.asyncio
async def test_session_converse_pushes_user_and_assistant_turns_to_theme():
    req = SessionConverseRequest(
        session_id="sess_abc123",
        customer_id="C001",
        customer_message="Meri income 65000 hai",
    )

    fake_theme = MagicMock()
    fake_theme.is_enabled.return_value = True
    fake_theme.send_transcript = AsyncMock(return_value=True)

    redis_payload = {
        "customer_id": "C001",
        "agent_id": "AGENT_1",
        "started_at": datetime.now(UTC).isoformat(),
        "preferred_language": "hindi",
    }

    fake_result = {
        "agent_response": "Thik hai, income update kar diya.",
        "facts_to_update": [{"type": "income", "value": "65000"}],
        "income_revised": True,
        "new_income_value": 65000,
    }

    with patch("src.core.conversation_agent.ConversationAgent") as cls:
        cls.return_value.respond.return_value = fake_result

        resp = await session_converse(
            req=req,
            wal_logger=MagicMock(append=MagicMock()),
            tokenizer=_NoOpTokenizer(),
            briefing_builder=MagicMock(build=AsyncMock(return_value={})),
            redis_cache=_FakeRedis(redis_payload),
            theme_memory_client=fake_theme,
        )

    assert resp.memory_updated is True
    assert fake_theme.send_transcript.await_count == 2
    first_call = fake_theme.send_transcript.await_args_list[0].args
    second_call = fake_theme.send_transcript.await_args_list[1].args
    assert first_call[1] == "user"
    assert second_call[1] == "assistant"


@pytest.mark.asyncio
async def test_session_end_sends_end_report_to_theme():
    req = SessionEndRequest(
        session_id="sess_end_001",
        transcript="Customer confirmed salary and uploaded payslip.",
    )

    fake_theme = MagicMock()
    fake_theme.is_enabled.return_value = True
    fake_theme.send_call_end = AsyncMock(return_value=True)

    redis_payload = {
        "customer_id": "C001",
        "agent_id": "AGENT_1",
        "started_at": datetime.now(UTC).isoformat(),
    }

    resp = await session_end(
        req=req,
        background_tasks=BackgroundTasks(),
        redis_cache=_FakeRedis(redis_payload),
        wal_logger=MagicMock(replay=MagicMock(return_value=[]), append=MagicMock()),
        mem0_bridge=MagicMock(add_after_wal=AsyncMock(return_value={"status": "ok"})),
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=fake_theme,
    )

    assert resp.status == "completed"
    fake_theme.send_call_end.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_start_uses_phone_from_customer_id_for_theme_calls():
    req = SessionStartRequest(
        customer_id="phone:919999000111",
        session_type="home_loan_processing",
        agent_id="AGENT_1",
        consent_id="consent_123",
    )

    fake_theme = MagicMock()
    fake_theme.is_enabled.return_value = True
    fake_theme.send_call_start = AsyncMock(return_value=True)
    fake_theme.get_briefing = AsyncMock(
        return_value={
            "phone_number": "+919999000111",
            "total_calls_found": 0,
            "highlights": [],
        }
    )

    resp = await session_start(
        req=req,
        background_tasks=BackgroundTasks(),
        wal_logger=MagicMock(),
        mem0_bridge=MagicMock(),
        consent_db=MagicMock(verify_consent=MagicMock(return_value=True)),
        cbs_preseeder=MagicMock(preseed=AsyncMock(return_value=[])),
        briefing_builder=MagicMock(build=AsyncMock(return_value={"context_summary": "Local summary"})),
        briefing_speech_builder=MagicMock(build_opening=MagicMock(return_value="Welcome")),
        redis_cache=_FakeRedis(),
        redpanda_producer=None,
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=fake_theme,
    )

    assert resp.status == "ready"
    fake_theme.send_call_start.assert_awaited_once()
    sent_ref = fake_theme.send_call_start.await_args.kwargs["customer_ref"]
    assert sent_ref == "+919999000111"
    fake_theme.get_briefing.assert_awaited_once_with("+919999000111")


@pytest.mark.asyncio
async def test_session_start_uses_redis_theme_ref_mapping_when_present():
    req = SessionStartRequest(
        customer_id="C001",
        session_type="home_loan_processing",
        agent_id="AGENT_1",
        consent_id="consent_123",
    )

    fake_redis = _FakeRedis()
    await fake_redis.set("theme_ref:C001", "+919888777666", 86400)

    fake_theme = MagicMock()
    fake_theme.is_enabled.return_value = True
    fake_theme.send_call_start = AsyncMock(return_value=True)
    fake_theme.get_briefing = AsyncMock(
        return_value={
            "phone_number": "+919888777666",
            "total_calls_found": 0,
            "highlights": [],
        }
    )

    resp = await session_start(
        req=req,
        background_tasks=BackgroundTasks(),
        wal_logger=MagicMock(),
        mem0_bridge=MagicMock(),
        consent_db=MagicMock(verify_consent=MagicMock(return_value=True)),
        cbs_preseeder=MagicMock(preseed=AsyncMock(return_value=[])),
        briefing_builder=MagicMock(build=AsyncMock(return_value={"context_summary": "Local summary"})),
        briefing_speech_builder=MagicMock(build_opening=MagicMock(return_value="Welcome")),
        redis_cache=fake_redis,
        redpanda_producer=None,
        tokenizer=_NoOpTokenizer(),
        theme_memory_client=fake_theme,
    )

    assert resp.status == "ready"
    fake_theme.get_briefing.assert_awaited_once_with("+919888777666")