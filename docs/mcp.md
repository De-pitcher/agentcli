# Model Context Protocol (MCP) Integration

`agentcli` implements a native, zero-dependency **Model Context Protocol (MCP)** JSON-RPC 2.0 stdio server. This allows host applications (such as Claude Desktop, Antigravity, or Cursor) to directly invoke `agentcli` tools and sub-agents.

---

## 🔌 Supported Protocol Methods

- `initialize`: Returns protocol version `2024-11-05` and server metadata.
- `ping`: Health check.
- `tools/list`: Enumerate available tools (`file_ops`, `shell_execution`, `code_analyzer`, `web_search`, and any loaded custom plugins).
- `tools/call`: Execute a specified tool and return formatted text output.

---

## ⚙️ Configuring Claude Desktop / Host Applications

Add `agentcli` to your MCP client configuration (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agentcli": {
      "command": "agentcli",
      "args": ["mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```

To include custom tool plugins in the MCP server:

```json
{
  "mcpServers": {
    "agentcli": {
      "command": "agentcli",
      "args": ["--plugin", "C:/path/to/my_tools.py", "mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```
