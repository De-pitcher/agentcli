from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcli.agent.events import (
    FinishEvent,
    LoopErrorEvent,
    PlanEvent,
    ReflectEvent,
    StepResultEvent,
    StepStartEvent,
)
from agentcli.agent.loop import AgentLoop, LoopIterationLimitError, is_agentic_task
from agentcli.agent.reflector import DefaultReflector, ReflectDecision, ReflectOutcome
from agentcli.agent.registry import ToolRegistry
from agentcli.config import AgentLoopConfig, Config, ConfigError, load_config
from agentcli.session import AgentSession
from agentcli.subagents.base import SubAgentResult, SubAgentType
from agentcli.subagents.planner import PlannerAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(success: bool, error: str | None = None) -> SubAgentResult:
    return SubAgentResult(
        task_id="t",
        agent_type=SubAgentType.FILE_OPS,
        success=success,
        output={"content": "done"} if success else None,
        error=error,
    )


def _plan_step(agent_type: str = "file_ops") -> dict[str, Any]:
    return {"agent_type": agent_type, "payload": {}, "priority": 1, "goal_criterion": ""}


async def _collect(loop: AgentLoop) -> list[object]:
    """Drive the loop and collect all events."""
    events: list[object] = []
    async for ev in loop.run():
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# DefaultReflector unit tests
# ---------------------------------------------------------------------------


class TestDefaultReflector:
    def setup_method(self) -> None:
        self.reflector = DefaultReflector()

    def test_all_success_returns_finish(self) -> None:
        results = [_make_result(True)]
        outcome = self.reflector.reflect("goal", [_plan_step()], results)
        assert outcome.decision == ReflectDecision.FINISH

    def test_hard_failure_returns_replan(self) -> None:
        results = [_make_result(False, "file not found")]
        outcome = self.reflector.reflect("goal", [_plan_step()], results)
        assert outcome.decision == ReflectDecision.REPLAN

    def test_transient_failure_all_returns_retry(self) -> None:
        results = [_make_result(False, "rate limit exceeded")]
        outcome = self.reflector.reflect("goal", [_plan_step()], results)
        assert outcome.decision == ReflectDecision.RETRY

    def test_no_results_returns_fail(self) -> None:
        outcome = self.reflector.reflect("goal", [], [])
        assert outcome.decision == ReflectDecision.FAIL

    def test_partial_success_transient_returns_replan(self) -> None:
        results = [_make_result(True), _make_result(False, "timeout")]
        outcome = self.reflector.reflect("goal", [_plan_step(), _plan_step()], results)
        assert outcome.decision == ReflectDecision.REPLAN

    def test_goal_criterion_unmet_returns_replan(self) -> None:
        step = {"agent_type": "file_ops", "payload": {}, "goal_criterion": "expected_value"}
        result = SubAgentResult(
            task_id="t",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "something_else"},
        )
        outcome = self.reflector.reflect("goal", [step], [result])
        assert outcome.decision == ReflectDecision.REPLAN

    def test_goal_criterion_met_returns_finish(self) -> None:
        step = {"agent_type": "file_ops", "payload": {}, "goal_criterion": "done"}
        result = SubAgentResult(
            task_id="t",
            agent_type=SubAgentType.FILE_OPS,
            success=True,
            output={"content": "task done successfully"},
        )
        outcome = self.reflector.reflect("goal", [step], [result])
        assert outcome.decision == ReflectDecision.FINISH


# ---------------------------------------------------------------------------
# ToolRegistry unit tests
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_registered_types_contains_defaults(self) -> None:
        registry = ToolRegistry()
        types = registry.registered_types()
        assert "file_ops" in types
        assert "shell_execution" in types
        assert "code_analyzer" in types
        assert "web_search" in types

    def test_custom_tool_registration(self) -> None:
        registry = ToolRegistry()
        mock_agent = MagicMock()
        mock_agent.return_value.run = AsyncMock(return_value=_make_result(True))
        registry.register("custom_tool", mock_agent)
        assert "custom_tool" in registry.registered_types()

    @pytest.mark.asyncio
    async def test_execute_unknown_type_returns_failure(self) -> None:
        registry = ToolRegistry()
        result = await registry.execute("unknown_xyz", {})
        assert result.success is False
        assert "No tool registered" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_custom_tool_success(self) -> None:
        registry = ToolRegistry()
        expected = _make_result(True)
        mock_agent = MagicMock()
        mock_agent.return_value.run = AsyncMock(return_value=expected)
        registry.register("my_tool", mock_agent)
        result = await registry.execute("my_tool", {"key": "val"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_captures_exception(self) -> None:
        registry = ToolRegistry()
        mock_agent = MagicMock()
        mock_agent.return_value.run = AsyncMock(side_effect=RuntimeError("boom"))
        registry.register("bad_tool", mock_agent)
        result = await registry.execute("bad_tool", {})
        assert result.success is False
        assert "boom" in (result.error or "")


# ---------------------------------------------------------------------------
# AgentLoop control-flow tests (all with mocked planner/executor/reflector)
# ---------------------------------------------------------------------------


def _make_loop(
    goal: str = "test goal",
    max_iterations: int = 5,
    plan: list[dict[str, Any]] | None = None,
    execute_result: SubAgentResult | None = None,
    reflect_outcome: ReflectOutcome | None = None,
) -> tuple[AgentLoop, MagicMock, MagicMock, MagicMock]:
    """Build an AgentLoop with mocked planner, registry, and reflector."""
    plan = plan or [_plan_step()]
    execute_result = execute_result or _make_result(True)
    reflect_outcome = reflect_outcome or ReflectOutcome(ReflectDecision.FINISH, "all done")

    mock_planner = MagicMock()
    mock_planner.run = AsyncMock(
        return_value=SubAgentResult(
            task_id="p",
            agent_type=SubAgentType.PLANNER,
            success=True,
            output={"plan": plan},
        )
    )

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=execute_result)
    mock_registry._safe_type = ToolRegistry._safe_type  # keep the static method

    mock_reflector = MagicMock()
    mock_reflector.reflect = MagicMock(return_value=reflect_outcome)

    loop = AgentLoop(
        goal=goal,
        registry=mock_registry,
        planner=mock_planner,
        reflector=mock_reflector,
        max_iterations=max_iterations,
    )
    return loop, mock_planner, mock_registry, mock_reflector


class TestAgentLoopHappyPath:
    @pytest.mark.asyncio
    async def test_emits_plan_step_reflect_finish_events(self) -> None:
        loop, _, _, _ = _make_loop()
        events = await _collect(loop)

        types = [type(e) for e in events]
        assert PlanEvent in types
        assert StepStartEvent in types
        assert StepResultEvent in types
        assert ReflectEvent in types
        assert FinishEvent in types

    @pytest.mark.asyncio
    async def test_finish_event_carries_summary(self) -> None:
        loop, _, _, _ = _make_loop()
        events = await _collect(loop)
        finish = next(e for e in events if isinstance(e, FinishEvent))
        assert "1/1" in finish.summary

    @pytest.mark.asyncio
    async def test_planner_called_once(self) -> None:
        loop, mock_planner, _, _ = _make_loop()
        await _collect(loop)
        mock_planner.run.assert_awaited_once()


class TestAgentLoopReplanPath:
    @pytest.mark.asyncio
    async def test_replan_invokes_planner_twice(self) -> None:
        call_count = 0
        reflector_responses = [
            ReflectOutcome(ReflectDecision.REPLAN, "retry"),
            ReflectOutcome(ReflectDecision.FINISH, "done"),
        ]

        def reflect_side(*_: Any) -> ReflectOutcome:
            nonlocal call_count
            resp = reflector_responses[min(call_count, len(reflector_responses) - 1)]
            call_count += 1
            return resp

        loop, mock_planner, _, mock_reflector = _make_loop(max_iterations=3)
        mock_reflector.reflect = MagicMock(side_effect=reflect_side)

        events = await _collect(loop)
        assert mock_planner.run.await_count == 2
        assert any(isinstance(e, FinishEvent) for e in events)

    @pytest.mark.asyncio
    async def test_replan_event_has_is_replan_true(self) -> None:
        reflector_responses = [
            ReflectOutcome(ReflectDecision.REPLAN, "retry"),
            ReflectOutcome(ReflectDecision.FINISH, "done"),
        ]
        idx = 0

        def reflect_side(*_: Any) -> ReflectOutcome:
            nonlocal idx
            r = reflector_responses[min(idx, 1)]
            idx += 1
            return r

        loop, _, _, mock_reflector = _make_loop(max_iterations=3)
        mock_reflector.reflect = MagicMock(side_effect=reflect_side)
        events = await _collect(loop)
        plan_events = [e for e in events if isinstance(e, PlanEvent)]
        # Second plan event should have is_replan=True
        assert len(plan_events) == 2
        assert plan_events[1].is_replan is True


class TestAgentLoopRetryPath:
    @pytest.mark.asyncio
    async def test_retry_preserves_plan_and_does_not_reinvoke_planner(self) -> None:
        """On RETRY, the loop re-executes the current plan without calling planner again."""
        call_count = 0
        reflector_responses = [
            ReflectOutcome(ReflectDecision.RETRY, "transient 429"),
            ReflectOutcome(ReflectDecision.FINISH, "done on retry"),
        ]

        def reflect_side(*_: Any) -> ReflectOutcome:
            nonlocal call_count
            resp = reflector_responses[min(call_count, len(reflector_responses) - 1)]
            call_count += 1
            return resp

        loop, mock_planner, mock_registry, mock_reflector = _make_loop(max_iterations=3)
        mock_reflector.reflect = MagicMock(side_effect=reflect_side)

        events = await _collect(loop)

        # Planner should only have been called once on iteration 1
        assert mock_planner.run.await_count == 1
        # Step execution ran twice (iteration 1 failed, iteration 2 retried)
        assert mock_registry.execute.await_count == 2
        # Only 1 PlanEvent should have been emitted
        plan_events = [e for e in events if isinstance(e, PlanEvent)]
        assert len(plan_events) == 1
        # FinishEvent should have been reached
        assert any(isinstance(e, FinishEvent) for e in events)


class TestAgentLoopIterationCeiling:
    @pytest.mark.asyncio
    async def test_raises_limit_error_on_ceiling(self) -> None:
        """A misbehaving reflector that always returns RETRY must be stopped."""
        loop, _, _, mock_reflector = _make_loop(max_iterations=3)
        mock_reflector.reflect = MagicMock(
            return_value=ReflectOutcome(ReflectDecision.RETRY, "keep going")
        )
        with pytest.raises(LoopIterationLimitError):
            await _collect(loop)

    @pytest.mark.asyncio
    async def test_ceiling_of_one_works(self) -> None:
        loop, _, _, _ = _make_loop(max_iterations=1)
        events = await _collect(loop)
        assert any(isinstance(e, FinishEvent) for e in events)


class TestAgentLoopMidLoopError:
    @pytest.mark.asyncio
    async def test_openrouter_error_surfaces_as_step_failure(self) -> None:
        """Mid-loop model errors must produce a failed SubAgentResult, not crash the loop."""
        from agentcli.openrouter_client import RateLimitedError

        loop, _, mock_registry, mock_reflector = _make_loop()
        mock_registry.execute = AsyncMock(side_effect=RateLimitedError("rate limited"))
        # After the failed step, reflector says fail so loop terminates cleanly
        mock_reflector.reflect = MagicMock(
            return_value=ReflectOutcome(ReflectDecision.FAIL, "unrecoverable")
        )
        events = await _collect(loop)
        assert any(isinstance(e, LoopErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_step_failure_does_not_lose_prior_results(self) -> None:
        """Results from already-completed steps must survive a later step failure."""
        plan = [_plan_step("file_ops"), _plan_step("shell_execution")]
        results = [_make_result(True), _make_result(False, "not found")]

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(side_effect=results)
        mock_registry._safe_type = ToolRegistry._safe_type

        mock_planner = MagicMock()
        mock_planner.run = AsyncMock(
            return_value=SubAgentResult(
                task_id="p",
                agent_type=SubAgentType.PLANNER,
                success=True,
                output={"plan": plan},
            )
        )
        mock_reflector = MagicMock()
        mock_reflector.reflect = MagicMock(
            return_value=ReflectOutcome(ReflectDecision.FINISH, "partial ok")
        )

        loop = AgentLoop(
            goal="multi-step",
            registry=mock_registry,
            planner=mock_planner,
            reflector=mock_reflector,
            max_iterations=3,
        )
        events = await _collect(loop)
        step_results = [e for e in events if isinstance(e, StepResultEvent)]
        assert len(step_results) == 2
        assert step_results[0].result is not None and step_results[0].result.success
        assert step_results[1].result is not None and not step_results[1].result.success


class TestAgentLoopFail:
    @pytest.mark.asyncio
    async def test_reflector_fail_emits_loop_error_event(self) -> None:
        loop, _, _, mock_reflector = _make_loop()
        mock_reflector.reflect = MagicMock(
            return_value=ReflectOutcome(ReflectDecision.FAIL, "hard fail")
        )
        events = await _collect(loop)
        assert any(isinstance(e, LoopErrorEvent) for e in events)
        assert not any(isinstance(e, FinishEvent) for e in events)


# ---------------------------------------------------------------------------
# Integration test: real PlannerAgent + ToolRegistry (mocked execute)
# ---------------------------------------------------------------------------


class TestLoopIntegration:
    @pytest.mark.asyncio
    async def test_real_planner_produces_plan_and_loop_runs(self) -> None:
        """End-to-end: PlannerAgent → ToolRegistry (mocked execute) → finish."""
        goal = "analyze and review the code"

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=_make_result(True))
        mock_registry._safe_type = ToolRegistry._safe_type

        loop = AgentLoop(
            goal=goal,
            registry=mock_registry,
            planner=PlannerAgent(),  # Real planner
            reflector=DefaultReflector(),  # Real reflector
            max_iterations=3,
        )
        events = await _collect(loop)

        # PlannerAgent should have produced at least one step
        plan_events = [e for e in events if isinstance(e, PlanEvent)]
        assert plan_events, "Expected at least one PlanEvent"
        assert len(plan_events[0].plan) >= 1

        # Loop should finish (all steps mocked to succeed)
        assert any(isinstance(e, FinishEvent) for e in events)


# ---------------------------------------------------------------------------
# Router integration & Model overrides tests
# ---------------------------------------------------------------------------


class TestAgentLoopRouterAndOverrides:
    @pytest.mark.asyncio
    async def test_planner_receives_plan_model_override(self) -> None:
        loop, mock_planner, _, _ = _make_loop()
        loop.plan_model = "custom-planner-model"
        await _collect(loop)

        mock_planner.run.assert_awaited_once()
        task = mock_planner.run.await_args[0][0]
        assert task.payload.get("model") == "custom-planner-model"

    @pytest.mark.asyncio
    async def test_planner_receives_router_candidates(self) -> None:
        from agentcli.routing.router import Router, RoutingDecision

        mock_router = MagicMock(spec=Router)
        mock_router.decide.return_value = RoutingDecision(
            primary="model-primary", fallbacks=("model-fb1", "model-fb2")
        )

        loop, mock_planner, _, _ = _make_loop()
        loop.router = mock_router
        await _collect(loop)

        mock_planner.run.assert_awaited_once()
        task = mock_planner.run.await_args[0][0]
        assert task.payload.get("model") == "model-primary"
        assert task.payload.get("models") == ["model-primary", "model-fb1", "model-fb2"]

    @pytest.mark.asyncio
    async def test_execute_step_receives_router_fallback(self) -> None:
        from agentcli.routing.router import Router, RoutingDecision

        mock_router = MagicMock(spec=Router)
        mock_router.decide.return_value = RoutingDecision(
            primary="code-primary", fallbacks=("code-fb",)
        )

        loop, _, mock_registry, _ = _make_loop(
            plan=[{"agent_type": "code_analyzer", "payload": {}}]
        )
        loop.router = mock_router
        await _collect(loop)

        mock_registry.execute.assert_awaited_once()
        agent_type, payload = mock_registry.execute.await_args[0]
        assert agent_type == "code_analyzer"
        assert payload.get("model") == "code-primary"
        assert payload.get("models") == ["code-primary", "code-fb"]


# ---------------------------------------------------------------------------
# is_agentic_task regression tests — simple chat must NOT match
# ---------------------------------------------------------------------------


class TestIsAgenticTask:
    def test_simple_question_returns_false(self) -> None:
        assert is_agentic_task("What is the capital of France?") is False

    def test_hello_returns_false(self) -> None:
        assert is_agentic_task("Hello, how are you?") is False

    def test_write_hello_world_returns_false(self) -> None:
        assert is_agentic_task("write hello world in python") is False

    def test_please_do_simple_request_returns_false(self) -> None:
        assert is_agentic_task("Please do a quick check on this text") is False

    def test_multi_step_with_then_returns_true(self) -> None:
        assert is_agentic_task("Read the file and then summarize it") is True

    def test_multi_step_first_then_returns_true(self) -> None:
        assert is_agentic_task("First, list the files, then analyze each one") is True

    def test_multi_step_without_comma_first_then_returns_true(self) -> None:
        assert is_agentic_task("first check the file then run tests") is True

    def test_execute_keyword_returns_true(self) -> None:
        assert is_agentic_task("execute the test suite and report results") is True

    def test_step_keywords_return_true(self) -> None:
        assert is_agentic_task("step 1: read config, step 2: validate") is True

    def test_numbered_list_returns_true(self) -> None:
        assert is_agentic_task("1. check files 2. run analyzer") is True

    def test_also_read_returns_true(self) -> None:
        assert is_agentic_task("Can you list the files in src? Also read main.py") is True


# ---------------------------------------------------------------------------
# Config [agent_loop] parsing tests
# ---------------------------------------------------------------------------


class TestAgentLoopConfig:
    def test_defaults_when_section_absent(self, tmp_path: Any) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text("[openrouter]\napi_key_env = 'K'\n", encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg.agent_loop.enabled is False
        assert cfg.agent_loop.max_iterations == 5
        assert cfg.agent_loop.reflection_enabled is True

    def test_custom_values_parsed(self, tmp_path: Any) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n"
            "[agent_loop]\nenabled = true\nmax_iterations = 10\nreflection_enabled = false\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.agent_loop.enabled is True
        assert cfg.agent_loop.max_iterations == 10
        assert cfg.agent_loop.reflection_enabled is False

    def test_invalid_max_iterations_raises_config_error(self, tmp_path: Any) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n[agent_loop]\nmax_iterations = 0\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="agent_loop.max_iterations"):
            load_config(cfg_file)

    def test_non_integer_max_iterations_raises_config_error(self, tmp_path: Any) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n[agent_loop]\nmax_iterations = \"bad\"\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_config(cfg_file)

    def test_model_overrides_parsed(self, tmp_path: Any) -> None:
        cfg_file = tmp_path / "agentcli.toml"
        cfg_file.write_text(
            "[openrouter]\napi_key_env = 'K'\n"
            "[agent_loop]\nplan_model_override = 'gpt-4o'\nreflect_model_override = 'claude-3'\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.agent_loop.plan_model_override == "gpt-4o"
        assert cfg.agent_loop.reflect_model_override == "claude-3"


# ---------------------------------------------------------------------------
# Session.should_use_loop gating tests
# ---------------------------------------------------------------------------


class TestSessionShouldUseLoop:
    def _make_session(self, loop_enabled: bool) -> AgentSession:
        import os
        from unittest.mock import patch as _patch

        cfg = Config()
        cfg.agent_loop = AgentLoopConfig(enabled=loop_enabled, max_iterations=5)
        # AgentSession.__init__ creates OpenRouterClient which requires an API key env var.
        # Patch the env var so the client construction succeeds without a real key.
        with _patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test-dummy"}):
            return AgentSession(cfg)

    def test_returns_false_when_loop_disabled(self) -> None:
        session = self._make_session(loop_enabled=False)
        assert session.should_use_loop("First do X, then do Y") is False

    def test_returns_false_for_simple_chat_even_if_enabled(self) -> None:
        session = self._make_session(loop_enabled=True)
        assert session.should_use_loop("Hello there") is False

    def test_returns_true_for_agentic_task_when_enabled(self) -> None:
        session = self._make_session(loop_enabled=True)
        assert session.should_use_loop("First list the files, then analyze each one") is True


class TestToolRegistryPlugins:
    @pytest.mark.asyncio
    async def test_register_callable_sync_and_async(self) -> None:
        registry = ToolRegistry()

        def add_sync(a: int, b: int) -> int:
            return a + b

        async def add_async(a: int, b: int) -> dict[str, int]:
            return {"sum": a + b}

        registry.register_callable("add_sync", add_sync)
        registry.register_callable("add_async", add_async)

        res_sync = await registry.execute("add_sync", {"a": 10, "b": 20})
        assert res_sync.success is True
        assert res_sync.output == {"output": "30"}

        res_async = await registry.execute("add_async", {"a": 15, "b": 25})
        assert res_async.success is True
        assert res_async.output == {"sum": 40}

    @pytest.mark.asyncio
    async def test_load_plugin_file(self, tmp_path: Any) -> None:
        plugin_file = tmp_path / "custom_tool.py"
        plugin_file.write_text(
            """
def multiply(x, y):
    return x * y

def register_tools(registry):
    registry.register_callable("multiplier", multiply)
""",
            encoding="utf-8",
        )

        registry = ToolRegistry()
        registry.load_plugin_file(plugin_file)
        assert "multiplier" in registry.registered_types()

        result = await registry.execute("multiplier", {"x": 5, "y": 6})
        assert result.success is True
        assert result.output == {"output": "30"}


class TestObservabilityAndCancellation:
    @pytest.mark.asyncio
    async def test_run_id_and_duration_observability(self) -> None:
        planner = MagicMock()
        planner.run = AsyncMock(
            return_value=SubAgentResult(
                task_id="p1",
                agent_type=SubAgentType.PLANNER,
                success=True,
                output={"plan": [_plan_step()]},
            )
        )
        registry = MagicMock()
        registry.execute = AsyncMock(return_value=_make_result(True))

        loop = AgentLoop(
            goal="Observability test",
            registry=registry,
            planner=planner,
            run_id="run-custom-123",
        )
        assert loop.run_id == "run-custom-123"

        events = await _collect(loop)
        assert len(events) >= 4

        for ev in events:
            assert getattr(ev, "run_id", None) == "run-custom-123"

        step_res = next(e for e in events if isinstance(e, StepResultEvent))
        assert step_res.duration_seconds >= 0.0

        finish = next(e for e in events if isinstance(e, FinishEvent))
        assert finish.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_loop_cancel_terminates_tasks(self) -> None:
        import asyncio

        loop = AgentLoop(goal="Cancellation test")

        async def dummy_slow_coro() -> None:
            await asyncio.sleep(10.0)

        task = asyncio.create_task(dummy_slow_coro())
        loop._running_tasks.append(task)

        assert not task.done()
        loop.cancel()
        assert task.cancelling() or task.cancelled()

        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.cancelled()


class TestLLMReflector:
    @pytest.mark.asyncio
    async def test_llm_reflector_replan_decision(self) -> None:
        from agentcli.agent.reflector import LLMReflector, ReflectDecision

        class MockClient:
            async def chat_stream(self, messages, model=None):
                yield '{"decision": "REPLAN", "reason": "More file writes needed."}'

        reflector = LLMReflector(client=MockClient())  # type: ignore[arg-type]
        plan = [{"agent_type": "file_ops"}]
        results = [_make_result(True)]

        outcome = await reflector.areflect("Compound goal", plan, results)
        assert outcome.decision == ReflectDecision.REPLAN
        assert "More file writes needed." in outcome.reason

    @pytest.mark.asyncio
    async def test_llm_reflector_finish_decision(self) -> None:
        from agentcli.agent.reflector import LLMReflector, ReflectDecision

        class MockClient:
            async def chat_stream(self, messages, model=None):
                yield '```json\n{"decision": "FINISH", "reason": "All steps verified completely."}\n```'

        reflector = LLMReflector(client=MockClient())  # type: ignore[arg-type]
        plan = [{"agent_type": "file_ops"}]
        results = [_make_result(True)]

        outcome = await reflector.areflect("Single goal", plan, results)
        assert outcome.decision == ReflectDecision.FINISH
        assert "All steps verified completely." in outcome.reason

    @pytest.mark.asyncio
    async def test_llm_reflector_fallback_on_error(self) -> None:
        from agentcli.agent.reflector import LLMReflector, ReflectDecision

        class BrokenClient:
            async def chat_stream(self, messages, model=None):
                raise RuntimeError("API timeout")
                yield ""  # pragma: no cover

        reflector = LLMReflector(client=BrokenClient())  # type: ignore[arg-type]
        plan = [{"agent_type": "file_ops"}]
        results = [_make_result(True)]

        # Should fall back to heuristic (which decides FINISH for 0 failures)
        outcome = await reflector.areflect("Test goal", plan, results)
        assert outcome.decision == ReflectDecision.FINISH

