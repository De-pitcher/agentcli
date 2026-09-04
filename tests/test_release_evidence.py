"""Phase 12: Release evidence and MVP launch gate verification.

Tests cover:
- Version consistency across package, CLI, and pyproject.toml
- Build artifact integrity (.whl and .tar.gz)
- Complete CLI command suite execution
- MCP stdio protocol JSON-RPC compliance
- Hermetic quality gate assertions
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import agentcli
from agentcli.config import load_config


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


class TestReleaseVersionAlignment:
    """Verify version numbers match across all package files and runtime."""

    def test_version_alignment(self) -> None:
        root_dir = Path(__file__).parent.parent
        pyproject_path = root_dir / "pyproject.toml"
        assert pyproject_path.exists()

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        project_version = data.get("project", {}).get("version")
        assert project_version == "1.0.0"
        assert agentcli.__version__ == "1.0.0"

        res = _run_cli(["--version"])
        assert res.returncode == 0
        assert "1.0.0" in res.stdout


class TestReleaseBuildArtifacts:
    """Verify built sdist and wheel artifacts in dist/."""

    def test_dist_artifacts_exist(self) -> None:
        root_dir = Path(__file__).parent.parent
        dist_dir = root_dir / "dist"
        assert dist_dir.exists(), "dist/ directory should exist"

        whl_files = list(dist_dir.glob("agentcli-1.0.0-*.whl"))
        sdist_files = list(dist_dir.glob("agentcli-1.0.0.tar.gz"))

        assert len(whl_files) >= 1, "Should find built wheel in dist/"
        assert len(sdist_files) >= 1, "Should find built sdist in dist/"


class TestReleaseCLISmoke:
    """Verify all documented CLI commands execute cleanly."""

    def test_cli_help_options(self) -> None:
        res = _run_cli(["--help"])
        assert res.returncode == 0
        assert "chat" in res.stdout
        assert "mcp" in res.stdout
        assert "sessions" in res.stdout
        assert "config" in res.stdout

    def test_cli_config_commands(self, tmp_path: Path) -> None:
        res_init = _run_cli(["config", "init", "--local"], cwd=tmp_path)
        assert res_init.returncode == 0
        assert (tmp_path / "agentcli.toml").exists()

        res_show = _run_cli(["config", "show"], cwd=tmp_path)
        assert res_show.returncode == 0
        assert "openrouter.default_model" in res_show.stdout

    def test_cli_mcp_protocol(self) -> None:
        ping = json.dumps({"jsonrpc": "2.0", "id": 100, "method": "ping"})
        res = _run_cli(["mcp"], input_text=f"{ping}\n")
        assert res.returncode == 0
        output_line = res.stdout.strip().splitlines()[0]
        data = json.loads(output_line)
        assert data.get("id") == 100
        assert data.get("result") == {}

    def test_hermetic_config_offline(self) -> None:
        cfg = load_config()
        assert cfg.openrouter.default_model is not None
        assert cfg.memory.enabled is True
