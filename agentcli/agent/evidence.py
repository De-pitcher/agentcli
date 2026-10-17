"""Deterministic execution facts, separate from successful tool transport."""

from __future__ import annotations

from typing import Any

from ..subagents.base import SubAgentResult

NEGATIVE = {"failed", "killed", "timed_out", "cancelled", "unverified"}


def annotate(
    result: SubAgentResult, name: str, operation: str, payload: dict[str, Any], manager: Any = None
) -> None:
    provenance: dict[str, Any] = {"tool": name, "operation": operation}
    output = result.output if isinstance(result.output, dict) else {}
    if name == "task_manager":
        identity = output.get("id", output.get("task_id", payload.get("task_id")))
        if isinstance(identity, str):
            provenance["owned_task_id"] = identity
            state, code = output.get("status"), output.get("returncode")
            owned = manager.get_task(identity) if manager is not None else None
            if owned is None:
                outcome = "unverified"
            elif output.get("truncated"):
                outcome = "incomplete"
            elif state == "completed" and code == 0 and not isinstance(code, bool):
                outcome = "completed"
            elif state in NEGATIVE:
                outcome = state
            elif operation == "run" and result.success and owned.process is not None:
                outcome = "started"
            elif state == "running":
                outcome = "running"
            else:
                outcome = "incomplete"
            provenance.update(execution_outcome=outcome, returncode=code)
    elif name in {"shell_execution", "diagnostics_check"} and payload.get("command"):
        code = output.get("returncode", output.get("return_code", output.get("exit_code")))
        provenance["execution_outcome"] = (
            "completed"
            if result.success and code == 0 and not output.get("truncated")
            else "failed"
        )
    result.metadata["provenance"] = provenance


def negative(result: SubAgentResult) -> bool:
    return (
        not result.success
        or result.metadata.get("provenance", {}).get("execution_outcome") in NEGATIVE
    )


def completion_error(
    results: list[SubAgentResult], mode: str = "completed", context: Any = None
) -> str | None:
    """Never accept launch/retrieval as terminal zero-exit execution evidence."""
    latest: dict[str, SubAgentResult] = {}
    launched: set[str] = set()
    for result in results:
        provenance = result.metadata.get("provenance", {})
        identity = provenance.get("owned_task_id")
        if identity:
            latest[identity] = result
            if provenance.get("operation") == "run" and provenance.get("execution_outcome") in {
                "started",
                "completed",
            }:
                launched.add(identity)
        if provenance.get("execution_outcome") in NEGATIVE and mode != "observed":
            return "Execution outcome is failed, killed, timed-out or unverified"
    for identity, result in latest.items():
        provenance = result.metadata["provenance"]
        output = result.output if isinstance(result.output, dict) else {}
        if (
            not result.success
            or output.get("truncated")
            or provenance.get("execution_outcome") == "incomplete"
        ):
            return "Task execution evidence is incomplete or truncated"
        if context is None or context.tasks.get_task(identity) is None:
            return "Task execution ownership is unverified"
        status = context.tasks.get_status(identity)
        if mode == "completed" and (
            provenance.get("execution_outcome") != "completed"
            or status.get("status") != "completed"
            or status.get("returncode") != 0
            or status.get("truncated")
        ):
            return "Task completion requires terminal successful execution evidence (completed, returncode=0)"
        if mode == "started" and (
            provenance.get("execution_outcome") not in {"started", "running", "completed"}
            or status.get("status") not in {"running", "completed"}
        ):
            return "Task launch is not successful start-only evidence"
    if mode == "observed" and not latest:
        return "Observation-mode completion requires actual owned task evidence"
    if mode == "started" and (not launched or set(latest) - launched):
        return "Start-only completion requires actual owned task launch evidence"
    return None


def narrow_completion_text(results: list[SubAgentResult], mode: str, context: Any) -> str | None:
    """Render validated narrow outcomes consistently for every loop driver."""
    if mode not in {"started", "observed"}:
        return None
    identities = sorted(
        {
            result.metadata["provenance"]["owned_task_id"]
            for result in results
            if result.metadata.get("provenance", {}).get("owned_task_id")
        }
    )
    if mode == "started":
        return (
            "Started owned task(s): "
            + ", ".join(identities)
            + ". Execution completion has not been verified."
        )
    facts = []
    for identity in identities:
        status = context.tasks.get_status(identity)
        facts.append(
            identity
            + "="
            + str(status.get("status"))
            + " (returncode="
            + str(status.get("returncode"))
            + ")"
        )
    return "Observed owned task(s): " + "; ".join(facts) + ". Execution success is not certified."
