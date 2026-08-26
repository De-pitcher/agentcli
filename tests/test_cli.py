import argparse
from unittest.mock import AsyncMock

import pytest

from agentcli.cli import main, run_chat, run_config
from agentcli.config import Config


@pytest.mark.asyncio
async def test_run_chat_preload_survives_trimming(monkeypatch, tmp_path):
    f = tmp_path / "sys.txt"
    f.write_text("context")

    args = argparse.Namespace(model="test", file=[str(f)])
    config = Config()
    config.app.history_turns = 1

    # Fake input: user says something twice, then /exit
    inputs = ["hi", "hello again", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.history_lengths = []
            self.last_history = []

        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            self.history_lengths.append(len(messages))
            self.last_history = messages
            yield "Response"

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == 0
    # Expected trimming:
    # After "hi" (history length 2: sys + user) -> len=2
    # After "hello again" (history length 4: sys + user + assistant + user).
    # history_turns=1, so max messages = 2*1+1 = 3 + 1 (sys) = 4
    assert len(fake_client.last_history) == 4
    assert fake_client.last_history[0].role == "system"


@pytest.mark.asyncio
async def test_run_chat_failed_turn_not_polluting(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    inputs = ["fail me", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    from agentcli.openrouter_client import OpenRouterError

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            raise OpenRouterError("Oops")
            yield "never"

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == 0


def test_run_config_init_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("agentcli.config.find_config_path", lambda: tmp_path / "agentcli.toml")
    args_init = argparse.Namespace(config_command="init")
    config = Config()

    assert run_config(args_init, config) == 0
    assert (tmp_path / "agentcli.toml").exists()

    args_show = argparse.Namespace(config_command="show")
    assert run_config(args_show, config) == 0
    out, _ = capsys.readouterr()
    assert "app.stream" in out


def test_main_chat(monkeypatch):
    monkeypatch.setattr("agentcli.cli.run_chat", AsyncMock(return_value=0))
    assert main(["chat"]) == 0


def test_main_config(monkeypatch):
    monkeypatch.setattr("agentcli.cli.run_config", lambda a, c: 0)
    assert main(["config", "show"]) == 0


def test_main_help(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        main(["--help"])


@pytest.mark.asyncio
async def test_run_chat_keyboard_interrupt_mid_stream(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    inputs = ["start", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    class FakeClient:
        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            yield "partial"
            raise KeyboardInterrupt()

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == 0
