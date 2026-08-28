"""Event-driven message bus for inter-agent communication.

Provides a lightweight pub/sub mechanism for sub-agents to communicate
with each other and with the main session. Optimized for <10ms latency.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MessageType(str, Enum):
    """Types of messages that can be sent on the bus."""

    TASK_SUBMIT = "task_submit"
    TASK_RESULT = "task_result"
    TASK_PROGRESS = "task_progress"
    AGENT_STATUS = "agent_status"
    AGENT_SPAWN = "agent_spawn"
    AGENT_KILL = "agent_kill"
    RESOURCE_WARNING = "resource_warning"
    SHUTDOWN = "shutdown"
    CUSTOM = "custom"


@dataclass
class Message:
    """A message on the message bus.

    Attributes:
        id: Unique message identifier.
        type: Type of message.
        source: Agent ID or component that sent the message.
        target: Target agent ID or None for broadcast.
        payload: Message payload (type varies by message type).
        timestamp: When the message was created.
        correlation_id: Optional ID to correlate related messages.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: MessageType = MessageType.CUSTOM
    source: str = ""
    target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utc_now)
    correlation_id: str | None = None


MessageHandler = Callable[[Message], Awaitable[None]]


class MessageBus:
    """Event-driven message bus for inter-agent communication.

    Supports both direct (targeted) and broadcast messages.
    Handlers are called asynchronously with a timeout to prevent
    a slow handler from blocking the bus.
    """

    def __init__(self, handler_timeout: float = 5.0) -> None:
        self._subscribers: dict[MessageType, list[MessageHandler]] = defaultdict(list)
        self._targeted_handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._broadcast_handlers: list[MessageHandler] = []
        self._handler_timeout = handler_timeout
        self._logger = logging.getLogger(f"{__name__}.MessageBus")

    def subscribe(
        self,
        message_type: MessageType,
        handler: MessageHandler,
        *,
        target: str | None = None,
    ) -> None:
        """Subscribe to messages of a specific type.

        Args:
            message_type: Type of message to subscribe to.
            handler: Async function to handle messages.
            target: If set, only receive messages targeted at this agent ID.
        """
        if target:
            self._targeted_handlers[target].append(handler)
        else:
            self._subscribers[message_type].append(handler)

    def subscribe_broadcast(self, handler: MessageHandler) -> None:
        """Subscribe to all broadcast messages (no target)."""
        self._broadcast_handlers.append(handler)

    def unsubscribe(self, handler: MessageHandler) -> None:
        """Unsubscribe a handler from all message types."""
        for handlers in self._subscribers.values():
            if handler in handlers:
                handlers.remove(handler)
        for handlers in self._targeted_handlers.values():
            if handler in handlers:
                handlers.remove(handler)
        if handler in self._broadcast_handlers:
            self._broadcast_handlers.remove(handler)

    async def publish(self, message: Message) -> None:
        """Publish a message to all relevant handlers.

        Args:
            message: The message to publish.
        """
        handlers_to_call: list[MessageHandler] = []

        # Add type-specific handlers
        handlers_to_call.extend(self._subscribers.get(message.type, []))

        # Add targeted handlers
        if message.target:
            handlers_to_call.extend(self._targeted_handlers.get(message.target, []))

        # Add broadcast handlers if no specific target
        if not message.target:
            handlers_to_call.extend(self._broadcast_handlers)

        if not handlers_to_call:
            self._logger.debug("No handlers for message %s", message.id)
            return

        # Execute all handlers with timeout
        tasks = [self._run_handler_with_timeout(h, message) for h in handlers_to_call]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_handler_with_timeout(self, handler: MessageHandler, message: Message) -> None:
        """Run a handler with timeout protection."""
        try:
            await asyncio.wait_for(handler(message), timeout=self._handler_timeout)
        except TimeoutError:
            self._logger.warning("Handler timed out for message %s", message.id)
        except BaseException as e:  # noqa: BLE001
            self._logger.error("Handler error for message %s: %s", message.id, e)

    async def request_response(
        self,
        message: Message,
        expected_type: MessageType,
        timeout: float = 10.0,
    ) -> Message | None:
        """Send a request and wait for a response of the expected type.

        Args:
            message: The request message to send.
            expected_type: The expected response message type.
            timeout: Maximum time to wait for response.

        Returns:
            The response message, or None if timeout.
        """
        response_event = asyncio.Event()
        response_message: Message | None = None

        async def response_handler(msg: Message) -> None:
            nonlocal response_message
            if msg.type == expected_type and msg.correlation_id == message.id:
                response_message = msg
                response_event.set()

        self.subscribe(expected_type, response_handler)
        try:
            await self.publish(message)
            await asyncio.wait_for(response_event.wait(), timeout=timeout)
            return response_message
        except TimeoutError:
            return None
        finally:
            self.unsubscribe(response_handler)

    def broadcast(self, message_type: MessageType, payload: dict[str, Any], source: str) -> Message:
        """Convenience method to broadcast a message."""
        message = Message(
            type=message_type,
            source=source,
            payload=payload,
            target=None,
        )
        return message
