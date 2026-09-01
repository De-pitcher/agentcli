from dataclasses import dataclass

from agentcli.agent.events import (
    FinishEvent,
    LoopErrorEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
)
from agentcli.subagents.base import SubAgentResult, SubAgentType
from agentcli.ui.render import ConsoleRenderer


@dataclass
class DummySession:
    id: str
    title: str
    updated_at: str


def test_renderer_detection(monkeypatch):
    # 1. Plain flag
    r_plain = ConsoleRenderer(plain=True)
    assert r_plain.is_rich_enabled is False

    # 2. No-color flag
    r_nocolor = ConsoleRenderer(no_color=True)
    assert r_nocolor.is_rich_enabled is False

    # 3. Non-TTY
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    r_nontty = ConsoleRenderer()
    assert r_nontty.is_rich_enabled is False

    # 4. NO_COLOR env var
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    r_env = ConsoleRenderer()
    assert r_env.is_rich_enabled is False

    # 5. TERM=dumb
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    r_dumb = ConsoleRenderer()
    assert r_dumb.is_rich_enabled is False

    # 6. TTY with no disabling env vars
    monkeypatch.delenv("TERM", raising=False)
    r_active = ConsoleRenderer()
    assert r_active.is_rich_enabled is True


def test_renderer_print_chunk(capsys):
    renderer = ConsoleRenderer(plain=True)
    renderer.print_chunk("Hello, ")
    renderer.print_chunk("world!")
    out = capsys.readouterr().out
    assert out == "Hello, world!"


def test_renderer_markdown(capsys, monkeypatch):
    # Plain
    r_plain = ConsoleRenderer(plain=True)
    r_plain.render_markdown("# Title\n- item 1")
    out_plain = capsys.readouterr().out
    assert "# Title" in out_plain

    # Rich
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    r_rich.render_markdown("# Title\n- item 1")
    out_rich = capsys.readouterr().out
    assert "Title" in out_rich


def test_renderer_file_preview(capsys, monkeypatch):
    # Plain
    r_plain = ConsoleRenderer(plain=True)
    r_plain.render_file_preview("test.py", "print(123)")
    assert "[loaded @test.py]" in capsys.readouterr().out

    # Rich
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    r_rich.render_file_preview("test.py", "def foo():\n    return 42\n" * 15)
    out_rich = capsys.readouterr().out
    assert "test.py" in out_rich


def test_renderer_loop_events_plain_and_rich(capsys, monkeypatch):
    events = [
        PlanEvent(
            iteration=1,
            plan=[{"agent_type": "shell_execution", "payload": {"command": "ls"}}],
            is_replan=False,
        ),
        StepStartEvent(iteration=1, step_index=0, agent_type="shell_execution"),
        StepResultEvent(
            iteration=1,
            step_index=0,
            result=SubAgentResult(
                task_id="t1", agent_type=SubAgentType.SHELL_EXECUTION, success=True, output={}
            ),
        ),
        StepResultEvent(
            iteration=1,
            step_index=1,
            result=SubAgentResult(
                task_id="t2", agent_type=SubAgentType.SHELL_EXECUTION, success=False, error="fail"
            ),
        ),
        ReflectEvent(iteration=1, decision="RETRY", reason="need more info"),
        FinishEvent(iteration=1, summary="All tasks complete"),
        LoopErrorEvent(iteration=1, error="Timeout exceeded"),
    ]

    # Test plain mode
    r_plain = ConsoleRenderer(plain=True)
    for ev in events:
        r_plain.render_loop_event(ev, verbose=True)
    out_plain = capsys.readouterr().out
    assert "[plan]" in out_plain
    assert "step 1" in out_plain
    assert "[reflect]" in out_plain
    assert "[done]" in out_plain
    assert "[loop-error]" in out_plain

    # Test rich mode
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    for ev in events:
        r_rich.render_loop_event(ev, verbose=True)
    out_rich = capsys.readouterr().out
    assert "Plan" in out_rich
    assert "Done" in out_rich


def test_renderer_sessions_table(capsys, monkeypatch):
    sessions = [
        DummySession(id="sess_1", title="First chat", updated_at="2026-08-29T05:00:00.000Z"),
        DummySession(id="sess_2", title="Second chat", updated_at="2026-08-29T05:10:00.000Z"),
    ]
    stats_map = {
        "sess_1": {"message_count": 4, "total_tokens": 120},
        "sess_2": {"message_count": 8, "total_tokens": 350},
    }

    # Plain
    r_plain = ConsoleRenderer(plain=True)
    r_plain.render_sessions_table(sessions, lambda sid: stats_map[sid])
    out_plain = capsys.readouterr().out
    assert "SESSION ID" in out_plain
    assert "sess_1" in out_plain
    assert "First chat" in out_plain

    # Plain empty
    r_plain.render_sessions_table([], lambda sid: {})
    assert "No saved sessions found." in capsys.readouterr().out

    # Rich
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    r_rich.render_sessions_table(sessions, lambda sid: stats_map[sid])
    out_rich = capsys.readouterr().out
    assert "Persisted Sessions" in out_rich
    assert "sess_1" in out_rich


def test_renderer_model_badge(capsys, monkeypatch):
    # Plain
    r_plain = ConsoleRenderer(plain=True)
    r_plain.render_model_badge(
        "openai/gpt-4o", is_fallback=True, requested_category="code", served_category="chat"
    )
    assert "fallback from category code to chat" in capsys.readouterr().out

    r_plain.render_model_badge("anthropic/claude-3.5-sonnet", requested_primary="openai/gpt-4o")
    assert "routed from openai/gpt-4o" in capsys.readouterr().out

    r_plain.render_model_badge("google/gemma-4", show_always=True)
    assert "[model: google/gemma-4]" in capsys.readouterr().out

    # Rich
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    r_rich.render_model_badge("openai/gpt-4o", show_always=True)
    assert "openai/gpt-4o" in capsys.readouterr().out


def test_renderer_token_usage(capsys, monkeypatch):
    # Plain exact
    r_plain = ConsoleRenderer(plain=True)
    r_plain.render_token_usage({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
    assert "prompt=10, completion=20, total=30" in capsys.readouterr().out

    # Plain heuristic estimate
    r_plain.render_token_usage(
        None, expanded_prompt="hello", full_reply="world", estimate_fn=lambda text: len(text)
    )
    assert "prompt~5, completion~5, total~10" in capsys.readouterr().out

    # Rich
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    r_rich = ConsoleRenderer(plain=False)
    r_rich.render_token_usage({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
    assert "tokens:" in capsys.readouterr().out
