"""Immutable local-run capabilities; trusted execution is NOT an OS sandbox."""

from __future__ import annotations

import contextvars
import ipaddress
import os
import shlex
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse


class PolicyDenied(PermissionError):
    """A request exceeds the capabilities granted by the host."""


@dataclass(frozen=True)
class RunPolicy:
    root: Path = field(default_factory=Path.cwd)
    allow_write: bool = False
    allow_execution: bool = False
    allow_discard: bool = False
    allow_network: bool = True
    readable_roots: tuple[Path, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    max_output_bytes: int = 65536
    timeout_seconds: float = 30.0
    max_tasks: int = 2
    max_retained_tasks: int = 32
    max_file_bytes: int = 1048576

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(os.path.abspath(self.root)))
        object.__setattr__(
            self, "readable_roots", tuple(Path(os.path.abspath(p)) for p in self.readable_roots)
        )
        if (
            min(self.max_output_bytes, self.max_file_bytes, self.max_tasks, self.max_retained_tasks)
            <= 0
        ):
            raise ValueError("Resource limits must be positive")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("Deadline must be between zero and 300 seconds")

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = active_policy.set(self)
        try:
            yield
        finally:
            active_policy.reset(token)

    @classmethod
    def from_config(cls, config: Any = None, root: str | Path | None = None) -> RunPolicy:
        if isinstance(config, dict):
            writable = bool(config.get("allow_write", False)) and not config.get("read_only", False)
            return cls(
                root=Path(
                    root or config.get("working_dir") or config.get("workspace_dir") or Path.cwd()
                ),
                allow_write=writable,
                allow_execution=bool(config.get("allow_execution", False)),
                allow_discard=bool(config.get("allow_discard", False)),
                allow_network=bool(config.get("allow_network", True)),
                readable_roots=tuple(Path(p) for p in config.get("readable_roots", ())),
                allowed_hosts=tuple(config.get("allowed_hosts", ())),
                max_file_bytes=int(config.get("max_file_bytes", 1048576)),
                max_tasks=int(config.get("max_tasks", 2)),
                max_retained_tasks=int(config.get("max_retained_tasks", 32)),
                max_output_bytes=int(config.get("max_output_bytes", 65536)),
                timeout_seconds=min(float(config.get("command_timeout", 30)), 300),
            )
        sub = getattr(config, "subagents", None)
        return cls(
            root=Path(root or Path.cwd()),
            allow_write=bool(getattr(sub, "allow_write", False)),
            allow_execution=bool(getattr(sub, "allow_execution", False)),
            allow_discard=bool(getattr(sub, "allow_discard", False)),
            max_output_bytes=int(getattr(sub, "max_output_bytes", 65536)),
            timeout_seconds=min(float(getattr(sub, "default_timeout_seconds", 30)), 300),
        )

    def path(self, value: str | Path, *, write: bool = False) -> Path:
        from .io_boundary import filesystem_request, in_worker, query_sync

        if not in_worker:
            return Path(
                query_sync(filesystem_request("path", self, path=str(value), write=write), self)
            )
        if write and not self.allow_write:
            raise PolicyDenied("Mutation denied in read-only mode")
        raw = Path(value)
        candidate = raw if raw.is_absolute() else self.root / raw
        # Reject static symlinks/junctions rather than following them. This is not
        # protection against a hostile actor concurrently replacing directories.
        for part in (candidate, *candidate.parents):
            if part.exists() or part.is_symlink():
                info = part.lstat()
                if part.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
                ):
                    raise PolicyDenied("Links and reparse paths are unsupported")
        resolved = candidate.resolve()
        roots = (self.root,) if write else (self.root, *self.readable_roots)
        if not any(resolved.is_relative_to(r) for r in roots):
            raise PolicyDenied("Path is outside the granted workspace")
        relative = resolved.relative_to(next(r for r in roots if resolved.is_relative_to(r)))
        if any(
            p.lower() in {".git", ".aws", ".codex", ".agents"} or p.lower().startswith(".env")
            for p in relative.parts
        ):
            raise PolicyDenied("Credential/instruction metadata is not tool-readable")
        return resolved

    def command(self, command: str, cwd: str | Path | None = None) -> Path:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("No command provided")
        if not self.allow_execution:
            raise PolicyDenied(
                "Arbitrary process execution is disabled; grant trusted execution explicitly"
            )
        return self.path(cwd or self.root)

    def authorize(self, name: str, payload: dict[str, Any]) -> None:
        if name == "task_manager" and self.max_output_bytes < 4096:
            raise PolicyDenied(
                "Task control results require max_output_bytes >= 4096; no operation dispatched"
            )
        if not isinstance(payload, dict):
            raise TypeError("Tool arguments must be an object")
        if any(
            k in payload
            for k in (
                "allow_write",
                "read_only",
                "allow_outside_working_dir",
                "security_mode",
                "allow_execution",
                "allow_discard",
            )
        ):
            raise PolicyDenied("Payload cannot grant capabilities")
        op = payload.get("operation", payload.get("action", ""))
        if not isinstance(op, str):
            raise TypeError("Operation/action must be a string")
        for key in (
            "operation",
            "action",
            "path",
            "file",
            "target",
            "file_path",
            "filename",
            "working_dir",
            "cwd",
            "command",
            "encoding",
        ):
            if key in payload and payload[key] is not None and not isinstance(payload[key], str):
                raise TypeError(f"{key} must be a string")
        if "files" in payload and (
            not isinstance(payload["files"], list)
            or any(not isinstance(p, str) for p in payload["files"])
        ):
            raise TypeError("files must be an array of strings")
        mutation = name == "file_ops" and op in {"write", "create", "append", "delete", "mkdir"}
        if name == "workspace" and op in {"git_branch", "git_worktree"}:
            mutation = payload.get("action", "create") not in {"list", "status", "diff"}
        if name == "worktree" and op in {
            "create",
            "add",
            "new",
            "merge",
            "apply",
            "discard",
            "remove",
            "delete",
            "prune",
        }:
            mutation = True
        if mutation and not self.allow_write:
            raise PolicyDenied("Mutation denied in read-only mode")
        if payload.get("force") and not self.allow_discard:
            raise PolicyDenied("Force/discard capability is not granted")
        for key in ("path", "file", "target", "file_path", "filename", "working_dir", "cwd"):
            if payload.get(key):
                self.path(payload[key], write=mutation)
        for value in payload.get("files", []):
            self.path(value)
        if name == "task_manager" and op in {
            "run",
            "start",
            "run_background",
            "send_input",
            "stdin",
            "kill",
            "cancel",
        }:
            self.command(payload.get("command", "owned task"), payload.get("cwd"))
        if name == "diagnostics_check" and payload.get("command"):
            self.command(payload["command"], payload.get("cwd"))
        if name == "shell_execution":
            value = payload.get("command", "")
            if not isinstance(value, str):
                raise TypeError("command must be a string")
            parts = shlex.split(value, posix=os.name != "nt")
            cmd = parts[0].lower() if parts else ""
            if cmd not in {
                "pwd",
                "cwd",
                "echo",
                "whoami",
                "ls",
                "dir",
                "cat",
                "type",
                "head",
                "tail",
                "which",
                "where",
                "cd",
            }:
                self.command(payload.get("command", ""), payload.get("working_dir"))
            if cmd in {"cat", "type", "head", "tail", "cd", "ls", "dir"}:
                for arg in parts[1:]:
                    if not arg.startswith("-"):
                        self.path(arg.strip('"'))

    async def aauthorize(self, name: str, payload: dict[str, Any]) -> None:
        from .io_boundary import filesystem_request, in_worker, query

        # Pure capability/type checks need no OS boundary. Every path/command
        # branch that can touch the filesystem still runs inside an owned helper.
        filesystem = isinstance(payload, dict) and (
            any(
                payload.get(key)
                for key in (
                    "path",
                    "file",
                    "target",
                    "file_path",
                    "filename",
                    "working_dir",
                    "cwd",
                    "files",
                )
            )
            or (
                name == "task_manager"
                and payload.get("operation", payload.get("action"))
                in {"run", "start", "run_background", "send_input", "stdin", "kill", "cancel"}
            )
            or (name == "diagnostics_check" and bool(payload.get("command")))
        )
        if name == "shell_execution" and isinstance(payload, dict):
            value = payload.get("command", "")
            if not isinstance(value, str):
                raise TypeError("command must be a string")
            parts = shlex.split(value, posix=os.name != "nt")
            command = parts[0].lower() if parts else ""
            filesystem |= command not in {
                "pwd",
                "cwd",
                "echo",
                "whoami",
                "which",
                "where",
                "ls",
                "dir",
                "cat",
                "type",
                "head",
                "tail",
                "cd",
            }
            filesystem |= command in {"ls", "dir", "cat", "type", "head", "tail", "cd"} and bool(
                parts[1:]
            )
        if in_worker or not filesystem:
            self.authorize(name, payload)
        else:
            await query(filesystem_request("authorize", self, name=name, payload=payload), self)

    def _url_target(self, url: str) -> tuple[str, int]:
        if not self.allow_network:
            raise PolicyDenied("Network access is disabled")
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise PolicyDenied("Only credential-free HTTP(S) URLs are supported")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise PolicyDenied("Destination is not granted")
        return host, parsed.port or (443 if parsed.scheme == "https" else 80)

    def _addresses(self, host: str, port: int) -> list[str]:
        from .io_boundary import query_sync

        return cast(
            list[str], query_sync({"operation": "resolve", "host": host, "port": port}, self)
        )

    async def _aaddresses(self, host: str, port: int) -> list[str]:
        from .io_boundary import query

        return cast(
            list[str], await query({"operation": "resolve", "host": host, "port": port}, self)
        )

    @staticmethod
    def _public_address(addresses: list[str]) -> str:
        if not isinstance(addresses, list) or not 0 < len(addresses) <= 64:
            raise PolicyDenied("DNS address count exceeds supported limits")
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise PolicyDenied("Private, loopback and link-local destinations are denied")
        return str(addresses[0])

    def url(self, url: str) -> str:
        host, port = self._url_target(url)
        return self._public_address(self._addresses(host, port))

    async def aurl(self, url: str) -> str:
        host, port = self._url_target(url)
        return self._public_address(await self._aaddresses(host, port))

    def environment(self) -> dict[str, str]:
        # Deliberately exclude provider tokens and interpreter injection variables.
        keys = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "LANG",
            "LC_ALL",
            "APPDATA",
            "LOCALAPPDATA",
            "USERPROFILE",
            "USERNAME",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in keys}
        env["PYTHONIOENCODING"] = "utf-8"
        # Internal Git must not inherit global/system hooks, filters or credentials.
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        return env


active_policy: contextvars.ContextVar[RunPolicy | None] = contextvars.ContextVar(
    "agentcli_policy", default=None
)


def tool_policy(config: Any = None) -> RunPolicy:
    return active_policy.get() or RunPolicy.from_config(config)
