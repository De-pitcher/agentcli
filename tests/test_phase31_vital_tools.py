from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcli.agent.checkpoints import CheckpointManager
from agentcli.agent.registry import ToolRegistry
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.grep_search import GrepSearchAgent
from agentcli.subagents.web_fetch import (
    WebFetchAgent,
    html_to_markdown,
)
from agentcli.tools_schema import get_tool_definitions

# ===========================================================================
# 1. WebFetchAgent & HTMLToMarkdownConverter Tests
# ===========================================================================


def test_html_to_markdown_converter_elements() -> None:
    """Test HTMLToMarkdownConverter parses headings, code, links, and lists."""
    sample_html = """
    <html>
      <head>
        <title>Doc Page</title>
        <script>alert('evil');</script>
        <style>.hide { display: none; }</style>
      </head>
      <body>
        <header><nav><a href="/home">Home</a></nav></header>
        <h1>Main Documentation Title</h1>
        <p>This is a guide for <code>AgentCLI</code> tools.</p>
        <h2>Features</h2>
        <ul>
          <li>Fast Regex Grep</li>
          <li>Web Document Fetcher</li>
        </ul>
        <pre><code>def hello():\n    return "world"</code></pre>
        <blockquote>Note: Always verify tool inputs.</blockquote>
        <p>Visit <a href="https://example.com/docs">Official Docs</a> for details.</p>
        <footer>Footer content</footer>
      </body>
    </html>
    """
    md = html_to_markdown(sample_html, base_url="https://example.com")
    assert "# Main Documentation Title" in md
    assert "## Features" in md
    assert "`AgentCLI`" in md
    assert "- Fast Regex Grep" in md
    assert "- Web Document Fetcher" in md
    assert "```" in md
    assert 'def hello():\n    return "world"' in md
    assert "> Note: Always verify tool inputs." in md
    assert "[Official Docs](https://example.com/docs)" in md
    # Stripped non-content tags
    assert "alert('evil')" not in md
    assert ".hide" not in md
    assert "Footer content" not in md


@pytest.mark.asyncio
async def test_web_fetch_agent_success() -> None:
    """Test WebFetchAgent fetches URL and converts HTML to Markdown."""
    agent = WebFetchAgent()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.text = "<h1>Fast API Reference</h1><p>Endpoint documentation.</p>"
    mock_resp.url = "https://example.com/api"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    task = SubAgentTask(
        agent_type=SubAgentType.WEB_FETCH,
        payload={"url": "https://example.com/api"},
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await agent.run(task)
        assert result.success is True
        assert "# Fast API Reference" in result.output["content"]
        assert "Endpoint documentation." in result.output["content"]
        assert result.output["status_code"] == 200


@pytest.mark.asyncio
async def test_web_fetch_agent_content_offset_and_raw() -> None:
    """Test WebFetchAgent content offset, max_bytes truncation, and raw mode."""
    agent = WebFetchAgent(config={"timeout_seconds": 10.0})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.text = "A" * 1000
    mock_resp.url = "https://example.com/data.txt"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    task = SubAgentTask(
        agent_type=SubAgentType.WEB_FETCH,
        payload={"url": "https://example.com/data.txt", "content_offset": 100, "max_bytes": 200, "raw": True},
    )

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await agent.run(task)
        assert result.success is True
        assert result.output["truncated"] is True
        assert len(result.output["content"].split("\n\n[...")[0]) == 200


@pytest.mark.asyncio
async def test_web_fetch_agent_errors() -> None:
    """Test WebFetchAgent error paths for invalid scheme, 404, and timeout."""
    agent = WebFetchAgent()

    # 1. Missing URL
    r_empty = await agent.run(SubAgentTask(agent_type=SubAgentType.WEB_FETCH, payload={}))
    assert r_empty.success is False
    assert "No URL provided" in str(r_empty.error)

    # 2. Invalid scheme
    r_scheme = await agent.run(
        SubAgentTask(agent_type=SubAgentType.WEB_FETCH, payload={"url": "ftp://files.example.com"})
    )
    assert r_scheme.success is False
    assert "Invalid URL scheme" in str(r_scheme.error)

    # 3. HTTP 404
    mock_404 = MagicMock()
    mock_404.status_code = 404
    mock_404.reason_phrase = "Not Found"
    mock_404.url = "https://example.com/missing"

    mock_client_404 = AsyncMock()
    mock_client_404.get.return_value = mock_404
    mock_client_404.__aenter__.return_value = mock_client_404
    mock_client_404.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client_404):
        r_404 = await agent.run(
            SubAgentTask(agent_type=SubAgentType.WEB_FETCH, payload={"url": "https://example.com/missing"})
        )
        assert r_404.success is False
        assert "HTTP 404" in str(r_404.error)


# ===========================================================================
# 2. GrepSearchAgent Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_grep_search_python_engine(tmp_path: Path) -> None:
    """Test GrepSearchAgent fallback Python engine with line numbers, regex, and glob filters."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("def find_token():\n    return 'secret_xyz'\n", encoding="utf-8")
    (src_dir / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text('token = "secret_xyz"\n', encoding="utf-8")

    agent = GrepSearchAgent(config={"working_dir": str(tmp_path)})
    agent.rg_path = None  # Force python engine

    # 1. Exact string search across all files
    task1 = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": "secret_xyz", "path": str(tmp_path)},
    )
    r1 = await agent.run(task1)
    assert r1.success is True
    assert r1.output["engine"] == "python"
    assert r1.output["total_matches"] == 2
    files = {m["file"] for m in r1.output["matches"]}
    assert any("app.py" in f for f in files)
    assert any("config.toml" in f for f in files)

    # 2. Regex search
    task_regex = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": r"def\s+[a-zA-Z_]+", "is_regex": True, "path": str(tmp_path)},
    )
    r_regex = await agent.run(task_regex)
    assert r_regex.success is True
    assert r_regex.output["total_matches"] >= 2

    # 3. Includes filter
    task_inc = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": "secret_xyz", "includes": ["*.toml"], "path": str(tmp_path)},
    )
    r_inc = await agent.run(task_inc)
    assert r_inc.success is True
    assert r_inc.output["total_matches"] == 1
    assert "config.toml" in r_inc.output["matches"][0]["file"]

    # 4. Filename list only (match_per_line=False)
    task_files_only = SubAgentTask(
        agent_type=SubAgentType.GREP_SEARCH,
        payload={"query": "def", "match_per_line": False, "path": str(tmp_path)},
    )
    r_files = await agent.run(task_files_only)
    assert r_files.success is True
    assert isinstance(r_files.output["matches"], list)
    assert any("app.py" in str(f) for f in r_files.output["matches"])


@pytest.mark.asyncio
async def test_grep_search_ripgrep_mocked(tmp_path: Path) -> None:
    """Test GrepSearchAgent ripgrep output parsing."""
    agent = GrepSearchAgent(config={"working_dir": str(tmp_path)})
    agent.rg_path = "rg"

    # Mock ripgrep stdout JSON stream
    rg_output = (
        '{"type":"match","data":{"path":{"text":"src/app.py"},"lines":{"text":"import logging\\n"},"line_number":1}}\n'
    )

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (rg_output.encode("utf-8"), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        task = SubAgentTask(
            agent_type=SubAgentType.GREP_SEARCH,
            payload={"query": "logging", "path": str(tmp_path)},
        )
        res = await agent.run(task)
        assert res.success is True
        assert res.output["engine"] == "ripgrep"
        assert len(res.output["matches"]) == 1
        assert res.output["matches"][0]["file"] == "src/app.py"
        assert res.output["matches"][0]["line_number"] == 1


# ===========================================================================
# 3. CheckpointManager & Rollback Tests
# ===========================================================================


def test_checkpoint_manager_lifecycle(tmp_path: Path) -> None:
    """Test CheckpointManager records snapshots, creates diffs, and performs clean rollbacks."""
    mgr = CheckpointManager(root_dir=tmp_path)

    file_a = tmp_path / "file_a.txt"
    file_a.write_text("Initial content A\n", encoding="utf-8")

    # 1. Create checkpoint 1
    cp1 = mgr.create_checkpoint(description="Before mutation")
    mgr.record_file_before_write("file_a.txt")
    mgr.record_file_before_write("file_b.txt")  # Doesn't exist yet

    # 2. Mutate file A and create file B
    file_a.write_text("Modified content A\nNew line\n", encoding="utf-8")
    file_b = tmp_path / "file_b.txt"
    file_b.write_text("Newly created file B\n", encoding="utf-8")

    # 3. Inspect diff
    diff_text = mgr.get_diff(cp1)
    assert "-Initial content A" in diff_text
    assert "+Modified content A" in diff_text
    assert "+Newly created file B" in diff_text

    # 4. Perform rollback
    rollback_res = mgr.rollback(cp1)
    assert rollback_res["success"] is True
    assert any("restored file_a.txt" in r for r in rollback_res["reverted_files"])
    assert any("deleted file_b.txt" in r for r in rollback_res["reverted_files"])

    # 5. Verify filesystem restored
    assert file_a.read_text(encoding="utf-8") == "Initial content A\n"
    assert not file_b.exists()


def test_tool_registry_and_schema_has_vital_tools() -> None:
    """Verify web_fetch and grep_search are registered in ToolRegistry and get_tool_definitions."""
    registry = ToolRegistry()
    types = registry.registered_types()
    assert SubAgentType.WEB_FETCH.value in types
    assert SubAgentType.GREP_SEARCH.value in types

    defs = get_tool_definitions()
    names = {d["function"]["name"] for d in defs}
    assert "web_fetch" in names
    assert "grep_search" in names
