"""
Redpanda (Kafka-compatible) Central WAL Consumer for  central hub.

Reads tokenized WAL events from the per-bank topic and calls a handler
(typically Mem0Bridge.add_from_wal_entry) for each message.

Consumer group ID enables horizontal scaling: multiple consumer instances
share partition load automatically.
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError, KafkaError

logger = logging.getLogger(__name__)

_BACKOFF_INITIAL = 1      # seconds
_BACKOFF_MAX = 300        # seconds


class RedpandaConsumer:
    """
    Async Redpanda/Kafka consumer for the central hub WAL processor.

    Topic   : {bank_id}.session.events
    Group   : group_id (default "central-processor")
    Scaling : run multiple instances — Kafka balances partitions across them.
    """

    def __init__(
        self,
        brokers: list,
        bank_id: str,
        group_id: str = "central-processor",
    ):
        self.brokers = brokers
        self.bank_id = bank_id
        self.group_id = group_id
        self.topic = f"{bank_id}.session.events"
        self._consumer: Optional[AIOKafkaConsumer] = None

    async def connect(self) -> None:
        """Create and start the AIOKafkaConsumer with exponential backoff."""
        backoff = _BACKOFF_INITIAL
        while True:
            try:
                self._consumer = AIOKafkaConsumer(
                    self.topic,
                    bootstrap_servers=self.brokers,
                    group_id=self.group_id,
                    enable_auto_commit=False,   # we commit after successful handler call
                    auto_offset_reset="earliest",
                    value_deserializer=None,    # raw bytes; we parse in consume()
                )
                await self._consumer.start()
                logger.info(
                    "RedpandaConsumer connected: topic=%s group=%s brokers=%s",
                    self.topic,
                    self.group_id,
                    self.brokers,
                )
                return
            except (KafkaConnectionError, KafkaError) as exc:
                logger.warning(
                    "RedpandaConsumer connect failed (%s); retrying in %ss", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def consume(
        self, handler: Callable[[dict], Awaitable[None]]
    ) -> None:
        """
        Infinite consume loop.

        For each message:
          1. Deserialize JSON bytes → dict
          2. Call handler(entry) and await it
          3. Commit offset

        On deserialization error: log and skip (never crash).
        On handler error: log and skip (never crash) — WAL replay ensures
        at-least-once delivery if needed.
        """
        if self._consumer is None:
            raise RuntimeError("Consumer not connected. Call connect() first.")

        async for msg in self._consumer:
            # --- Deserialize ---
            try:
                entry = json.loads(msg.value.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error(
                    "Skipping undeserializable message at offset=%s partition=%s: %s",
                    msg.offset,
                    msg.partition,
                    exc,
                )
                await self._consumer.commit()
                continue

            # --- Handle ---
            try:
                await handler(entry)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Handler error for customer=%s session=%s: %s",
                    entry.get("customer_id"),
                    entry.get("session_id"),
                    exc,
                    exc_info=True,
                )
                # Still commit so we don't endlessly reprocess a poison pill.
                # WAL replay on the branch edge can recover if needed.

            await self._consumer.commit()

    async def close(self) -> None:
        """Stop the consumer and release resources."""
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            logger.info("RedpandaConsumer closed")
