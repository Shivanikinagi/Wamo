"""Live end-to-end tests for PS01 <-> Theme integration.

These tests call running services and are skipped automatically when either
service is not reachable.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
import pytest


PS01_BASE_URL = os.getenv("PS01_BASE_URL", "http://127.0.0.1:8000")
THEME_BASE_URL = os.getenv("THEME_BASE_URL", "http://127.0.0.1:8099")


async def _is_up(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            res = await client.get(url)
            return res.status_code == 200
    except Exception:
        return False


@pytest.mark.asyncio
async def test_e2e_mapping_and_memory_handoff_live_services():
    if not await _is_up(f"{PS01_BASE_URL}/health"):
        pytest.skip("PS01 is not running")
    if not await _is_up(f"{THEME_BASE_URL}/health"):
        pytest.skip("Theme app is not running")

    customer_id = "itest_customer_001"
    phone = "+919999000111"

    async with httpx.AsyncClient(timeout=15) as client:
        map_resp = await client.post(
            f"{PS01_BASE_URL}/session/theme-ref/set",
            json={"customer_id": customer_id, "phone_number": phone},
        )
        if map_resp.status_code == 404:
            pytest.skip("PS01 server is running an older build; restart PS01 to load /session/theme-ref/set")
        assert map_resp.status_code == 200
        assert map_resp.json().get("theme_customer_ref") == phone

        start_resp = await client.post(
            f"{PS01_BASE_URL}/session/start",
            json={
                "customer_id": customer_id,
                "session_type": "home_loan_processing",
                "agent_id": "AGENT_E2E",
                "consent_id": "consent_e2e_001",
            },
        )
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        session_id = start_data.get("session_id")
        assert session_id

        end_resp = await client.post(
            f"{PS01_BASE_URL}/session/end",
            json={
                "session_id": session_id,
                "transcript": "Customer confirms salary and requests home loan follow-up.",
            },
        )
        assert end_resp.status_code == 200

        encoded_phone = quote(phone, safe="")
        ctx_resp = await client.get(f"{THEME_BASE_URL}/api/memory/context/{encoded_phone}")
        assert ctx_resp.status_code == 200
        memories = ctx_resp.json().get("memories", [])
        assert isinstance(memories, list)
        assert any(m.get("call_id") == session_id for m in memories)
