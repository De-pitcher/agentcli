import argparse
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentcli.cli import main, run_chat, run_config
from agentcli.config import Config
from agentcli.exit_codes import ExitCode
from agentcli.openrouter_client import OpenRouterError
from agentcli.session import AgentSession


@pytest.fixture(autouse=True)
def set_cli_test_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-dummy-key")


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
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

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
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert any(m.role == "user" and m.content == "def hello():\n    print('world')" for m in fake_client.last_history)


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
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "(model returned an empty response)" in out


@pytest.mark.asyncio
async def test_run_chat_exit_codes_missing_api_key(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    def raise_init(*a, **k):
        raise OpenRouterError("No API key")

    monkeypatch.setattr("agentcli.session.OpenRouterClient", raise_init)
    assert await run_chat(args, config) == ExitCode.CONFIG_ERROR


@pytest.mark.asyncio
async def test_run_chat_exit_codes_missing_preload_file(monkeypatch):
    args = argparse.Namespace(model="test", file=["nonexistent_file_path_123.txt"])
    config = Config()

    class FakeClient:
        async def aclose(self):
            pass

    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: FakeClient())
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

    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: FakeClient())
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
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

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
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS


def test_main_keyboard_interrupt_returns_user_interrupt(monkeypatch):
    def raise_ki(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("agentcli.cli.run_chat", raise_ki)
    assert main(["chat"]) == ExitCode.USER_INTERRUPT


@pytest.mark.asyncio
async def test_run_chat_survives_aclose_failure(monkeypatch):
    args = argparse.Namespace(model="test", file=[])
    config = Config()

    inputs = ["hi", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    class FakeClient:
        async def aclose(self):
            raise KeyboardInterrupt()

        async def chat_stream(self, messages, model):
            yield "ok"

    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: FakeClient())
    assert await run_chat(args, config) == ExitCode.SUCCESS


class RecordingClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.last_served_model = "served/model:free"

    async def aclose(self):
        pass

    async def chat_stream(self, messages, model=None, models=None):
        self.calls.append({"model": model, "models": models})
        yield "ok"


@pytest.mark.asyncio
async def test_run_chat_auto_routes_by_task(monkeypatch):
    args = argparse.Namespace(model=None, file=[], show_model=False)
    config = Config()

    inputs = ["write a def hello() function in python", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    fake_client = RecordingClient()
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert fake_client.calls[0]["model"] is None
    models = fake_client.calls[0]["models"]
    assert isinstance(models, list)
    assert models[0] == "cohere/north-mini-code:free"
    assert len(models) > 1


@pytest.mark.asyncio
async def test_run_chat_model_flag_bypasses_routing(monkeypatch):
    args = argparse.Namespace(model="forced/model:free", file=[], show_model=False)
    config = Config()

    inputs = ["explain why the sky is blue", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    fake_client = RecordingClient()
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert fake_client.calls[0] == {"model": "forced/model:free", "models": None}


@pytest.mark.asyncio
async def test_run_chat_routing_disabled_uses_default_model(monkeypatch):
    args = argparse.Namespace(model=None, file=[], show_model=False)
    config = Config()
    config.routing.enabled = False

    inputs = ["hello", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    fake_client = RecordingClient()
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    assert fake_client.calls[0]["model"] == config.openrouter.default_model
    assert fake_client.calls[0]["models"] is None


@pytest.mark.asyncio
async def test_run_chat_show_model_reports_routed_model(monkeypatch, capsys):
    args = argparse.Namespace(model=None, file=[], show_model=True)
    config = Config()

    inputs = ["explain why the sky is blue", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    fake_client = RecordingClient()
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)
    assert await run_chat(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "[model: served/model:free" in out
    assert "routed from" in out


@pytest.mark.asyncio
async def test_run_chat_verbose_token_reporting(monkeypatch, capsys):
    args = argparse.Namespace(model=None, file=[], show_model=False, verbose=True)
    config = Config()

    inputs = ["hello world", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    class UsageClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    fake_client = UsageClient()
    monkeypatch.setattr("agentcli.session.OpenRouterClient", lambda _: fake_client)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "[tokens: prompt=10, completion=5, total=15]" in out


@pytest.mark.asyncio
async def test_run_chat_handles_no_available_model_error(monkeypatch, capsys):
    from agentcli.routing.router import NoAvailableModelError

    args = argparse.Namespace(model=None, file=[], show_model=False)
    config = Config()

    inputs = ["test message", "/exit"]

    def fake_input(prompt):
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    class DummyRouter:
        def decide(self, category):
            raise NoAvailableModelError("All models cooling down")

    def make_session(cfg, **kwargs):
        s = AgentSession(cfg, **kwargs)
        s.router = DummyRouter()
        return s

    monkeypatch.setattr("agentcli.cli.AgentSession", make_session)

    assert await run_chat(args, config) == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "[routing error] All models cooling down" in out


def test_sessions_show_and_list_displays_tokens(tmp_path: Path, capsys: Any):
    from agentcli.memory.store import MemoryStore

    db_file = tmp_path / "cli_tokens.db"
    cfg_file = tmp_path / "agentcli.toml"
    cfg_file.write_text(
        f"[openrouter]\napi_key_env = 'K'\n[memory]\nenabled = true\ndb_path = '{db_file.as_posix()}'\n",
        encoding="utf-8",
    )

    store = MemoryStore(db_file)
    store.create_session("sess_tok", title="Token Session")
    store.append_message("sess_tok", "user", "Hello", token_count=8)
    store.append_message("sess_tok", "assistant", "Hi there!", token_count=12)
    store.close()

    with patch.dict(os.environ, {"K": "sk-test", "AGENTCLI_CONFIG": str(cfg_file)}):
        # List command displays TOKENS header
        assert main(["sessions", "list"]) == ExitCode.SUCCESS
        out_list = capsys.readouterr().out
        assert "TOKENS" in out_list
        assert "20" in out_list

        # Show command displays Token Usage summary
        assert main(["sessions", "show", "sess_tok"]) == ExitCode.SUCCESS
        out_show = capsys.readouterr().out
        assert "Token Usage: 20 total (8 prompt, 12 completion)" in out_show
        assert "Est. Cost:" in out_show
        assert "$0.0000" in out_show
        assert "[8 tokens]" in out_show
        assert "[12 tokens]" in out_show


def test_cli_preset_and_mcp_command(monkeypatch):
    monkeypatch.setattr("agentcli.mcp.run_mcp", lambda **kwargs: ExitCode.SUCCESS)
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-dummy"}):
        assert main(["--preset", "coding", "mcp"]) == ExitCode.SUCCESS


@pytest.mark.asyncio
async def test_run_goal_success(monkeypatch, capsys):
    from agentcli.agent.events import (
        FinishEvent,
        PlanEvent,
        ReflectEvent,
        StepResultEvent,
        StepStartEvent,
    )
    from agentcli.cli import run_goal
    from agentcli.subagents.base import SubAgentResult, SubAgentType

    args = argparse.Namespace(
        goal="Build feature X",
        model="google/gemma-4-31b-it:free",
        file=[],
        max_iterations=3,
        no_agents_md=True,
        allow_write=True,
        plain=True,
        no_color=True,
    )
    config = Config()

    class MockLoop:
        def __init__(self, *args, **kwargs):
            self.initial_context = kwargs.get("initial_context")

        async def run(self):
            yield PlanEvent(iteration=1, run_id="r1", plan=[{"agent_type": "file_ops", "payload": {}}])
            yield StepStartEvent(iteration=1, run_id="r1", step_index=0, agent_type="file_ops", payload={})
            yield StepResultEvent(
                iteration=1,
                run_id="r1",
                step_index=0,
                result=SubAgentResult(task_id="t1", agent_type=SubAgentType.FILE_OPS, success=True, output={"status": "ok"}),
                duration_seconds=0.15,
            )
            yield ReflectEvent(iteration=1, run_id="r1", decision="FINISH", reason="Task completed")
            yield FinishEvent(iteration=1, run_id="r1", summary="All done successfully", output={"status": "ok"}, duration_seconds=0.5)

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)

    code = await run_goal(args, config)
    assert code == ExitCode.SUCCESS
    out, _ = capsys.readouterr()
    assert "Goal: Build feature X" in out
    assert "All done successfully" in out


@pytest.mark.asyncio
async def test_run_goal_loop_error(monkeypatch):
    from agentcli.agent.events import LoopErrorEvent
    from agentcli.cli import run_goal

    args = argparse.Namespace(
        goal="Faulty task",
        model=None,
        file=[],
        max_iterations=None,
        no_agents_md=True,
        allow_write=False,
        plain=True,
        no_color=True,
    )
    config = Config()

    class MockLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            yield LoopErrorEvent(iteration=1, run_id="r1", error="Fatal planner explosion")

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)

    code = await run_goal(args, config)
    assert code == ExitCode.GENERAL_ERROR


@pytest.mark.asyncio
async def test_run_goal_iteration_limit(monkeypatch, capsys):
    from agentcli.agent.loop import LoopIterationLimitError
    from agentcli.cli import run_goal

    args = argparse.Namespace(
        goal="Endless loop",
        model=None,
        file=[],
        max_iterations=2,
        no_agents_md=True,
        allow_write=False,
        plain=True,
        no_color=True,
    )
    config = Config()

    class MockLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            if True:
                raise LoopIterationLimitError("Hit 2 iterations")
            yield

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)

    code = await run_goal(args, config)
    assert code == ExitCode.GENERAL_ERROR
    out, _ = capsys.readouterr()
    assert "Hit 2 iterations" in out


@pytest.mark.asyncio
async def test_run_goal_keyboard_interrupt(monkeypatch, capsys):
    from agentcli.cli import run_goal

    args = argparse.Namespace(
        goal="Interrupt me",
        model=None,
        file=[],
        max_iterations=5,
        no_agents_md=True,
        allow_write=False,
        plain=True,
        no_color=True,
    )
    config = Config()

    class MockLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            if True:
                raise KeyboardInterrupt()
            yield

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)

    code = await run_goal(args, config)
    assert code == ExitCode.USER_INTERRUPT
    out, _ = capsys.readouterr()
    assert "[interrupted]" in out


@pytest.mark.asyncio
async def test_run_goal_context_files_and_agents_md(tmp_path, monkeypatch):
    from agentcli.agent.events import FinishEvent
    from agentcli.cli import run_goal

    context_file = tmp_path / "extra.py"
    context_file.write_text("print('extra')", encoding="utf-8")

    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project Rules\nRule 1: Always test.", encoding="utf-8")

    args = argparse.Namespace(
        goal="Refactor project",
        model="custom-model",
        file=[str(context_file)],
        max_iterations=5,
        no_agents_md=False,
        allow_write=True,
        plain=True,
        no_color=True,
    )
    config = Config()

    captured_context = None

    class MockLoop:
        def __init__(self, *args, **kwargs):
            nonlocal captured_context
            captured_context = kwargs.get("initial_context")

        async def run(self):
            yield FinishEvent(iteration=1, run_id="r1", summary="Complete", duration_seconds=0.1)

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)
    monkeypatch.chdir(tmp_path)

    code = await run_goal(args, config)
    assert code == ExitCode.SUCCESS
    assert captured_context is not None
    assert "Rule 1: Always test." in captured_context
    assert "print('extra')" in captured_context


@pytest.mark.asyncio
async def test_run_goal_invalid_file(tmp_path):
    from agentcli.cli import run_goal

    args = argparse.Namespace(
        goal="Refactor project",
        model="custom-model",
        file=[str(tmp_path / "non_existent.py")],
        max_iterations=5,
        no_agents_md=True,
        allow_write=True,
        plain=True,
        no_color=True,
    )
    config = Config()

    code = await run_goal(args, config)
    assert code == ExitCode.GENERAL_ERROR


def test_main_run_command_success(monkeypatch):
    from agentcli.agent.events import FinishEvent

    class MockLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            yield FinishEvent(iteration=1, run_id="r1", summary="Success", duration_seconds=0.1)

    monkeypatch.setattr("agentcli.agent.loop.AgentLoop", MockLoop)

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-dummy"}):
        exit_code = main(["run", "Fix bug in parser", "--model", "test/model", "--allow-write"])
        assert exit_code == ExitCode.SUCCESS

