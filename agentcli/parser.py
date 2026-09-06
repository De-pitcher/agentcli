"""Resilient multi-format LLM JSON parser & syntax auto-repair engine (Phase 29).

Extracts and repairs JSON payloads emitted by LLMs that may contain markdown fences,
trailing commas, conversational prefixes/suffixes, single-quoted keys/strings,
or Python boolean/None literals.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Regex for stripping markdown code block fences
_MARKDOWN_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

# Regex for finding outermost object { ... } or array [ ... ]
_JSON_OBJECT_RE = re.compile(r"(\{.*\})", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"(\[.*\])", re.DOTALL)

# Regex for removing trailing commas before closing braces/brackets
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Regex for Python literal substitution (word boundaries)
_PY_BOOL_NONE_RE = [
    (re.compile(r"\bTrue\b"), "true"),
    (re.compile(r"\bFalse\b"), "false"),
    (re.compile(r"\bNone\b"), "null"),
]

# Regex for single-quoted keys: {'key': ...} or {, 'key': ...}
_SINGLE_QUOTE_KEY_RE = re.compile(r"([{\[,]\s*)'([^'\\]*(?:\\.[^'\\]*)*)'(\s*:)")
# Regex for single-quoted string values: {..., 'key': 'val', ...}
_SINGLE_QUOTE_VAL_RE = re.compile(r"(:\s*)'([^'\\]*(?:\\.[^'\\]*)*)'(\s*[,}\]])")


def extract_json_payload(text: str) -> str:
    """Extract raw JSON substring from LLM conversational text and markdown fences.

    Args:
        text: Raw text string from LLM response.

    Returns:
        Cleaned potential JSON string.
    """
    if not text:
        return ""

    cleaned = text.strip()

    # 1. Check for markdown code blocks
    match = _MARKDOWN_BLOCK_RE.search(cleaned)
    if match:
        return match.group(1).strip()

    # 2. Extract outermost [ ... ] array or { ... } object
    # Check which boundary appears first
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")

    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        last_brace = cleaned.rfind("}")
        if last_brace > first_brace:
            return cleaned[first_brace : last_brace + 1].strip()

    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        last_bracket = cleaned.rfind("]")
        if last_bracket > first_bracket:
            return cleaned[first_bracket : last_bracket + 1].strip()

    return cleaned


def repair_json_syntax(text: str) -> str:
    """Apply heuristic syntax repairs to fix common LLM JSON errors.

    Repairs:
      - Trailing commas in objects and arrays
      - Python boolean/None literals (`True`, `False`, `None`)
      - Single-quoted keys and string values
    """
    repaired = text

    # Remove trailing commas: {"a": 1,} -> {"a": 1} or [1, 2,] -> [1, 2]
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)

    # Replace Python True/False/None with true/false/null
    for pattern, replacement in _PY_BOOL_NONE_RE:
        repaired = pattern.sub(replacement, repaired)

    # Convert single-quoted keys to double quotes: {'key': 1} -> {"key": 1}
    repaired = _SINGLE_QUOTE_KEY_RE.sub(r'\1"\2"\3', repaired)

    # Convert single-quoted string values to double quotes: {"key": 'val'} -> {"key": "val"}
    repaired = _SINGLE_QUOTE_VAL_RE.sub(r'\1"\2"\3', repaired)

    return repaired


def robust_json_loads(text: str) -> Any:
    """Robustly parse JSON from an LLM response with multiple fallback repair passes.

    Strategies:
      1. Standard `json.loads` on extracted payload.
      2. Heuristic syntax repair (strip trailing commas, Python literals, single quotes).
      3. Python `ast.literal_eval` safe conversion for Python dictionary/list representations.

    Args:
        text: Raw LLM output string or JSON fragment.

    Returns:
        Parsed Python dictionary, list, or primitive.

    Raises:
        json.JSONDecodeError: If all parsing and repair strategies fail.
    """
    if not text or not text.strip():
        raise json.JSONDecodeError("Empty JSON string", text or "", 0)

    raw_extracted = extract_json_payload(text)

    # Pass 1: Direct json.loads
    try:
        return json.loads(raw_extracted)
    except json.JSONDecodeError:
        pass

    # Pass 2: Syntax auto-repair
    repaired = repair_json_syntax(raw_extracted)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Pass 3: Safe ast.literal_eval fallback for Python dict/list formats
    try:
        evaluated = ast.literal_eval(raw_extracted)
        if isinstance(evaluated, (dict, list, int, float, bool, str)) or evaluated is None:
            return evaluated
    except (ValueError, SyntaxError, MemoryError):
        pass

    # Pass 4: ast.literal_eval on repaired string
    try:
        evaluated = ast.literal_eval(repaired)
        if isinstance(evaluated, (dict, list, int, float, bool, str)) or evaluated is None:
            return evaluated
    except (ValueError, SyntaxError, MemoryError):
        pass

    # If all failed, re-raise original json.loads error on raw extracted
    return json.loads(raw_extracted)


__all__ = [
    "extract_json_payload",
    "repair_json_syntax",
    "robust_json_loads",
]
