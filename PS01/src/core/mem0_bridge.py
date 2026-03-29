# src/core/mem0_bridge.py
import json
import logging
import asyncio
import sqlite3
import os
from datetime import datetime, UTC
from typing import List, Dict, Optional
from uuid import uuid4
try:
    from mem0 import Memory
except Exception:  # pragma: no cover - optional in local test environments
    Memory = object
from .wal import WALLogger
from ..api.middleware import require_consent
from ..infra import RedisCache

logger = logging.getLogger(__name__)


class Mem0Bridge:
    def __init__(self, memory: Memory, wal_logger: WALLogger, bank_id: str = "default", redis_cache: Optional[RedisCache] = None):
        self.memory = memory
        self.wal = wal_logger
        self.bank_id = bank_id
        self.redis_cache = redis_cache

    def _build_mem0_text(self, facts: List[Dict]) -> str:
        """Serialize facts into a compact text payload for Mem0 embedding/search."""
        lines = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            f_type = str(fact.get("type", "unknown"))
            f_value = str(fact.get("value", ""))
            f_source = str(fact.get("source", ""))
            lines.append(f"{f_type}: {f_value} ({f_source})")
        return "\n".join(lines) if lines else "no_facts"

    def _history_db_path(self, bank_id: str) -> str:
        """Resolve mem0 history sqlite path using the same convention as init_mem0."""
        history_db_base = os.getenv("MEM0_HISTORY_DB_PATH", "./mem0_history")
        return f"{history_db_base}/{bank_id}/{bank_id}.db"

    def _persist_history_mirror(self, bank_id: str, memory_id: str, payload_text: str) -> None:
        """Persist a local mirror row so history DB is auditable even if mem0.add fails."""
        db_path = self._history_db_path(bank_id)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        con = sqlite3.connect(db_path)
        try:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    old_memory TEXT,
                    new_memory TEXT,
                    new_value TEXT,
                    event TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    is_deleted INTEGER
                )
                """
            )
            now = datetime.now(UTC).isoformat()
            cur.execute(
                """
                INSERT INTO history
                (id, memory_id, old_memory, new_memory, new_value, event, created_at, updated_at, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    memory_id,
                    None,
                    payload_text,
                    payload_text,
                    "wal_synced",
                    now,
                    now,
                    0,
                ),
            )
            con.commit()
        finally:
            con.close()

    def _write_mem0(self, composite_user_id: str, agent_id: str, facts: List[Dict]) -> None:
        """Write facts to Mem0, supporting multiple client signatures.

        NOTE: Some mem0 builds reject extra filters/kwargs like agent_id on add().
        Keep add() calls minimal and append agent context to payload text.
        """
        payload_text = self._build_mem0_text(facts)
        payload_with_agent = f"agent_id: {agent_id}\n{payload_text}"

        # Preferred signature in this runtime: add(data, user_id=...)
        try:
            self.memory.add(payload_with_agent, user_id=composite_user_id)
            return
        except (TypeError, ValueError):
            pass

        # Compatibility fallback for older/newer mem0 variants accepting messages=...
        try:
            self.memory.add(
                messages=[{"role": "system", "content": payload_with_agent}],
                user_id=composite_user_id,
            )
            return
        except (TypeError, ValueError):
            pass

        # Last fallback: bare call if kwargs behavior differs.
        self.memory.add(payload_with_agent)

    async def add_after_wal(
        self,
        session_id: str,
        customer_id: str,
        agent_id: str,
        facts: List[Dict],
        bank_id: str = "",
    ):
        """
        Write to Mem0 assuming WAL is already persisted by caller.

        This is used by API routes that already enforce WAL-first sequencing.
        """
        effective_bank_id = bank_id or self.bank_id
        composite_user_id = f"{effective_bank_id}::{customer_id}"
        payload_text = self._build_mem0_text(facts)

        # Always mirror facts into history DB for observability/audit.
        try:
            self._persist_history_mirror(effective_bank_id, composite_user_id, payload_text)
        except Exception as exc:
            logger.warning("history mirror write failed: %s", exc)

        try:
            lock_token = None
            if self.redis_cache is not None:
                lock_token = await self.redis_cache.acquire_lock(customer_id)
                if lock_token is None:
                    logger.warning(
                        "Could not acquire Redis lock for customer=%s; proceeding without lock",
                        customer_id,
                    )

            try:
                mem0_timeout = float(os.getenv("MEM0_ADD_TIMEOUT", "20"))
                await asyncio.wait_for(
                    asyncio.to_thread(self._write_mem0, composite_user_id, agent_id, facts),
                    timeout=mem0_timeout,
                )
            finally:
                if self.redis_cache is not None and lock_token is not None:
                    await self.redis_cache.release_lock(customer_id, lock_token)

            return {"status": "ok", "facts_added": len(facts), "wal_written": True}
        except Exception as e:
            return {"status": "error", "wal_written": True, "error": str(e)}

    @require_consent(scope="home_loan_processing")
    async def add_with_wal(self, session_id: str, customer_id: str, agent_id: str, facts: List[Dict], bank_id: str = ""):
        """
        Step 1: Write WAL
        Step 2: Acquire Redis lock (non-blocking)
        Step 3: Write Mem0
        Step 4: Release Redis lock
        Step 5: Return status
        """
        effective_bank_id = bank_id or self.bank_id
        composite_user_id = f"{effective_bank_id}::{customer_id}"
        payload_text = self._build_mem0_text(facts)

        try:
            # Step 1: WAL append (crash-safe)
            self.wal.append(session_id, customer_id, agent_id, effective_bank_id, facts)

            # Step 1b: Mirror into history DB for visibility/audit.
            try:
                self._persist_history_mirror(effective_bank_id, composite_user_id, payload_text)
            except Exception as exc:
                logger.warning("history mirror write failed: %s", exc)

            # Step 2: Acquire Redis lock (non-blocking for hackathon)
            lock_token = None
            if self.redis_cache is not None:
                lock_token = await self.redis_cache.acquire_lock(customer_id)
                if lock_token is None:
                    logger.warning("Could not acquire Redis lock for customer=%s; proceeding without lock", customer_id)

            # Step 3: mem0.add()
            try:
                mem0_timeout = float(os.getenv("MEM0_ADD_TIMEOUT", "20"))
                await asyncio.wait_for(
                    asyncio.to_thread(self._write_mem0, composite_user_id, agent_id, facts),
                    timeout=mem0_timeout,
                )
            finally:
                # Step 4: Release lock if acquired
                if self.redis_cache is not None and lock_token is not None:
                    await self.redis_cache.release_lock(customer_id, lock_token)

            return {"status": "ok", "facts_added": len(facts)}
        except Exception as e:
            # WAL survives crash; Mem0 write failed but can retry
            return {"status": "error", "wal_written": True, "error": str(e)}
