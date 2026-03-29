"""
Redpanda (Kafka-compatible) WAL Shipper for  branch edge nodes.

Publishes tokenized WAL entries to the per-bank Redpanda topic so the
central hub can apply them to Mem0.

PII must have been stripped by spaCy tokenizer BEFORE calling publish_wal_entry().
"""

import asyncio
import json
import logging
from typing import Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError, KafkaError

logger = logging.getLogger(__name__)

_BACKOFF_INITIAL = 1      # seconds
_BACKOFF_MAX = 300        # seconds


class RedpandaProducer:
    """
    Async Redpanda/Kafka producer for WAL entries.

    Topic: {bank_id}.session.events
    Message key: customer_id (bytes) — enables partition affinity per customer.
    """

    def __init__(self, brokers: list, bank_id: str):
        self.brokers = brokers
        self.bank_id = bank_id
        self.topic = f"{bank_id}.session.events"
        self._producer: Optional[AIOKafkaProducer] = None

    async def connect(self) -> None:
        """Create and start the AIOKafkaProducer with exponential backoff."""
        backoff = _BACKOFF_INITIAL
        while True:
            try:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=self.brokers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
                )
                await self._producer.start()
                logger.info(
                    "RedpandaProducer connected to brokers=%s topic=%s",
                    self.brokers,
                    self.topic,
                )
                return
            except (KafkaConnectionError, KafkaError, Exception) as exc:
                logger.warning(
                    "RedpandaProducer connect failed (%s); retrying in %ss", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def publish_wal_entry(self, entry: dict, max_retries: int = 10) -> None:
        """
        Publish a single WAL entry dict to Redpanda.

        The entry must already have PII stripped.
        Expects entry to contain 'customer_id' and 'session_id' keys.
        """
        if self._producer is None:
            raise RuntimeError("Producer not connected. Call connect() first.")

        customer_id = entry.get("customer_id", "unknown")
        session_id = entry.get("session_id", "unknown")

        backoff = _BACKOFF_INITIAL
        attempts = 0
        while True:
            try:
                await self._producer.send_and_wait(
                    self.topic,
                    value=entry,
                    key=customer_id,
                )
                logger.info(
                    "WAL entry published: session_id=%s customer_id=%s topic=%s",
                    session_id,
                    customer_id,
                    self.topic,
                )
                return
            except (KafkaConnectionError, KafkaError) as exc:
                attempts += 1
                if attempts >= max_retries:
                    raise RuntimeError(
                        f"publish_wal_entry exceeded max_retries={max_retries} "
                        f"for customer={customer_id}"
                    ) from exc
                logger.warning(
                    "publish_wal_entry failed for customer=%s (%s); retrying in %ss",
                    customer_id,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def close(self) -> None:
        """Flush pending messages and stop the producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("RedpandaProducer closed")
