"""Base classes and protocols for the sub-agent system.

Defines the shared interface that all sub-agents must implement,
ensuring they can be managed uniformly by the spawner and message bus.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bus import MessageBus

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SubAgentType(str, Enum):
    """Enumeration of available sub-agent types."""

    CODE_ANALYZER = "code_analyzer"
    FILE_OPS = "file_ops"
    SHELL_EXECUTION = "shell_execution"
    WEB_SEARCH = "web_search"
    PLANNER = "planner"
    WORKSPACE = "workspace"


class SubAgentStatus(str, Enum):
    """Current status of a sub-agent."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


@dataclass(frozen=True)
class SubAgentTask:
    """A task assigned to a sub-agent.

    Attributes:
        id: Unique identifier for this task.
        agent_type: The type of sub-agent that should execute this task.
        payload: Task-specific data (e.g., file paths, code snippets, commands).
        parent_task_id: Optional ID of the parent task that spawned this one.
        priority: Task priority (higher = more urgent).
        created_at: Timestamp when the task was created.
        metadata: Additional metadata for the task.
        depth: Current delegation recursion depth (0 = top-level).
        max_depth: Maximum allowed delegation depth to prevent infinite loops.
        delegation_path: Ordered tuple of agent types in the delegation chain.
        delegator_id: Unique agent ID of the agent that delegated this task.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_type: SubAgentType = SubAgentType.CODE_ANALYZER
    payload: dict[str, Any] = field(default_factory=dict)
    parent_task_id: str | None = None
    priority: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    max_depth: int = 3
    delegation_path: tuple[str, ...] = field(default_factory=tuple)
    delegator_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.agent_type, str):
            object.__setattr__(self, "agent_type", SubAgentType(self.agent_type))


@dataclass
class SubAgentResult:
    """Result of a sub-agent task execution.

    Attributes:
        task_id: ID of the task that produced this result.
        agent_type: Type of agent that produced this result.
        success: Whether the task completed successfully.
        output: Result data (type varies by agent type).
        error: Error message if the task failed.
        started_at: When the task started.
        completed_at: When the task completed.
        metadata: Additional metadata about the execution.
    """

    task_id: str
    agent_type: SubAgentType
    success: bool
    output: Any = None
    error: str | None = None
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.agent_type, str):
            self.agent_type = SubAgentType(self.agent_type)


class SubAgent(ABC):
    """Abstract base class for all sub-agents.

    All sub-agents must implement the `run` method and can optionally
    override lifecycle hooks. The spawner manages the lifecycle and
    communicates via the message bus.
    """

    def __init__(
        self,
        agent_type: SubAgentType,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        self.agent_type = (
            agent_type if isinstance(agent_type, SubAgentType) else SubAgentType(agent_type)
        )
        self.config: dict[str, Any] = config or {}
        self.message_bus = message_bus
        self.status = SubAgentStatus.IDLE
        self.current_task_id: str | None = None
        self.current_task: SubAgentTask | None = None
        self._task: asyncio.Task[SubAgentResult] | None = None
        self._idle_since: float | None = None
        self._start_time: float | None = None
        self._logger = logging.getLogger(f"{__name__}.{self.agent_type.value}")

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return f"{self.agent_type.value}-{id(self):x}"

    @abstractmethod
    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute the given task and return the result.

        This is the main entry point for task execution. Implementations
        should handle the task payload and return a result.

        Args:
            task: The task to execute.

        Returns:
            SubAgentResult containing the execution result.
        """
        ...

    async def delegate(
        self,
        target_type: SubAgentType | str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        timeout: float = 30.0,
    ) -> SubAgentResult:
        """Delegate a sub-task directly to another peer agent.

        Enforces bounded recursion depth and prevents delegation cycles.

        Args:
            target_type: Target agent type (e.g. SubAgentType.FILE_OPS).
            payload: Payload for the delegated task.
            priority: Priority of the delegated task.
            timeout: Maximum timeout in seconds to wait for peer response.

        Returns:
            SubAgentResult from the peer sub-agent.
        """
        target = target_type if isinstance(target_type, SubAgentType) else SubAgentType(target_type)
        cur_depth = self.current_task.depth if self.current_task else 0
        max_d = self.current_task.max_depth if self.current_task else 3

        if cur_depth >= max_d:
            self._logger.warning(
                "Delegation depth limit reached for agent %s (depth=%d, max=%d)",
                self.agent_id,
                cur_depth,
                max_d,
            )
            return SubAgentResult(
                task_id=uuid.uuid4().hex[:12],
                agent_type=target,
                success=False,
                error=f"Maximum delegation depth ({max_d}) reached",
            )

        cur_path = self.current_task.delegation_path if self.current_task else ()
        new_path = (*cur_path, self.agent_type.value)

        # Detect delegation cycle
        if new_path.count(target.value) >= 2:
            self._logger.warning(
                "Delegation cycle detected for %s -> %s (path=%s)",
                self.agent_id,
                target.value,
                new_path,
            )
            return SubAgentResult(
                task_id=uuid.uuid4().hex[:12],
                agent_type=target,
                success=False,
                error=f"Delegation cycle detected targeting {target.value}",
            )

        delegated_task = SubAgentTask(
            agent_type=target,
            payload=payload,
            parent_task_id=self.current_task_id,
            priority=priority,
            depth=cur_depth + 1,
            max_depth=max_d,
            delegation_path=new_path,
            delegator_id=self.agent_id,
        )

        if not self.message_bus:
            self._logger.error("No message bus available on agent %s to delegate task", self.agent_id)
            return SubAgentResult(
                task_id=delegated_task.id,
                agent_type=target,
                success=False,
                error="Message bus unavailable for peer delegation",
            )

        return await self.message_bus.delegate_task(delegated_task, timeout=timeout)

    async def on_start(self, task: SubAgentTask) -> None:
        """Called when the agent starts executing a task."""
        self.status = SubAgentStatus.RUNNING
        self.current_task_id = task.id
        self.current_task = task
        self._start_time = asyncio.get_running_loop().time()
        self._idle_since = None
        self._logger.debug("Agent %s started task %s (depth=%d)", self.agent_id, task.id, task.depth)

    async def on_complete(self, task: SubAgentTask, result: SubAgentResult) -> None:
        """Called when the agent completes a task successfully."""
        self.status = SubAgentStatus.COMPLETED
        self.current_task = None
        elapsed = asyncio.get_running_loop().time() - (self._start_time or 0)
        self._logger.debug("Agent %s completed task %s in %.2fs", self.agent_id, task.id, elapsed)

    async def on_failure(self, task: SubAgentTask, error: BaseException) -> None:
        """Called when the agent fails to execute a task."""
        self.status = SubAgentStatus.FAILED
        self.current_task = None
        self._logger.error("Agent %s failed task %s: %s", self.agent_id, task.id, error)

    async def on_idle(self) -> None:
        """Called when the agent becomes idle."""
        self.status = SubAgentStatus.IDLE
        self.current_task_id = None
        self.current_task = None
        self._idle_since = asyncio.get_running_loop().time()
        self._logger.debug("Agent %s is now idle", self.agent_id)

    async def kill(self) -> None:
        """Forcefully terminate the agent's current task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = SubAgentStatus.KILLED
        self.current_task_id = None
        self._logger.warning("Agent %s was killed", self.agent_id)

    def is_idle(self) -> bool:
        """Check if the agent is currently idle."""
        return self.status == SubAgentStatus.IDLE

    def idle_duration(self) -> float | None:
        """Return the duration in seconds since the agent became idle."""
        if self._idle_since is not None:
            return asyncio.get_running_loop().time() - self._idle_since
        return None

    def execution_duration(self) -> float | None:
        """Return the duration of the current task execution."""
        if self._start_time is not None and self.status == SubAgentStatus.RUNNING:
            return asyncio.get_running_loop().time() - self._start_time
        return None


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent type.

    Attributes:
        enabled: Whether this sub-agent type is enabled.
        max_concurrent: Maximum number of concurrent instances.
        idle_timeout_seconds: Seconds before idle agent is killed.
        max_concurrent_global: Global limit across all agent types.
        specific_config: Type-specific configuration options.
    """

    enabled: bool = True
    max_concurrent: int = 3
    idle_timeout_seconds: float = 300.0
    max_concurrent_global: int = 10
    specific_config: dict[str, Any] = field(default_factory=dict)
