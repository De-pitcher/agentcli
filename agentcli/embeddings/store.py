"""SQLite persistence layer for semantic code chunk embeddings (Phase 24).

Caches chunk embeddings locally by content hash (SHA-256) to ensure zero re-embedding
of unchanged source files.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .chunker import CodeChunk

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_vector_db_path() -> Path:
    """Resolve platform-aware path for vector embeddings SQLite store."""
    env_override = os.environ.get("AGENTCLI_VECTOR_DB")
    if env_override:
        return Path(env_override)

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / "agentcli" / "vectors.db"


class VectorStore:
    """Thread-safe SQLite store for code chunk vector embeddings."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_vector_db_path()
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(
                    str(self.db_path),
                    timeout=30.0,
                    check_same_thread=False,
                )
                self._conn.row_factory = sqlite3.Row
                if str(self.db_path) != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA foreign_keys=ON;")
            return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    chunk_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_embeddings_file ON chunk_embeddings(file_path);
                CREATE INDEX IF NOT EXISTS idx_embeddings_model ON chunk_embeddings(model);
                CREATE INDEX IF NOT EXISTS idx_embeddings_sha ON chunk_embeddings(sha256);
                """
            )

    def get_embedding(self, chunk_id: str, sha256: str, model: str) -> list[float] | None:
        """Retrieve cached vector embedding if chunk_id, hash, and model match."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute(
                """
                SELECT vector_json FROM chunk_embeddings
                WHERE chunk_id = ? AND sha256 = ? AND model = ?
                """,
                (chunk_id, sha256, model),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                vec = json.loads(row["vector_json"])
                return [float(v) for v in vec]
            except (json.JSONDecodeError, ValueError):
                return None

    def save_embedding(self, chunk: CodeChunk, model: str, vector: list[float]) -> None:
        """Save an individual chunk embedding record."""
        now = _utc_now_iso()
        vec_json = json.dumps(vector)
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO chunk_embeddings
                    (chunk_id, file_path, sha256, start_line, end_line, chunk_type, content, model, vector_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        sha256=excluded.sha256,
                        start_line=excluded.start_line,
                        end_line=excluded.end_line,
                        chunk_type=excluded.chunk_type,
                        content=excluded.content,
                        model=excluded.model,
                        vector_json=excluded.vector_json,
                        created_at=excluded.created_at
                    """,
                    (
                        chunk.chunk_id,
                        chunk.file_path,
                        chunk.sha256,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.chunk_type,
                        chunk.content,
                        model,
                        vec_json,
                        now,
                    ),
                )

    def save_embeddings_batch(
        self,
        records: list[tuple[CodeChunk, str, list[float]]],
    ) -> None:
        """Batch save multiple chunk embeddings in a single atomic transaction."""
        if not records:
            return
        now = _utc_now_iso()
        params = [
            (
                chunk.chunk_id,
                chunk.file_path,
                chunk.sha256,
                chunk.start_line,
                chunk.end_line,
                chunk.chunk_type,
                chunk.content,
                model,
                json.dumps(vector),
                now,
            )
            for chunk, model, vector in records
        ]
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.executemany(
                    """
                    INSERT INTO chunk_embeddings
                    (chunk_id, file_path, sha256, start_line, end_line, chunk_type, content, model, vector_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        sha256=excluded.sha256,
                        start_line=excluded.start_line,
                        end_line=excluded.end_line,
                        chunk_type=excluded.chunk_type,
                        content=excluded.content,
                        model=excluded.model,
                        vector_json=excluded.vector_json,
                        created_at=excluded.created_at
                    """,
                    params,
                )

    def get_all_for_model(self, model: str) -> list[tuple[CodeChunk, list[float]]]:
        """Retrieve all cached chunks and vector embeddings for a given model."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute(
                """
                SELECT chunk_id, file_path, sha256, start_line, end_line, chunk_type, content, vector_json
                FROM chunk_embeddings
                WHERE model = ?
                """,
                (model,),
            )
            results: list[tuple[CodeChunk, list[float]]] = []
            for row in cur.fetchall():
                try:
                    vec = [float(x) for x in json.loads(row["vector_json"])]
                    chunk = CodeChunk(
                        file_path=row["file_path"],
                        chunk_id=row["chunk_id"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        content=row["content"],
                        sha256=row["sha256"],
                        chunk_type=row["chunk_type"],
                    )
                    results.append((chunk, vec))
                except (json.JSONDecodeError, ValueError):
                    continue
            return results

    def delete_file_chunks(self, file_path: str) -> int:
        """Delete all cached chunk embeddings for a specific file path."""
        p_str = str(Path(file_path).resolve())
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute("DELETE FROM chunk_embeddings WHERE file_path = ?", (p_str,))
                return cur.rowcount

    def clear(self) -> int:
        """Clear all vector embeddings from database."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute("DELETE FROM chunk_embeddings")
                return cur.rowcount

    def stats(self) -> dict[str, Any]:
        """Return total chunk count and model distribution."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute("SELECT COUNT(*) as total FROM chunk_embeddings")
            total = cur.fetchone()["total"]
            cur = conn.execute("SELECT model, COUNT(*) as cnt FROM chunk_embeddings GROUP BY model")
            by_model = {row["model"]: row["cnt"] for row in cur.fetchall()}
            return {"total_chunks": total, "by_model": by_model}

    def close(self) -> None:
        """Close SQLite database connection."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __del__(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except BaseException:  # noqa: BLE001,S110
                pass
            self._conn = None
