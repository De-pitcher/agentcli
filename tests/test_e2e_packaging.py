"""Phase 11: End-to-end reliability, observability, and packaging tests.

Tests cover:
- Packaging metadata and console scripts entrypoints
- Subprocess CLI commands (--version, --help, config, sessions, mcp)
- Fresh installation config initialization and persistence across restarts
- MCP JSON-RPC protocol over stdio subprocess
- Environment resilience under TERM=dumb and NO_COLOR=1
- Clean offline / hermetic execution
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from agentcli.config import Config, load_config
from agentcli.memory.store import MemoryStore


def _run_cli(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Helper to run agentcli CLI entrypoint via python -m agentcli."""
    cmd = [sys.executable, "-m", "agentcli", *args]
    run_env = dict(os.environ)
    run_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=run_env,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


class TestPackagingMetadata:
    """Validate pyproject.toml packaging metadata and entrypoints."""

    def test_pyproject_metadata(self) -> None:
        root_dir = Path(__file__).parent.parent
        pyproject_path = root_dir / "pyproject.toml"
        assert pyproject_path.exists(), "pyproject.toml must exist at repo root"

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        project = data.get("project", {})
        assert project.get("name") == "agentcli"
        assert project.get("version") == "1.0.0"
        assert project.get("requires-python") == ">=3.11"
        assert "agentcli" in project.get("scripts", {})
        assert project["scripts"]["agentcli"] == "agentcli.cli:main"

    def test_package_import_and_version(self) -> None:
        import agentcli

        assert hasattr(agentcli, "__version__")
        assert agentcli.__version__ == "1.0.0"


class TestCLIEntrypoints:
    """Validate console script and python -m agentcli subprocess invocations."""

    def test_cli_version(self) -> None:
        res = _run_cli(["--version"])
        assert res.returncode == 0
        assert "1.0.0" in res.stdout

    def test_cli_help(self) -> None:
        res = _run_cli(["--help"])
        assert res.returncode == 0
        assert "chat" in res.stdout
        assert "mcp" in res.stdout
        assert "sessions" in res.stdout
        assert "config" in res.stdout

    def test_cli_unknown_subcommand(self) -> None:
        res = _run_cli(["nonexistent-command"])
        assert res.returncode != 0

    def test_cli_environment_resilience_term_dumb(self) -> None:
        """Verify CLI behaves cleanly with TERM=dumb and NO_COLOR=1."""
        res = _run_cli(
            ["--version"],
            env={"TERM": "dumb", "NO_COLOR": "1"},
        )
        assert res.returncode == 0
        assert "1.0.0" in res.stdout


class TestConfigE2E:
    """Validate config init and show in isolated environments."""

    def test_config_init_and_show_isolated(self, tmp_path: Path) -> None:
        # 1. Run config init --local in fresh directory
        res_init = _run_cli(["config", "init", "--local"], cwd=tmp_path)
        assert res_init.returncode == 0
        cfg_file = tmp_path / "agentcli.toml"
        assert cfg_file.exists(), "config init --local should create agentcli.toml"

        # 2. Verify created config parses as valid TOML
        with open(cfg_file, "rb") as f:
            cfg_data = tomllib.load(f)
        assert "openrouter" in cfg_data

        # 3. Second run should not overwrite
        res_init2 = _run_cli(["config", "init", "--local"], cwd=tmp_path)
        assert res_init2.returncode == 0
        assert "already exists" in res_init2.stdout

        # 4. Run config show
        res_show = _run_cli(["config", "show"], cwd=tmp_path)
        assert res_show.returncode == 0
        assert "openrouter.default_model" in res_show.stdout
        assert "memory.enabled" in res_show.stdout


class TestSessionsPersistenceE2E:
    """Validate session creation, persistence, show, list, and clear across process restarts."""

    def test_session_lifecycle_across_subprocesses(self, tmp_path: Path) -> None:
        db_path = tmp_path / "e2e_sessions.db"
        cfg_path = tmp_path / "agentcli.toml"
        cfg_path.write_text(
            f"""
[openrouter]
api_key_env = "OPENROUTER_API_KEY"
default_model = "google/gemma-4-31b-it:free"

[memory]
enabled = true
db_path = "{db_path.as_posix()}"
""",
            encoding="utf-8",
        )

        # Pre-seed session using MemoryStore directly
        store = MemoryStore(db_path)
        session_id = "sess-e2e-001"
        store.create_session(session_id, title="E2E Packaging Session")
        store.append_message(session_id, "user", "What is the capital of France?", token_count=7)
        store.append_message(
            session_id, "assistant", "Paris is the capital of France.", token_count=8
        )
        store.close()

        # 1. List sessions via subprocess
        res_list = _run_cli(["sessions", "list"], cwd=tmp_path)
        assert res_list.returncode == 0
        assert session_id in res_list.stdout
        assert "E2E Packaging" in res_list.stdout

        # 2. Show session details via subprocess
        res_show = _run_cli(["sessions", "show", session_id], cwd=tmp_path)
        assert res_show.returncode == 0
        assert session_id in res_show.stdout
        assert "What is the capital of France?" in res_show.stdout
        assert "Paris is the capital of France." in res_show.stdout

        # 3. Clear sessions via subprocess with --yes
        res_clear = _run_cli(["sessions", "clear", "--yes"], cwd=tmp_path)
        assert res_clear.returncode == 0
        assert "Cleared 1 stored session" in res_clear.stdout

        # 4. Verify empty list after clear
        res_list_after = _run_cli(["sessions", "list"], cwd=tmp_path)
        assert res_list_after.returncode == 0
        assert "No saved sessions found." in res_list_after.stdout


class TestMCPStdioProtocolE2E:
    """Validate MCP JSON-RPC protocol communication over stdio subprocess."""

    def test_mcp_ping_and_tools_list_stdio(self) -> None:
        ping_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        input_payload = f"{ping_req}\n{tools_req}\n"

        res = _run_cli(["mcp"], input_text=input_payload)
        assert res.returncode == 0

        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        assert len(lines) >= 2

        # Verify ping response
        pong_data = json.loads(lines[0])
        assert pong_data.get("id") == 1
        assert pong_data.get("result") == {}

        # Verify tools/list response
        tools_data = json.loads(lines[1])
        assert tools_data.get("id") == 2
        tools_list = tools_data.get("result", {}).get("tools", [])
        tool_names = [t.get("name") for t in tools_list]
        assert "file_ops" in tool_names
        assert "code_analyzer" in tool_names


class TestHermeticExecution:
    """Verify hermetic offline behavior without external network."""

    def test_config_load_offline(self) -> None:
        config: Config = load_config()
        assert config.openrouter.default_model is not None
        assert config.agent_loop.max_iterations > 0
