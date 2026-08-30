"""Example custom tool plugin for agentcli.

Demonstrates how external contributors can register custom tools into agentcli's
Plan → Act → Reflect loop or MCP server without modifying core files.

Usage:
    agentcli --plugin examples/custom_tool_plugin.py chat
    agentcli --plugin examples/custom_tool_plugin.py mcp
"""

from __future__ import annotations

from typing import Any

from agentcli.agent.registry import ToolRegistry


def calculate_bmi(weight_kg: float = 70.0, height_m: float = 1.75) -> dict[str, Any]:
    """Calculate Body Mass Index."""
    bmi = float(weight_kg) / (float(height_m) ** 2)
    return {
        "bmi": round(bmi, 2),
        "category": "normal" if 18.5 <= bmi <= 24.9 else "out_of_range",
    }


def string_reverser(text: str = "") -> str:
    """Reverse a string."""
    return text[::-1]


def register_tools(registry: ToolRegistry) -> None:
    """Plugin hook called automatically by agentcli on startup."""
    registry.register_callable(
        name="calculate_bmi",
        func=calculate_bmi,
        description="Calculates Body Mass Index from weight_kg and height_m",
    )
    registry.register_callable(
        name="reverse_string",
        func=string_reverser,
        description="Reverses the provided text string",
    )
