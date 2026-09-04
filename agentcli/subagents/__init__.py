"""Sub-agent system for agentcli.

Provides a framework for spawning and managing lightweight sub-agents
that can execute specialized tasks in parallel.
"""

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType
from .bus import Message, MessageBus
from .code_analyzer import CodeAnalyzerAgent
from .file_ops import FileOpsAgent
from .planner import PlannerAgent
from .shell import ShellExecutionAgent
from .spawner import SubAgentPool, SubAgentSpawner
from .web_search import WebSearchAgent
from .workspace import WorkspaceAgent

__all__ = [
    "CodeAnalyzerAgent",
    "FileOpsAgent",
    "Message",
    "MessageBus",
    "PlannerAgent",
    "ShellExecutionAgent",
    "SubAgent",
    "SubAgentPool",
    "SubAgentResult",
    "SubAgentSpawner",
    "SubAgentTask",
    "SubAgentType",
    "WebSearchAgent",
    "WorkspaceAgent",
]
