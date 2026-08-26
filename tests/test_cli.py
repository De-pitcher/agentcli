import argparse
from unittest.mock import AsyncMock

import pytest

from agentcli.cli import main, run_chat, run_config
from agentcli.config import Config
from agentcli.exit_codes import ExitCode
from agentcli.openrouter_client import OpenRouterError


@pytest.mark.asyncio
async def test_run_chat_preload_survives_trimming(monkeypatch, tmp_path):
    f = tmp_path / "sys.txt"
    f.write_text("context")

    args = argparse.Namespace(model="test", file=[str(f)])
    config = Config()
    config.app.history_turns = 1

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

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert len(fake_client.last_history) == 4
    assert fake_client.last_history[0].role == "system"


@pytest.mark.asyncio
async def test_run_chat_multiline_input(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    # User inputs line 1 with backslash, line 2 without, then /exit
    inputs = ["def hello():\\", "    print('world')", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.last_history = []

        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            self.last_history = messages
            yield "Got code"

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert len(fake_client.last_history) == 1
    assert fake_client.last_history[0].content == "def hello():\n    print('world')"


@pytest.mark.asyncio
async def test_run_chat_empty_model_response(monkeypatch, capsys):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    inputs = ["test prompt", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    class FakeClient:
        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            yield "   "  # whitespace only

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "(model returned an empty response)" in out


@pytest.mark.asyncio
async def test_run_chat_exit_codes_missing_api_key(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    def raise_init(*a, **k):
        raise OpenRouterError("No API key")

    monkeypatch.setattr("agentcli.cli.OpenRouterClient", raise_init)
    assert await run_chat(args, config) == ExitCode.CONFIG_ERROR


@pytest.mark.asyncio
async def test_run_chat_exit_codes_missing_preload_file(monkeypatch):
    args = argparse.Namespace(model="test", file=["nonexistent_file_path_123.txt"])
    config = Config()

    class FakeClient:
        async def aclose(self):
            pass

    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: FakeClient())
    assert await run_chat(args, config) == ExitCode.CONFIG_ERROR


@pytest.mark.asyncio
async def test_run_chat_top_level_interrupt(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    def raise_interrupt(prompt):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", raise_interrupt)

    class FakeClient:
        async def aclose(self):
            pass

    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: FakeClient())
    assert await run_chat(args, config) == ExitCode.USER_INTERRUPT


@pytest.mark.asyncio
async def test_run_chat_failed_turn_not_polluting(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    inputs = ["fail me", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    class FakeClient:
        async def aclose(self):
            pass

        async def chat_stream(self, messages, model):
            raise OpenRouterError("Oops")
            yield "never"

    fake_client = FakeClient()
    monkeypatch.setattr("agentcli.cli.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS


def test_run_config_init_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("agentcli.config.find_config_path", lambda: tmp_path / "agentcli.toml")
    args_init = argparse.Namespace(config_command="init")
    config = Config()

    assert run_config(args_init, config) == ExitCode.SUCCESS
    assert (tmp_path / "agentcli.toml").exists()

    args_show = argparse.Namespace(config_command="show")
    assert run_config(args_show, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "app.stream" in out


def test_run_config_init_existing_file_reports_and_preserves(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "agentcli.toml"
    monkeypatch.setattr("agentcli.config.find_config_path", lambda: cfg_path)
    args = argparse.Namespace(config_command="init")
    config = Config()

    assert run_config(args, config) == ExitCode.SUCCESS
    cfg_path.write_text("# custom config")

    assert run_config(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "already exists" in out
    assert cfg_path.read_text() == "# custom config"


def test_main_chat(monkeypatch):
    monkeypatch.setattr("agentcli.cli.run_chat", AsyncMock(return_value=ExitCode.SUCCESS))
    assert main(["chat"]) == ExitCode.SUCCESS


def test_main_config(monkeypatch):
    monkeypatch.setattr("agentcli.cli.run_config", lambda a, c: ExitCode.SUCCESS)
    assert main(["config", "show"]) == ExitCode.SUCCESS


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

    assert await run_chat(args, config) == ExitCode.SUCCESS
