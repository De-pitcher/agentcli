"""Tests for Phase 36: Agent Self-Healing, Plan Drift Detection & Auto-Rollback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.document import Document

from agentcli.agent.drift_detector import (
    DriftReport,
    DriftSeverity,
    PlanDriftDetector,
)
from agentcli.agent.events import (
    LoopEvent,
)
from agentcli.agent.loop import AgentLoop
from agentcli.agent.protocols import ExecutorProtocol, PlannerProtocol, ReflectorProtocol
from agentcli.agent.reflector import ReflectDecision, ReflectOutcome
from agentcli.agent.rollback import AutoHealingManager
from agentcli.config import Config
from agentcli.session import AgentSession
from agentcli.subagents.base import SubAgentResult, SubAgentTask, SubAgentType
from agentcli.ui.prompt import SlashAndFileCompleter

# ---------------------------------------------------------------------------
# Test PlanDriftDetector
# ---------------------------------------------------------------------------


class TestPlanDriftDetector:
    def test_drift_detector_initial_state(self) -> None:
        detector = PlanDriftDetector()
        report = detector.evaluate_drift()
        assert report.drift_score == 0.0
        assert report.severity == DriftSeverity.LOW
        assert not report.is_loop_detected
        assert report.cycle_signature is None
        assert report.consecutive_failures == 0
        assert report.total_actions_count == 0
        assert len(detector.get_action_history()) == 0

    def test_drift_detector_record_actions_success(self) -> None:
        detector = PlanDriftDetector()
        rec1 = detector.record_action(
            iteration=1,
            step_index=0,
            agent_type="code_analyzer",
            payload={"path": "main.py"},
            success=True,
        )
        assert rec1.success is True
        assert "main.py" in rec1.action_signature
        assert detector.evaluate_drift().severity == DriftSeverity.LOW
        assert detector.evaluate_drift().consecutive_failures == 0

    def test_drift_detector_consecutive_identical_failures_cycle(self) -> None:
        detector = PlanDriftDetector(loop_threshold=2)
        detector.record_action(
            iteration=1,
            step_index=0,
            agent_type="shell_execution",
            payload={"command": "pytest tests/test_core.py"},
            success=False,
            error="Process failed with exit code 1",
        )
        rep1 = detector.evaluate_drift()
        assert rep1.consecutive_failures == 1
        assert rep1.severity in (DriftSeverity.LOW, DriftSeverity.MODERATE)

        # Second failure with same action signature
        detector.record_action(
            iteration=1,
            step_index=1,
            agent_type="shell_execution",
            payload={"command": "pytest tests/test_core.py"},
            success=False,
            error="Process failed with exit code 1",
        )
        rep2 = detector.evaluate_drift()
        assert rep2.is_loop_detected is True
        assert rep2.cycle_signature is not None
        assert rep2.severity == DriftSeverity.CRITICAL
        assert any("pytest tests/test_core.py" in r for r in rep2.reasons)

    def test_drift_detector_ping_pong_oscillation_cycle(self) -> None:
        detector = PlanDriftDetector()
        # Ping-pong sequence: A -> B -> A -> B
        detector.record_action(
            iteration=1, step_index=0, agent_type="file_ops", payload={"path": "a.py"}, success=True
        )
        detector.record_action(
            iteration=1, step_index=1, agent_type="file_ops", payload={"path": "b.py"}, success=True
        )
        detector.record_action(
            iteration=2, step_index=0, agent_type="file_ops", payload={"path": "a.py"}, success=True
        )
        detector.record_action(
            iteration=2, step_index=1, agent_type="file_ops", payload={"path": "b.py"}, success=True
        )

        rep = detector.evaluate_drift()
        assert rep.is_loop_detected is True
        assert rep.cycle_signature is not None
        assert "<->" in rep.cycle_signature
        assert rep.severity == DriftSeverity.CRITICAL

    def test_drift_detector_repeated_tool_actions_cycle(self) -> None:
        detector = PlanDriftDetector()
        for i in range(3):
            detector.record_action(
                iteration=1,
                step_index=i,
                agent_type="workspace",
                payload={"query": "find_symbol"},
                success=True,
            )
        rep = detector.evaluate_drift()
        assert rep.is_loop_detected is True
        assert rep.severity == DriftSeverity.CRITICAL
        assert any("Repeated execution" in r for r in rep.reasons)

    def test_drift_detector_file_mutation_cycle(self) -> None:
        detector = PlanDriftDetector()
        detector.record_action(
            iteration=1, step_index=0, agent_type="file_ops", payload={"path": "src/app.py", "op": "write"}, success=False, error="Syntax error"
        )
        detector.record_action(
            iteration=1, step_index=1, agent_type="code_analyzer", payload={"path": "src/other.py"}, success=True
        )
        detector.record_action(
            iteration=2, step_index=0, agent_type="file_ops", payload={"path": "src/app.py", "op": "edit"}, success=False, error="Type error"
        )
        detector.record_action(
            iteration=2, step_index=1, agent_type="file_ops", payload={"path": "src/app.py", "op": "patch"}, success=False, error="Test failure"
        )
        rep = detector.evaluate_drift()
        assert rep.is_loop_detected is True
        assert "src/app.py" in str(rep.cycle_signature)

    def test_drift_detector_reset(self) -> None:
        detector = PlanDriftDetector()
        detector.record_action(
            iteration=1, step_index=0, agent_type="tool", payload={"cmd": "ls"}, success=False
        )
        assert len(detector.get_action_history()) == 1
        detector.reset()
        assert len(detector.get_action_history()) == 0
        assert detector.evaluate_drift().consecutive_failures == 0


# ---------------------------------------------------------------------------
# Test AutoHealingManager
# ---------------------------------------------------------------------------


class TestAutoHealingManager:
    def test_auto_healing_enable_disable(self) -> None:
        mgr = AutoHealingManager(enabled=True)
        assert mgr.is_enabled is True
        mgr.disable()
        assert mgr.is_enabled is False
        mgr.enable()
        assert mgr.is_enabled is True

    def test_auto_healing_snapshot_and_rollback(self, tmp_path: Path) -> None:
        mgr = AutoHealingManager(root_dir=tmp_path, enabled=True)

        test_file = tmp_path / "hello.txt"
        test_file.write_text("original content", encoding="utf-8")

        new_file = tmp_path / "created.txt"

        # Capture snapshot
        snap_id = mgr.take_snapshot(
            description="Before file edits",
            paths=["hello.txt", "created.txt"],
        )
        assert snap_id != ""

        # Mutate existing file and create new file
        test_file.write_text("corrupted content", encoding="utf-8")
        new_file.write_text("transient file", encoding="utf-8")
        assert test_file.read_text(encoding="utf-8") == "corrupted content"
        assert new_file.exists()

        # Perform rollback
        res = mgr.rollback_to_snapshot(snap_id)
        assert res["success"] is True
        assert "restored hello.txt" in res["reverted_files"]
        assert "deleted created.txt" in res["reverted_files"]

        # Verify restoration
        assert test_file.read_text(encoding="utf-8") == "original content"
        assert not new_file.exists()

    def test_auto_healing_rollback_last(self, tmp_path: Path) -> None:
        mgr = AutoHealingManager(root_dir=tmp_path, enabled=True)
        f = tmp_path / "data.json"
        f.write_text('{"v": 1}', encoding="utf-8")

        mgr.take_snapshot(description="Snap 1", paths=["data.json"])
        f.write_text('{"v": 2}', encoding="utf-8")

        res = mgr.rollback_last()
        assert res["success"] is True
        assert f.read_text(encoding="utf-8") == '{"v": 1}'

    def test_auto_healing_empty_rollback_error(self, tmp_path: Path) -> None:
        mgr = AutoHealingManager(root_dir=tmp_path, enabled=True)
        res = mgr.rollback_last()
        assert res["success"] is False
        assert "No auto-healing snapshots available" in res["error"]

    def test_auto_healing_failure_tracking(self) -> None:
        mgr = AutoHealingManager()
        assert mgr.record_failure("Err 1") == 1
        assert mgr.record_failure("Err 2") == 2
        assert len(mgr.get_recent_errors()) == 2
        mgr.reset_consecutive_failures()
        assert mgr.record_failure("Err 3") == 1

    def test_auto_healing_synthesize_recovery_prompt(self) -> None:
        mgr = AutoHealingManager()
        mgr.record_failure("ImportError: module missing")
        report = DriftReport(
            drift_score=0.85,
            severity=DriftSeverity.CRITICAL,
            is_loop_detected=True,
            cycle_signature="shell_execution:pytest#123",
            reasons=["Repeated execution of failing test suite"],
        )
        prompt = mgr.synthesize_recovery_prompt(drift_report=report, goal="Fix tests")
        assert "SELF-HEALING RECOVERY GUIDANCE" in prompt
        assert "CRITICAL" in prompt
        assert "BREAK CYCLE" in prompt
        assert "ImportError: module missing" in prompt

    def test_auto_healing_list_and_reset(self, tmp_path: Path) -> None:
        mgr = AutoHealingManager(root_dir=tmp_path)
        mgr.take_snapshot(description="Test snap")
        assert len(mgr.list_snapshots()) == 1
        mgr.reset()
        assert len(mgr.list_snapshots()) == 0


# ---------------------------------------------------------------------------
# Test Events and Loop Integration
# ---------------------------------------------------------------------------


class MockFailingExecutor(ExecutorProtocol):
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.call_count = 0

    def list_tools(self) -> list[str]:
        return ["file_ops", "code_analyzer", "shell_execution"]

    async def execute(self, agent_type: str, payload: dict[str, Any]) -> SubAgentResult:
        self.call_count += 1

        # Mutate file on attempt
        target_path = payload.get("path")
        if target_path:
            p = (self.tmp_path / target_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("bad code that fails build", encoding="utf-8")

        return SubAgentResult(
            task_id="step-fail",
            agent_type=SubAgentType.FILE_OPS,
            success=False,
            error="SyntaxError: invalid syntax",
        )


class MockRecoveryPlanner(PlannerProtocol):
    def __init__(self) -> None:
        self.call_count = 0
        self.received_contexts: list[str] = []

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        self.call_count += 1
        ctx = task.payload.get("context", "")
        self.received_contexts.append(ctx)

        if self.call_count == 1:
            plan = [
                {"agent_type": "file_ops", "payload": {"path": "module.py", "op": "write"}},
                {"agent_type": "file_ops", "payload": {"path": "module.py", "op": "write"}},
            ]
        else:
            plan = [
                {"agent_type": "code_analyzer", "payload": {"path": "module.py"}},
            ]

        return SubAgentResult(
            task_id="plan-1",
            agent_type=SubAgentType.PLANNER,
            success=True,
            output={"plan": plan},
        )


class MockSimpleReflector(ReflectorProtocol):
    def __init__(self) -> None:
        self.iteration = 0

    def reflect(
        self,
        goal: str,
        plan: list[dict[str, Any]],
        results: list[SubAgentResult],
    ) -> ReflectOutcome:
        self.iteration += 1
        if self.iteration == 1:
            return ReflectOutcome(decision=ReflectDecision.REPLAN, reason="Steps failed, replan")
        return ReflectOutcome(decision=ReflectDecision.FINISH, reason="All complete")


class TestLoopAutoHealingIntegration:
    @pytest.mark.asyncio
    async def test_loop_auto_healing_triggers_rollback_and_recovery(self, tmp_path: Path) -> None:
        mod_file = tmp_path / "module.py"
        mod_file.write_text("initial clean code", encoding="utf-8")

        executor = MockFailingExecutor(tmp_path)
        planner = MockRecoveryPlanner()
        reflector = MockSimpleReflector()

        drift_detector = PlanDriftDetector(loop_threshold=2)
        auto_healing = AutoHealingManager(root_dir=tmp_path, enabled=True)

        loop = AgentLoop(
            goal="Refactor module without breaking tests",
            registry=executor,
            planner=planner,
            reflector=reflector,
            max_iterations=3,
            drift_detector=drift_detector,
            auto_healing=auto_healing,
        )

        events: list[LoopEvent] = []
        async for ev in loop.run():
            events.append(ev)

        # Check emitted events
        event_types = [type(ev).__name__ for ev in events]
        assert "PlanEvent" in event_types
        assert "StepStartEvent" in event_types
        assert "StepResultEvent" in event_types
        assert "DriftDetectedEvent" in event_types
        assert "AutoHealingRollbackEvent" in event_types
        assert "StrategyRecoveryEvent" in event_types
        assert "FinishEvent" in event_types

        # Verify auto-rollback restored original file
        assert mod_file.read_text(encoding="utf-8") == "initial clean code"

        # Verify recovery guidance was received by planner on re-planning
        assert len(planner.received_contexts) >= 2
        assert "SELF-HEALING RECOVERY GUIDANCE" in planner.received_contexts[1]


# ---------------------------------------------------------------------------
# Test Session and CLI Autocomplete Integration
# ---------------------------------------------------------------------------


class TestSessionAndPromptIntegration:
    def test_session_has_healing_and_drift_detector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key-1234")
        cfg = Config()
        session = AgentSession(cfg)
        try:
            assert isinstance(session.auto_healing, AutoHealingManager)
            assert isinstance(session.drift_detector, PlanDriftDetector)
            assert session.auto_healing.is_enabled is True
        finally:
            session.close()

    def test_slash_completer_healing(self) -> None:
        completer = SlashAndFileCompleter()

        # Check /healing in root slash commands
        doc = Document("/heal")
        completions = list(completer.get_completions(doc, None))  # type: ignore[arg-type]
        matches = [c.text for c in completions]
        assert "/healing" in matches

        # Check /healing subcommands
        doc_sub = Document("/healing ")
        sub_completions = list(completer.get_completions(doc_sub, None))  # type: ignore[arg-type]
        sub_matches = [c.text for c in sub_completions]
        assert "status" in sub_matches
        assert "rollback" in sub_matches
        assert "reset" in sub_matches
        assert "enable" in sub_matches
        assert "disable" in sub_matches

    def test_renderer_renders_healing_events(self) -> None:
        from agentcli.agent.events import (
            AutoHealingRollbackEvent,
            DriftDetectedEvent,
            StrategyRecoveryEvent,
        )
        from agentcli.ui.render import ConsoleRenderer

        ev1 = DriftDetectedEvent(
            iteration=1,
            drift_score=0.85,
            severity="critical",
            is_loop_detected=True,
            reasons=["Loop detected on file edit"],
        )
        ev2 = AutoHealingRollbackEvent(
            iteration=1,
            snapshot_id="snap1",
            trigger="cycle_detected",
            reverted_files=["app.py"],
        )
        ev3 = StrategyRecoveryEvent(
            iteration=1,
            diagnostics="Score: 0.85, Cycle: action#1",
            strategy_prompt="recovery guidance",
        )

        renderer_plain = ConsoleRenderer()
        renderer_plain._rich_available = False
        renderer_plain.render_loop_event(ev1, verbose=True)
        renderer_plain.render_loop_event(ev2, verbose=True)
        renderer_plain.render_loop_event(ev3, verbose=True)

        renderer_rich = ConsoleRenderer()
        renderer_rich._rich_available = True
        renderer_rich.render_loop_event(ev1, verbose=True)
        renderer_rich.render_loop_event(ev2, verbose=True)
        renderer_rich.render_loop_event(ev3, verbose=True)


