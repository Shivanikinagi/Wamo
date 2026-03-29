# src/core/wal_shipper.py
"""
Background WAL Shipper for  branch edge nodes.

Polls the WALLogger for unshipped entries and publishes them to Redpanda.
Runs as a long-lived asyncio background task.
"""

import asyncio
import logging
from typing import Optional

from src.core.wal import WALLogger
from src.infra import RedpandaProducer

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5  # seconds (default, kept for backward-compat)


class WALShipper:
    """Ships unshipped WAL entries to Redpanda on a polling loop."""

    def __init__(
        self,
        wal_logger: WALLogger,
        producer: RedpandaProducer,
        poll_interval: float = 5.0,
    ):
        self.wal_logger = wal_logger
        self.producer = producer
        self._poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background shipping loop."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                while True:
                    await asyncio.sleep(self._poll_interval)
                    await self._ship_pending()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("WALShipper _run crashed, restarting in 5s: %s", e)
                await asyncio.sleep(5)

    async def _ship_pending(self) -> None:
        entries = self.wal_logger.get_unshipped()
        for entry in entries:
            ikey = entry.get("idempotency_key")
            try:
                await self.producer.publish_wal_entry(entry)
                if ikey:
                    self.wal_logger.mark_shipped(ikey)
                    logger.info("WALShipper: shipped idempotency_key=%s", ikey)
            except Exception as exc:
                logger.error(
                    "WALShipper: failed to ship idempotency_key=%s (%s); "
                    "entry remains unshipped",
                    ikey,
                    exc,
                )
