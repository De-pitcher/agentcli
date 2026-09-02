import pytest

from agentcli.config import Config
from agentcli.openrouter_client import ChatMessage
from agentcli.session import AgentSession


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")


@pytest.mark.asyncio
async def test_session_history_trimming():
    config = Config()
    config.app.history_turns = 1  # keep 1 pair + 1 current message = 3 messages max

    session = AgentSession(config)
    session.add_user_message("1")
    session.add_assistant_message("2")
    session.add_user_message("3")
    session.add_assistant_message("4")
    session.add_user_message("5")

    trimmed = session._trim_history()
    assert len(trimmed) == 3
    assert trimmed[0].content == "3"
    assert trimmed[1].content == "4"
    assert trimmed[2].content == "5"


@pytest.mark.asyncio
async def test_session_history_trimming_preserves_system():
    config = Config()
    config.app.history_turns = 1

    history = [ChatMessage(role="system", content="sys")]
    session = AgentSession(config, initial_history=history)
    session.add_user_message("1")
    session.add_assistant_message("2")
    session.add_user_message("3")
    session.add_assistant_message("4")
    session.add_user_message("5")

    trimmed = session._trim_history()
    assert len(trimmed) == 4
    assert trimmed[0].content == "sys"
    assert trimmed[1].content == "3"
    assert trimmed[2].content == "4"
    assert trimmed[3].content == "5"


def test_session_pop_last_message():
    session = AgentSession(Config())
    session.add_user_message("hi")
    session.pop_last_message()
    assert len(session.history) == 0
    session.pop_last_message()  # should not raise


@pytest.mark.asyncio
async def test_session_async_add_messages_and_stats(tmp_path):
    config = Config()
    config.memory.db_path = str(tmp_path / "sess_test.db")
    session = AgentSession(config)

    await session.async_add_user_message("User query", token_count=10)
    await session.async_add_assistant_message("Assistant reply", token_count=15)

    stats = await session.get_session_stats()
    assert stats["message_count"] == 2
    assert stats["total_tokens"] == 25
    assert stats["user_tokens"] == 10
    assert stats["assistant_tokens"] == 15

    await session.aclose()


@pytest.mark.asyncio
async def test_session_history_trimming_honors_budget_ratio():
    config = Config()
    config.memory.budget_ratio = 0.5  # 50% of 1000 = 500 token budget

    session = AgentSession(config)
    session.add_user_message("A" * 800)  # ~210 tokens
    session.add_assistant_message("B" * 800)  # ~210 tokens
    session.add_user_message("C" * 800)  # ~210 tokens

    # With max_context_tokens = 1000 and budget_ratio = 0.5, budget is 500 tokens
    trimmed = session._trim_history(max_context_tokens=1000)
    # The last 2 messages fit (~420 tokens), 3 messages (~630 tokens) exceeds 500
    assert len(trimmed) == 2
    assert trimmed[0].content == "B" * 800
    assert trimmed[1].content == "C" * 800
    await session.aclose()


@pytest.mark.asyncio
async def test_session_loads_agents_md(tmp_path, monkeypatch):
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("Project instructions: always write unit tests.")
    monkeypatch.chdir(tmp_path)

    config = Config()
    config.app.load_agents_md = True
    session = AgentSession(config)

    assert len(session.history) == 1
    assert session.history[0].role == "system"
    assert "Project instructions: always write unit tests." in session.history[0].content
    await session.aclose()


# ---- New tests for uncovered paths ----


@pytest.mark.asyncio
async def test_session_memory_store_init_exception(monkeypatch, tmp_path):
    """Test session handles memory store initialization failure gracefully."""
    config = Config()
    config.memory.db_path = str(tmp_path / "bad.db")
    # Make the path unwritable to trigger exception
    monkeypatch.setattr("agentcli.memory.store.MemoryStore.__init__", lambda self, *a, **k: (_ for _ in ()).throw(Exception("init failed")))

    session = AgentSession(config)
    # Should not raise, just log warning and continue without memory store
    assert session.memory_store is None
    await session.aclose()


@pytest.mark.asyncio
async def test_session_resume_nonexistent_session_id():
    """Test resuming a non-existent session creates new session."""
    config = Config()
    session = AgentSession(config, session_id="nonexistent_session_id")
    assert session.is_resumed is False
    assert session.session_id == "nonexistent_session_id"
    assert len(session.history) == 0
    await session.aclose()


@pytest.mark.asyncio
async def test_session_context_window_lookup_from_registry():
    """Test _resolve_context_window uses registry when available."""
    config = Config()
    config.routing.enabled = True
    session = AgentSession(config)

    # Test with known model from registry
    window = session._resolve_context_window("google/gemma-4-31b-it:free")
    assert window > 0  # Should get context window from registry

    # Test with unknown model (fallback to default)
    window = session._resolve_context_window("unknown/model")
    from agentcli.memory.budget import DEFAULT_CONTEXT_WINDOW
    assert window == DEFAULT_CONTEXT_WINDOW

    # Test with None model
    window = session._resolve_context_window(None)
    assert window == DEFAULT_CONTEXT_WINDOW

    await session.aclose()


@pytest.mark.asyncio
async def test_session_add_user_message_persist_failure(monkeypatch, tmp_path):
    """Test add_user_message handles persistence failure gracefully."""
    config = Config()
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    # Break the memory store's append_message
    def failing_append(*args, **kwargs):
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(session.memory_store, "append_message", failing_append)

    # Should not raise, just log debug
    session.add_user_message("test message")

    await session.aclose()


@pytest.mark.asyncio
async def test_session_async_add_user_message_persist_failure(monkeypatch, tmp_path):
    """Test async_add_user_message handles persistence failure gracefully."""
    config = Config()
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    # Break the memory store's aappend_message
    async def failing_aappend(*args, **kwargs):
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(session.memory_store, "aappend_message", failing_aappend)

    # Should not raise, just log debug
    await session.async_add_user_message("test message")

    await session.aclose()


@pytest.mark.asyncio
async def test_session_add_assistant_message_persist_failure(monkeypatch, tmp_path):
    """Test add_assistant_message handles persistence failure gracefully."""
    config = Config()
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    def failing_append(*args, **kwargs):
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(session.memory_store, "append_message", failing_append)

    session.add_assistant_message("assistant reply")
    await session.aclose()


@pytest.mark.asyncio
async def test_session_async_add_assistant_message_persist_failure(monkeypatch, tmp_path):
    """Test async_add_assistant_message handles persistence failure gracefully."""
    config = Config()
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    async def failing_aappend(*args, **kwargs):
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(session.memory_store, "aappend_message", failing_aappend)

    await session.async_add_assistant_message("assistant reply")
    await session.aclose()


@pytest.mark.asyncio
async def test_session_get_session_stats_no_memory_store():
    """Test get_session_stats when memory_store is None."""
    config = Config()
    config.memory.enabled = False
    session = AgentSession(config)

    stats = await session.get_session_stats()
    # Should return stats based on in-memory history
    assert stats["message_count"] == 0
    assert stats["total_tokens"] == 0
    await session.aclose()


@pytest.mark.asyncio
async def test_session_mark_success_no_registry():
    """Test mark_success when registry is None (routing disabled)."""
    config = Config()
    config.routing.enabled = False
    session = AgentSession(config)

    # Should not raise
    session.mark_success("some-model")
    await session.aclose()


@pytest.mark.asyncio
async def test_session_mark_failure_with_registry(monkeypatch):
    """Test mark_failure with registry and rate limited error."""
    from agentcli.openrouter_client import RateLimitedError

    config = Config()
    config.routing.enabled = True
    session = AgentSession(config)

    # Mock the registry's mark_failure
    monkeypatch.setattr(session.registry, "mark_failure", lambda model, rate_limited=False: None)

    exc = RateLimitedError("rate limited")
    session.mark_failure("test-model", exc, rate_limited=True)
    await session.aclose()


@pytest.mark.asyncio
async def test_session_mark_failure_no_registry():
    """Test mark_failure when registry is None."""
    config = Config()
    config.routing.enabled = False
    session = AgentSession(config)

    # Should not raise
    session.mark_failure("test-model", Exception("error"))
    await session.aclose()


@pytest.mark.asyncio
async def test_session_send_method(tmp_path):
    """Test the send method for routing decisions."""
    config = Config()
    config.routing.enabled = True
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    # Mock the client's chat_stream
    class MockClient:
        def __init__(self) -> None:
            self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        async def chat_stream(self, messages, model=None, models=None):
            yield "response"

        async def aclose(self) -> None:
            pass

    session.client = MockClient()

    reply = await session.send("analyze this code")
    assert hasattr(reply.stream, "__aiter__")  # Should be an async iterator
    await session.aclose()


@pytest.mark.asyncio
async def test_session_run_loop_integration(tmp_path, monkeypatch):
    """Test run_loop integration."""
    config = Config()
    config.agent_loop.enabled = True
    config.agent_loop.max_iterations = 1
    config.memory.db_path = str(tmp_path / "test.db")
    session = AgentSession(config)

    # Mock the agent loop to avoid actual LLM calls
    from agentcli.agent.events import FinishEvent

    async def mock_run_loop(goal):
        yield FinishEvent(iteration=1, summary="Done")

    monkeypatch.setattr(session, "run_loop", mock_run_loop)

    events = []
    async for event in session.run_loop("test goal"):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], FinishEvent)
    await session.aclose()