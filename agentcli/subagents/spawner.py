"""Sub-agent spawner and pool management.

Manages the lifecycle of sub-agent instances, including spawning,
idle timeout enforcement, resource monitoring, and priority queuing.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from .base import (
    SubAgent,
    SubAgentConfig,
    SubAgentResult,
    SubAgentStatus,
    SubAgentTask,
    SubAgentType,
)
from .bus import MessageBus

logger = logging.getLogger(__name__)


@dataclass
class ResourceUsage:
    """Current resource usage snapshot."""

    active_agents: int = 0
    queued_tasks: int = 0
    max_concurrent_global: int = 10
    cpu_percent: float = 0.0
    memory_mb: float = 0.0


class SubAgentPool:
    """Manages a pool of sub-agents of a specific type.

    Handles spawning, reuse, idle timeout, and resource limits for
    a single sub-agent type.
    """

    _registry: ClassVar[set[SubAgentPool]] = set()

    def __init__(
        self,
        agent_type: SubAgentType,
        config: SubAgentConfig,
        agent_factory: Callable[[], SubAgent],
        message_bus: MessageBus,
    ) -> None:
        self.agent_type = agent_type
        self.config = config
        self._factory = agent_factory
        self._bus = message_bus
        self._idle_agents: list[SubAgent] = []
        self._active_agents: dict[str, SubAgent] = {}
        self._task_queue: deque[tuple[SubAgentTask, asyncio.Future[SubAgentResult]]] = deque()
        self._lock = asyncio.Lock()
        self._shutdown = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(f"{__name__}.{agent_type.value}")
        SubAgentPool._registry.add(self)

    @classmethod
    def _all_pools(cls) -> list[SubAgentPool]:
        """Get all pool instances (for global limit checking)."""
        return list(cls._registry)

    async def start(self) -> None:
        """Start the pool's background monitoring."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def shutdown(self) -> None:
        """Shutdown the pool and clean up all agents."""
        self._shutdown = True
        SubAgentPool._registry.discard(self)
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Kill all active agents
        async with self._lock:
            for agent in list(self._active_agents.values()):
                await agent.kill()
            for agent in list(self._idle_agents):
                await agent.kill()
            self._active_agents.clear()
            self._idle_agents.clear()

    async def submit_task(self, task: SubAgentTask) -> SubAgentResult:
        """Submit a task to the pool for execution.

        Returns a future that will be resolved with the result.
        """
        future: asyncio.Future[SubAgentResult] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._task_queue.append((task, future))
            await self._maybe_spawn_agent()

        return await future

    async def _maybe_spawn_agent(self) -> bool:
        """Try to spawn an agent if there are queued tasks and capacity."""
        if self._shutdown or not self.config.enabled:
            return False

        if not self._task_queue:
            return False

        # Check global limit
        total_active = sum(len(p._active_agents) for p in SubAgentPool._all_pools())
        if total_active >= self.config.max_concurrent_global:
            return False

        # Check type-specific limit
        if len(self._active_agents) >= self.config.max_concurrent:
            return False

        # Try to reuse an idle agent first
        if self._idle_agents:
            agent = self._idle_agents.pop()
        else:
            # Spawn new agent
            agent = self._factory()
            agent.message_bus = self._bus

        # Assign task to agent
        task, future = self._task_queue.popleft()
        self._active_agents[agent.agent_id] = agent

        # Execute task
        asyncio.create_task(self._execute_task(agent, task, future))
        return True

    async def _execute_task(
        self,
        agent: SubAgent,
        task: SubAgentTask,
        future: asyncio.Future[SubAgentResult],
    ) -> None:
        """Execute a task on an agent and handle completion."""
        try:
            await agent.on_start(task)
            result = await agent.run(task)
            await agent.on_complete(task, result)
            if not future.done():
                future.set_result(result)
        except asyncio.CancelledError:
            agent.status = SubAgentStatus.KILLED
            if not future.done():
                future.cancel()
        except BaseException as e:  # noqa: BLE001
            await agent.on_failure(task, e)
            if not future.done():
                future.set_exception(e)
        finally:
            async with self._lock:
                self._active_agents.pop(agent.agent_id, None)
                if not self._shutdown:
                    agent.status = SubAgentStatus.IDLE
                    agent._idle_since = asyncio.get_running_loop().time()
                    self._idle_agents.append(agent)
                # Try to process next task
                await self._maybe_spawn_agent()

    async def _monitor_loop(self) -> None:
        """Background task to monitor idle agents and enforce timeouts."""
        while not self._shutdown:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                await self._check_idle_agents()
                await self._enforce_resource_limits()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in pool monitor")

    async def _check_idle_agents(self) -> None:
        """Kill agents that have been idle too long."""
        async with self._lock:
            to_kill: list[SubAgent] = []
            for agent in self._idle_agents:
                idle_time = agent.idle_duration()
                if idle_time is not None and idle_time > self.config.idle_timeout_seconds:
                    to_kill.append(agent)

            for agent in to_kill:
                self._idle_agents.remove(agent)
                await agent.kill()
                self._logger.info(
                    "Killed idle agent %s after %.0fs",
                    agent.agent_id,
                    self.config.idle_timeout_seconds,
                )

    async def _enforce_resource_limits(self) -> None:
        """Check and enforce global resource limits."""
        # Optional CPU/memory monitoring hooks


class SubAgentSpawner:
    """High-level spawner that manages all sub-agent pools.

    Coordinates multiple agent pools, handles task routing, and
    provides a unified interface for the main session to spawn sub-agents.
    """

    def __init__(
        self,
        config: dict[str, SubAgentConfig],
        agent_factories: dict[str, Callable[[], SubAgent]],
        message_bus: MessageBus,
    ) -> None:
        self._config = config
        self._factories = agent_factories
        self._bus = message_bus
        self._pools: dict[str, SubAgentPool] = {}
        self._logger = logging.getLogger(__name__)

        # Create pools for each enabled agent type
        for agent_type_str, agent_config in config.items():
            if agent_config.enabled and agent_type_str in self._factories:
                agent_type = SubAgentType(agent_type_str)
                pool = SubAgentPool(
                    agent_type=agent_type,
                    config=agent_config,
                    agent_factory=self._factories[agent_type_str],
                    message_bus=message_bus,
                )
                self._pools[agent_type_str] = pool

    async def start(self) -> None:
        """Start all pools."""
        for pool in self._pools.values():
            await pool.start()

    async def shutdown(self) -> None:
        """Shutdown all pools."""
        for pool in self._pools.values():
            await pool.shutdown()

    async def submit_task(
        self,
        agent_type: SubAgentType,
        task: SubAgentTask,
    ) -> SubAgentResult:
        """Submit a task to the appropriate pool."""
        pool = self._pools.get(agent_type.value)
        if not pool:
            raise ValueError(f"No pool for agent type: {agent_type}")
        return await pool.submit_task(task)

    async def submit_planner_task(self, task: SubAgentTask) -> SubAgentResult:
        """Submit a task to the planner agent."""
        return await self.submit_task(SubAgentType.PLANNER, task)

    def get_resource_usage(self) -> dict[str, int]:
        """Get current resource usage across all pools."""
        return {agent_type: len(pool._active_agents) for agent_type, pool in self._pools.items()}

    async def get_status(self) -> dict[str, Any]:
        """Get status of all pools."""
        status: dict[str, Any] = {}
        for agent_type, pool in self._pools.items():
            status[agent_type] = {
                "active": len(pool._active_agents),
                "idle": len(pool._idle_agents),
                "queued": len(pool._task_queue),
            }
        return status
