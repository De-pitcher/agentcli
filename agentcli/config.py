"""Configuration loading for agentcli.

Config file resolution order (first found wins):
  1. ./agentcli.toml                  (project-local)
  2. $AGENTCLI_CONFIG                 (explicit override path)
  3. platform config dir              (~/.config/agentcli/config.toml on
                                        Linux/macOS, %APPDATA%\\agentcli\\config.toml
                                        on Windows)
If nothing is found, in-memory defaults are used (no error) so `agentcli chat`
works immediately as long as the API key env var is set.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "google/gemma-4-31b-it:free"

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_TOML = f'''# agentcli configuration

[openrouter]
api_key_env = "OPENROUTER_API_KEY"   # name of the env var holding your OpenRouter key
default_model = "{DEFAULT_MODEL}"
timeout_seconds = 30
max_retries = 3
base_url = "https://openrouter.ai/api/v1"

[app]
stream = true
history_turns = 20     # number of prior user/assistant turn-pairs resent for context

[routing]
enabled = true         # auto-route each message to a category-matched free model
max_fallbacks = 2      # extra candidates sent after the primary via the models array
cooldown_seconds = 300
failure_threshold = 3  # consecutive failures before a model enters cooldown

[subagents]
enabled = true         # enable sub-agent system
max_concurrent = 5     # max concurrent sub-agents per type
idle_timeout_seconds = 300
default_timeout_seconds = 30
max_output_bytes = 1048576  # 1MB

[agent_loop]
enabled = false        # set to true to enable Plan→Act→Reflect for multi-step tasks
max_iterations = 5     # hard ceiling on plan/act/reflect cycles (prevents runaway loops)
reflection_enabled = true
# plan_model_override = ""    # optional: force a specific model for the planning step
# reflect_model_override = "" # optional: force a specific model for the reflection step

[memory]
enabled = true         # persist chat sessions to local SQLite database
# db_path = ""         # optional: custom path to SQLite database
retention_days = 30    # auto-prune sessions older than N days (0 to disable)
cache_enabled = true   # cache unchanged file context to save tokens and disk I/O
max_shared_context_bytes = 524288  # 512KB capacity for shared sub-agent context pool
'''


@dataclass
class OpenRouterConfig:
    api_key_env: str = "OPENROUTER_API_KEY"
    default_model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    max_retries: int = 3
    base_url: str = "https://openrouter.ai/api/v1"

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass
class AppConfig:
    stream: bool = True
    history_turns: int = 20
    load_agents_md: bool = False
    plugins: list[str] = field(default_factory=list)


PRESETS: dict[str, dict[str, Any]] = {
    "coding": {
        "agent_loop": {"enabled": True, "max_iterations": 8},
        "app": {"history_turns": 30},
        "routing": {"enabled": True},
    },
    "chat": {
        "agent_loop": {"enabled": False},
        "app": {"history_turns": 15},
        "routing": {"enabled": True},
    },
    "minimal": {
        "agent_loop": {"enabled": False},
        "app": {"history_turns": 10},
        "memory": {"enabled": False},
        "routing": {"enabled": False},
    },
}


@dataclass
class RoutingModelEntry:
    id: str
    categories: list[str] = field(default_factory=lambda: ["chat"])
    priority: int = 50
    context_window: int = 32768
    tier: str = "low"


@dataclass
class RoutingConfig:
    enabled: bool = True
    max_fallbacks: int = 2
    cooldown_seconds: float = 300.0
    failure_threshold: int = 3
    budget_tier: str = "low"
    max_cost_usd: float | None = None
    models: list[RoutingModelEntry] = field(default_factory=list)


@dataclass
class SubAgentModelEntry:
    id: str
    command: str
    args: list[str] = field(default_factory=list)
    timeout_seconds: float = 30.0
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SubAgentsConfig:
    enabled: bool = True
    max_concurrent: int = 5
    idle_timeout_seconds: float = 300.0
    default_timeout_seconds: float = 30.0
    max_output_bytes: int = 1024 * 1024  # 1MB
    allow_write: bool = False
    models: list[SubAgentModelEntry] = field(default_factory=list)


@dataclass
class AgentLoopConfig:
    """Configuration for the Plan → Act → Reflect agent loop (Phase 4).

    Fields:
        enabled:              Enable the loop for multi-step tasks.
        max_iterations:       Hard ceiling to prevent runaway cycles.
        reflection_enabled:   Whether to run the reflect stage.
        plan_model_override:  Force a specific model for the planning step.
        reflect_model_override: Force a specific model for reflection.
    """

    enabled: bool = False
    max_iterations: int = 5
    reflection_enabled: bool = True
    plan_model_override: str = ""
    reflect_model_override: str = ""


@dataclass
class MemoryConfig:
    """Configuration for session persistence, caching, and context pooling (Phase 5 & 6).

    Fields:
        enabled:                 Persist conversation sessions to SQLite.
        db_path:                 Custom path override for the SQLite database.
        retention_days:          Auto-prune sessions older than N days (0 to disable).
        cache_enabled:           Cache unchanged file references to save tokens and disk I/O.
        max_shared_context_bytes: Maximum byte budget for sub-agent shared context pool.
        budget_ratio:            Fraction of model context window allocated to history (0.1–1.0).
        max_cache_entries:       Maximum number of files cached in context cache.
        max_cache_bytes:         Maximum byte budget for cached file contexts.
    """

    enabled: bool = True
    db_path: str = ""
    retention_days: int = 30
    cache_enabled: bool = True
    max_shared_context_bytes: int = 524288  # 512KB
    budget_ratio: float = 0.75
    max_cache_entries: int = 256
    max_cache_bytes: int = 10485760  # 10MB


@dataclass
class CapabilityConfig:
    """Configuration for capability policy enforcement (Phase 10).

    Fields:
        read_only:              Default to read-only operations; mutations require explicit approval.
        workspace_only:         Restrict file operations to workspace directory.
        allowed_commands:       Allowlist of permitted shell commands (empty = all allowed).
        approval_hooks:         Require explicit approval for mutations (write, delete, shell).
        plugin_trust_boundary:  Plugins run in restricted context; no host access by default.
    """

    read_only: bool = True
    workspace_only: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    approval_hooks: bool = True
    plugin_trust_boundary: bool = True


@dataclass
class MCPServerConfig:
    """Configuration for an external Model Context Protocol (MCP) server connection (Phase 19).

    Fields:
        name:     Unique identifier for this server.
        command:  Executable or runtime command (e.g. 'npx', 'python', 'docker').
        args:     Command-line arguments.
        env:      Environment variables passed to the server process.
        enabled:  Whether this server connection is active.
        url:      Optional HTTP/SSE endpoint URL.
    """

    name: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    url: str = ""


@dataclass
class WatcherConfig:
    """Configuration for autonomous project watcher and continuous TDD loop (Phase 22).

    Fields:
        enabled:                Whether watcher capabilities are enabled.
        test_command:           Command to run on file change (default: 'python -m pytest').
        paths:                  List of directories/paths to watch (default: ['.']).
        debounce_seconds:       Debounce interval in seconds to coalesce rapid file saves.
        cooldown_seconds:       Cooldown period in seconds between test runs.
        auto_apply:             Whether to apply verified fixes to working tree automatically.
        max_cost_usd:           Optional cumulative budget limit in USD per session.
        max_repair_iterations:  Maximum repair loop iterations per failure attempt.
        budget_tier:            Budget tier for routing during repair ('low', 'medium', 'high').
        model:                  Optional forced model override for repair.
    """

    enabled: bool = True
    test_command: str = "python -m pytest"
    paths: list[str] = field(default_factory=lambda: ["."])
    debounce_seconds: float = 1.5
    cooldown_seconds: float = 5.0
    auto_apply: bool = False
    max_cost_usd: float | None = None
    max_repair_iterations: int = 5
    budget_tier: str = "low"
    model: str | None = None


@dataclass
class EmbeddingsConfig:
    """Configuration for semantic code embeddings and vector search (Phase 24).

    Fields:
        enabled:              Whether semantic vector indexing is enabled.
        model:                OpenRouter embeddings model (default: 'openai/text-embedding-3-small').
        batch_size:           Batch size for API embedding requests.
        cache_path:           Custom override path for SQLite vector database.
        similarity_threshold: Minimum cosine similarity score (0.0 to 1.0) for search results.
        max_results:          Default maximum number of search results to return.
        chunk_max_lines:      Maximum line count per code chunk.
        chunk_overlap_lines:  Overlap lines between consecutive code chunks.
    """

    enabled: bool = True
    model: str = "openai/text-embedding-3-small"
    batch_size: int = 32
    cache_path: str = ""
    similarity_threshold: float = 0.30
    max_results: int = 5
    chunk_max_lines: int = 60
    chunk_overlap_lines: int = 10


@dataclass
class WorkspaceConfig:
    """Configured workspace within a monorepo mesh."""

    name: str
    path: str
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class MeshConfig:
    """Configuration for monorepo mesh and multi-repository orchestration (Phase 25).

    Fields:
        enabled:         Whether multi-repo mesh features are enabled.
        auto_discover:   Whether to automatically scan for sub-projects with manifests.
        discovery_depth: Maximum directory recursion depth for workspace auto-discovery.
        workspaces:      Explicitly configured workspace roots.
    """

    enabled: bool = True
    auto_discover: bool = True
    discovery_depth: int = 3
    workspaces: list[WorkspaceConfig] = field(default_factory=list)


@dataclass
class BenchmarkConfig:
    """Configuration for automated benchmarks and developer task evaluation (Phase 26).

    Fields:
        default_suite:           Default benchmark suite name (default: 'core').
        default_timeout_seconds: Default per-task timeout in seconds.
        output_dir:              Directory for persisting benchmark and arena scorecards.
        record_traces:           Whether to capture per-turn trace events.
    """

    default_suite: str = "core"
    default_timeout_seconds: int = 60
    output_dir: str = ".agentcli/benchmarks"
    record_traces: bool = True


@dataclass
class Config:
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    app: AppConfig = field(default_factory=AppConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    subagents: SubAgentsConfig = field(default_factory=SubAgentsConfig)
    agent_loop: AgentLoopConfig = field(default_factory=AgentLoopConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    mesh: MeshConfig = field(default_factory=MeshConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)


def _platform_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "agentcli" / "config.toml"


def find_config_path() -> Path:
    local = Path("agentcli.toml")
    if local.exists():
        return local
    env_override = os.environ.get("AGENTCLI_CONFIG")
    if env_override:
        return Path(env_override)
    return _platform_config_path()


class ConfigError(Exception):
    pass


def _parse_int(val: object, key: str, default: int) -> int:
    if val is None:
        return default
    try:
        return int(val)  # type: ignore
    except (ValueError, TypeError):
        raise ConfigError(f"Invalid value for '{key}': expected an integer, got '{val}'")


def _parse_float(val: object, key: str, default: float) -> float:
    if val is None:
        return default
    try:
        return float(val)  # type: ignore
    except (ValueError, TypeError):
        raise ConfigError(f"Invalid value for '{key}': expected a number, got '{val}'")


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dictionary into base."""
    merged = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(path: Path | None = None, preset: str | None = None) -> Config:
    path = path or find_config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)

    if preset is not None:
        if preset not in PRESETS:
            raise ConfigError(
                f"Unknown preset '{preset}'. Available presets: {', '.join(PRESETS.keys())}"
            )
        raw = _merge_dict(raw, PRESETS[preset])

    or_raw = raw.get("openrouter", {})
    app_raw = raw.get("app", {})
    routing_raw = raw.get("routing", {})
    subagents_raw = raw.get("subagents", {})
    loop_raw = raw.get("agent_loop", {})

    entries = []
    for entry in routing_raw.get("models", []):
        if not entry.get("id"):
            logger.warning("Skipping routing.models entry without 'id' field: %s", entry)
            continue
        entries.append(
            RoutingModelEntry(
                id=str(entry["id"]),
                categories=[str(c) for c in entry.get("categories", ["chat"])],
                priority=_parse_int(entry.get("priority"), "priority", 50),
                context_window=_parse_int(entry.get("context_window"), "context_window", 32768),
                tier=str(entry.get("tier", "low")).lower(),
            )
        )

    budget_tier_raw = str(routing_raw.get("budget_tier", "low")).lower()
    if budget_tier_raw not in {"low", "medium", "high"}:
        raise ConfigError(
            f"Invalid value for 'routing.budget_tier': expected one of ['low', 'medium', 'high'], got '{budget_tier_raw}'"
        )

    max_cost_raw = routing_raw.get("max_cost_usd")
    max_cost_usd = _parse_float(max_cost_raw, "routing.max_cost_usd", 0.0) if max_cost_raw is not None else None
    if max_cost_usd is not None and max_cost_usd < 0:
        raise ConfigError(
            f"Invalid value for 'routing.max_cost_usd': must be >= 0, got {max_cost_usd}"
        )

    subagent_entries = []
    for entry in subagents_raw.get("models", []):
        if not entry.get("id"):
            logger.warning("Skipping subagents.models entry without 'id' field: %s", entry)
            continue
        subagent_entries.append(
            SubAgentModelEntry(
                id=str(entry["id"]),
                command=str(entry.get("command", "")),
                args=[str(a) for a in entry.get("args", [])],
                timeout_seconds=_parse_float(entry.get("timeout_seconds"), "timeout_seconds", 30.0),
                env={str(k): str(v) for k, v in entry.get("env", {}).items()},
            )
        )

    loop_max_iter = _parse_int(loop_raw.get("max_iterations"), "agent_loop.max_iterations", 5)
    if loop_max_iter < 1:
        raise ConfigError(
            f"Invalid value for 'agent_loop.max_iterations': must be >= 1, got {loop_max_iter}"
        )

    memory_raw = raw.get("memory", {})
    retention_days = _parse_int(memory_raw.get("retention_days"), "memory.retention_days", 30)
    if retention_days < 0:
        raise ConfigError(
            f"Invalid value for 'memory.retention_days': must be >= 0, got {retention_days}"
        )

    max_shared_bytes = _parse_int(
        memory_raw.get("max_shared_context_bytes"),
        "memory.max_shared_context_bytes",
        524288,
    )
    if max_shared_bytes < 1024:
        raise ConfigError(
            f"Invalid value for 'memory.max_shared_context_bytes': must be >= 1024, got {max_shared_bytes}"
        )

    budget_ratio = _parse_float(memory_raw.get("budget_ratio"), "memory.budget_ratio", 0.75)
    if not (0.1 <= budget_ratio <= 1.0):
        raise ConfigError(
            f"Invalid value for 'memory.budget_ratio': must be between 0.1 and 1.0, got {budget_ratio}"
        )

    max_cache_entries = _parse_int(
        memory_raw.get("max_cache_entries"), "memory.max_cache_entries", 256
    )
    if max_cache_entries < 1:
        raise ConfigError(
            f"Invalid value for 'memory.max_cache_entries': must be >= 1, got {max_cache_entries}"
        )

    max_cache_bytes = _parse_int(
        memory_raw.get("max_cache_bytes"), "memory.max_cache_bytes", 10485760
    )
    if max_cache_bytes < 1024:
        raise ConfigError(
            f"Invalid value for 'memory.max_cache_bytes': must be >= 1024, got {max_cache_bytes}"
        )

    watcher_raw = raw.get("watcher", {})
    watcher_paths_raw = watcher_raw.get("paths", ["."])
    watcher_paths = [str(p) for p in watcher_paths_raw] if isinstance(watcher_paths_raw, list) else ["."]
    watcher_max_cost_raw = watcher_raw.get("max_cost_usd")
    watcher_max_cost = (
        _parse_float(watcher_max_cost_raw, "watcher.max_cost_usd", 0.0)
        if watcher_max_cost_raw is not None
        else None
    )
    watcher_cfg = WatcherConfig(
        enabled=bool(watcher_raw.get("enabled", True)),
        test_command=str(watcher_raw.get("test_command", "python -m pytest")),
        paths=watcher_paths if watcher_paths else ["."],
        debounce_seconds=_parse_float(
            watcher_raw.get("debounce_seconds"), "watcher.debounce_seconds", 1.5
        ),
        cooldown_seconds=_parse_float(
            watcher_raw.get("cooldown_seconds"), "watcher.cooldown_seconds", 5.0
        ),
        auto_apply=bool(watcher_raw.get("auto_apply", False)),
        max_cost_usd=watcher_max_cost,
        max_repair_iterations=_parse_int(
            watcher_raw.get("max_repair_iterations"), "watcher.max_repair_iterations", 5
        ),
        budget_tier=str(watcher_raw.get("budget_tier", "low")).lower(),
        model=str(watcher_raw["model"]) if watcher_raw.get("model") else None,
    )

    return Config(
        openrouter=OpenRouterConfig(
            api_key_env=str(or_raw.get("api_key_env", "OPENROUTER_API_KEY")),
            default_model=str(or_raw.get("default_model", DEFAULT_MODEL)),
            timeout_seconds=_parse_float(or_raw.get("timeout_seconds"), "timeout_seconds", 30.0),
            max_retries=_parse_int(or_raw.get("max_retries"), "max_retries", 3),
            base_url=str(or_raw.get("base_url", "https://openrouter.ai/api/v1")),
        ),
        app=AppConfig(
            stream=bool(app_raw.get("stream", True)),
            history_turns=_parse_int(app_raw.get("history_turns"), "history_turns", 20),
            load_agents_md=bool(app_raw.get("load_agents_md", False)),
            plugins=[str(p) for p in app_raw.get("plugins", [])],
        ),
        routing=RoutingConfig(
            enabled=bool(routing_raw.get("enabled", True)),
            max_fallbacks=_parse_int(routing_raw.get("max_fallbacks"), "max_fallbacks", 2),
            cooldown_seconds=_parse_float(
                routing_raw.get("cooldown_seconds"), "cooldown_seconds", 300.0
            ),
            failure_threshold=_parse_int(
                routing_raw.get("failure_threshold"), "failure_threshold", 3
            ),
            budget_tier=budget_tier_raw,
            max_cost_usd=max_cost_usd,
            models=entries,
        ),
        subagents=SubAgentsConfig(
            enabled=bool(subagents_raw.get("enabled", True)),
            max_concurrent=_parse_int(subagents_raw.get("max_concurrent"), "max_concurrent", 5),
            idle_timeout_seconds=_parse_float(
                subagents_raw.get("idle_timeout_seconds"), "idle_timeout_seconds", 300.0
            ),
            default_timeout_seconds=_parse_float(
                subagents_raw.get("default_timeout_seconds"), "default_timeout_seconds", 30.0
            ),
            max_output_bytes=_parse_int(
                subagents_raw.get("max_output_bytes"), "max_output_bytes", 1024 * 1024
            ),
            allow_write=bool(subagents_raw.get("allow_write", False)),
            models=subagent_entries,
        ),
        agent_loop=AgentLoopConfig(
            enabled=bool(loop_raw.get("enabled", False)),
            max_iterations=loop_max_iter,
            reflection_enabled=bool(loop_raw.get("reflection_enabled", True)),
            plan_model_override=str(loop_raw.get("plan_model_override", "")),
            reflect_model_override=str(loop_raw.get("reflect_model_override", "")),
        ),
        memory=MemoryConfig(
            enabled=bool(memory_raw.get("enabled", True)),
            db_path=str(memory_raw.get("db_path", "")),
            retention_days=retention_days,
            cache_enabled=bool(memory_raw.get("cache_enabled", True)),
            max_shared_context_bytes=max_shared_bytes,
            budget_ratio=budget_ratio,
            max_cache_entries=max_cache_entries,
            max_cache_bytes=max_cache_bytes,
        ),
        mcp_servers={
            str(name): MCPServerConfig(
                name=str(name),
                command=str(s_cfg.get("command", "")),
                args=[str(a) for a in s_cfg.get("args", [])],
                env={str(k): str(v) for k, v in s_cfg.get("env", {}).items()},
                enabled=bool(s_cfg.get("enabled", True)),
                url=str(s_cfg.get("url", "")),
            )
            for name, s_cfg in raw.get("mcp_servers", {}).items()
            if isinstance(s_cfg, dict)
        },
        watcher=watcher_cfg,
        embeddings=EmbeddingsConfig(
            enabled=bool(raw.get("embeddings", {}).get("enabled", True)),
            model=str(raw.get("embeddings", {}).get("model", "openai/text-embedding-3-small")),
            batch_size=_parse_int(raw.get("embeddings", {}).get("batch_size"), "embeddings.batch_size", 32),
            cache_path=str(raw.get("embeddings", {}).get("cache_path", "")),
            similarity_threshold=_parse_float(
                raw.get("embeddings", {}).get("similarity_threshold"), "embeddings.similarity_threshold", 0.30
            ),
            max_results=_parse_int(raw.get("embeddings", {}).get("max_results"), "embeddings.max_results", 5),
            chunk_max_lines=_parse_int(
                raw.get("embeddings", {}).get("chunk_max_lines"), "embeddings.chunk_max_lines", 60
            ),
            chunk_overlap_lines=_parse_int(
                raw.get("embeddings", {}).get("chunk_overlap_lines"), "embeddings.chunk_overlap_lines", 10
            ),
        ),
        mesh=MeshConfig(
            enabled=bool(raw.get("mesh", {}).get("enabled", True)),
            auto_discover=bool(raw.get("mesh", {}).get("auto_discover", True)),
            discovery_depth=_parse_int(
                raw.get("mesh", {}).get("discovery_depth"), "mesh.discovery_depth", 3
            ),
            workspaces=[
                WorkspaceConfig(
                    name=str(w.get("name", "")),
                    path=str(w.get("path", "")),
                    dependencies=[str(d) for d in w.get("dependencies", [])],
                    tags=[str(t) for t in w.get("tags", [])],
                    description=str(w.get("description", "")),
                )
                for w in (
                    raw.get("mesh", {}).get("workspaces", [])
                    if isinstance(raw.get("mesh", {}).get("workspaces"), list)
                    else (
                        [{"name": k, **v} for k, v in raw.get("mesh", {}).get("workspaces", {}).items() if isinstance(v, dict)]
                        if isinstance(raw.get("mesh", {}).get("workspaces"), dict)
                        else []
                    )
                )
                if isinstance(w, dict) and w.get("name") and w.get("path")
            ],
        ),
        benchmark=BenchmarkConfig(
            default_suite=str(raw.get("benchmark", {}).get("default_suite", "core")),
            default_timeout_seconds=_parse_int(
                raw.get("benchmark", {}).get("default_timeout_seconds"), "benchmark.default_timeout_seconds", 60
            ),
            output_dir=str(raw.get("benchmark", {}).get("output_dir", ".agentcli/benchmarks")),
            record_traces=bool(raw.get("benchmark", {}).get("record_traces", True)),
        ),
    )


def init_config(path: Path | None = None, overwrite: bool = False) -> tuple[Path, bool]:
    """
    Writes the default config unless a file already exists (and overwrite is
    False). Returns (path, written) so callers can report honestly.
    """
    path = path or find_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return path, False
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path, True
