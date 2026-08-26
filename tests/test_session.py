import pytest
from agentcli.config import Config
from agentcli.session import AgentSession
from agentcli.openrouter_client import ChatMessage

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")

@pytest.mark.asyncio
async def test_session_history_trimming():
    config = Config()
    config.app.history_turns = 1 # keep 1 pair + 1 current message = 3 messages max
    
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
    session.pop_last_message() # should not raise

