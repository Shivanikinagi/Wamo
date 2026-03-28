"""Client for integrating PS01 with Theme long-context memory service."""

from __future__ import annotations

import logging
import os
from datetime import datetime, UTC
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ThemeMemoryClient:
    """Push PS01 call events to Theme service and fetch memory briefing."""

    def __init__(self) -> None:
        self.base_url = os.getenv("THEME_MEMORY_BASE_URL", "").strip().rstrip("/")
        enabled_raw = os.getenv("PS01_THEME_INTEGRATION_ENABLED", "false").strip().lower()
        self.enabled = enabled_raw in {"1", "true", "yes", "on"}
        self.timeout_seconds = float(os.getenv("THEME_MEMORY_TIMEOUT_SECONDS", "4"))

    def is_enabled(self) -> bool:
        return self.enabled and bool(self.base_url)

    async def get_briefing(self, customer_ref: str) -> dict[str, Any]:
        if not self.is_enabled():
            return {"phone_number": customer_ref, "total_calls_found": 0, "highlights": []}

        url = f"{self.base_url}/api/memory/briefing/{customer_ref}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.get(url)
                res.raise_for_status()
                data = res.json()
                if isinstance(data, dict):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Theme briefing fetch failed: %s", exc)

        return {"phone_number": customer_ref, "total_calls_found": 0, "highlights": []}

    async def send_call_start(self, call_id: str, customer_ref: str) -> bool:
        payload = {
            "message": {
                "type": "call-start",
                "call": {"id": call_id, "customer": {"number": customer_ref}},
            }
        }
        return await self._post_webhook(payload)

    async def send_transcript(self, call_id: str, role: str, text: str) -> bool:
        if not text.strip():
            return True

        payload = {
            "message": {
                "type": "transcript",
                "call": {"id": call_id},
                "role": role,
                "transcript": text,
            }
        }
        return await self._post_webhook(payload)

    async def send_call_end(
        self,
        call_id: str,
        full_transcript: str,
        duration_seconds: int = 0,
        customer_ref: str = "",
    ) -> bool:
        payload = {
            "message": {
                "type": "end-of-call-report",
                "durationSeconds": max(int(duration_seconds), 0),
                "transcript": full_transcript or "",
                "endedAt": datetime.now(UTC).isoformat(),
                "call": {
                    "id": call_id,
                    "customer": {"number": customer_ref} if customer_ref else {},
                },
            }
        }
        return await self._post_webhook(payload)

    async def _post_webhook(self, payload: dict[str, Any]) -> bool:
        if not self.is_enabled():
            return False

        url = f"{self.base_url}/api/vapi/webhook"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(url, json=payload)
                res.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Theme webhook post failed: %s", exc)
            return False