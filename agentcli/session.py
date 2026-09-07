from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent.checkpoints import CheckpointManager
from .agent.events import LoopEvent
from .agent.loop import AgentLoop, is_agentic_task
from .agent.registry import ToolRegistry
from .agent.tasks import TaskManager
from .config import Config
from .files import load_agents_md
from .mcp.manager import MCPClientManager
from .memory.adaptive_compressor import AdaptiveContextCompressor
from .memory.budget import DEFAULT_CONTEXT_WINDOW, estimate_tokens, trim_history_to_budget
from .memory.governor import TokenBudgetGovernor
from .memory.store import MemoryStore
from .openrouter_client import (
    ChatMessage,
    OpenRouterClient,
    OpenRouterError,
)
from .routing.classifier import classify
from .routing.registry import ModelRegistry
from .routing.router import Router
from .skills.engine import SkillEngine
from .worktree.manager import WorktreeManager

logger = logging.getLogger(__name__)



@dataclass
class SessionReply:
    stream: AsyncIterator[str]
    requested_primary: str | None


def build_environment_system_prompt(config: Config) -> str:
    """Construct environment-grounded system instructions for developer workflows."""
    import platform

    os_name = platform.system()
    shell_hint = "PowerShell" if os_name == "Windows" else "Bash/Zsh"
    cwd_path = str(Path.cwd().resolve())
    preset = getattr(config.app, "preset", "coding") or "coding"

    return (
        f"You are AgentCLI, an expert, budget-conscious AI developer assistant.\n"
        f"Current Environment:\n"
        f"- Operating System: {os_name} (Shell: {shell_hint})\n"
        f"- Working Directory: {cwd_path}\n"
        f"- Preset: {preset}\n"
        f"Guidelines:\n"
        f"- You are running live in a real terminal environment.\n"
        f"- Never hallucinate fake command outputs or fake filesystem responses (such as mock /home/user or pretend bash outputs).\n"
        f"- When the user requests actions (reading/writing files, inspecting directories, running commands/tests), state the plan clearly or explain what is being executed."
    )


class AgentSession:
    """Manages chat state, persistence, routing, and openrouter client interactions."""

    def __init__(
        self,
        config: Config,
        forced_model: str | None = None,
        initial_history: list[ChatMessage] | None = None,
        session_id: str | None = None,
    ):
        self.config = config
        self.client = OpenRouterClient(config.openrouter)
        self.forced_model = forced_model
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.is_resumed: bool = False

        self.memory_store: MemoryStore | None = None
        if config.memory.enabled:
            try:
                self.memory_store = MemoryStore(config.memory.db_path or None)
                if config.memory.retention_days > 0:
                    self.memory_store.prune_older_than(config.memory.retention_days)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to initialize memory store: %s", exc)
                self.memory_store = None

        self.history: list[ChatMessage] = []
        if session_id and self.memory_store is not None:
            existing = self.memory_store.get_session(session_id)
            if existing is not None:
                self.is_resumed = True
                saved_msgs = self.memory_store.get_messages(session_id)
                self.history = [ChatMessage(role=m.role, content=m.content) for m in saved_msgs]
            else:
                self.is_resumed = False
                if initial_history is not None:
                    self.history = list(initial_history)
        else:
            if initial_history is not None:
                self.history = list(initial_history)

        if not self.is_resumed and config.app.load_agents_md:
            has_system = any(m.role == "system" for m in self.history)
            if not has_system:
                agents_context = load_agents_md()
                if agents_context:
                    self.history.insert(0, ChatMessage(role="system", content=agents_context))

        self.cumulative_cost_usd: float = 0.0
        max_cost = getattr(config.routing, "max_cost_usd", None)
        self.governor: TokenBudgetGovernor = TokenBudgetGovernor(max_cost_usd=max_cost)
        self.compressor: AdaptiveContextCompressor = AdaptiveContextCompressor(
            target_budget_ratio=config.memory.budget_ratio
        )
        self.registry: ModelRegistry | None = None
        self.router: Router | None = None
        self.mcp_manager: MCPClientManager = MCPClientManager(config=self.config)
        self.checkpoint_manager: CheckpointManager = CheckpointManager()
        self.task_manager: TaskManager = TaskManager()
        self.skill_engine: SkillEngine = SkillEngine()
        self.worktree_manager: WorktreeManager = WorktreeManager()
        self.active_worktree_path: Path | None = None


        if config.routing.enabled:
            self.registry = ModelRegistry(config.routing)
            if not forced_model:
                self.router = Router(
                    self.registry,
                    config.routing.max_fallbacks,
                    budget_tier=getattr(config.routing, "budget_tier", "low"),
                )

    async def initialize_mcp(self) -> None:
        """Initialize external MCP servers and discover tools."""
        if self.config.mcp_servers:
            await self.mcp_manager.initialize()

    def record_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        agent_type: str = "main",
    ) -> float:
        """Calculate and accumulate the USD cost for a model invocation."""
        cost = self.governor.record_usage(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            agent_type=agent_type,
        )
        self.cumulative_cost_usd = self.governor.total_cost_usd
        return cost

    def is_budget_exceeded(self) -> bool:
        """Check if session cumulative cost has reached or exceeded max_cost_usd."""
        max_cost = getattr(self.config.routing, "max_cost_usd", None) or self.governor.max_cost_usd
        if (
            max_cost is not None
            and max_cost > 0
            and max(self.cumulative_cost_usd, self.governor.total_cost_usd) >= max_cost
        ):
            return True
        return self.governor.is_exceeded()

    def close(self) -> None:
        store = getattr(self, "memory_store", None)
        if store is not None:
            store.close()
            self.memory_store = None

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.mcp_manager.aclose()
        if hasattr(self, "task_manager") and self.task_manager is not None:
            await self.task_manager.cleanup_all()
        self.close()

    def __del__(self) -> None:
        self.close()

    def _resolve_context_window(self, model_id: str | None) -> int:
        """Look up context window size from registry if known, or fallback to default."""
        if not model_id:
            return DEFAULT_CONTEXT_WINDOW
        if self.registry is not None:
            rec = self.registry.get_model(model_id)
            if rec is not None:
                return rec.context_window
        return DEFAULT_CONTEXT_WINDOW

    def _trim_history(self, max_context_tokens: int | None = None) -> list[ChatMessage]:
        """Trim conversation history using dynamic token budget and adaptive compression."""
        target_window = (
            max_context_tokens
            if max_context_tokens is not None
            else self._resolve_context_window(self.forced_model)
        )
        trimmed = trim_history_to_budget(
            self.history,
            max_context_tokens=target_window,
            max_turns=self.config.app.history_turns,
            budget_ratio=self.config.memory.budget_ratio,
        )
        return self.compressor.compress(trimmed, max_context_tokens=target_window)


    def add_user_message(self, content: str, token_count: int | None = None) -> None:
        self.history.append(ChatMessage(role="user", content=content))
        if self.memory_store is not None:
            try:
                self.memory_store.append_message(
                    session_id=self.session_id,
                    role="user",
                    content=content,
                    token_count=token_count
                    if token_count is not None
                    else estimate_tokens(content),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to persist user message: %s", exc)

    async def async_add_user_message(self, content: str, token_count: int | None = None) -> None:
        self.history.append(ChatMessage(role="user", content=content))
        if self.memory_store is not None:
            try:
                await self.memory_store.aappend_message(
                    session_id=self.session_id,
                    role="user",
                    content=content,
                    token_count=token_count
                    if token_count is not None
                    else estimate_tokens(content),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to persist user message asynchronously: %s", exc)

    def add_assistant_message(self, content: str, token_count: int | None = None) -> None:
        self.history.append(ChatMessage(role="assistant", content=content))
        if self.memory_store is not None:
            try:
                self.memory_store.append_message(
                    session_id=self.session_id,
                    role="assistant",
                    content=content,
                    token_count=token_count
                    if token_count is not None
                    else estimate_tokens(content),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to persist assistant message: %s", exc)

    async def async_add_assistant_message(
        self, content: str, token_count: int | None = None
    ) -> None:
        self.history.append(ChatMessage(role="assistant", content=content))
        if self.memory_store is not None:
            try:
                await self.memory_store.aappend_message(
                    session_id=self.session_id,
                    role="assistant",
                    content=content,
                    token_count=token_count
                    if token_count is not None
                    else estimate_tokens(content),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to persist assistant message asynchronously: %s", exc)

    async def get_session_stats(self) -> dict[str, Any]:
        """Return message and token stats for this session."""
        if self.memory_store is not None:
            return await self.memory_store.aget_session_stats(self.session_id)
        return {
            "message_count": len(self.history),
            "total_tokens": sum(estimate_tokens(m.content or "") for m in self.history),
            "user_tokens": sum(
                estimate_tokens(m.content or "") for m in self.history if m.role == "user"
            ),
            "assistant_tokens": sum(
                estimate_tokens(m.content or "") for m in self.history if m.role == "assistant"
            ),
        }

    def pop_last_message(self) -> None:
        if self.history:
            self.history.pop()
        if self.memory_store is not None:
            self.memory_store.delete_last_message(self.session_id)

    @property
    def last_served_model(self) -> str | None:
        return self.client.last_served_model

    def mark_success(self, requested_primary: str | None) -> None:
        if self.registry is not None and requested_primary is not None:
            self.registry.mark_success(self.client.last_served_model or requested_primary)

    def mark_failure(
        self, requested_primary: str | None, exc: OpenRouterError, rate_limited: bool = False
    ) -> None:
        if self.registry is not None and requested_primary is not None:
            self.registry.mark_failure(
                self.client.last_served_model or requested_primary,
                rate_limited=rate_limited,
            )

    def get_grounded_history(self, max_context_tokens: int | None = None) -> list[ChatMessage]:
        """Return trimmed history with environment grounding injected if no system instructions exist."""
        trimmed = self._trim_history(max_context_tokens=max_context_tokens)
        if not any(m.role == "system" for m in trimmed):
            env_msg = ChatMessage(
                role="system", content=build_environment_system_prompt(self.config)
            )
            return [env_msg, *trimmed]
        return trimmed

    async def send(self, text_for_classification: str) -> SessionReply:
        decision = None
        if self.router is not None:
            decision = self.router.decide(classify(text_for_classification))

        requested_primary = decision.primary if decision is not None else self.forced_model
        context_window = self._resolve_context_window(requested_primary)
        trimmed = self.get_grounded_history(max_context_tokens=context_window)

        if decision is not None:
            stream = self.client.chat_stream(trimmed, models=decision.models)
        else:
            stream = self.client.chat_stream(trimmed, model=self.forced_model)

        return SessionReply(stream=stream, requested_primary=requested_primary)

    def prepare_prompt(self, user_text: str) -> str:
        """Expand @file, @repo, @semantic tokens in user text before sending/persisting."""
        from .files import expand_file_references

        try:
            return expand_file_references(user_text) if "@" in user_text else user_text
        except Exception as exc:  # noqa: BLE001
            logger.debug("Prompt token expansion notice: %s", exc)
            return user_text

    async def step(self, user_text: str) -> str:
        """Execute a single user turn, streaming and persisting the response, and return full reply."""
        if self.is_budget_exceeded():
            limit_msg = (
                f"Session cost ceiling reached (${self.cumulative_cost_usd:.4f}). Turn aborted."
            )
            await self.async_add_assistant_message(limit_msg)
            return limit_msg

        expanded_text = self.prepare_prompt(user_text)
        await self.async_add_user_message(expanded_text)

        if self.should_use_loop(expanded_text):
            finish_output = None
            loop_summary = None
            async for event in self.run_loop(expanded_text):
                from .agent.events import FinishEvent, LoopErrorEvent

                if isinstance(event, FinishEvent):
                    finish_output = getattr(event, "output", None)
                    loop_summary = event.summary
                elif isinstance(event, LoopErrorEvent):
                    loop_summary = f"[loop error] {event.error}"

            full_reply = finish_output if finish_output else (loop_summary or "(loop completed)")
            await self.async_add_assistant_message(full_reply)
            return full_reply

        reply = await self.send(expanded_text)
        chunks: list[str] = []
        async for delta in reply.stream:
            chunks.append(delta)
        full_reply = "".join(chunks)
        await self.async_add_assistant_message(full_reply)
        self.mark_success(reply.requested_primary)

        # Track usage cost on the session level
        if reply.requested_primary:
            from .memory.budget import estimate_tokens

            prompt_tok = estimate_tokens(expanded_text)
            comp_tok = estimate_tokens(full_reply)
            self.record_cost(reply.requested_primary, prompt_tok, comp_tok)

        return full_reply

    # ------------------------------------------------------------------
    # Phase 4: agentic loop integration
    # ------------------------------------------------------------------

    def should_use_loop(self, user_text: str) -> bool:
        """Return True if this input should be routed through the agent loop.

        Conditions:
          - agent_loop.enabled is True in config
          - The text matches the multi-step task heuristic

        Simple single-turn chat is NEVER routed here — zero added latency
        for the simple case.
        """
        return self.config.agent_loop.enabled and is_agentic_task(user_text)

    async def run_loop(self, goal: str, run_id: str | None = None) -> AsyncIterator[LoopEvent]:
        """Drive the Plan → Act → Reflect loop for a multi-step goal.

        Yields LoopEvent objects consumed by cli.py for display.
        Raises LoopIterationLimitError if the ceiling is hit.
        """
        loop_cfg = self.config.agent_loop
        registry = ToolRegistry(config=self.config)
        self.mcp_manager.register_tools(registry)

        # Load any configured tool plugins into the registry
        if self.config.app.plugins:
            for plugin_path in self.config.app.plugins:
                registry.load_plugin_file(plugin_path)

        # Prepare rich initial context combining environment and recent conversation history
        conv_parts: list[str] = [build_environment_system_prompt(self.config)]
        if self.history:
            recent_turns = [m for m in self.history if m.role in ("user", "assistant")][-6:]
            if recent_turns:
                conv_summary = "\n".join(
                    f"{m.role.upper()}: {(m.content or '')[:250]}" for m in recent_turns
                )
                conv_parts.append(f"Conversation Context:\n{conv_summary}")
        initial_context = "\n\n".join(conv_parts)

        max_cost = getattr(self.config.routing, "max_cost_usd", None) or getattr(
            self.config.agent_loop, "max_cost_usd", None
        )

        loop = AgentLoop(
            goal=goal,
            registry=registry,
            router=self.router,
            max_iterations=loop_cfg.max_iterations,
            plan_model=loop_cfg.plan_model_override or None,
            reflect_model=loop_cfg.reflect_model_override or None,
            config=self.config,
            run_id=run_id,
            initial_context=initial_context,
            max_cost_usd=max_cost,
        )

        try:
            async for event in loop.run():
                yield event
        finally:
            cost = getattr(loop, "cumulative_cost_usd", 0.0)
            if cost > 0.0:
                self.cumulative_cost_usd += cost

    async def auto_ground_workspace(self, root_dir: str | Path = ".") -> str | None:
        """Inspect git repository state and inject workspace context into system instructions."""
        from .subagents.base import SubAgentTask, SubAgentType
        from .subagents.workspace import WorkspaceAgent

        agent = WorkspaceAgent()
        task = SubAgentTask(
            agent_type=SubAgentType.WORKSPACE,
            payload={"operation": "git_status", "path": str(root_dir)},
        )
        res = await agent.run(task)
        if res.success and isinstance(res.output, dict) and res.output.get("is_git_repo"):
            summary = str(res.output.get("summary", ""))
            ctx_msg = ChatMessage(
                role="system",
                content=f"[Workspace Context: {summary}]",
            )
            self.history.insert(0, ctx_msg)
            return summary
        return None
