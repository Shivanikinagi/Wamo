"""Local SQLite archive for session metadata and full conversation transcripts."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class TranscriptArchive:
    """Persist sessions and turns locally so the demo works without Redis."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS archived_sessions (
                        session_id TEXT PRIMARY KEY,
                        customer_id TEXT NOT NULL,
                        agent_id TEXT,
                        preferred_language TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        ended_reason TEXT,
                        full_transcript TEXT DEFAULT '',
                        metadata_json TEXT
                    );

                    CREATE TABLE IF NOT EXISTS archived_turns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        text TEXT NOT NULL,
                        language TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(session_id) REFERENCES archived_sessions(session_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_archived_sessions_customer
                    ON archived_sessions(customer_id);

                    CREATE INDEX IF NOT EXISTS idx_archived_turns_session
                    ON archived_turns(session_id, id);
                    """
                )
                conn.commit()

    def start_session(
        self,
        session_id: str,
        customer_id: str,
        agent_id: str,
        preferred_language: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        now = _utc_now()

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO archived_sessions
                    (
                        session_id, customer_id, agent_id, preferred_language,
                        status, started_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        customer_id = excluded.customer_id,
                        agent_id = excluded.agent_id,
                        preferred_language = COALESCE(excluded.preferred_language, archived_sessions.preferred_language),
                        status = 'active',
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        session_id,
                        customer_id,
                        agent_id,
                        preferred_language,
                        now,
                        metadata_json,
                    ),
                )
                conn.commit()

    def append_turn(
        self,
        session_id: str,
        role: str,
        text: str,
        language: Optional[str] = None,
    ) -> None:
        clean_text = str(text or "").strip()
        if not clean_text:
            return

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO archived_turns (session_id, role, text, language, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, role, clean_text, language, _utc_now()),
                )
                conn.commit()

    def update_session(
        self,
        session_id: str,
        *,
        preferred_language: Optional[str] = None,
        status: Optional[str] = None,
        ended_reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        full_transcript: Optional[str] = None,
    ) -> None:
        existing = self.get_session(session_id) or {}
        merged_metadata = dict(existing.get("metadata", {}))
        if metadata:
            merged_metadata.update(metadata)

        updates: list[str] = []
        values: list[Any] = []

        if preferred_language is not None:
            updates.append("preferred_language = ?")
            values.append(preferred_language)
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if ended_reason is not None:
            updates.append("ended_reason = ?")
            values.append(ended_reason)
        if metadata is not None:
            updates.append("metadata_json = ?")
            values.append(json.dumps(merged_metadata))
        if full_transcript is not None:
            updates.append("full_transcript = ?")
            values.append(full_transcript)

        if not updates:
            return

        values.append(session_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE archived_sessions SET {', '.join(updates)} WHERE session_id = ?",
                    values,
                )
                conn.commit()

    def finalize_session(
        self,
        session_id: str,
        *,
        preferred_language: Optional[str] = None,
        full_transcript: Optional[str] = None,
        ended_reason: str = "completed",
    ) -> str:
        transcript = (full_transcript or "").strip() or self.build_full_transcript(session_id)

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE archived_sessions
                    SET preferred_language = COALESCE(?, preferred_language),
                        status = 'completed',
                        ended_at = ?,
                        ended_reason = ?,
                        full_transcript = ?
                    WHERE session_id = ?
                    """,
                    (
                        preferred_language,
                        _utc_now(),
                        ended_reason,
                        transcript,
                        session_id,
                    ),
                )
                conn.commit()

        return transcript

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT session_id, customer_id, agent_id, preferred_language, status,
                           started_at, ended_at, ended_reason, full_transcript, metadata_json
                    FROM archived_sessions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()

        if row is None:
            return None

        metadata_raw = row["metadata_json"]
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except Exception:
            metadata = {}

        return {
            "session_id": row["session_id"],
            "customer_id": row["customer_id"],
            "agent_id": row["agent_id"],
            "preferred_language": row["preferred_language"],
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "ended_reason": row["ended_reason"],
            "full_transcript": row["full_transcript"] or "",
            "metadata": metadata,
            **metadata,
        }

    def build_full_transcript(self, session_id: str) -> str:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT role, text
                    FROM archived_turns
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id,),
                ).fetchall()

        lines = []
        for row in rows:
            role = "USER" if row["role"] == "user" else "AI"
            lines.append(f"{role}: {row['text']}")
        return "\n".join(lines)

    def get_turns(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, role, text, language, created_at
                    FROM archived_turns
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id,),
                ).fetchall()

        return [
            {
                "id": row["id"],
                "role": row["role"],
                "text": row["text"],
                "language": row["language"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
