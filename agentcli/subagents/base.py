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
    from ..policy import RunPolicy
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
    WEB_FETCH = "web_fetch"
    GREP_SEARCH = "grep_search"
    PLANNER = "planner"
    WORKSPACE = "workspace"
    CONSENSUS = "consensus"
    TASK_MANAGER = "task_manager"
    ASK_QUESTION = "ask_question"
    DIAGNOSTICS_CHECK = "diagnostics_check"
    SKILL_RUNNER = "skill_runner"
    WORKTREE = "worktree"


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


def intersect_policies(policies: list[RunPolicy]) -> RunPolicy:
    """Compose multiple explicit RunPolicy authorities into a single safe intersection.

    Every explicit policy constraint is preserved:
    - Root: must be identical or one must be within another (narrowing to child root).
      If neither contains the other, PolicyDenied is raised.
    - Readable roots: intersection of all allowed read paths across all policies.
    - Network: all policies must allow network.
    - Allowed hosts: intersection of non-empty allowed_hosts sets if restricted.
    - Limits: min(...) of all positive limits.
    - Booleans: all(...) of write, execution, and discard flags.
    """
    from ..policy import PolicyDenied

    if not policies:
        raise ValueError("Cannot intersect empty policy list")
    if len(policies) == 1:
        return policies[0]

    # 1. Roots intersection:
    # A path is valid under all policies only if it is within every policy's root/readable_roots.
    # For execution and primary root, if roots are not identical, check if one is relative to the other.
    chosen_root = policies[0].root
    for other in policies[1:]:
        if chosen_root == other.root:
            continue
        if chosen_root.is_relative_to(other.root):
            # chosen_root is narrower (child of other.root)
            continue
        elif other.root.is_relative_to(chosen_root):
            # other.root is narrower (child of chosen_root)
            chosen_root = other.root
        else:
            raise PolicyDenied(
                f"Conflicting workspace roots cannot be reconciled: {chosen_root} vs {other.root}"
            )

    # 2. Readable roots intersection
    # For every policy, the set of allowed readable paths includes its root and its readable_roots.
    # An allowed readable root under the intersection must be allowed under all policies.
    # Start with the candidates from all policies (excluding the chosen primary root)
    candidate_readable = set(policies[0].readable_roots)
    for other in policies[1:]:
        other_allowed = (other.root, *other.readable_roots)
        # Keep candidate r if it is within any allowed root of other
        candidate_readable = {
            r
            for r in candidate_readable
            if any(r == o or r.is_relative_to(o) for o in other_allowed)
        }

    # 3. Network and allowed hosts
    merged_allow_network = all(p.allow_network for p in policies)
    merged_allowed_hosts: tuple[str, ...] = ()
    if merged_allow_network:
        # If any policy specifies allowed_hosts, intersect them
        restricted_hosts = [set(p.allowed_hosts) for p in policies if p.allowed_hosts]
        if restricted_hosts:
            intersected = restricted_hosts[0]
            for hset in restricted_hosts[1:]:
                intersected = intersected.intersection(hset)
            if not intersected:
                # Disjoint host restrictions with no members cannot grant unrestricted access;
                # fail closed by denying network access.
                merged_allow_network = False
                merged_allowed_hosts = ()
            else:
                merged_allowed_hosts = tuple(sorted(intersected))
    else:
        merged_allowed_hosts = ()

    # 4. Booleans
    merged_allow_write = all(p.allow_write for p in policies)
    merged_allow_exec = all(p.allow_execution for p in policies)
    merged_allow_disc = all(p.allow_discard for p in policies)

    # 5. Limits
    merged_timeout = min(p.timeout_seconds for p in policies)
    merged_max_output = min(p.max_output_bytes for p in policies)
    merged_max_file_bytes = min(p.max_file_bytes for p in policies)
    merged_max_tasks = min(p.max_tasks for p in policies)
    merged_max_retained_tasks = min(p.max_retained_tasks for p in policies)

    from ..policy import RunPolicy

    return RunPolicy(
        root=chosen_root,
        allow_write=merged_allow_write,
        allow_execution=merged_allow_exec,
        allow_discard=merged_allow_disc,
        allow_network=merged_allow_network,
        readable_roots=tuple(sorted(candidate_readable)),
        allowed_hosts=merged_allowed_hosts,
        max_output_bytes=merged_max_output,
        timeout_seconds=merged_timeout,
        max_tasks=merged_max_tasks,
        max_retained_tasks=merged_max_retained_tasks,
        max_file_bytes=merged_max_file_bytes,
    )


def _parse_helper_result(h_res: Any, task_id: str, agent_type: SubAgentType) -> SubAgentResult:
    """Strictly validate and parse raw I/O helper response dictionary.

    Rejects:
    - Non-dict response structures.
    - Non-boolean 'success' types (e.g. string 'false' or numeric 0/1).
    - Success responses with nonzero return codes or conflicting error messages.
    - Malformed output structures (missing required typed fields).
    """
    if not isinstance(h_res, dict):
        raise TypeError("Invalid helper response format: expected dict")

    success = h_res.get("success")
    if type(success) is not bool:
        raise ValueError(
            f"Invalid helper response: 'success' must be strict bool, got {type(success).__name__}"
        )

    output = h_res.get("output")
    error = h_res.get("error")

    if success:
        if error is not None:
            raise ValueError(
                "Coherence violation in helper response: 'success' is True but 'error' is present"
            )
        if not isinstance(output, dict):
            raise ValueError(
                f"Invalid helper response: output must be dict on success, got {type(output).__name__}"
            )
        stdout = output.get("stdout")
        stderr = output.get("stderr")
        returncode = output.get("returncode")
        truncated = output.get("truncated")

        if not isinstance(stdout, str):
            raise ValueError("Invalid helper output: 'stdout' must be str")
        if not isinstance(stderr, str):
            raise ValueError("Invalid helper output: 'stderr' must be str")
        if type(returncode) is not int or returncode != 0:
            raise ValueError(
                f"Coherence violation in helper response: 'success' is True but returncode is {returncode!r}"
            )
        if type(truncated) is not bool:
            raise ValueError("Invalid helper output: 'truncated' must be strict bool")

        return SubAgentResult(
            task_id=task_id,
            agent_type=agent_type,
            success=True,
            output=output,
            error=None,
        )
    else:
        if error is not None and not isinstance(error, str):
            raise ValueError(
                f"Invalid helper response: 'error' must be str or None, got {type(error).__name__}"
            )
        if output is not None:
            if not isinstance(output, dict):
                raise ValueError("Invalid helper response: 'output' must be dict or None")
            stdout = output.get("stdout")
            stderr = output.get("stderr")
            rc = output.get("returncode")
            truncated = output.get("truncated")

            if not isinstance(stdout, str):
                raise ValueError("Invalid helper failure output: 'stdout' must be str")
            if not isinstance(stderr, str):
                raise ValueError("Invalid helper failure output: 'stderr' must be str")
            if type(rc) is not int:
                raise ValueError("Invalid helper failure output: 'returncode' must be strict int")
            if type(truncated) is not bool:
                raise ValueError("Invalid helper failure output: 'truncated' must be strict bool")
            if rc == 0 and not error:
                raise ValueError(
                    "Coherence violation in helper response: 'success' is False but returncode is 0 without error"
                )
        return SubAgentResult(
            task_id=task_id,
            agent_type=agent_type,
            success=False,
            output=output,
            error=error or "Helper command execution failed",
        )


def _parse_workspace_list_tree_result(
    h_res: Any,
    task_id: str,
    agent_type: SubAgentType,
) -> SubAgentResult:
    """Validate and parse a workspace_list_tree helper response."""
    if not isinstance(h_res, dict):
        raise TypeError(f"Invalid helper response: expected dict, got {type(h_res).__name__}")
    root = h_res.get("root")
    tree = h_res.get("tree")
    if not isinstance(root, str):
        raise TypeError("Invalid helper response: 'root' must be str")
    if not isinstance(tree, list):
        raise TypeError("Invalid helper response: 'tree' must be list")
    for item in tree:
        if not isinstance(item, str):
            raise TypeError("Invalid helper response: 'tree' items must be str")
    return SubAgentResult(
        task_id=task_id,
        agent_type=agent_type,
        success=True,
        output={"root": root, "tree": tree},
        error=None,
    )


class SubAgent(ABC):
    """Abstract base class for all sub-agents.

    All sub-agents must implement the `run` method and can optionally
    override lifecycle hooks. The spawner manages the lifecycle and
    communicates via the message bus.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        handler = cls.__dict__.get("run")
        if handler is None:
            return

        async def guarded(self: Any, task: SubAgentTask) -> SubAgentResult:
            from ..policy import PolicyDenied, active_policy

            # Resolve effective policy authorities:
            # 1. active_policy.get() (current dynamic context)
            # 2. explicit manager policy
            # 3. explicit task_manager policy
            # 4. explicit agent policy (configured, captured at construction, or assigned after construction)
            active = active_policy.get()
            manager_pol = getattr(getattr(self, "manager", None), "policy", None)
            task_pol = getattr(getattr(self, "task_manager", None), "policy", None)

            # Check if self.policy was explicitly assigned after construction or configured
            agent_explicit = getattr(self, "_explicit_policy", False) or getattr(
                self, "policy", None
            ) is not getattr(self, "_default_policy", None)
            agent_pol = getattr(self, "policy", None) if agent_explicit else None
            captured_pol = getattr(self, "_captured_policy", None)

            # Collect all applicable explicit policies
            explicit_policies = [
                p for p in (active, manager_pol, task_pol, agent_pol, captured_pol) if p is not None
            ]

            if explicit_policies:
                # Governed invocation: compose every explicit policy constraint safely.
                # If conflicting scopes cannot be reconciled, intersect_policies raises PolicyDenied.
                try:
                    policy = intersect_policies(explicit_policies)
                except PolicyDenied as exc:
                    return SubAgentResult(
                        task.id,
                        task.agent_type,
                        False,
                        error=str(exc),
                    )

                token = active_policy.set(policy)
                try:
                    async with asyncio.timeout(policy.timeout_seconds):
                        name = task.agent_type.value
                        await policy.aauthorize(name, task.payload)
                        from ..bounds import bound_result
                        from ..io_boundary import filesystem_request, in_worker, query

                        if not in_worker and name == "shell_execution":
                            # Dispatch narrow governed shell to helper with preserved caller configuration
                            caller_config: dict[str, Any] = {
                                "security_mode": getattr(self, "security_mode", "denylist"),
                                "allowlist": list(getattr(self, "allowlist", [])),
                                "denylist": list(getattr(self, "denylist", [])),
                                "working_dir": getattr(self, "working_dir", None),
                                "command_timeout": getattr(self, "command_timeout", None),
                                "max_output_bytes": getattr(self, "max_output_bytes", None),
                            }
                            req = filesystem_request(
                                "shell_execution",
                                policy,
                                task_id=task.id,
                                payload=task.payload,
                                config=caller_config,
                            )
                            h_res = await query(req, policy)
                            res_obj = _parse_helper_result(h_res, task.id, task.agent_type)
                            return bound_result(res_obj, policy.max_output_bytes)

                        if not in_worker and name == "workspace":
                            operation = task.payload.get("operation")
                            if operation in ("list_tree", "workspace_list_tree"):
                                raw_path = task.payload.get("path")
                                target_path = policy.path(raw_path or policy.root, write=False)
                                raw_depth = task.payload.get("max_depth", 2)
                                if type(raw_depth) is not int:
                                    raise TypeError("max_depth must be an integer")
                                if not 0 <= raw_depth <= 20:
                                    raise ValueError("max_depth must be between 0 and 20")

                                raw_entries = task.payload.get("max_entries", 4096)
                                if type(raw_entries) is not int:
                                    raise TypeError("max_entries must be an integer")
                                if not 1 <= raw_entries <= 4096:
                                    raise ValueError("max_entries must be between 1 and 4096")

                                req = filesystem_request(
                                    "workspace_list_tree",
                                    policy,
                                    task_id=task.id,
                                    path=str(target_path),
                                    max_depth=raw_depth,
                                    max_entries=raw_entries,
                                    payload=task.payload,
                                )
                                h_res = await query(req, policy)
                                res_obj = _parse_workspace_list_tree_result(
                                    h_res, task.id, task.agent_type
                                )
                                return bound_result(res_obj, policy.max_output_bytes)

                        # If this agent requires filesystem_tool, in Batch 1 it is not yet available in _io_worker.
                        # Governed callers must fail closed rather than falling back to unmanaged blocking handlers.
                        if (
                            not in_worker
                            and not (
                                name == "workspace"
                                and task.payload.get("operation") == "semantic_search"
                            )
                            and type(self).__module__
                            in {
                                "agentcli.subagents.grep_search",
                                "agentcli.subagents.workspace",
                                "agentcli.subagents.shell",
                            }
                        ):
                            raise NotImplementedError(
                                "Helper operation 'filesystem_tool' is unsupported on this foundation runtime"
                            )

                        result = await asyncio.wait_for(handler(self, task), policy.timeout_seconds)
                        return bound_result(result, policy.max_output_bytes)
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    TimeoutError,
                    NotImplementedError,
                    PolicyDenied,
                ) as exc:
                    return SubAgentResult(
                        task.id,
                        task.agent_type,
                        False,
                        error=str(exc) or "Tool timed out at its deadline",
                    )
                finally:
                    active_policy.reset(token)
                    if getattr(self, "_owns_client", False):
                        await self._client.aclose()
                        self._client = None
                        self._owns_client = False
            else:
                # Demonstrably unmanaged standalone invocation (Phase 3 baseline behavior)
                try:
                    res: SubAgentResult = await handler(self, task)
                    return res
                finally:
                    if getattr(self, "_owns_client", False):
                        await self._client.aclose()
                        self._client = None
                        self._owns_client = False

        # Wrap the subclass execution boundary.
        cls.run = guarded  # type: ignore[method-assign]

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
        from ..policy import RunPolicy, active_policy, tool_policy

        # Capture active policy context at construction time
        self._captured_policy: RunPolicy | None = active_policy.get()

        # Check for explicitly configured policy object
        cfg_policy = self.config.get("policy", self.config.get("run_policy"))
        if cfg_policy is not None:
            if not isinstance(cfg_policy, RunPolicy):
                raise ValueError("Configured policy must be a RunPolicy instance")
            self._explicit_policy = True
            self._default_policy = cfg_policy
            self.policy = cfg_policy
        else:
            boundary_keys = (
                "allow_write",
                "read_only",
                "allow_execution",
                "allow_discard",
                "allow_network",
                "readable_roots",
                "allowed_hosts",
                "max_file_bytes",
                "max_output_bytes",
                "command_timeout",
                "timeout_seconds",
                "max_tasks",
                "max_retained_tasks",
            )
            has_explicit_cfg = any(k in self.config for k in boundary_keys)
            self._explicit_policy = has_explicit_cfg
            if has_explicit_cfg:
                # Build the configured authority independently of any ambient active context
                configured_authority = RunPolicy.from_config(self.config)
                self._default_policy = configured_authority
                self.policy = configured_authority
            else:
                self._default_policy = tool_policy(self.config)
                self.policy = self._default_policy
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
            self._logger.error(
                "No message bus available on agent %s to delegate task", self.agent_id
            )
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
        self._logger.debug(
            "Agent %s started task %s (depth=%d)", self.agent_id, task.id, task.depth
        )

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
