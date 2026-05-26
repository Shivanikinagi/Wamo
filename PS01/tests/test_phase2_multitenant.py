"""
Tests for Phase 2 multi-tenant components:
  - TenantContext / TenantMiddleware / get_tenant
  - WALLogger multi-tenant fields (bank_id, idempotency_key, shipped)
  - WALShipper background task
  - ConsentDB bank_id column
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


def run(coro):
    return asyncio.run(coro)


# ===========================================================================
# TenantContext / TenantMiddleware
# ===========================================================================

class TestTenantContext(unittest.TestCase):

    def test_dataclass_fields(self):
        from src.api.tenant import TenantContext
        tc = TenantContext(bank_id="bank_a", branch_id="branch_north")
        self.assertEqual(tc.bank_id, "bank_a")
        self.assertEqual(tc.branch_id, "branch_north")

    def test_branch_id_optional(self):
        from src.api.tenant import TenantContext
        tc = TenantContext(bank_id="bank_a")
        self.assertIsNone(tc.branch_id)


class TestTenantMiddleware(unittest.TestCase):

    def _make_app(self):
        from fastapi import FastAPI, Depends
        from src.api.tenant import TenantMiddleware, get_tenant, TenantContext

        app = FastAPI()
        app.add_middleware(TenantMiddleware)

        @app.get("/ping")
        def ping(tenant: TenantContext = Depends(get_tenant)):
            return {"bank_id": tenant.bank_id, "branch_id": tenant.branch_id}

        return app

    def _get(self, app, path, headers=None):
        """Use httpx.AsyncClient with ASGITransport — compatible with httpx>=0.23."""
        import httpx

        async def _call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get(path, headers=headers or {})

        return asyncio.run(_call())

    def test_missing_bank_id_returns_400(self):
        app = self._make_app()
        resp = self._get(app, "/ping")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "X-Bank-ID header required")

    def test_valid_bank_id_passes(self):
        app = self._make_app()
        resp = self._get(app, "/ping", headers={"X-Bank-ID": "hdfc"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["bank_id"], "hdfc")
        self.assertIsNone(resp.json()["branch_id"])

    def test_branch_id_forwarded(self):
        app = self._make_app()
        resp = self._get(
            app,
            "/ping",
            headers={"X-Bank-ID": "hdfc", "X-Branch-ID": "mumbai_north"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["branch_id"], "mumbai_north")


# ===========================================================================
# WALLogger — multi-tenant fields
# ===========================================================================

class TestWALLoggerMultitenant(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        self.wal_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.wal_path)

    def _make_wal(self):
        from src.core.wal import WALLogger
        return WALLogger(wal_path=self.wal_path)

    def test_append_writes_bank_id(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [{"fact": "has loan"}])
        with open(self.wal_path) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["bank_id"], "bank_x")

    def test_append_generates_idempotency_key(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [])
        with open(self.wal_path) as f:
            entry = json.loads(f.readline())
        self.assertIn("idempotency_key", entry)
        self.assertTrue(len(entry["idempotency_key"]) > 0)

    def test_append_accepts_explicit_idempotency_key(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [], idempotency_key="ikey-123")
        with open(self.wal_path) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["idempotency_key"], "ikey-123")

    def test_append_shipped_defaults_false(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [])
        with open(self.wal_path) as f:
            entry = json.loads(f.readline())
        self.assertFalse(entry["shipped"])

    def test_get_unshipped_returns_unshipped(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [], idempotency_key="k1")
        wal.append("s2", "c2", "a1", "bank_x", [], idempotency_key="k2")
        unshipped = wal.get_unshipped()
        self.assertEqual(len(unshipped), 2)

    def test_mark_shipped_updates_entry(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [], idempotency_key="k1")
        wal.append("s2", "c2", "a1", "bank_x", [], idempotency_key="k2")
        wal.mark_shipped("k1")
        unshipped = wal.get_unshipped()
        self.assertEqual(len(unshipped), 1)
        self.assertEqual(unshipped[0]["idempotency_key"], "k2")

    def test_mark_shipped_idempotent(self):
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [], idempotency_key="k1")
        wal.mark_shipped("k1")
        wal.mark_shipped("k1")  # second call should not raise
        self.assertEqual(wal.get_unshipped(), [])

    def test_get_unshipped_empty_file(self):
        wal = self._make_wal()
        self.assertEqual(wal.get_unshipped(), [])

    def test_replay_still_works(self):
        """Backward-compat: replay() still returns facts for a session_id."""
        wal = self._make_wal()
        wal.append("s1", "c1", "a1", "bank_x", [{"fact": "needs loan"}])
        facts = wal.replay("s1")
        self.assertEqual(facts, [{"fact": "needs loan"}])


# ===========================================================================
# WALShipper
# ===========================================================================

class TestWALShipper(unittest.TestCase):

    def _make_wal_with_entries(self, path, entries):
        from src.core.wal import WALLogger
        wal = WALLogger(wal_path=path)
        for e in entries:
            wal.append(
                e["session_id"],
                e["customer_id"],
                "agent1",
                e.get("bank_id", "bank_x"),
                e.get("facts", []),
                idempotency_key=e.get("idempotency_key"),
            )
        return wal

    def test_ship_pending_calls_producer_and_marks_shipped(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            wal = self._make_wal_with_entries(path, [
                {"session_id": "s1", "customer_id": "c1", "idempotency_key": "k1"},
            ])
            mock_producer = AsyncMock()
            mock_producer.publish_wal_entry = AsyncMock()

            from src.core.wal_shipper import WALShipper
            shipper = WALShipper(wal, mock_producer)

            run(shipper._ship_pending())

            mock_producer.publish_wal_entry.assert_awaited_once()
            self.assertEqual(wal.get_unshipped(), [])
        finally:
            os.unlink(path)

    def test_ship_pending_keeps_entry_on_runtime_error(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            wal = self._make_wal_with_entries(path, [
                {"session_id": "s1", "customer_id": "c1", "idempotency_key": "k1"},
            ])
            mock_producer = AsyncMock()
            mock_producer.publish_wal_entry = AsyncMock(
                side_effect=RuntimeError("broker down")
            )

            from src.core.wal_shipper import WALShipper
            shipper = WALShipper(wal, mock_producer)

            run(shipper._ship_pending())

            # Entry should still be unshipped
            self.assertEqual(len(wal.get_unshipped()), 1)
        finally:
            os.unlink(path)

    def test_start_creates_background_task(self):
        from src.core.wal import WALLogger
        from src.core.wal_shipper import WALShipper

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            wal = WALLogger(wal_path=path)
            mock_producer = AsyncMock()

            async def run_test():
                shipper = WALShipper(wal, mock_producer)
                await shipper.start()
                self.assertIsNotNone(shipper._task)
                await shipper.stop()
                self.assertIsNone(shipper._task)

            run(run_test())
        finally:
            os.unlink(path)

    def test_stop_cancels_task(self):
        from src.core.wal import WALLogger
        from src.core.wal_shipper import WALShipper

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            wal = WALLogger(wal_path=path)
            mock_producer = AsyncMock()

            async def run_test():
                shipper = WALShipper(wal, mock_producer)
                await shipper.start()
                await shipper.stop()
                # Calling stop again should be safe
                await shipper.stop()

            run(run_test())
        finally:
            os.unlink(path)


# ===========================================================================
# ConsentDB — bank_id column
# ===========================================================================

class TestConsentDBMultitenant(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def _make_db(self):
        from src.api.middleware import ConsentDB
        return ConsentDB(db_path=self.db_path)

    def test_bank_id_column_exists(self):
        import sqlite3
        self._make_db()
        with sqlite3.connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(consent)").fetchall()}
        self.assertIn("bank_id", cols)

    def test_record_consent_with_bank_id(self):
        db = self._make_db()
        db.record_consent("sess1", "cust1", "memory_read", "biometric", bank_id="hdfc")
        self.assertTrue(db.verify_consent("sess1", "memory_read", bank_id="hdfc"))

    def test_verify_consent_wrong_bank_id_returns_false(self):
        db = self._make_db()
        db.record_consent("sess1", "cust1", "memory_read", "biometric", bank_id="hdfc")
        self.assertFalse(db.verify_consent("sess1", "memory_read", bank_id="icici"))

    def test_verify_consent_no_bank_id_filter(self):
        """Empty bank_id → match any bank."""
        db = self._make_db()
        db.record_consent("sess1", "cust1", "memory_read", "biometric", bank_id="hdfc")
        self.assertTrue(db.verify_consent("sess1", "memory_read"))

    def test_backward_compat_no_bank_id(self):
        """Old call-sites that don't pass bank_id still work."""
        db = self._make_db()
        db.record_consent("sess2", "cust2", "memory_write", "pin")
        self.assertTrue(db.verify_consent("sess2", "memory_write"))

    def test_schema_migration_idempotent(self):
        """Creating ConsentDB twice on the same file should not raise."""
        from src.api.middleware import ConsentDB
        ConsentDB(db_path=self.db_path)
        ConsentDB(db_path=self.db_path)  # should not raise


# ===========================================================================
# require_consent decorator — bank_id forwarding
# ===========================================================================

class TestRequireConsent(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.db_path)

    def test_verify_consent_with_correct_bank_id(self):
        from src.api.middleware import ConsentDB
        db = ConsentDB(db_path=self.db_path)
        db.record_consent("sess1", "cust1", "memory_read", "biometric", bank_id="bank_a")
        self.assertTrue(db.verify_consent("sess1", "memory_read", bank_id="bank_a"))

    def test_verify_consent_cross_tenant_returns_false(self):
        from src.api.middleware import ConsentDB
        db = ConsentDB(db_path=self.db_path)
        db.record_consent("sess1", "cust1", "memory_read", "biometric", bank_id="bank_a")
        self.assertFalse(db.verify_consent("sess1", "memory_read", bank_id="bank_b"))


if __name__ == "__main__":
    unittest.main()
