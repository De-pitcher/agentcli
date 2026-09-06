"""Compiler, linter, and test diagnostics extraction and parser.

Extracts structured error spans (file, line, column, rule ID, severity, message)
from compiler, linter, and test outputs (pytest, ruff, mypy, tsc, eslint, cargo, gcc).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..subagents.base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticSpan:
    """Represents a single structured compiler/linter error span."""

    file: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"  # "error" | "warning" | "note"
    code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DiagnosticsParser:
    """Parses raw text outputs from various compilers, linters, and test runners."""

    # Ruff / Flake8: file.py:12:5: E501 line too long
    RUFF_PATTERN = re.compile(
        r"^(?P<file>[^:\n\r]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<code>[A-Z0-9]+)\s*(?P<msg>.+)$",
        re.MULTILINE,
    )

    # Mypy: file.py:42: error: Incompatible types [arg-type]
    MYPY_PATTERN = re.compile(
        r"^(?P<file>[^:\n\r]+):(?P<line>\d+):\s*(?P<severity>error|warning|note):\s*(?P<msg>.+?)(?:\s*\[(?P<code>[^\]]+)\])?$",
        re.MULTILINE,
    )

    # TypeScript (tsc): src/index.ts(15,22): error TS2322: Message OR src/index.ts:15:22 - error TS2322: Message
    TSC_PATTERN = re.compile(
        r"^(?P<file>[^(\n\r:]+)(?:\((?P<line>\d+),(?P<col>\d+)\)|:(?P<line2>\d+):(?P<col2>\d+))\s*[-:]\s*(?P<severity>error|warning)\s+(?P<code>TS\d+):\s*(?P<msg>.+)$",
        re.MULTILINE,
    )

    # ESLint: file.js: line 12, col 5, Error - Message (rule) OR file.js:12:5: error: Message [rule]
    ESLINT_PATTERN = re.compile(
        r"^(?P<file>[^:\n\r]+):\s*(?:line\s*)?(?P<line>\d+)(?::(?P<col>\d+)|\s*,\s*col\s*(?P<col2>\d+))\s*[-:,]\s*(?P<severity>error|warning)\s*[-:]?\s*(?P<msg>.+?)(?:\s*[\(\[](?P<code>[^\)\]]+)[\)\]])?$",
        re.MULTILINE | re.IGNORECASE,
    )

    # Rust (cargo/rustc): error[E0425]: cannot find value --> src/main.rs:12:5
    RUST_SPAN_PATTERN = re.compile(
        r"(?:error|warning)(?:\[(?P<code>[A-Z0-9]+)\])?:\s*(?P<msg>.+?)\n\s*-->\s*(?P<file>[^:\n\r]+):(?P<line>\d+):(?P<col>\d+)",
        re.MULTILINE,
    )

    # Pytest failures: FAILED tests/test_foo.py::test_bar - AssertionError: msg
    PYTEST_SUMMARY_PATTERN = re.compile(
        r"^FAILED\s+(?P<file>[^:\n\r]+)::(?P<test>[^\s-]+)(?:\s*-\s*(?P<msg>.+))?$",
        re.MULTILINE,
    )

    # Pytest traceback line: tests/test_foo.py:42: AssertionError
    PYTEST_LOCATION_PATTERN = re.compile(
        r"^(?P<file>[^:\n\r]+\.py):(?P<line>\d+):\s*(?P<msg>.+)$",
        re.MULTILINE,
    )

    # Generic: file:line:col: severity: msg or file:line: severity: msg
    GENERIC_PATTERN = re.compile(
        r"^(?P<file>[a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:(?P<severity>error|warning|note|fatal)\s*:\s*)?(?P<msg>.+)$",
        re.MULTILINE | re.IGNORECASE,
    )

    @classmethod
    def parse(cls, text: str, framework: str = "auto") -> list[DiagnosticSpan]:
        """Parse raw text output into structured DiagnosticSpan records."""
        if not text:
            return []

        framework = framework.lower()
        spans: list[DiagnosticSpan] = []

        if framework in ("pytest", "auto"):
            pytest_spans = cls._parse_pytest(text)
            if pytest_spans:
                spans.extend(pytest_spans)

        if framework in ("ruff", "flake8", "auto"):
            for m in cls.RUFF_PATTERN.finditer(text):
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(m.group("line")),
                        column=int(m.group("col")),
                        severity="error",
                        code=m.group("code"),
                        message=m.group("msg").strip(),
                    )
                )

        if framework in ("mypy", "pyright", "auto"):
            for m in cls.MYPY_PATTERN.finditer(text):
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(m.group("line")),
                        severity=m.group("severity").lower(),
                        code=m.group("code"),
                        message=m.group("msg").strip(),
                    )
                )

        if framework in ("tsc", "typescript", "auto"):
            for m in cls.TSC_PATTERN.finditer(text):
                line = m.group("line") or m.group("line2")
                col = m.group("col") or m.group("col2")
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(line) if line else None,
                        column=int(col) if col else None,
                        severity=m.group("severity").lower(),
                        code=m.group("code"),
                        message=m.group("msg").strip(),
                    )
                )

        if framework in ("eslint", "auto"):
            for m in cls.ESLINT_PATTERN.finditer(text):
                col = m.group("col") or m.group("col2")
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(m.group("line")),
                        column=int(col) if col else None,
                        severity=m.group("severity").lower(),
                        code=m.group("code"),
                        message=m.group("msg").strip(),
                    )
                )

        if framework in ("rust", "cargo", "rustc", "auto"):
            for m in cls.RUST_SPAN_PATTERN.finditer(text):
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(m.group("line")),
                        column=int(m.group("col")),
                        severity="error",
                        code=m.group("code"),
                        message=m.group("msg").strip(),
                    )
                )

        # If auto and nothing found yet, try generic pattern
        if not spans:
            for m in cls.GENERIC_PATTERN.finditer(text):
                spans.append(
                    DiagnosticSpan(
                        file=m.group("file").strip(),
                        line=int(m.group("line")),
                        column=int(m.group("col")) if m.group("col") else None,
                        severity=(m.group("severity") or "error").lower(),
                        message=m.group("msg").strip(),
                    )
                )

        # Deduplicate spans while preserving ordering
        seen = set()
        deduped: list[DiagnosticSpan] = []
        for s in spans:
            key = (s.file, s.line, s.column, s.code, s.message)
            if key not in seen:
                seen.add(key)
                deduped.append(s)

        return deduped

    @classmethod
    def _parse_pytest(cls, text: str) -> list[DiagnosticSpan]:
        """Extract test failure locations from pytest output."""
        spans: list[DiagnosticSpan] = []

        # Find location mappings: file.py:42: Error
        loc_map: dict[str, tuple[int, str]] = {}
        for m in cls.PYTEST_LOCATION_PATTERN.finditer(text):
            f = m.group("file")
            loc_map[f] = (int(m.group("line")), m.group("msg").strip())

        # Match FAILED lines
        for m in cls.PYTEST_SUMMARY_PATTERN.finditer(text):
            file_path = m.group("file").strip()
            test_name = m.group("test").strip()
            msg = m.group("msg") or f"Test failed: {test_name}"

            line, detail_msg = loc_map.get(file_path, (None, ""))
            full_msg = f"{test_name}: {msg}"
            if detail_msg and detail_msg not in full_msg:
                full_msg += f" ({detail_msg})"

            spans.append(
                DiagnosticSpan(
                    file=file_path,
                    line=line,
                    severity="error",
                    code="pytest_failure",
                    message=full_msg,
                )
            )

        return spans


class DiagnosticsAgent(SubAgent):
    """Sub-agent responsible for running compiler/linter checks and extracting structured diagnostics."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(
            agent_type=SubAgentType.DIAGNOSTICS_CHECK,
            config=config or {},
        )

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute diagnostics check."""
        payload = task.payload
        command = payload.get("command")
        raw_output = payload.get("output", "")
        framework = payload.get("framework", "auto")
        cwd = payload.get("cwd") or payload.get("working_dir") or self.config.get("working_dir")

        exit_code = 0

        # If a command is given, execute it asynchronously
        if command:
            work_dir = Path(cwd).resolve() if cwd else Path.cwd()
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout_bytes, _ = await proc.communicate()
                raw_output = stdout_bytes.decode("utf-8", errors="replace")
                exit_code = proc.returncode or 0
            except Exception as exc:  # noqa: BLE001
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Failed to execute diagnostics command '{command}': {exc}",
                )

        # Parse diagnostics
        spans = DiagnosticsParser.parse(raw_output, framework=framework)

        errors_count = sum(1 for s in spans if s.severity == "error")
        warnings_count = sum(1 for s in spans if s.severity == "warning")

        # Build concise markdown summary
        if not spans:
            if exit_code == 0:
                summary = "✅ All diagnostics passed with 0 errors and 0 warnings."
            else:
                summary = f"Command exited with code {exit_code}, but no standard diagnostic spans were parsed.\nOutput:\n{raw_output[:500]}"
        else:
            summary_lines = [f"Found {errors_count} error(s) and {warnings_count} warning(s):"]
            for s in spans[:20]:
                loc = f"{s.file}"
                if s.line is not None:
                    loc += f":{s.line}"
                    if s.column is not None:
                        loc += f":{s.column}"
                code_str = f" [{s.code}]" if s.code else ""
                summary_lines.append(f"- **{loc}** ({s.severity.upper()}{code_str}): {s.message}")
            if len(spans) > 20:
                summary_lines.append(f"... and {len(spans) - 20} more issues.")
            summary = "\n".join(summary_lines)

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={
                "command": command,
                "exit_code": exit_code,
                "errors_count": errors_count,
                "warnings_count": warnings_count,
                "total_diagnostics": len(spans),
                "diagnostics": [s.to_dict() for s in spans],
                "summary": summary,
            },
        )
