"""
Redis cache layer for  multi-branch banking memory system.

Supports both primary (hub) and replica (branch edge) roles.
Keys are namespaced by bank_id for multi-tenant isolation.
"""

import logging
import uuid
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

logger = logging.getLogger(__name__)


class RedisCache:
    """
    Async Redis client for summary caching and distributed locking.

    Key schema:
      summary : {bank_id}:summary:{customer_id}
      lock    : {bank_id}:lock:{customer_id}
    """

    def __init__(self, host: str, port: int, bank_id: str, role: str = "primary"):
        self.host = host
        self.port = port
        self.bank_id = bank_id
        self.role = role
        self._client: Optional[aioredis.Redis] = None

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis(
                host=self.host,
                port=self.port,
                decode_responses=True,
            )
        return self._client

    def _summary_key(self, customer_id: str) -> str:
        return f"{self.bank_id}:summary:{customer_id}"

    def _lock_key(self, customer_id: str) -> str:
        return f"{self.bank_id}:lock:{customer_id}"

    async def get_summary(self, customer_id: str) -> Optional[str]:
        """Return cached summary string, or None on miss / connection failure."""
        try:
            return await self._get_client().get(self._summary_key(customer_id))
        except RedisConnectionError:
            logger.warning(
                "Redis connection error on get_summary for customer=%s", customer_id
            )
            return None

    async def set_summary(
        self, customer_id: str, summary: str, ttl: int = 14400
    ) -> None:
        """Cache summary with TTL (default 4 hours)."""
        try:
            await self._get_client().set(
                self._summary_key(customer_id), summary, ex=ttl
            )
        except RedisConnectionError:
            logger.warning(
                "Redis connection error on set_summary for customer=%s", customer_id
            )

    async def acquire_lock(self, customer_id: str, ttl: int = 30) -> Optional[str]:
        """
        Acquire a distributed lock via SET NX EX.

        Returns a unique token string if acquired, or None if already held / on error.
        The caller must pass the same token to release_lock().
        """
        token = str(uuid.uuid4())
        try:
            result = await self._get_client().set(
                self._lock_key(customer_id), token, nx=True, ex=ttl
            )
            return token if result is True else None
        except RedisConnectionError:
            logger.warning(
                "Redis connection error on acquire_lock for customer=%s", customer_id
            )
            return None

    _RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

    async def release_lock(self, customer_id: str, token: str) -> None:
        """Release the distributed lock only if the stored token matches."""
        try:
            await self._get_client().eval(
                self._RELEASE_SCRIPT, 1, self._lock_key(customer_id), token
            )
        except RedisConnectionError:
            logger.warning(
                "Redis connection error on release_lock for customer=%s", customer_id
            )

    async def invalidate_summary(self, customer_id: str) -> None:
        """Delete the cached summary (e.g. after a new mem0.add())."""
        try:
            await self._get_client().delete(self._summary_key(customer_id))
        except RedisConnectionError:
            logger.warning(
                "Redis connection error on invalidate_summary for customer=%s",
                customer_id,
            )

    async def close(self) -> None:
        """Close the underlying connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
