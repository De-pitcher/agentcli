"""Shell Execution sub-agent.

Provides direct process execution with allowlist/denylist filtering,
output bounding, timeout enforcement, environment variable sanitization,
and cross-platform built-in shims (pwd, cwd, ls, dir, echo, whoami, which).
"""

from __future__ import annotations

import asyncio
import getpass
import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SubAgent, SubAgentResult, SubAgentTask, SubAgentType

if TYPE_CHECKING:
    from .bus import MessageBus

# Environment variables that could be used for code injection or privilege escalation
DANGEROUS_ENV_VARS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "BASH_ENV",
        "ENV",
        "PERL5OPT",
        "RUBYOPT",
    }
)

DEFAULT_DENYLIST: list[str] = [
    "rm",
    "del",
    "erase",
    "rmdir",
    "rd",
    "format",
    "mkfs",
    "dd",
    "fdisk",
    "parted",
    "shutdown",
    "reboot",
    "poweroff",
    "init",
    "chmod",
    "chown",
    "sudo",
    "su",
    "powershell",
    "cmd",
    "bash",
    "sh",
    "zsh",
]


class ShellExecutionAgent(SubAgent):
    """Sub-agent for executing commands safely with cross-platform compatibility.

    Implements security checks and platform shims through:
    - Direct binary execution (create_subprocess_exec, no shell=True)
    - Cross-platform built-in shims for standard commands (pwd, cwd, ls, dir, echo, whoami, which)
    - Command allowlist/denylist
    - Dangerous environment variable rejection
    - Output size bounding
    - Command timeout enforcement
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        super().__init__(SubAgentType.SHELL_EXECUTION, config, message_bus)
        # Security configuration
        self.allowlist: list[str] = list(self.config.get("allowlist", []))
        custom_denylist = self.config.get("denylist")
        if custom_denylist is not None:
            self.denylist: list[str] = list(custom_denylist)
        else:
            self.denylist = list(DEFAULT_DENYLIST)

        self.max_output_bytes: int = self.config.get("max_output_bytes", 1024 * 1024)  # 1MB
        self.command_timeout: float = self.config.get("command_timeout", 30.0)
        self.working_dir: str = self.config.get("working_dir", os.getcwd())
        # Security mode: "allowlist" or "denylist"
        self.security_mode: str = self.config.get("security_mode", "denylist")

    def _validate_command(self, command: str) -> tuple[bool, str, list[str]]:
        """Validate a command against allowlist/denylist and parse parts.

        Returns:
            Tuple of (is_allowed, reason, parts)
        """
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return False, f"Invalid command syntax: {e}", []

        if not parts:
            return False, "Empty command", []

        base_command = os.path.basename(parts[0]).lower()

        # Strip Windows executable extensions if present for matching
        if base_command.endswith((".exe", ".cmd", ".bat")):
            base_name_no_ext = os.path.splitext(base_command)[0]
        else:
            base_name_no_ext = base_command

        if self.security_mode == "allowlist":
            allowed = any(
                base_command == allowed.lower() or base_name_no_ext == allowed.lower()
                for allowed in self.allowlist
            )
            if not allowed:
                return False, f"Command '{base_command}' not in allowlist", parts
            return True, "Allowed by allowlist", parts
        else:
            denied = any(
                base_command == denied.lower() or base_name_no_ext == denied.lower()
                for denied in self.denylist
            )
            if denied:
                return False, f"Command '{base_command}' is denied", parts
            return True, "Not in denylist", parts

    def _execute_builtin_shim(
        self, parts: list[str], working_dir: str, task_id: str
    ) -> SubAgentResult | None:
        """Handle common built-in shell commands cross-platform without spawning external binaries."""
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("pwd", "cwd"):
            out = str(Path(working_dir).resolve()) + "\n"
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={"stdout": out, "stderr": "", "returncode": 0, "truncated": False},
            )

        if cmd == "whoami":
            out = getpass.getuser() + "\n"
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={"stdout": out, "stderr": "", "returncode": 0, "truncated": False},
            )

        if cmd == "echo":
            out = " ".join(args) + "\n"
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=True,
                output={"stdout": out, "stderr": "", "returncode": 0, "truncated": False},
            )

        if cmd in ("which", "where"):
            if not args:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=False,
                    output={"stdout": "", "stderr": "Usage: which <command>\n", "returncode": 1, "truncated": False},
                    error="Usage: which <command>",
                )
            target = args[0]
            loc = shutil.which(target)
            if loc:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"stdout": f"{loc}\n", "stderr": "", "returncode": 0, "truncated": False},
                )
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                output={"stdout": "", "stderr": f"{target} not found\n", "returncode": 1, "truncated": False},
                error=f"{target} not found",
            )

        # Fallback shim for ls/dir and cat/type when native binary does not exist
        if cmd in ("ls", "dir") and shutil.which(cmd) is None:
            target_path = Path(working_dir)
            if args:
                potential_path = Path(working_dir) / args[-1]
                if potential_path.is_dir():
                    target_path = potential_path
            try:
                entries = [p.name + ("/" if p.is_dir() else "") for p in target_path.iterdir()]
                out = "\n".join(sorted(entries)) + "\n"
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=True,
                    output={"stdout": out, "stderr": "", "returncode": 0, "truncated": False},
                )
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=False,
                    output={"stdout": "", "stderr": str(exc), "returncode": 1, "truncated": False},
                    error=str(exc),
                )

        if cmd in ("cat", "type") and shutil.which(cmd) is None:
            if not args:
                return SubAgentResult(
                    task_id=task_id,
                    agent_type=self.agent_type,
                    success=False,
                    output={"stdout": "", "stderr": "Usage: cat <file>\n", "returncode": 1, "truncated": False},
                    error="Usage: cat <file>",
                )
            file_path = Path(working_dir) / args[0]
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    return SubAgentResult(
                        task_id=task_id,
                        agent_type=self.agent_type,
                        success=True,
                        output={"stdout": content, "stderr": "", "returncode": 0, "truncated": False},
                    )
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    return SubAgentResult(
                        task_id=task_id,
                        agent_type=self.agent_type,
                        success=False,
                        output={"stdout": "", "stderr": str(exc), "returncode": 1, "truncated": False},
                        error=str(exc),
                    )
            return SubAgentResult(
                task_id=task_id,
                agent_type=self.agent_type,
                success=False,
                output={"stdout": "", "stderr": f"File not found: {args[0]}\n", "returncode": 1, "truncated": False},
                error=f"File not found: {args[0]}",
            )

        return None

    async def run(self, task: SubAgentTask) -> SubAgentResult:
        """Execute a command directly without shell interpolation.

        Expected payload:
            - command: command string to execute
            - timeout: optional per-command timeout override
            - working_dir: optional working directory override
            - env: optional environment variables dict
        """
        payload = task.payload
        command = payload.get("command", "").strip()

        if not command:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error="No command specified",
            )

        # Validate command
        allowed, reason, parts = self._validate_command(command)
        if not allowed:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Command rejected: {reason}",
            )

        # Prepare execution parameters
        timeout = payload.get("timeout", self.command_timeout)
        working_dir = payload.get("working_dir", self.working_dir)

        # Check for cross-platform built-in shims first (e.g. pwd, whoami, echo, ls/dir fallback)
        shim_result = self._execute_builtin_shim(parts, working_dir, task.id)
        if shim_result is not None:
            return shim_result

        # Validate environment variables
        env = os.environ.copy()
        user_env = payload.get("env")
        if user_env:
            for k in user_env:
                if k.upper() in DANGEROUS_ENV_VARS:
                    return SubAgentResult(
                        task_id=task.id,
                        agent_type=self.agent_type,
                        success=False,
                        error=f"Dangerous environment variable override rejected: {k}",
                    )
            env.update(user_env)

        # Find executable path
        executable = shutil.which(parts[0])
        if executable is None:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Command not found: '{parts[0]}'",
            )

        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *parts[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                return SubAgentResult(
                    task_id=task.id,
                    agent_type=self.agent_type,
                    success=False,
                    error=f"Command timed out after {timeout}s",
                )

            # Decode output with size bounding
            stdout_bytes = stdout[: self.max_output_bytes]
            stderr_bytes = stderr[: self.max_output_bytes]
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            truncated = len(stdout) > self.max_output_bytes or len(stderr) > self.max_output_bytes

            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=process.returncode == 0,
                output={
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "returncode": process.returncode,
                    "truncated": truncated,
                },
                error=stderr_text if process.returncode != 0 else None,
            )
        except (OSError, ValueError) as e:
            return SubAgentResult(
                task_id=task.id,
                agent_type=self.agent_type,
                success=False,
                error=f"Execution failed: {e}",
            )