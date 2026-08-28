# Custom Tool Plugins in agentcli

`agentcli` allows external developers to extend the Plan → Act → Reflect agent loop and MCP server with custom tools without modifying core source code.

---

## 🛠️ Creating a Plugin

A plugin is simply a Python file that defines a `register_tools(registry: ToolRegistry)` or `setup(registry: ToolRegistry)` function.

### Example: `my_tools.py`

```python
from typing import Any
from agentcli.agent.registry import ToolRegistry


def fetch_stock_quote(symbol: str) -> dict[str, Any]:
    """Fetch current stock price for a symbol."""
    return {"symbol": symbol.upper(), "price": 142.50, "currency": "USD"}


def register_tools(registry: ToolRegistry) -> None:
    # Register a plain Python callable
    registry.register_callable(
        name="stock_quote",
        func=fetch_stock_quote,
        description="Fetch real-time stock quotes by ticker symbol",
    )
```

---

## 🚀 Running with Plugins

Pass the `--plugin` flag to `agentcli`:

```bash
# In interactive chat / agent loop
agentcli --plugin path/to/my_tools.py chat

# In MCP server mode
agentcli --plugin path/to/my_tools.py mcp
```

You can also specify default plugins in `agentcli.toml`:

```toml
[app]
plugins = ["plugins/custom_tools.py", "plugins/git_helpers.py"]
```
