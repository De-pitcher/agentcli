"""Scorecard formatters and leaderboard generators for AgentCLI Arena."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentcli.arena.evaluator import TaskResult


@dataclass
class _ModelSummary:
    model: str
    pass_rate: float
    total: int
    passed: int
    avg_latency: float
    total_cost: float
    avg_turns: float


class ScorecardFormatter:
    """Formats benchmark and arena results into ASCII tables, Markdown reports, and JSON."""

    @staticmethod
    def render_table(results: list[TaskResult], title: str = "Benchmark Results") -> str:
        """Render a terminal ASCII table summary of task results."""
        lines = []
        lines.append("=" * 80)
        lines.append(f" {title.upper()}")
        lines.append("=" * 80)
        lines.append(
            f"{'Task ID':<35} {'Status':<8} {'Latency':<9} {'Turns':<6} {'Tools':<6} {'Cost ($)':<9}"
        )
        lines.append("-" * 80)

        total_pass = sum(1 for r in results if r.success)
        total_latency = sum(r.latency_seconds for r in results)
        total_cost = sum(r.cost_usd for r in results)
        total_turns = sum(r.turns_count for r in results)
        total_tools = sum(r.tool_calls_count for r in results)

        for r in results:
            status_str = "PASS" if r.success else "FAIL"
            lines.append(
                f"{r.task_id[:34]:<35} {status_str:<8} {r.latency_seconds:>7.2f}s "
                f"{r.turns_count:>5} {r.tool_calls_count:>5} ${r.cost_usd:>8.4f}"
            )

        lines.append("-" * 80)
        count = len(results) or 1
        pass_rate = (total_pass / count) * 100.0
        avg_latency = total_latency / count
        lines.append(
            f"{'TOTAL / AVERAGE':<35} {f'{pass_rate:.1f}%':<8} {avg_latency:>7.2f}s "
            f"{total_turns:>5} {total_tools:>5} ${total_cost:>8.4f}"
        )
        lines.append("=" * 80)
        return "\n".join(lines)

    @staticmethod
    def render_markdown_report(suite_name: str, results: list[TaskResult]) -> str:
        """Render a full Markdown report suitable for CI artifacts or PR summaries."""
        total_pass = sum(1 for r in results if r.success)
        count = len(results) or 1
        pass_rate = (total_pass / count) * 100.0
        total_latency = sum(r.latency_seconds for r in results)
        total_cost = sum(r.cost_usd for r in results)

        lines = [
            f"# Benchmark Evaluation Report: `{suite_name}`",
            "",
            f"- **Total Tasks**: {len(results)}",
            f"- **Passed Tasks**: {total_pass} ({pass_rate:.1f}%)",
            f"- **Total Latency**: {total_latency:.2f}s (Avg: {total_latency / count:.2f}s/task)",
            f"- **Total Estimated Cost**: ${total_cost:.4f}",
            "",
            "## Task Breakdown",
            "",
            "| Task ID | Title | Status | Latency | Turns | Tools | Cost ($) | Reason |",
            "|---|---|---|---|---|---|---|---|",
        ]

        for r in results:
            status_icon = "✅ PASS" if r.success else "❌ FAIL"
            lines.append(
                f"| `{r.task_id}` | {r.task_title} | {status_icon} | {r.latency_seconds:.2f}s | "
                f"{r.turns_count} | {r.tool_calls_count} | ${r.cost_usd:.4f} | `{r.exit_reason}` |"
            )

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_arena_leaderboard(arena_results: dict[str, list[TaskResult]]) -> str:
        """Render a comparative Markdown leaderboard across multiple models."""
        lines = [
            "# 🏆 AgentCLI Arena Leaderboard",
            "",
            "| Rank | Model | Pass Rate (%) | Total Tasks | Passed | Avg Latency (s) | Total Cost ($) | Avg Turns |",
            "|---|---|---|---|---|---|---|---|",
        ]

        model_stats: list[_ModelSummary] = []
        for model, results in arena_results.items():
            count = len(results) or 1
            passed = sum(1 for r in results if r.success)
            pass_rate = (passed / count) * 100.0
            avg_latency = sum(r.latency_seconds for r in results) / count
            total_cost = sum(r.cost_usd for r in results)
            avg_turns = sum(r.turns_count for r in results) / count
            model_stats.append(
                _ModelSummary(
                    model=model,
                    pass_rate=pass_rate,
                    total=len(results),
                    passed=passed,
                    avg_latency=avg_latency,
                    total_cost=total_cost,
                    avg_turns=avg_turns,
                )
            )

        # Sort by pass rate descending, then latency ascending
        model_stats.sort(key=lambda s: (-s.pass_rate, s.avg_latency, s.total_cost))

        for rank, stat in enumerate(model_stats, start=1):
            lines.append(
                f"| #{rank} | **{stat.model}** | {stat.pass_rate:.1f}% | {stat.total} | "
                f"{stat.passed} | {stat.avg_latency:.2f}s | ${stat.total_cost:.4f} | {stat.avg_turns:.1f} |"
            )

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def calculate_percentiles(values: list[float]) -> tuple[float, float]:
        """Calculate p50 (median) and p95 percentiles for a list of floats."""
        if not values:
            return 0.0, 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        p50_idx = int(n * 0.50)
        p95_idx = min(n - 1, int(n * 0.95))
        return sorted_vals[p50_idx], sorted_vals[p95_idx]

    @staticmethod
    def render_field_trial_summary(results: list[TaskResult]) -> str:
        """Render a comprehensive production field trial scorecard (Phase 30)."""
        count = len(results) or 1
        passed = sum(1 for r in results if r.success)
        pass_rate = (passed / count) * 100.0
        latencies = [r.latency_seconds for r in results]
        p50_lat, p95_lat = ScorecardFormatter.calculate_percentiles(latencies)
        total_cost = sum(r.cost_usd for r in results)
        total_turns = sum(r.turns_count for r in results)
        avg_turns = total_turns / count

        lines = [
            "=" * 80,
            " 🚀 AGENTCLI PRODUCTION FIELD TRIAL SCORECARD",
            "=" * 80,
            f" Pass@1 Success Rate : {pass_rate:.1f}% ({passed}/{len(results)} tasks passed)",
            f" Turn Latency (p50)   : {p50_lat:.2f}s",
            f" Turn Latency (p95)   : {p95_lat:.2f}s",
            f" Turn Efficiency      : {avg_turns:.1f} turns/task",
            f" Cumulative Cost (USD): ${total_cost:.6f}",
            "-" * 80,
            f"{'Task ID':<32} {'Category':<16} {'Status':<8} {'Latency':<9} {'Cost ($)':<9}",
            "-" * 80,
        ]

        for r in results:
            status_str = "PASS" if r.success else "FAIL"
            # Extract category from task_id or default to field
            cat = (
                "bug_fix"
                if "bugfix" in r.task_id
                else (
                    "refactor"
                    if "refactor" in r.task_id
                    else ("mesh" if "mesh" in r.task_id else "tool_use")
                )
            )
            lines.append(
                f"{r.task_id[:31]:<32} {cat:<16} {status_str:<8} {r.latency_seconds:>7.2f}s ${r.cost_usd:>8.4f}"
            )

        lines.append("=" * 80)
        return "\n".join(lines)

    @staticmethod
    def to_json(data: list[TaskResult] | dict[str, list[TaskResult]]) -> str:
        """Export results to formatted JSON."""
        payload: list[dict[str, Any]] | dict[str, list[dict[str, Any]]]
        if isinstance(data, list):
            payload = [r.to_dict() for r in data]
        else:
            payload = {m: [r.to_dict() for r in results] for m, results in data.items()}
        return json.dumps(payload, indent=2)
