"""
Tests for Phase 1 infrastructure: RedisCache, RedpandaProducer, RedpandaConsumer.

All external services (redis, aiokafka) are mocked — no real connections needed.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run an async coroutine in the current thread."""
    return asyncio.run(coro)


# ===========================================================================
# RedisCache tests
# ===========================================================================

class TestRedisCache(unittest.TestCase):

    def _make_cache(self, mock_redis_cls):
        """Instantiate RedisCache with a patched redis.asyncio.Redis."""
        from src.infra.redis_cache import RedisCache
        cache = RedisCache(host="localhost", port=6379, bank_id="demo_bank")
        # Replace lazily-created client with mock
        cache._client = mock_redis_cls
        return cache

    # --- get_summary --------------------------------------------------------

    def test_get_summary_hit(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="Customer has existing loan.")
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        result = run(cache.get_summary("cust_001"))

        mock_client.get.assert_awaited_once_with("demo_bank:summary:cust_001")
        self.assertEqual(result, "Customer has existing loan.")

    def test_get_summary_miss(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        result = run(cache.get_summary("cust_002"))
        self.assertIsNone(result)

    def test_get_summary_connection_error_returns_none(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RedisConnectionError("refused"))
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        result = run(cache.get_summary("cust_003"))
        self.assertIsNone(result)  # graceful degradation

    # --- set_summary --------------------------------------------------------

    def test_set_summary_calls_set_with_ttl(self):
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        run(cache.set_summary("cust_001", "Has home loan", ttl=7200))

        mock_client.set.assert_awaited_once_with(
            "demo_bank:summary:cust_001", "Has home loan", ex=7200
        )

    def test_set_summary_default_ttl(self):
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        run(cache.set_summary("cust_001", "summary"))

        _, kwargs = mock_client.set.call_args
        self.assertEqual(kwargs["ex"], 14400)

    def test_set_summary_connection_error_does_not_raise(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=RedisConnectionError("refused"))
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        # Should not raise
        run(cache.set_summary("cust_001", "summary"))

    # --- acquire_lock -------------------------------------------------------

    def test_acquire_lock_success(self):
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        token = run(cache.acquire_lock("cust_001"))
        self.assertIsNotNone(token)
        self.assertIsInstance(token, str)
        # Verify SET was called with the token, nx=True, ex=30
        call_args = mock_client.set.call_args
        self.assertEqual(call_args.args[0], "demo_bank:lock:cust_001")
        self.assertEqual(call_args.args[1], token)
        self.assertTrue(call_args.kwargs["nx"])
        self.assertEqual(call_args.kwargs["ex"], 30)

    def test_acquire_lock_already_held(self):
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=None)  # SETNX fails → None
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        token = run(cache.acquire_lock("cust_001"))
        self.assertIsNone(token)

    def test_acquire_lock_connection_error_returns_none(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=RedisConnectionError("refused"))
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        token = run(cache.acquire_lock("cust_001"))
        self.assertIsNone(token)

    # --- release_lock -------------------------------------------------------

    def test_release_lock_calls_lua_script(self):
        mock_client = AsyncMock()
        mock_client.eval = AsyncMock(return_value=1)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        run(cache.release_lock("cust_001", "some-token"))
        mock_client.eval.assert_awaited_once()
        call_args = mock_client.eval.call_args
        # KEYS[1] and ARGV[1]
        self.assertEqual(call_args.args[1], 1)
        self.assertEqual(call_args.args[2], "demo_bank:lock:cust_001")
        self.assertEqual(call_args.args[3], "some-token")

    # --- invalidate_summary -------------------------------------------------

    def test_invalidate_summary_calls_delete(self):
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        run(cache.invalidate_summary("cust_001"))
        mock_client.delete.assert_awaited_once_with("demo_bank:summary:cust_001")

    # --- key namespacing ----------------------------------------------------

    def test_key_namespacing_bank_id(self):
        from src.infra.redis_cache import RedisCache
        cache_a = RedisCache("localhost", 6379, "bank_a")
        cache_b = RedisCache("localhost", 6379, "bank_b")

        self.assertEqual(cache_a._summary_key("cust_1"), "bank_a:summary:cust_1")
        self.assertEqual(cache_b._summary_key("cust_1"), "bank_b:summary:cust_1")
        self.assertNotEqual(
            cache_a._summary_key("cust_1"), cache_b._summary_key("cust_1")
        )

    # --- close --------------------------------------------------------------

    def test_close_calls_aclose(self):
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        from src.infra.redis_cache import RedisCache
        cache = RedisCache("localhost", 6379, "demo_bank")
        cache._client = mock_client

        run(cache.close())
        mock_client.aclose.assert_awaited_once()
        self.assertIsNone(cache._client)


# ===========================================================================
# RedpandaProducer tests
# ===========================================================================

class TestRedpandaProducer(unittest.TestCase):

    def _make_producer(self):
        from src.infra.redpanda_producer import RedpandaProducer
        return RedpandaProducer(brokers=["redpanda:9092"], bank_id="demo_bank")

    def test_topic_name(self):
        p = self._make_producer()
        self.assertEqual(p.topic, "demo_bank.session.events")

    @patch("src.infra.redpanda_producer.AIOKafkaProducer")
    def test_connect_starts_producer(self, MockProducer):
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance

        p = self._make_producer()
        run(p.connect())

        MockProducer.assert_called_once()
        mock_instance.start.assert_awaited_once()
        self.assertIs(p._producer, mock_instance)

    @patch("src.infra.redpanda_producer.AIOKafkaProducer")
    def test_publish_wal_entry_sends_to_correct_topic(self, MockProducer):
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance

        p = self._make_producer()
        run(p.connect())

        entry = {
            "session_id": "sess_001",
            "customer_id": "cust_001",
            "text": "needs home loan",
        }
        run(p.publish_wal_entry(entry))

        mock_instance.send_and_wait.assert_awaited_once_with(
            "demo_bank.session.events",
            value=entry,
            key="cust_001",
        )

    def test_publish_without_connect_raises(self):
        p = self._make_producer()
        entry = {"customer_id": "cust_001", "session_id": "s1"}
        with self.assertRaises(RuntimeError):
            run(p.publish_wal_entry(entry))

    @patch("src.infra.redpanda_producer.AIOKafkaProducer")
    def test_close_stops_producer(self, MockProducer):
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance

        p = self._make_producer()
        run(p.connect())
        run(p.close())

        mock_instance.stop.assert_awaited_once()
        self.assertIsNone(p._producer)

    @patch("src.infra.redpanda_producer.asyncio.sleep", new_callable=AsyncMock)
    @patch("src.infra.redpanda_producer.AIOKafkaProducer")
    def test_connect_retries_on_failure(self, MockProducer, mock_sleep):
        from aiokafka.errors import KafkaConnectionError
        # Fail twice then succeed
        mock_good = AsyncMock()
        MockProducer.side_effect = [
            KafkaConnectionError("refused"),
            KafkaConnectionError("refused"),
            mock_good,
        ]
        # For the third call we need start() to succeed
        mock_good.start = AsyncMock()

        p = self._make_producer()
        run(p.connect())

        self.assertEqual(MockProducer.call_count, 3)
        self.assertEqual(mock_sleep.await_count, 2)
        # First backoff 1s, second 2s
        sleeps = [call.args[0] for call in mock_sleep.await_args_list]
        self.assertEqual(sleeps, [1, 2])


# ===========================================================================
# RedpandaConsumer tests
# ===========================================================================

class TestRedpandaConsumer(unittest.TestCase):

    def _make_consumer(self):
        from src.infra.redpanda_consumer import RedpandaConsumer
        return RedpandaConsumer(
            brokers=["redpanda:9092"],
            bank_id="demo_bank",
            group_id="test-group",
        )

    def test_topic_name(self):
        c = self._make_consumer()
        self.assertEqual(c.topic, "demo_bank.session.events")

    def test_group_id_default(self):
        from src.infra.redpanda_consumer import RedpandaConsumer
        c = RedpandaConsumer(brokers=[], bank_id="b")
        self.assertEqual(c.group_id, "central-processor")

    @patch("src.infra.redpanda_consumer.AIOKafkaConsumer")
    def test_connect_starts_consumer(self, MockConsumer):
        mock_instance = AsyncMock()
        MockConsumer.return_value = mock_instance

        c = self._make_consumer()
        run(c.connect())

        MockConsumer.assert_called_once_with(
            "demo_bank.session.events",
            bootstrap_servers=["redpanda:9092"],
            group_id="test-group",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=None,
        )
        mock_instance.start.assert_awaited_once()

    def test_consume_without_connect_raises(self):
        c = self._make_consumer()
        async def noop(e): pass
        with self.assertRaises(RuntimeError):
            run(c.consume(noop))

    @patch("src.infra.redpanda_consumer.AIOKafkaConsumer")
    def test_consume_calls_handler_and_commits(self, MockConsumer):
        """Handler is called for each message; offset committed after."""
        entry = {"session_id": "s1", "customer_id": "cust_1", "text": "loan"}
        raw_msg = MagicMock()
        raw_msg.value = json.dumps(entry).encode("utf-8")
        raw_msg.offset = 0
        raw_msg.partition = 0

        mock_instance = AsyncMock()
        MockConsumer.return_value = mock_instance

        received = []

        async def handler(e):
            received.append(e)
            # Stop after first message by raising StopAsyncIteration
            raise StopAsyncIteration

        # Make the consumer async-iterable with one message
        async def _aiter():
            yield raw_msg

        mock_instance.__aiter__ = lambda self: _aiter().__aiter__()

        c = self._make_consumer()
        c._consumer = mock_instance

        # consume() will exit when StopAsyncIteration propagates from handler
        # — but we wrap so the test doesn't see the exception.
        async def run_consume():
            try:
                await c.consume(handler)
            except StopAsyncIteration:
                pass

        run(run_consume())
        self.assertEqual(received, [entry])

    @patch("src.infra.redpanda_consumer.AIOKafkaConsumer")
    def test_consume_skips_bad_json(self, MockConsumer):
        """Malformed JSON messages are skipped; no exception raised."""
        bad_msg = MagicMock()
        bad_msg.value = b"not-valid-json{{{"
        bad_msg.offset = 5
        bad_msg.partition = 0

        mock_instance = AsyncMock()
        MockConsumer.return_value = mock_instance

        calls = []

        async def handler(e):
            calls.append(e)
            raise StopAsyncIteration

        async def _aiter():
            yield bad_msg

        mock_instance.__aiter__ = lambda self: _aiter().__aiter__()

        c = self._make_consumer()
        c._consumer = mock_instance

        async def run_consume():
            try:
                await c.consume(handler)
            except StopAsyncIteration:
                pass

        run(run_consume())
        # handler should NOT have been called for bad JSON
        self.assertEqual(calls, [])
        # but commit should have been called
        mock_instance.commit.assert_awaited()

    @patch("src.infra.redpanda_consumer.AIOKafkaConsumer")
    def test_close_stops_consumer(self, MockConsumer):
        mock_instance = AsyncMock()
        MockConsumer.return_value = mock_instance

        c = self._make_consumer()
        run(c.connect())
        run(c.close())

        mock_instance.stop.assert_awaited_once()
        self.assertIsNone(c._consumer)


if __name__ == "__main__":
    unittest.main()
