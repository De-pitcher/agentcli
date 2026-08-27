"""Heuristic task classifier: maps a user message to a routing category.

Pure regex/keyword rules — no I/O, no network, microsecond-scale. Categories
match the registry tags: "code", "reasoning", "chat" (default).
"""

from __future__ import annotations

import re

CODE = "code"
REASONING = "reasoning"
CHAT = "chat"

_CODE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"```",
        r"\b(?:def|class|func|function|import|from|package|struct|impl)\s+\w+",
        r"\b(?:traceback|stack ?trace|segfault|core dump|null ?pointer)\b",
        r"\b(?:refactor|debug|compile|regex|sql|schema migration|unit test)\b",
        r"\b\w+\.(?:py|js|ts|tsx|jsx|java|go|rs|c|cpp|h|hpp|rb|php|cs|swift|kt|sh|toml|yaml|yml)\b",
        r"\b(?:git|docker|kubectl|npm|pip|curl)\s+\w+",
        r"\b(?:bug|crash|exception|stack overflow|off-by-one|race condition)\b",
        r"(?:SELECT|INSERT|UPDATE|DELETE)\s.+\s(?:FROM|INTO|SET)\b",
    )
)

_REASONING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:why|how come|explain|analyze|analyise|compare|evaluate|derive|prove|justify)\b",
        r"\b(?:step by step|step-by-step|walk me through|think through|trade-?offs?)\b",
        r"\b(?:calculate|compute|solve|estimate|optimize)\b",
        r"\b(?:math|logic|puzzle|riddle|theorem|proof|paradox|fallacy)\b",
        r"\bpros\s*(?:and|\/|&)\s*cons\b",
        r"\b\d+\s*[+\-*/^%]\s*\d+\s*=",
    )
)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify(text: str) -> str:
    """Return the routing category for a user message."""
    if not text:
        return CHAT
    if _matches_any(text, _CODE_PATTERNS):
        return CODE
    if _matches_any(text, _REASONING_PATTERNS):
        return REASONING
    return CHAT
