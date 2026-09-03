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

---

## ⚠️ Windows Compatibility Notes

### MCP Server on Windows

**Known Issue**: The `agentcli mcp` command uses `asyncio` stdio pipes which have a known compatibility issue with Windows Proactor event loop. Running `agentcli mcp` directly on Windows may fail with Proactor pipe handle errors.

### Recommended Workarounds

#### Option 1: WSL2 (Recommended)
Run `agentcli mcp` inside WSL2 (Windows Subsystem for Linux 2) for full compatibility:

```bash
wsl agentcli mcp
```

Then configure your MCP client to use WSL:
```json
{
  "mcpServers": {
    "agentcli": {
      "command": "wsl",
      "args": ["agentcli", "mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```

#### Option 2: Docker Container
Run the MCP server in a Docker container:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e .
ENTRYPOINT ["agentcli", "mcp"]
```

```bash
docker build -t agentcli-mcp .
docker run -i --rm -e OPENROUTER_API_KEY=sk-or-... agentcli-mcp
```

Then configure your MCP client:
```json
{
  "mcpServers": {
    "agentcli": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "OPENROUTER_API_KEY", "agentcli-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```

#### Option 3: In-Process Handler (Testing Only)
For testing/development, you can use the in-process handler directly:
```python
from agentcli.mcp import MCPServer
from agentcli.agent.registry import ToolRegistry

server = MCPServer(ToolRegistry())
# Use server.handle_request() directly
```

---

## 🔌 Supported Protocol Methods

- `initialize`: Returns protocol version `2024-11-05` and server metadata.
- `ping`: Health check.
- `tools/list`: Enumerate available tools (`file_ops`, `shell_execution`, `code_analyzer`, `web_search`, and any loaded custom plugins).
- `tools/call`: Execute a specified tool and return formatted text output.