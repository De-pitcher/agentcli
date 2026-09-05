"""Autonomous project watcher and continuous TDD loop engine (Phase 22).

Watches project source/test files for modifications, executes fast test suites,
detects failures, and autonomously generates and verifies fixes in an isolated Git worktree.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent.events import LoopErrorEvent
from .agent.loop import AgentLoop
from .agent.registry import ToolRegistry
from .config import Config, WatcherConfig
from .exit_codes import ExitCode
from .routing.registry import ModelRegistry
from .routing.router import Router
from .ui.render import ConsoleRenderer
from .unicode import safe_print

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".pytest-temp",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
        ".egg-info",
        ".agentcli_worktrees",
        ".worktrees",
    }
)

WATCH_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".js",
        ".jsx",
        ".tsx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
        ".rs",
        ".go",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".java",
        ".kt",
    }
)


class FileWatcher:
    """Asynchronously monitors directories for file modifications with debouncing."""

    def __init__(
        self,
        paths: Sequence[str | Path] | None = None,
        ignored_dirs: set[str] | frozenset[str] | None = None,
        debounce_seconds: float = 1.5,
        extensions: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.paths = [Path(p).resolve() for p in (paths or ["."])]
        self.ignored_dirs = (
            set(ignored_dirs) if ignored_dirs is not None else set(DEFAULT_IGNORED_DIRS)
        )
        self.debounce_seconds = debounce_seconds
        self.extensions = set(extensions) if extensions is not None else set(WATCH_EXTENSIONS)
        self._snapshot: dict[Path, float] = {}
        self._running = False
        self._initialize_snapshot()

    def _is_dir_ignored(self, name: str) -> bool:
        if name in self.ignored_dirs:
            return True
        return any(
            name.startswith(ign)
            for ign in self.ignored_dirs
            if ign.startswith(".")
        )

    def scan(self) -> dict[Path, float]:
        """Scan watched paths and return a mapping of file path -> mtime."""
        current: dict[Path, float] = {}
        for root in self.paths:
            if not root.exists():
                continue
            if root.is_file():
                if not self.extensions or root.suffix in self.extensions:
                    try:
                        current[root] = root.stat().st_mtime
                    except OSError:
                        pass
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # Prune ignored directories in-place for efficiency
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not self._is_dir_ignored(d) and not d.startswith(".agentcli_worktree")
                ]
                d_path = Path(dirpath)
                for fname in filenames:
                    if self._is_dir_ignored(fname):
                        continue
                    f_path = d_path / fname
                    if self.extensions and f_path.suffix not in self.extensions:
                        continue
                    try:
                        current[f_path] = f_path.stat().st_mtime
                    except OSError:
                        pass
        return current

    def _initialize_snapshot(self) -> None:
        self._snapshot = self.scan()

    def detect_changes(self) -> set[Path]:
        """Compare current files with last snapshot and return changed/new/deleted paths."""
        current = self.scan()
        changed: set[Path] = set()

        # Check modified or added
        for path, mtime in current.items():
            if path not in self._snapshot or self._snapshot[path] != mtime:
                changed.add(path)

        # Check deleted
        for path in self._snapshot:
            if path not in current:
                changed.add(path)

        self._snapshot = current
        return changed

    async def watch(self, poll_interval: float = 0.5) -> AsyncIterator[set[Path]]:
        """Yield changed files batches debounced by debounce_seconds."""
        self._running = True
        accumulated_changes: set[Path] = set()
        last_change_time: float | None = None

        while self._running:
            changes = self.detect_changes()
            now = time.time()
            if changes:
                accumulated_changes.update(changes)
                last_change_time = now

            if (
                accumulated_changes
                and last_change_time is not None
                and (now - last_change_time) >= self.debounce_seconds
            ):
                yield accumulated_changes
                accumulated_changes = set()
                last_change_time = None

            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        """Stop watching."""
        self._running = False


class WorktreeManager:
    """Manages temporary isolated git worktrees for safe autonomous code repair."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = (root_dir or Path.cwd()).resolve()

    def is_git_repo(self) -> bool:
        """Check if root_dir is inside a git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:  # noqa: BLE001
            return False

    async def create_worktree(self, branch_prefix: str = "agentcli-repair") -> tuple[Path, str]:
        """Create an isolated worktree on a new temporary branch."""
        ts = int(time.time())
        unique_id = uuid.uuid4().hex[:6]
        branch_name = f"{branch_prefix}-{ts}-{unique_id}"
        worktree_dir = self.root_dir / ".agentcli_worktrees" / branch_name
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_dir),
            "HEAD",
            cwd=str(self.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = (
                stderr.decode(errors="replace").strip()
                or stdout.decode(errors="replace").strip()
            )
            raise RuntimeError(f"Failed to create git worktree: {err_msg}")

        return worktree_dir, branch_name

    async def remove_worktree(self, worktree_dir: Path, branch_name: str | None = None) -> bool:
        """Remove a temporary worktree and delete its branch."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "remove",
                "--force",
                str(worktree_dir),
                cwd=str(self.root_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error removing git worktree: %s", exc)

        if worktree_dir.exists():
            try:
                shutil.rmtree(worktree_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001, S110
                pass

        if branch_name:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "branch",
                    "-D",
                    branch_name,
                    cwd=str(self.root_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error deleting repair branch %s: %s", branch_name, exc)

        # Remove parent worktree directory if empty
        parent_dir = self.root_dir / ".agentcli_worktrees"
        if parent_dir.exists():
            try:
                if not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except Exception:  # noqa: BLE001, S110
                pass

        return True

    async def get_patch(self, worktree_dir: Path) -> str:
        """Extract git diff patch generated inside worktree."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "HEAD",
            cwd=str(worktree_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace")

    async def apply_patch(self, patch_diff: str) -> bool:
        """Apply patch to main root directory."""
        if not patch_diff.strip():
            return False
        proc = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            "--whitespace=nowarn",
            "-",
            cwd=str(self.root_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate(input=patch_diff.encode("utf-8"))
        if proc.returncode == 0:
            return True
        logger.warning(
            "git apply failed: %s",
            stderr.decode(errors="replace"),
        )
        return False


@dataclass
class TestExecutionResult:
    __test__ = False
    passed: bool
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    failure_summary: str = ""


class ContinuousTDDRunner:
    """Coordinates file watching, continuous test runs, and isolated worktree repairs."""

    def __init__(
        self,
        config: Config,
        watcher_config: WatcherConfig | None = None,
        root_dir: Path | None = None,
        renderer: ConsoleRenderer | None = None,
    ) -> None:
        self.config = config
        self.watcher_config = watcher_config or config.watcher
        self.root_dir = (root_dir or Path.cwd()).resolve()
        self.renderer = renderer or ConsoleRenderer()
        self.watcher = FileWatcher(
            paths=self.watcher_config.paths,
            ignored_dirs=DEFAULT_IGNORED_DIRS,
            debounce_seconds=self.watcher_config.debounce_seconds,
        )
        self.worktree_manager = WorktreeManager(self.root_dir)
        self.cumulative_cost_usd: float = 0.0
        self._is_running: bool = False

    def stop(self) -> None:
        """Signal the runner and watcher to stop."""
        self._is_running = False
        self.watcher.stop()

    def _extract_failure_summary(self, stdout: str, stderr: str) -> str:
        combined = (stdout + "\n" + stderr).strip()
        lines = combined.splitlines()
        error_lines = []
        for line in lines[-50:]:
            if any(
                kw in line
                for kw in ["FAILED", "FAILURES", "Error", "Exception", "AssertionError", "E   "]
            ):
                error_lines.append(line)
        if error_lines:
            return "\n".join(error_lines[-10:])
        return combined[-500:] if combined else "Test suite failed with non-zero exit code"

    async def run_tests(
        self, cwd: Path | None = None, timeout: float = 120.0
    ) -> TestExecutionResult:
        """Run the configured test command in the specified directory."""
        target_dir = cwd or self.root_dir
        cmd = self.watcher_config.test_command
        start = time.time()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(target_dir),
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            duration = time.time() - start
            stdout_str = stdout_b.decode(errors="replace")
            stderr_str = stderr_b.decode(errors="replace")
            passed = proc.returncode == 0
            failure_summary = ""
            if not passed:
                failure_summary = self._extract_failure_summary(stdout_str, stderr_str)
            return TestExecutionResult(
                passed=passed,
                return_code=proc.returncode if proc.returncode is not None else 0,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_seconds=duration,
                failure_summary=failure_summary,
            )
        except TimeoutError:
            duration = time.time() - start
            return TestExecutionResult(
                passed=False,
                return_code=-1,
                stdout="",
                stderr=f"Test command timed out after {timeout} seconds",
                duration_seconds=duration,
                failure_summary=f"Test command timed out after {timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            duration = time.time() - start
            return TestExecutionResult(
                passed=False,
                return_code=-1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                failure_summary=f"Error executing test command: {exc}",
            )

    async def attempt_repair(
        self,
        failure: TestExecutionResult,
        changed_files: set[Path] | None = None,
    ) -> bool:
        """Autonomously repair a test failure inside an isolated Git worktree."""
        max_cost = self.watcher_config.max_cost_usd or self.config.routing.max_cost_usd
        if max_cost is not None and self.cumulative_cost_usd >= max_cost:
            self._log_warning(
                f"Cumulative cost ceiling reached (${self.cumulative_cost_usd:.4f} >= ${max_cost:.4f}). Skipping autonomous repair."
            )
            return False

        if not self.worktree_manager.is_git_repo():
            self._log_warning(
                "Not inside a git repository. Autonomous worktree repair requires git."
            )
            return False

        self._log_info("🔧 Creating isolated git worktree for autonomous repair...")
        worktree_dir: Path | None = None
        branch_name: str | None = None

        try:
            worktree_dir, branch_name = await self.worktree_manager.create_worktree()
            self._log_info(f"Worktree created at {worktree_dir} (branch: {branch_name})")

            repair_config = Config(
                openrouter=self.config.openrouter,
                app=self.config.app,
                routing=self.config.routing,
                subagents=self.config.subagents,
                agent_loop=self.config.agent_loop,
                memory=self.config.memory,
                mcp_servers=self.config.mcp_servers,
                watcher=self.watcher_config,
            )
            repair_config.subagents.allow_write = True

            tool_configs: dict[str, dict[str, Any]] = {
                "file_ops": {
                    "working_dir": str(worktree_dir),
                    "allow_write": True,
                    "read_only": False,
                },
                "shell_execution": {
                    "working_dir": str(worktree_dir),
                },
                "workspace": {
                    "path": str(worktree_dir),
                },
            }
            registry = ToolRegistry(tool_configs=tool_configs, config=repair_config)

            router: Router | None = None
            if repair_config.routing.enabled and not self.watcher_config.model:
                model_registry = ModelRegistry(repair_config.routing)
                router = Router(
                    model_registry,
                    repair_config.routing.max_fallbacks,
                    budget_tier=self.watcher_config.budget_tier
                    or repair_config.routing.budget_tier,
                )

            changed_names = [p.name for p in changed_files] if changed_files else []
            changed_str = (
                f"Modified files: {', '.join(changed_names)}\n\n" if changed_names else ""
            )

            goal = (
                f"Fix the test failure in continuous TDD loop.\n\n"
                f"{changed_str}"
                f"Test Command: {self.watcher_config.test_command}\n\n"
                f"Failure Details:\n{failure.stdout[-2500:]}\n{failure.stderr[-1500:]}"
            )

            max_iter = self.watcher_config.max_repair_iterations
            loop = AgentLoop(
                goal=goal,
                registry=registry,
                router=router,
                max_iterations=max_iter,
                plan_model=self.watcher_config.model,
                reflect_model=self.watcher_config.model,
                config=repair_config,
                max_cost_usd=max_cost,
            )

            self._log_info(
                f"🤖 Running AgentLoop repair in worktree ({max_iter} max iterations)..."
            )
            async for event in loop.run():
                self.renderer.render_loop_event(event, verbose=False)
                if isinstance(event, LoopErrorEvent):
                    self._log_error(f"Repair loop error: {event.error}")

            cost = getattr(loop, "cumulative_cost_usd", 0.0)
            self.cumulative_cost_usd += cost

            self._log_info("🧪 Verifying repair inside isolated worktree...")
            verify_result = await self.run_tests(cwd=worktree_dir)

            if verify_result.passed:
                self._log_success(
                    f"🎉 Repair succeeded! Tests pass in worktree ({verify_result.duration_seconds:.2f}s)."
                )
                patch = await self.worktree_manager.get_patch(worktree_dir)
                if not patch.strip():
                    self._log_info("No file changes were made by the repair agent.")
                    return True

                if self.watcher_config.auto_apply:
                    self._log_info("Applying verified patch to working tree...")
                    applied = await self.worktree_manager.apply_patch(patch)
                    if applied:
                        self._log_success("✅ Verified patch successfully applied to project!")
                    else:
                        self._log_warning(
                            "Could not auto-apply git patch. Please inspect worktree patch."
                        )
                else:
                    self._log_info(
                        f"Patch available from repair branch '{branch_name}'. Run with --auto-apply to merge automatically."
                    )
                    if self.renderer.is_rich_enabled:
                        self.renderer.console.print(f"\n[dim]{patch[:1000]}[/dim]\n")
                    else:
                        print(f"\n{patch[:1000]}\n")
                return True
            else:
                self._log_error(
                    f"❌ Repair attempt did not resolve test failure ({verify_result.failure_summary})."
                )
                return False

        except Exception as exc:  # noqa: BLE001
            self._log_error(f"Error during autonomous repair: {exc}")
            return False
        finally:
            if worktree_dir and branch_name:
                self._log_info("Cleaning up temporary worktree...")
                await self.worktree_manager.remove_worktree(worktree_dir, branch_name)

    async def run(self, run_initial: bool = True) -> int:
        """Run continuous watch loop."""
        self._is_running = True
        self._log_info(
            f"👀 agentcli watch started — monitoring {', '.join(str(p) for p in self.watcher_config.paths)}"
        )
        self._log_info(
            f"Test command: '{self.watcher_config.test_command}' | "
            f"Debounce: {self.watcher_config.debounce_seconds}s | "
            f"Cooldown: {self.watcher_config.cooldown_seconds}s"
        )
        if self.watcher_config.auto_apply:
            self._log_info(
                "⚡ Auto-apply enabled: Verified fixes will be applied to working tree automatically."
            )

        if run_initial:
            self._log_info("▶️ Running initial test suite...")
            init_res = await self.run_tests()
            if init_res.passed:
                self._log_success(
                    f"✅ Initial test suite passed ({init_res.duration_seconds:.2f}s). Waiting for changes..."
                )
            else:
                self._log_error(f"❌ Initial test suite failed ({init_res.failure_summary}).")
                await self.attempt_repair(init_res)
                if self.watcher_config.cooldown_seconds > 0:
                    await asyncio.sleep(self.watcher_config.cooldown_seconds)

        try:
            async for changed_files in self.watcher.watch():
                if not self._is_running:
                    break
                names = [p.name for p in changed_files]
                self._log_info(
                    f"🔄 Detected changes in {len(changed_files)} file(s): {', '.join(names[:5])}"
                )

                # Auto-sync semantic vector index for changed files
                if getattr(self.config, "embeddings", None) and self.config.embeddings.enabled:
                    try:
                        from .embeddings import VectorIndex, VectorStore

                        v_store = VectorStore(self.config.embeddings.cache_path or None)
                        v_index = VectorIndex(store=v_store)
                        for cf in changed_files:
                            await v_index.sync_file(cf)
                        v_store.close()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Incremental vector sync notice: %s", exc)

                test_res = await self.run_tests()
                if test_res.passed:
                    self._log_success(f"✅ Tests passed ({test_res.duration_seconds:.2f}s).")
                else:
                    self._log_error(f"❌ Tests failed ({test_res.failure_summary}).")
                    await self.attempt_repair(test_res, changed_files=changed_files)

                if self.watcher_config.cooldown_seconds > 0:
                    self._log_info(f"⏳ Cooldown ({self.watcher_config.cooldown_seconds}s)...")
                    await asyncio.sleep(self.watcher_config.cooldown_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            self._is_running = False
            self.watcher.stop()
            self._log_info("Watch daemon stopped.")
        return ExitCode.SUCCESS

    def _log_info(self, msg: str) -> None:
        if self.renderer.is_rich_enabled:
            self.renderer.console.print(f"[cyan][watcher][/cyan] {msg}")
        else:
            safe_print(f"[watcher] {msg}")

    def _log_success(self, msg: str) -> None:
        if self.renderer.is_rich_enabled:
            self.renderer.console.print(f"[bold green][watcher][/bold green] {msg}")
        else:
            safe_print(f"[watcher] {msg}")

    def _log_warning(self, msg: str) -> None:
        if self.renderer.is_rich_enabled:
            self.renderer.console.print(f"[bold yellow][watcher][/bold yellow] {msg}")
        else:
            safe_print(f"[watcher] {msg}")

    def _log_error(self, msg: str) -> None:
        if self.renderer.is_rich_enabled:
            self.renderer.console.print(f"[bold red][watcher][/bold red] {msg}")
        else:
            safe_print(f"[watcher] {msg}")


async def run_watch(args: argparse.Namespace, config: Config) -> int:
    """Entrypoint for `agentcli watch` command."""
    watcher_cfg = config.watcher
    if getattr(args, "test_cmd", None):
        watcher_cfg.test_command = args.test_cmd
    if getattr(args, "debounce", None) is not None:
        watcher_cfg.debounce_seconds = args.debounce
    if getattr(args, "cooldown", None) is not None:
        watcher_cfg.cooldown_seconds = args.cooldown
    if getattr(args, "auto_apply", False):
        watcher_cfg.auto_apply = True
    if getattr(args, "max_cost", None) is not None:
        watcher_cfg.max_cost_usd = args.max_cost
    if getattr(args, "budget", None):
        watcher_cfg.budget_tier = args.budget
    if getattr(args, "model", None):
        watcher_cfg.model = args.model
    if getattr(args, "max_iterations", None) is not None:
        watcher_cfg.max_repair_iterations = args.max_iterations
    if getattr(args, "paths", None):
        watcher_cfg.paths = args.paths

    renderer = ConsoleRenderer(
        plain=getattr(args, "plain", False),
        no_color=getattr(args, "no_color", False),
    )

    runner = ContinuousTDDRunner(
        config=config,
        watcher_config=watcher_cfg,
        renderer=renderer,
    )

    try:
        return await runner.run(run_initial=not getattr(args, "no_initial", False))
    except (KeyboardInterrupt, asyncio.CancelledError):
        runner.stop()
        return ExitCode.USER_INTERRUPT
