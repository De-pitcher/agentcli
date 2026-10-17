"""Exactly-once provider reservations, actual usage, and unresolved billing."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Reservation:
    id: str
    models: tuple[str, ...]
    amount: float
    state: str = "reserved"
    actual: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    served_model: str | None = None


class UsageLedger:
    def __init__(self, ceiling: float | None = None) -> None:
        self.set_ceiling(ceiling)
        self.prices: dict[str, tuple[float, float]] = {}
        self.records: list[Reservation] = []
        self._archived_actual = 0.0
        self._archived_reserved = 0.0
        self._archived_unresolved = 0.0
        self._archived_calls = 0
        self.blocked = False

    def set_ceiling(self, ceiling: float | None) -> None:
        if ceiling is not None and (not math.isfinite(ceiling) or ceiling < 0):
            raise ValueError("Budget must be finite and nonnegative")
        self.ceiling = ceiling

    @property
    def actual(self) -> float:
        return self._archived_actual + sum(r.actual or 0.0 for r in self.records)

    @property
    def reserved(self) -> float:
        return self._archived_reserved + sum(
            r.amount for r in self.records if r.state in {"reserved", "unresolved"}
        )

    @property
    def unresolved(self) -> float:
        return self._archived_unresolved + sum(
            r.amount for r in self.records if r.state == "unresolved"
        )

    def rates(self, model: str) -> tuple[float, float]:
        if model.endswith(":free"):
            return (0.0, 0.0)
        if model not in self.prices:
            if self.ceiling is not None:
                raise BudgetExceeded("Fresh pricing is unavailable for bounded paid inference")
            # Unlimited-budget callers still label uncertain estimates explicitly.
            return (0.00001, 0.00003)
        return self.prices[model]

    def reserve(self, models: list[str], input_bytes: int, output_tokens: int) -> Reservation:
        if self.blocked:
            raise BudgetExceeded(
                "Provider returned an unexpected billing identity; review required"
            )
        if len(self.records) >= 1024:
            old = self.records[0]
            if old.state == "reserved":
                raise BudgetExceeded("Outstanding usage record capacity reached")
            self.records.pop(0)
            self._archived_actual += old.actual or 0.0
            if old.state == "unresolved":
                self._archived_reserved += old.amount
                self._archived_unresolved += old.amount
            self._archived_calls += 1
        amount = max(
            (input_bytes + 256) * self.rates(m)[0] + output_tokens * self.rates(m)[1]
            for m in models
        )
        if self.ceiling is not None and self.actual + self.reserved + amount > self.ceiling + 1e-12:
            raise BudgetExceeded("Remaining provider allowance is insufficient")
        record = Reservation(uuid.uuid4().hex, tuple(models), amount)
        self.records.append(record)
        return record

    def reject(self, record: Reservation) -> None:
        record.state = "rejected"
        record.actual = 0.0

    def settle(self, record: Reservation, served: str | None, usage: dict[str, Any]) -> None:
        if record.state != "reserved":
            return
        if served and served not in record.models:
            self.blocked = True
        if not served or served not in record.models or not isinstance(usage, dict):
            record.state = "unresolved"
            return
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if (
            not isinstance(prompt, int)
            or isinstance(prompt, bool)
            or not isinstance(completion, int)
            or isinstance(completion, bool)
            or min(prompt, completion) < 0
        ):
            record.state = "unresolved"
            return
        record.prompt_tokens, record.completion_tokens = prompt, completion
        record.served_model = served
        actual = usage.get("cost")
        if (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(actual)
            and actual >= 0
        ):
            record.actual = float(actual)
            record.state = "actual"
        elif served.endswith(":free"):
            record.actual = 0.0
            record.state = "actual"
        else:
            # Token-priced estimates do not become actual billing claims.
            record.state = "unresolved"

    def abandon(self, record: Reservation) -> None:
        if record.state == "reserved":
            record.state = "unresolved"

    def summary(self) -> dict[str, Any]:
        return {
            "ceiling_usd": self.ceiling,
            "actual_usd": self.actual,
            "reserved_usd": self.reserved,
            "unresolved_usd": self.unresolved,
            "calls": self._archived_calls + len(self.records),
        }


import contextvars

active_ledger: contextvars.ContextVar[UsageLedger | None] = contextvars.ContextVar(
    "agentcli_usage", default=None
)
