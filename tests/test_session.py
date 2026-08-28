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
