"""Token Budget Governor and Real-Time Accounting (Phase 35).

Provides dynamic token and cost tracking, soft warning thresholds, hard ceilings,
spend velocity calculations ($/hr, tokens/min), and per-agent usage breakdowns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .budget import calculate_cost, estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    """Record of a single model invocation token usage and cost."""

    timestamp: float
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    cost_usd: float = 0.0
    agent_type: str = "main"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class BudgetHealth:
    """Snapshot of budget health status and velocity metrics."""

    status: str  # "ok", "warning", "exceeded"
    used_cost_usd: float
    max_cost_usd: float | None
    used_tokens: int
    max_tokens: int | None
    utilization_pct: float
    cost_per_hour: float
    tokens_per_minute: float
    is_warning: bool
    is_exceeded: bool
    warning_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "used_cost_usd": round(self.used_cost_usd, 6),
            "max_cost_usd": self.max_cost_usd,
            "used_tokens": self.used_tokens,
            "max_tokens": self.max_tokens,
            "utilization_pct": round(self.utilization_pct, 2),
            "cost_per_hour": round(self.cost_per_hour, 6),
            "tokens_per_minute": round(self.tokens_per_minute, 2),
            "is_warning": self.is_warning,
            "is_exceeded": self.is_exceeded,
            "warning_ratio": self.warning_ratio,
        }


class TokenBudgetGovernor:
    """Real-time token usage governor with budget enforcement and velocity metrics."""

    def __init__(
        self,
        max_cost_usd: float | None = None,
        max_tokens: int | None = None,
        warning_ratio: float = 0.80,
    ) -> None:
        self.max_cost_usd = max_cost_usd
        self.max_tokens = max_tokens
        self.warning_ratio = max(0.1, min(0.99, warning_ratio))
        self._records: list[UsageRecord] = []
        self._start_time = time.time()

    @property
    def total_cost_usd(self) -> float:
        """Total cumulative spend across all recorded invocations in USD."""
        return sum(r.cost_usd for r in self._records)

    @property
    def total_tokens(self) -> int:
        """Total token count (prompt + completion) across all invocations."""
        return sum(r.total_tokens for r in self._records)

    @property
    def prompt_tokens(self) -> int:
        """Total prompt/input tokens consumed."""
        return sum(r.prompt_tokens for r in self._records)

    @property
    def completion_tokens(self) -> int:
        """Total completion/generated tokens consumed."""
        return sum(r.completion_tokens for r in self._records)

    @property
    def cached_tokens(self) -> int:
        """Total cached or deduplicated tokens saved."""
        return sum(r.cached_tokens for r in self._records)

    @property
    def records(self) -> list[UsageRecord]:
        return list(self._records)

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        agent_type: str = "main",
    ) -> float:
        """Record model invocation token usage and calculate USD cost.

        Args:
            model: Model identifier string.
            prompt_tokens: Number of prompt/input tokens.
            completion_tokens: Number of output/completion tokens.
            cached_tokens: Number of prompt tokens read from cache.
            agent_type: Subagent type or 'main'.

        Returns:
            Calculated cost in USD for this invocation.
        """
        cost = calculate_cost(model, prompt_tokens, completion_tokens)
        record = UsageRecord(
            timestamp=time.time(),
            model=model,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            cached_tokens=max(0, cached_tokens),
            cost_usd=cost,
            agent_type=agent_type or "main",
        )
        self._records.append(record)

        if self.is_exceeded():
            logger.warning(
                "Budget ceiling exceeded! Total: $%.4f (Limit: $%.4f)",
                self.total_cost_usd,
                self.max_cost_usd or 0.0,
            )
        elif self.is_warning():
            logger.info(
                "Approaching budget ceiling (%.1f%% utilized). Total: $%.4f",
                self.get_utilization_pct(),
                self.total_cost_usd,
            )

        return cost

    def estimate_and_record_text(
        self,
        model: str,
        prompt_text: str,
        completion_text: str,
        cached_tokens: int = 0,
        agent_type: str = "main",
    ) -> float:
        """Estimate token counts from raw strings using character heuristics and record usage."""
        p_tok = estimate_tokens(prompt_text)
        c_tok = estimate_tokens(completion_text)
        return self.record_usage(
            model=model,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            cached_tokens=cached_tokens,
            agent_type=agent_type,
        )

    def is_exceeded(self) -> bool:
        """Check if cumulative cost or tokens have reached or exceeded hard ceilings."""
        if self.max_cost_usd is not None and self.total_cost_usd >= self.max_cost_usd:
            return True
        return bool(self.max_tokens is not None and self.total_tokens >= self.max_tokens)

    def is_warning(self) -> bool:
        """Check if cumulative cost or tokens have reached soft warning threshold."""
        if self.is_exceeded():
            return True
        if (
            self.max_cost_usd is not None
            and self.total_cost_usd >= self.max_cost_usd * self.warning_ratio
        ):
            return True
        return bool(
            self.max_tokens is not None
            and self.total_tokens >= self.max_tokens * self.warning_ratio
        )


    def get_utilization_pct(self) -> float:
        """Get percentage utilization of the most constrained budget ceiling."""
        percentages: list[float] = []
        if self.max_cost_usd is not None and self.max_cost_usd > 0:
            percentages.append((self.total_cost_usd / self.max_cost_usd) * 100.0)
        if self.max_tokens is not None and self.max_tokens > 0:
            percentages.append((self.total_tokens / self.max_tokens) * 100.0)
        return max(percentages) if percentages else 0.0

    def cost_per_hour(self) -> float:
        """Calculate spend velocity in USD per hour."""
        if not self._records:
            return 0.0
        elapsed_seconds = max(1.0, time.time() - self._start_time)
        return (self.total_cost_usd / elapsed_seconds) * 3600.0

    def tokens_per_minute(self) -> float:
        """Calculate token throughput in tokens per minute."""
        if not self._records:
            return 0.0
        elapsed_seconds = max(1.0, time.time() - self._start_time)
        return (self.total_tokens / elapsed_seconds) * 60.0

    def get_agent_breakdown(self) -> dict[str, dict[str, Any]]:
        """Return token count, cost, and invocation count grouped by agent type."""
        breakdown: dict[str, dict[str, Any]] = {}
        for r in self._records:
            agent = r.agent_type or "main"
            if agent not in breakdown:
                breakdown[agent] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                }
            breakdown[agent]["calls"] += 1
            breakdown[agent]["prompt_tokens"] += r.prompt_tokens
            breakdown[agent]["completion_tokens"] += r.completion_tokens
            breakdown[agent]["total_tokens"] += r.total_tokens
            breakdown[agent]["cached_tokens"] += r.cached_tokens
            breakdown[agent]["cost_usd"] += r.cost_usd

        # Add percentage shares
        total_cost = self.total_cost_usd
        for data in breakdown.values():
            data["cost_usd"] = round(data["cost_usd"], 6)
            data["cost_share_pct"] = (
                round((data["cost_usd"] / total_cost) * 100.0, 1) if total_cost > 0 else 0.0
            )

        return breakdown

    def get_model_breakdown(self) -> dict[str, dict[str, Any]]:
        """Return token count, cost, and invocation count grouped by model."""
        breakdown: dict[str, dict[str, Any]] = {}
        for r in self._records:
            m = r.model or "unknown"
            if m not in breakdown:
                breakdown[m] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
            breakdown[m]["calls"] += 1
            breakdown[m]["prompt_tokens"] += r.prompt_tokens
            breakdown[m]["completion_tokens"] += r.completion_tokens
            breakdown[m]["total_tokens"] += r.total_tokens
            breakdown[m]["cost_usd"] += r.cost_usd

        for data in breakdown.values():
            data["cost_usd"] = round(data["cost_usd"], 6)

        return breakdown

    def check_health(self) -> BudgetHealth:
        """Generate a complete budget health snapshot."""
        if self.is_exceeded():
            status = "exceeded"
        elif self.is_warning():
            status = "warning"
        else:
            status = "ok"

        return BudgetHealth(
            status=status,
            used_cost_usd=self.total_cost_usd,
            max_cost_usd=self.max_cost_usd,
            used_tokens=self.total_tokens,
            max_tokens=self.max_tokens,
            utilization_pct=self.get_utilization_pct(),
            cost_per_hour=self.cost_per_hour(),
            tokens_per_minute=self.tokens_per_minute(),
            is_warning=self.is_warning(),
            is_exceeded=self.is_exceeded(),
            warning_ratio=self.warning_ratio,
        )

    def set_budget(
        self,
        max_cost_usd: float | None = None,
        max_tokens: int | None = None,
        warning_ratio: float | None = None,
    ) -> None:
        """Dynamically update budget limits and warning ratios."""
        if max_cost_usd is not None:
            self.max_cost_usd = max(0.0, float(max_cost_usd)) if max_cost_usd > 0 else None
        if max_tokens is not None:
            self.max_tokens = max(0, int(max_tokens)) if max_tokens > 0 else None
        if warning_ratio is not None:
            self.warning_ratio = max(0.1, min(0.99, float(warning_ratio)))

    def reset(self) -> None:
        """Reset all usage records and reset session timer."""
        self._records.clear()
        self._start_time = time.time()

    def format_summary(self) -> str:
        """Generate a human-readable CLI summary table of cost, tokens, and velocity."""
        health = self.check_health()
        lines: list[str] = [
            "================== Token & Cost Budget ==================",
            f" Cumulative Cost : ${health.used_cost_usd:.4f}"
            + (f" / ${health.max_cost_usd:.4f}" if health.max_cost_usd else " (No limit)"),
            f" Total Tokens    : {health.used_tokens:,}"
            + (f" / {health.max_tokens:,}" if health.max_tokens else ""),
            f"   - Prompt      : {self.prompt_tokens:,}",
            f"   - Completion  : {self.completion_tokens:,}",
            f"   - Cached/Saved: {self.cached_tokens:,}",
            f" Utilization     : {health.utilization_pct:.1f}%",
            f" Spend Velocity  : ${health.cost_per_hour:.4f}/hr  ({health.tokens_per_minute:.0f} tok/min)",
            f" Status          : {health.status.upper()}",
            "---------------------------------------------------------",
        ]

        # Breakdown by Agent
        agent_bd = self.get_agent_breakdown()
        if agent_bd:
            lines.append("By SubAgent:")
            for agent, stats in agent_bd.items():
                lines.append(
                    f"  * {agent:<14} : {stats['calls']:>2} calls | {stats['total_tokens']:>7,} tok | ${stats['cost_usd']:.4f} ({stats['cost_share_pct']}%)"
                )
            lines.append("---------------------------------------------------------")

        # Breakdown by Model
        model_bd = self.get_model_breakdown()
        if model_bd:
            lines.append("By Model:")
            for model, stats in model_bd.items():
                short_m = model.split("/")[-1] if "/" in model else model
                lines.append(
                    f"  * {short_m:<20} : {stats['calls']:>2} calls | {stats['total_tokens']:>7,} tok | ${stats['cost_usd']:.4f}"
                )
            lines.append("=========================================================")

        return "\n".join(lines)


__all__ = [
    "BudgetHealth",
    "TokenBudgetGovernor",
    "UsageRecord",
]
