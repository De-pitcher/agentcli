from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentcli.cli import main
from agentcli.config import Config, ConfigError, load_config
from agentcli.exit_codes import ExitCode
from agentcli.files import expand_file_references, read_file_for_context
from agentcli.memory.budget import (
    calculate_cost,
    estimate_message_tokens,
    estimate_tokens,
    trim_history_to_budget,
)
from agentcli.memory.cache import ContextCache
from agentcli.memory.context_pool import SharedContextPool
from agentcli.memory.store import MemoryStore
from agentcli.openrouter_client import ChatMessage
from agentcli.session import AgentSession

# ---------------------------------------------------------------------------
# 1. MemoryStore SQLite unit tests
# ---------------------------------------------------------------------------


class TestMemoryStore:
    def test_create_and_get_session(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_memory.db"
        with MemoryStore(db_file) as store:
            session = store.create_session(
                session_id="s123",
                title="Test Title",
                model="test-model",
                metadata={"tag": "unit-test"},
            )
            assert session.id == "s123"
            assert session.title == "Test Title"
            assert session.model == "test-model"
            assert session.metadata == {"tag": "unit-test"}

            fetched = store.get_session("s123")
            assert fetched is not None
            assert fetched.id == "s123"
            assert fetched.title == "Test Title"
            assert fetched.metadata == {"tag": "unit-test"}

    def test_list_and_update_sessions(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_memory.db"
        with MemoryStore(db_file) as store:
            store.create_session(session_id="s1", title="Session 1")
            store.create_session(session_id="s2", title="Session 2")

            sessions = store.list_sessions()
            assert len(sessions) == 2
            ids = [s.id for s in sessions]
            assert "s1" in ids and "s2" in ids

            updated = store.update_session("s1", title="Updated Session 1")
            assert updated is True
            assert store.get_session("s1").title == "Updated Session 1"  # type: ignore

    def test_delete_and_cascade_messages(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_memory.db"
        with MemoryStore(db_file) as store:
            store.create_session(session_id="s1")
            store.append_message("s1", "user", "Hello")
            store.append_message("s1", "assistant", "Hi there")

            assert len(store.get_messages("s1")) == 2

            deleted = store.delete_session("s1")
            assert deleted is True
            assert store.get_session("s1") is None
            assert len(store.get_messages("s1")) == 0

    def test_clear_all_sessions(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_memory.db"
        with MemoryStore(db_file) as store:
            store.create_session(session_id="s1")
            store.create_session(session_id="s2")
            assert len(store.list_sessions()) == 2

            count = store.clear_all_sessions()
            assert count == 2
            assert len(store.list_sessions()) == 0

    def test_prune_older_than(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_memory.db"
        with MemoryStore(db_file) as store:
            store.create_session(session_id="s_old")
            store.create_session(session_id="s_new")

            # Artificially set updated_at for s_old to 40 days ago
            conn = store._get_connection()
            old_date = "2020-01-01T00:00:00+00:00"
            with conn:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (old_date, "s_old"))

            pruned = store.prune_older_than(days=30)
            assert pruned == 1
            assert store.get_session("s_old") is None
            assert store.get_session("s_new") is not None


# ---------------------------------------------------------------------------
# 2. ContextCache unit tests
# ---------------------------------------------------------------------------


class TestContextCache:
    def test_cache_hit_and_stats(self, tmp_path: Path) -> None:
        f = tmp_path / "sample.py"
        f.write_text("print('hello')", encoding="utf-8")

        cache = ContextCache(enabled=True)
        read_count = 0

        def reader(p: Path) -> str:
            nonlocal read_count
            read_count += 1
            return p.read_text(encoding="utf-8")

        # Turn 1: Cache Miss
        content1, hit1 = cache.get_or_read(f, reader)
        assert hit1 is False
        assert read_count == 1
        assert content1 == "print('hello')"

        # Turn 2: Cache Hit (Unchanged file)
        content2, hit2 = cache.get_or_read(f, reader)
        assert hit2 is True
        assert read_count == 1  # reader not called
        assert content2 == "print('hello')"

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["cached_entries"] == 1

    def test_cache_invalidation_on_content_change(self, tmp_path: Path) -> None:
        f = tmp_path / "sample.py"
        f.write_text("v1", encoding="utf-8")

        cache = ContextCache(enabled=True)

        def reader(p: Path) -> str:
            return p.read_text(encoding="utf-8")

        c1, hit1 = cache.get_or_read(f, reader)
        assert hit1 is False
        assert c1 == "v1"

        # Modify file
        time.sleep(0.01)
        f.write_text("v2", encoding="utf-8")

        c2, hit2 = cache.get_or_read(f, reader)
        assert hit2 is False
        assert c2 == "v2"

    def test_cache_invalidation_on_deletion(self, tmp_path: Path) -> None:
        f = tmp_path / "temp.txt"
        f.write_text("data", encoding="utf-8")

        cache = ContextCache(enabled=True)
        cache.get_or_read(f, lambda p: p.read_text())
        assert cache.stats()["cached_entries"] == 1

        f.unlink()
        cache.get_or_read(f, lambda p: "fallback")
        assert cache.stats()["cached_entries"] == 0

    def test_files_module_integration_with_cache(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("x = 42", encoding="utf-8")

        cache = ContextCache(enabled=True)
        block1 = read_file_for_context(f, cache=cache)
        assert "x = 42" in block1
        assert cache.hits == 0

        block2 = read_file_for_context(f, cache=cache)
        assert block1 == block2
        assert cache.hits == 1

        # Test expand_file_references
        expanded = expand_file_references(f"Analyze @{f}", cache=cache)
        assert "x = 42" in expanded
        assert cache.hits == 2


# ---------------------------------------------------------------------------
# 3. Token budget & history trimming unit tests
# ---------------------------------------------------------------------------


class TestTokenBudget:
    def test_token_estimation(self) -> None:
        text = "Hello world! This is a test."
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert estimate_tokens("") == 0

        msg = ChatMessage(role="user", content="Hello world")
        assert estimate_message_tokens(msg) == estimate_tokens("Hello world") + 4

    def test_trim_history_preserves_system_message(self) -> None:
        history = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="Turn 1"),
            ChatMessage(role="assistant", content="Reply 1"),
            ChatMessage(role="user", content="Turn 2"),
            ChatMessage(role="assistant", content="Reply 2"),
        ]

        trimmed = trim_history_to_budget(history, max_context_tokens=1000, max_turns=1)
        assert len(trimmed) == 4  # system + 1 turn pair (Turn 2 + Reply 2) + latest
        assert trimmed[0].role == "system"
        assert trimmed[0].content == "You are a helpful assistant."

    def test_trim_history_enforces_token_ceiling(self) -> None:
        large_content = "word " * 500  # ~650 tokens
        history = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content=large_content),
            ChatMessage(role="assistant", content=large_content),
            ChatMessage(role="user", content="Latest small question"),
        ]

        # Small window limit (e.g. 500 tokens budget)
        trimmed = trim_history_to_budget(history, max_context_tokens=600, budget_ratio=0.75)
        # Should keep system message + latest message, discarding older oversized turns
        assert trimmed[0].role == "system"
        assert trimmed[-1].content == "Latest small question"
        assert len(trimmed) < len(history)


# ---------------------------------------------------------------------------
# 4. SharedContextPool unit tests
# ---------------------------------------------------------------------------


class TestSharedContextPool:
    @pytest.mark.asyncio
    async def test_put_get_and_reference_lifecycle(self) -> None:
        pool = SharedContextPool(max_bytes=10_000)
        item = await pool.put("k1", "data-1", source_agent="agent-a", initial_consumer="agent-b")
        assert item.key == "k1"
        assert item.ref_count == 1

        content = await pool.get("k1", consumer_id="agent-c")
        assert content == "data-1"

        stats = await pool.stats()
        assert stats["item_count"] == 1
        assert stats["active_references"] == 2

        await pool.release_ref("k1", "agent-b")
        await pool.release_ref("k1", "agent-c")
        stats_after = await pool.stats()
        assert stats_after["active_references"] == 0

    @pytest.mark.asyncio
    async def test_automatic_compaction_when_over_capacity(self) -> None:
        # 2KB pool limit
        pool = SharedContextPool(max_bytes=2048)

        # Put 1KB unreferenced item
        await pool.put("unref_old", "A" * 1000)
        # Put 1.5KB item with active reference
        await pool.put("ref_active", "B" * 1500, initial_consumer="agent-1")

        # Pool exceeded 2KB -> should have evicted unreferenced item
        stats = await pool.stats()
        assert stats["total_bytes"] <= 2048
        assert await pool.get("unref_old") is None
        assert await pool.get("ref_active") is not None

    @pytest.mark.asyncio
    async def test_concurrent_context_access_safety(self) -> None:
        pool = SharedContextPool(max_bytes=50_000)

        async def worker(idx: int) -> None:
            key = f"key_{idx}"
            await pool.put(key, f"content_{idx}" * 50, initial_consumer=f"worker_{idx}")
            await pool.get(key)
            await pool.release_ref(key, f"worker_{idx}")

        await asyncio.gather(*(worker(i) for i in range(30)))
        stats = await pool.stats()
        assert stats["item_count"] <= 30
        assert stats["active_references"] == 0


# ---------------------------------------------------------------------------
# 5. Session & Memory Store End-to-End Integration
# ---------------------------------------------------------------------------


class TestSessionMemoryIntegration:
    def _make_config(self, tmp_path: Path) -> Config:
        cfg = Config()
        cfg.memory.enabled = True
        cfg.memory.db_path = str(tmp_path / "session_test.db")
        return cfg

    def test_session_persists_messages_and_resumes(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-dummy"}):
            # Session 1: Create conversation
            s1 = AgentSession(cfg, session_id="test_session_1")
            s1.add_user_message("What is Python?")
            s1.add_assistant_message("Python is a programming language.")

            # Verify SQLite holds the messages
            store = MemoryStore(cfg.memory.db_path)
            msgs = store.get_messages("test_session_1")
            assert len(msgs) == 2
            assert msgs[0].content == "What is Python?"
            assert msgs[1].content == "Python is a programming language."

            # Session 2: Resume session by ID
            s2 = AgentSession(cfg, session_id="test_session_1")
            assert len(s2.history) == 2
            assert s2.history[0].content == "What is Python?"
            assert s2.history[1].content == "Python is a programming language."

            # Continue conversation on resumed session
            s2.add_user_message("Is it dynamically typed?")
            assert len(store.get_messages("test_session_1")) == 3

    def test_session_resume_nonexistent_vs_empty(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        store = MemoryStore(cfg.memory.db_path)
        # Create a real session that has 0 messages
        store.create_session("empty_session_id", title="Empty Session")
        store.close()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-dummy"}):
            # Case 1: Nonexistent session ID
            s_ghost = AgentSession(cfg, session_id="ghost_session_id")
            assert s_ghost.is_resumed is False
            assert s_ghost.session_id == "ghost_session_id"
            assert len(s_ghost.history) == 0

            # Case 2: Real-but-empty session ID
            s_empty = AgentSession(cfg, session_id="empty_session_id")
            assert s_empty.is_resumed is True
            assert s_empty.session_id == "empty_session_id"
            assert len(s_empty.history) == 0


# ---------------------------------------------------------------------------
# 6. CLI Subcommands Integration (`agentcli sessions ...`)
# ---------------------------------------------------------------------------


class TestCliSessions:
    def test_sessions_list_show_clear(self, tmp_path: Path, capsys: Any) -> None:
        db_file = tmp_path / "cli_mem.db"
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            f"[openrouter]\napi_key_env = 'K'\n[memory]\nenabled = true\ndb_path = '{db_file.as_posix()}'\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"K": "sk-test", "AGENTCLI_CONFIG": str(cfg_file)}):
            # Pre-seed session
            store = MemoryStore(db_file)
            store.create_session("sess_abc", title="Sample Chat")
            store.append_message("sess_abc", "user", "Hello there")
            store.append_message("sess_abc", "assistant", "General Kenobi")
            store.close()

            # 1. Test sessions list
            ret_list = main(["sessions", "list"])
            assert ret_list == ExitCode.SUCCESS
            out_list = capsys.readouterr().out
            assert "sess_abc" in out_list
            assert "Sample Chat" in out_list

            # 2. Test sessions show
            ret_show = main(["sessions", "show", "sess_abc"])
            assert ret_show == ExitCode.SUCCESS
            out_show = capsys.readouterr().out
            assert "Session ID: sess_abc" in out_show
            assert "General Kenobi" in out_show

            # 3. Test sessions clear --yes
            ret_clear = main(["sessions", "clear", "--yes"])
            assert ret_clear == ExitCode.SUCCESS
            out_clear = capsys.readouterr().out
            assert "Cleared 1 stored session(s)" in out_clear

    def test_chat_resume_cli_output(self, tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
        db_file = tmp_path / "cli_resume.db"
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            f"[openrouter]\napi_key_env = 'K'\n[memory]\nenabled = true\ndb_path = '{db_file.as_posix()}'\n",
            encoding="utf-8",
        )

        store = MemoryStore(db_file)
        store.create_session("empty_sess", title="Empty Session")
        store.create_session("active_sess", title="Active Session")
        store.append_message("active_sess", "user", "Hi")
        store.append_message("active_sess", "assistant", "Hello")
        store.close()

        # Mock input to exit chat loop immediately
        monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")

        with patch.dict(os.environ, {"K": "sk-test", "AGENTCLI_CONFIG": str(cfg_file)}):
            # 1. Nonexistent session ID
            main(["chat", "--resume", "ghost_sess"])
            out_ghost = capsys.readouterr().out
            assert (
                "No session found with ID 'ghost_sess'. Starting a new session instead."
                in out_ghost
            )

            # 2. Real-but-empty session ID
            main(["chat", "--resume", "empty_sess"])
            out_empty = capsys.readouterr().out
            assert "Resumed session: empty_sess (0 messages loaded)" in out_empty

            # 3. Real session with messages
            main(["chat", "--resume", "active_sess"])
            out_active = capsys.readouterr().out
            assert "Resumed session: active_sess (2 messages loaded)" in out_active


# ---------------------------------------------------------------------------
# 7. Config parsing tests for [memory]
# ---------------------------------------------------------------------------


class TestMemoryConfigParsing:
    def test_memory_defaults_when_absent(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text("[openrouter]\napi_key_env = 'K'\n", encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg.memory.enabled is True
        assert cfg.memory.retention_days == 30
        assert cfg.memory.cache_enabled is True
        assert cfg.memory.max_shared_context_bytes == 524288

    def test_custom_memory_config(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n"
            "[memory]\n"
            "enabled = false\n"
            "retention_days = 14\n"
            "cache_enabled = false\n"
            "max_shared_context_bytes = 1048576\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.memory.enabled is False
        assert cfg.memory.retention_days == 14
        assert cfg.memory.cache_enabled is False
        assert cfg.memory.max_shared_context_bytes == 1048576

    def test_invalid_retention_days_raises_config_error(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n[memory]\nretention_days = -1\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="memory.retention_days"):
            load_config(cfg_file)

    def test_invalid_max_shared_bytes_raises_config_error(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n[memory]\nmax_shared_context_bytes = 50\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="memory.max_shared_context_bytes"):
            load_config(cfg_file)


# ---------------------------------------------------------------------------
# 8. Extra Edge Cases & Branch Coverage
# ---------------------------------------------------------------------------


class TestMemoryEdgeCases:
    @pytest.mark.asyncio
    async def test_context_pool_in_use_items_preserved_during_compaction(self) -> None:
        # Small capacity pool (1024 bytes)
        pool = SharedContextPool(max_bytes=1024)
        # Put 1 unreferenced item (500 bytes)
        await pool.put("unref", "U" * 500)
        # Put 2 actively referenced items (600 bytes each)
        await pool.put("k1", "X" * 600, initial_consumer="c1")
        await pool.put("k2", "Y" * 600, initial_consumer="c2")

        # Unreferenced item should be evicted
        assert await pool.get("unref") is None

        # In-use items must NEVER be truncated or corrupted
        content1 = await pool.get("k1")
        content2 = await pool.get("k2")
        assert content1 == "X" * 600
        assert content2 == "Y" * 600

        # Release ref on k1 and trigger compaction -> k1 should now be evicted cleanly
        await pool.release_ref("k1", "c1")
        await pool.compact()
        assert await pool.get("k1") is None
        assert await pool.get("k2") == "Y" * 600

    @pytest.mark.asyncio
    async def test_context_pool_missing_keys_and_clear(self) -> None:
        pool = SharedContextPool(max_bytes=5000)
        assert await pool.get("nonexistent") is None
        assert await pool.acquire_ref("nonexistent", "c1") is False
        assert await pool.release_ref("nonexistent", "c1") is False

        await pool.put("k", "data")
        assert pool.item_count == 1
        await pool.clear()
        assert pool.item_count == 0

    def test_cache_methods(self, tmp_path: Path) -> None:
        cache = ContextCache(enabled=True)
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        cache.get_or_read(f, lambda p: p.read_text())
        assert cache.stats()["cached_entries"] == 1

        assert cache.invalidate(f) is True
        assert cache.invalidate(f) is False
        assert cache.stats()["cached_entries"] == 0

        cache.get_or_read(f, lambda p: p.read_text())
        cache.clear()
        assert cache.stats()["cached_entries"] == 0
        assert cache.hits == 0

    def test_store_corrupted_metadata_fallback(self, tmp_path: Path) -> None:
        db_file = tmp_path / "corrupt.db"
        with MemoryStore(db_file) as store:
            store.create_session("s_bad", title="Bad Meta")
            conn = store._get_connection()
            with conn:
                conn.execute("UPDATE sessions SET metadata = 'invalid json' WHERE id = 's_bad'")

            session = store.get_session("s_bad")
            assert session is not None
            assert session.metadata == {}

            sessions = store.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].metadata == {}

    def test_cli_sessions_edge_cases(self, tmp_path: Path, capsys: Any) -> None:
        db_file = tmp_path / "cli_edge.db"
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            f"[openrouter]\napi_key_env = 'K'\n[memory]\nenabled = true\ndb_path = '{db_file.as_posix()}'\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"K": "sk-test", "AGENTCLI_CONFIG": str(cfg_file)}):
            # Empty sessions list
            res = main(["sessions", "list"])
            assert res == ExitCode.SUCCESS
            assert "No saved sessions found" in capsys.readouterr().out

            # Missing session show
            res_show = main(["sessions", "show", "nonexistent_id"])
            assert res_show == ExitCode.GENERAL_ERROR

            # Config show includes memory settings
            res_cfg = main(["config", "show"])
            assert res_cfg == ExitCode.SUCCESS
            out_cfg = capsys.readouterr().out
            assert "memory.enabled" in out_cfg
            assert "memory.retention_days" in out_cfg
            assert "memory.budget_ratio" in out_cfg
            assert "memory.max_cache_entries" in out_cfg
            assert "memory.max_cache_bytes" in out_cfg


# ---------------------------------------------------------------------------
# 9. Phase 6 LRU Cache & Async Store Concurrency Tests
# ---------------------------------------------------------------------------


class TestPhase6Optimizations:
    def test_lru_cache_evicts_oldest_on_entry_limit(self, tmp_path: Path) -> None:
        cache = ContextCache(enabled=True, max_entries=3, max_bytes=1024 * 1024)
        files = []
        for i in range(5):
            p = tmp_path / f"file_{i}.txt"
            p.write_text(f"Content {i}", encoding="utf-8")
            files.append(p)

        def reader(p: Path) -> str:
            return p.read_text(encoding="utf-8")

        # Read files 0, 1, 2 (all cached)
        cache.get_or_read(files[0], reader)
        cache.get_or_read(files[1], reader)
        cache.get_or_read(files[2], reader)
        assert cache.stats()["cached_entries"] == 3

        # Access file 0 to make it MRU (order now: 1 [oldest], 2, 0 [newest])
        _, hit0 = cache.get_or_read(files[0], reader)
        assert hit0 is True

        # Read file 3 -> should evict file 1 (the oldest unaccessed)
        cache.get_or_read(files[3], reader)
        assert cache.stats()["cached_entries"] == 3

        # File 1 is evicted (cache miss)
        _, hit1 = cache.get_or_read(files[1], reader)
        assert hit1 is False

        # File 0 was preserved (cache hit)
        _, hit0_again = cache.get_or_read(files[0], reader)
        assert hit0_again is True

    def test_lru_cache_evicts_on_byte_budget_pressure(self, tmp_path: Path) -> None:
        # 1000-byte max capacity
        cache = ContextCache(enabled=True, max_entries=100, max_bytes=1024)
        files = []
        for i in range(4):
            p = tmp_path / f"large_{i}.txt"
            p.write_text("X" * 400, encoding="utf-8")
            files.append(p)

        def reader(p: Path) -> str:
            return p.read_text(encoding="utf-8")

        # 400 bytes
        cache.get_or_read(files[0], reader)
        # 800 bytes
        cache.get_or_read(files[1], reader)
        assert cache.current_bytes == 800

        # Adding 400 bytes more (total 1200 > 1024) -> evicts file 0
        cache.get_or_read(files[2], reader)
        assert cache.current_bytes <= 1024
        assert cache.stats()["cached_entries"] == 2

    @pytest.mark.asyncio
    async def test_async_store_methods_and_stats(self, tmp_path: Path) -> None:
        db_file = tmp_path / "async_store.db"
        store = MemoryStore(db_file)

        # Async session creation
        s = await store.acreate_session("sess_async", title="Async Session", model="m1")
        assert s.id == "sess_async"

        # Async append with tokens
        msg1 = await store.aappend_message("sess_async", "user", "What is 2+2?", token_count=12)
        msg2 = await store.aappend_message("sess_async", "assistant", "4", token_count=4)
        assert msg1.token_count == 12
        assert msg2.token_count == 4

        # Async get messages
        msgs = await store.aget_messages("sess_async")
        assert len(msgs) == 2

        # Async get stats
        stats = await store.aget_session_stats("sess_async")
        assert stats["message_count"] == 2
        assert stats["total_tokens"] == 16
        assert stats["user_tokens"] == 12
        assert stats["assistant_tokens"] == 4

        # Async list and update
        await store.aupdate_session("sess_async", title="Updated Async")
        sessions = await store.alist_sessions()
        assert len(sessions) == 1
        assert sessions[0].title == "Updated Async"

        # Async delete and clear
        assert await store.adelete_session("sess_async") is True
        assert await store.aclear_all_sessions() == 0

        store.close()

    @pytest.mark.asyncio
    async def test_async_sqlite_concurrent_event_loop_progress(self, tmp_path: Path) -> None:
        db_file = tmp_path / "nonblocking.db"
        store = MemoryStore(db_file)

        events_order: list[str] = []

        async def fast_task() -> None:
            for i in range(5):
                events_order.append(f"fast_{i}")
                await asyncio.sleep(0.001)

        async def slow_db_write() -> None:
            # Execute multiple writes in background thread
            for j in range(5):
                await store.aappend_message(
                    session_id="s_test",
                    role="user",
                    content=f"Slow write msg {j}",
                    token_count=10,
                )
                events_order.append(f"db_{j}")

        await asyncio.gather(fast_task(), slow_db_write())
        store.close()

        # Both fast async task and db writes interleaved without blocking
        assert "fast_0" in events_order
        assert "db_0" in events_order
        assert len(events_order) == 10


class TestCalculateCost:
    def test_calculate_cost_free_model(self) -> None:
        cost = calculate_cost("google/gemma-4-31b-it:free", prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0

    def test_calculate_cost_paid_models(self) -> None:
        # openai/gpt-4o-mini: $0.15 / 1M prompt, $0.60 / 1M completion
        cost_mini = calculate_cost("openai/gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert abs(cost_mini - 0.75) < 1e-6

        # anthropic/claude-3.5-sonnet: $3.00 / 1M prompt, $15.00 / 1M completion
        cost_sonnet = calculate_cost("anthropic/claude-3.5-sonnet", prompt_tokens=100_000, completion_tokens=50_000)
        # 0.1 * 3.00 + 0.05 * 15.00 = 0.30 + 0.75 = 1.05
        assert abs(cost_sonnet - 1.05) < 1e-6

    def test_calculate_cost_unknown_model_fallback(self) -> None:
        cost_unknown = calculate_cost("custom/unknown-model", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert cost_unknown > 0.0

