"""Diagnostics subagent module re-exporting DiagnosticsAgent."""

from ..tools.diagnostics import DiagnosticsAgent, DiagnosticSpan, DiagnosticsParser

__all__ = ["DiagnosticSpan", "DiagnosticsAgent", "DiagnosticsParser"]
