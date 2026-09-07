"""Sub-agent system for agentcli.

Provides a framework for spawning and managing lightweight sub-agents
that can execute specialized tasks in parallel.
"""

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType
from .bus import Message, MessageBus, MessageType
from .clarification import ClarificationAgent
from .code_analyzer import CodeAnalyzerAgent
from .consensus import AgentVote, ConsensusEngine, ConsensusResult, ConsensusStrategy
from .diagnostics import DiagnosticsAgent, DiagnosticSpan, DiagnosticsParser
from .file_ops import FileOpsAgent
from .grep_search import GrepSearchAgent
from .planner import PlannerAgent
from .shell import ShellExecutionAgent
from .skill_runner import SkillRunnerAgent
from .spawner import SubAgentPool, SubAgentSpawner
from .task_manager import TaskManagerAgent
from .web_fetch import HTMLToMarkdownConverter, WebFetchAgent, html_to_markdown
from .web_search import WebSearchAgent
from .workspace import WorkspaceAgent
from .worktree import WorktreeAgent

__all__ = [
    "AgentVote",
    "ClarificationAgent",
    "CodeAnalyzerAgent",
    "ConsensusEngine",
    "ConsensusResult",
    "ConsensusStrategy",
    "DiagnosticSpan",
    "DiagnosticsAgent",
    "DiagnosticsParser",
    "FileOpsAgent",
    "GrepSearchAgent",
    "HTMLToMarkdownConverter",
    "Message",
    "MessageBus",
    "MessageType",
    "PlannerAgent",
    "ShellExecutionAgent",
    "SkillRunnerAgent",
    "SubAgent",
    "SubAgentPool",
    "SubAgentResult",
    "SubAgentSpawner",
    "SubAgentTask",
    "SubAgentType",
    "TaskManagerAgent",
    "WebFetchAgent",
    "WebSearchAgent",
    "WorkspaceAgent",
    "WorktreeAgent",
    "html_to_markdown",
]
