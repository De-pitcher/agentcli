from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .agent.events import LoopEvent
from .agent.loop import AgentLoop, is_agentic_task
from .agent.reflector import DefaultReflector
from .agent.registry import ToolRegistry
from .config import Config
from .files import load_agents_md
from .memory.budget import DEFAULT_CONTEXT_WINDOW, estimate_tokens, trim_history_to_budget
from .memory.store import MemoryStore
from .openrouter_client import (
    ChatMessage,
    OpenRouterClient,
    OpenRouterError,
)
from .routing.classifier import classify
from .routing.registry import ModelRegistry
from .routing.router import Router

logger = logging.getLogger(__name__)


@dataclass
class SessionReply:
    stream: AsyncIterator[str]
    requested_primary: str | None


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

        self.registry: ModelRegistry | None = None
        self.router: Router | None = None

        if config.routing.enabled and not forced_model:
            self.registry = ModelRegistry(config.routing)
            self.router = Router(self.registry, config.routing.max_fallbacks)

    def close(self) -> None:
        store = getattr(self, "memory_store", None)
        if store is not None:
            store.close()
            self.memory_store = None

    async def aclose(self) -> None:
        await self.client.aclose()
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
        """Trim conversation history using dynamic token budget bounded by history_turns."""
        target_window = (
            max_context_tokens
            if max_context_tokens is not None
            else self._resolve_context_window(self.forced_model)
        )
        return trim_history_to_budget(
            self.history,
            max_context_tokens=target_window,
            max_turns=self.config.app.history_turns,
            budget_ratio=self.config.memory.budget_ratio,
        )

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
            "total_tokens": sum(estimate_tokens(m.content) for m in self.history),
            "user_tokens": sum(
                estimate_tokens(m.content) for m in self.history if m.role == "user"
            ),
            "assistant_tokens": sum(
                estimate_tokens(m.content) for m in self.history if m.role == "assistant"
            ),
        }

    def pop_last_message(self) -> None:
        if self.history:
            self.history.pop()

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

    async def send(self, text_for_classification: str) -> SessionReply:
        decision = None
        if self.router is not None:
            decision = self.router.decide(classify(text_for_classification))

        requested_primary = decision.primary if decision is not None else self.forced_model
        context_window = self._resolve_context_window(requested_primary)
        trimmed = self._trim_history(max_context_tokens=context_window)

        if decision is not None:
            stream = self.client.chat_stream(trimmed, models=decision.models)
        else:
            stream = self.client.chat_stream(trimmed, model=self.forced_model)

        return SessionReply(stream=stream, requested_primary=requested_primary)

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

    async def run_loop(self, goal: str) -> AsyncIterator[LoopEvent]:
        """Drive the Plan → Act → Reflect loop for a multi-step goal.

        Yields LoopEvent objects consumed by cli.py for display.
        Raises LoopIterationLimitError if the ceiling is hit.
        """
        loop_cfg = self.config.agent_loop
        loop = AgentLoop(
            goal=goal,
            registry=ToolRegistry(),
            reflector=DefaultReflector(),
            router=self.router,
            max_iterations=loop_cfg.max_iterations,
            plan_model=loop_cfg.plan_model_override or None,
            reflect_model=loop_cfg.reflect_model_override or None,
        )
        return await loop.run()
