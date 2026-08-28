"""Web Search sub-agent (stub).

Provides a placeholder for web search functionality.
Currently raises NotImplementedError as no search provider is wired up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


class WebSearchAgent(SubAgent):
    """Sub-agent for web search (stub implementation).

    Raises NotImplementedError as no search provider is currently
    configured.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.WEB_SEARCH, config, message_bus)
        self.provider = str(self.config.get("provider", "none"))

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a web search (not implemented).

        Expected payload:
            - query: search query string
            - max_results: maximum number of results (default: 10)
            - recency: time range for results (optional)
        """
        payload = task.payload
        query = payload.get("query", "")

        if not query:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No search query provided",
            )

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=False,
            error=(
                "Web search is not yet implemented. No search provider configured. "
                "To enable, configure a search provider (e.g., Brave, Serper, Google) "
                "and implement the search logic here."
            ),
        )
