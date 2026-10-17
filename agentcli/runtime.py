"""Single owner for policy, provider usage, tools, and cancellation resources."""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any

from .policy import RunPolicy

active_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentcli_run_owner", default=None
)


class RunContext:
    def __init__(
        self, config: Any = None, policy: RunPolicy | None = None, root: str | Path | None = None
    ) -> None:
        from .agent.checkpoints import CheckpointManager
        from .agent.tasks import TaskManager

        self.policy = policy or RunPolicy.from_config(config, root)
        from .usage import UsageLedger

        self.ledger = UsageLedger(getattr(getattr(config, "routing", None), "max_cost_usd", None))
        self.config = config
        self.journal = CheckpointManager(policy=self.policy)
        self.tasks = TaskManager(policy=self.policy)
        self.tasks.policy = self.policy
        self.client: Any = None
        self.closed = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors = []
        for resource in (self.tasks, self.client):
            if resource is None:
                continue
            try:
                if hasattr(resource, "cleanup_all"):
                    await resource.cleanup_all()
                else:
                    await self.client.aclose()
            except BaseException as exc:  # noqa: BLE001 — finish cleanup before propagating cancellation
                errors.append(exc)
        if errors:
            raise errors[0]
