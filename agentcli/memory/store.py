"""SQLite persistence layer for agentcli sessions and message history (Phase 5).

Provides local-first conversation storage, session hydration, and message history
retrieval with zero external runtime dependencies (using Python standard library sqlite3).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_memory_db_path() -> Path:
    """Resolve the default platform-aware path for the SQLite memory database."""
    env_override = os.environ.get("AGENTCLI_MEMORY_DB")
    if env_override:
        return Path(env_override)

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / "agentcli" / "memory.db"


@dataclass
class SessionRecord:
    """Represents a persisted chat session."""

    id: str
    created_at: str
    updated_at: str
    title: str = "New Session"
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageRecord:
    """Represents a persisted message in a chat session."""

    id: int | None
    session_id: str
    role: str
    content: str
    created_at: str
    token_count: int | None = None


class MemoryStore:
    """Manages conversation sessions and message persistence in a local SQLite database."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_memory_db_path()
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode and foreign keys for low-latency concurrent reads
            if str(self.db_path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    token_count INTEGER,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
                """
            )

    def create_session(
        self,
        session_id: str | None = None,
        title: str = "New Session",
        model: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        """Create a new session record."""
        sid = session_id or uuid.uuid4().hex[:12]
        now = _utc_now_iso()
        meta = metadata or {}
        meta_json = json.dumps(meta)

        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO sessions (id, created_at, updated_at, title, model, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, now, now, title, model, meta_json),
            )
        return SessionRecord(
            id=sid,
            created_at=now,
            updated_at=now,
            title=title,
            model=model,
            metadata=meta,
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Retrieve a session by its ID."""
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT id, created_at, updated_at, title, model, metadata FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            meta = json.loads(row["metadata"])
        except (ValueError, TypeError):
            meta = {}
        return SessionRecord(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            title=row["title"],
            model=row["model"],
            metadata=meta,
        )

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[SessionRecord]:
        """List recently updated sessions."""
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT id, created_at, updated_at, title, model, metadata
            FROM sessions
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        records: list[SessionRecord] = []
        for row in cur.fetchall():
            try:
                meta = json.loads(row["metadata"])
            except (ValueError, TypeError):
                meta = {}
            records.append(
                SessionRecord(
                    id=row["id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    title=row["title"],
                    model=row["model"],
                    metadata=meta,
                )
            )
        return records

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update session attributes and refresh updated_at timestamp."""
        existing = self.get_session(session_id)
        if not existing:
            return False

        new_title = title if title is not None else existing.title
        new_model = model if model is not None else existing.model
        new_meta = metadata if metadata is not None else existing.metadata
        now = _utc_now_iso()

        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE sessions
                SET title = ?, model = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_title, new_model, json.dumps(new_meta), now, session_id),
            )
        return True

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages (cascade)."""
        conn = self._get_connection()
        with conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    def clear_all_sessions(self) -> int:
        """Clear all stored sessions and messages. Returns deleted session count."""
        conn = self._get_connection()
        with conn:
            cur = conn.execute("DELETE FROM sessions")
            return cur.rowcount

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        token_count: int | None = None,
    ) -> MessageRecord:
        """Append a message to a session and touch the session's updated_at."""
        # Ensure session exists
        if not self.get_session(session_id):
            self.create_session(
                session_id=session_id,
                title=content[:40] if role == "user" else "New Session",
            )
        else:
            # If session is still named default and this is first user message, update title
            session = self.get_session(session_id)
            if session and session.title in {"New Session", ""} and role == "user":
                self.update_session(session_id, title=content[:40].replace("\n", " ").strip())

        now = _utc_now_iso()
        conn = self._get_connection()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, created_at, token_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, now, token_count),
            )
            msg_id = cur.lastrowid
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )

        return MessageRecord(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            token_count=token_count,
        )

    def get_messages(self, session_id: str, limit: int | None = None) -> list[MessageRecord]:
        """Retrieve messages for a session in chronological order."""
        conn = self._get_connection()
        query = "SELECT id, session_id, role, content, created_at, token_count FROM messages WHERE session_id = ? ORDER BY id ASC"
        params: list[Any] = [session_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cur = conn.execute(query, params)
        messages: list[MessageRecord] = []
        for row in cur.fetchall():
            messages.append(
                MessageRecord(
                    id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    content=row["content"],
                    created_at=row["created_at"],
                    token_count=row["token_count"],
                )
            )
        return messages

    def prune_older_than(self, days: int) -> int:
        """Delete sessions that have not been updated in more than *days* days."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        conn = self._get_connection()
        with conn:
            cur = conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            return cur.rowcount

    def close(self) -> None:
        """Close SQLite database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


__all__ = ["MemoryStore", "MessageRecord", "SessionRecord", "default_memory_db_path"]
