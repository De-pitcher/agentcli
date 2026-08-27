import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .config import Config
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
    """Manages chat state, routing, and openrouter client interactions."""

    def __init__(
        self,
        config: Config,
        forced_model: str | None = None,
        initial_history: list[ChatMessage] | None = None,
    ):
        self.config = config
        self.client = OpenRouterClient(config.openrouter)
        self.forced_model = forced_model

        self.history = initial_history or []
        self.registry: ModelRegistry | None = None
        self.router: Router | None = None

        if config.routing.enabled and not forced_model:
            self.registry = ModelRegistry(config.routing)
            self.router = Router(self.registry, config.routing.max_fallbacks)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _trim_history(self) -> list[ChatMessage]:
        if self.history and self.history[0].role == "system":
            # Preserve system message, trim the rest to (turns * 2) previous messages + 1 current message
            return [self.history[0]] + self.history[1:][-(self.config.app.history_turns * 2 + 1) :]
        return self.history[-(self.config.app.history_turns * 2 + 1) :]

    def add_user_message(self, content: str) -> None:
        self.history.append(ChatMessage(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self.history.append(ChatMessage(role="assistant", content=content))

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
        trimmed = self._trim_history()
        decision = None
        if self.router is not None:
            decision = self.router.decide(classify(text_for_classification))

        requested_primary = decision.primary if decision is not None else None

        if decision is not None:
            stream = self.client.chat_stream(trimmed, models=decision.models)
        else:
            stream = self.client.chat_stream(trimmed, model=self.forced_model)

        return SessionReply(stream=stream, requested_primary=requested_primary)
