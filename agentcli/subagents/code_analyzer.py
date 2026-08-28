"""Code Analyzer sub-agent.

Analyzes code files for issues, patterns, and quality metrics.
Reuses the existing file reading logic from Phase 1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Config
from ..files import FileReadError, expand_file_references
from ..openrouter_client import OpenRouterClient
from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus


class CodeAnalyzerAgent(SubAgent):
    """Sub-agent for analyzing code files.

    Can review code for bugs, style issues, security concerns,
    and provide refactoring suggestions.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.CODE_ANALYZER, config, message_bus)
        self._client: OpenRouterClient | None = None

    async def _get_client(self, config: Config) -> OpenRouterClient:
        if self._client is None:
            self._client = OpenRouterClient(config.openrouter)
        return self._client

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Analyze code based on the task payload.

        Expected payload:
            - files: list of file paths to analyze
            - focus: optional focus area (e.g., "security", "performance", "style")
            - context: additional context for the analysis
        """
        payload = task.payload
        files = payload.get("files", [])
        focus = payload.get("focus", "general")
        context = payload.get("context", "")

        if not files:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No files provided for analysis",
            )

        # Read file contents
        file_contents = []
        for file_path in files:
            try:
                # Use existing file expansion logic
                content = expand_file_references(f"@{file_path}")
                file_contents.append(f"### File: {file_path}\n```\n{content}\n```")
            except (OSError, FileReadError) as e:
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Failed to read {file_path}: {e}",
                )

        # Build analysis prompt
        files_text = "\n\n".join(file_contents)
        prompt = f"""Analyze the following code with focus on {focus}.

Context: {context}

Files to analyze:
{files_text}

Please provide:
1. Summary of the code's purpose
2. Issues found (bugs, security, performance, style)
3. Specific recommendations for improvement
4. Any security concerns
"""

        return SubAgentResult(
            task_id=task.id,
            agent_type=self.agent_type,
            success=True,
            output={
                "files_analyzed": files,
                "focus": focus,
                "prompt": prompt,
                "summary": f"Analyzed {len(files)} files with focus on {focus}",
            },
        )
