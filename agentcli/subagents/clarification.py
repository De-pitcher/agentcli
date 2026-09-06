"""Interactive user disambiguation and clarification subagent."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

logger = logging.getLogger(__name__)

# Type for interactive callback: (question, options, is_multi_select) -> answer
ClarificationHandler = Callable[[str, list[str] | None, bool], Any]


class ClarificationAgent(SubAgent):
    """Sub-agent responsible for asking clarifying questions to the user during tasks."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        handler: ClarificationHandler | None = None,
    ) -> None:
        super().__init__(
            agent_type=SubAgentType.ASK_QUESTION,
            config=config or {},
        )
        self.handler = handler

    def set_handler(self, handler: ClarificationHandler) -> None:
        """Register the interactive UI/CLI prompt handler."""
        self.handler = handler

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute clarification question."""
        payload = task.payload

        question = payload.get("question") or payload.get("prompt")
        options = payload.get("options")
        is_multi_select = bool(payload.get("is_multi_select", False))
        context = payload.get("context", "")

        if not question:
            # Check if questions array was passed
            questions = payload.get("questions")
            if isinstance(questions, list) and questions:
                first = questions[0]
                question = first.get("question")
                options = first.get("options", options)
                is_multi_select = bool(first.get("is_multi_select", is_multi_select))

        if not question:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="Missing required 'question' in clarification payload",
            )

        # Normalize options
        if isinstance(options, list):
            options = [str(opt) for opt in options if opt is not None]
        else:
            options = None

        logger.info("ClarificationAgent: asking user question: %s", question)

        # If interactive handler is present, call it
        if self.handler is not None:
            try:
                if inspect.iscoroutinefunction(self.handler):
                    answer = await self.handler(question, options, is_multi_select)
                else:
                    answer = self.handler(question, options, is_multi_select)

                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=True,
                    output={
                        "question": question,
                        "options": options,
                        "answer": answer,
                        "interactive": True,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ClarificationAgent handler raised error: %s", exc)

        # Non-interactive fallback (CI, batch scripts, --plain mode)
        selected_default = None
        if options:
            # Look for recommendation prefix or take first
            recommended = next((o for o in options if "(recommended)" in o.lower()), options[0])
            selected_default = [recommended] if is_multi_select else recommended
            answer_text = f"[Non-interactive mode: auto-selected default '{selected_default}']"
        else:
            answer_text = "[Non-interactive mode: proceeding with standard defaults]"

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={
                "question": question,
                "options": options,
                "answer": answer_text,
                "selected": selected_default,
                "interactive": False,
                "context": context,
            },
        )
