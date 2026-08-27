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


@dataclass
class RoutingModelEntry:
    id: str
    categories: list[str] = field(default_factory=lambda: ["chat"])
    priority: int = 50
    context_window: int = 32768


@dataclass
class RoutingConfig:
    enabled: bool = True
    max_fallbacks: int = 2
    cooldown_seconds: float = 300.0
    failure_threshold: int = 3
    models: list[RoutingModelEntry] = field(default_factory=list)


@dataclass
class Config:
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    app: AppConfig = field(default_factory=AppConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)


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


def load_config(path: Path | None = None) -> Config:
    path = path or find_config_path()
    if not path.exists():
        return Config()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    or_raw = raw.get("openrouter", {})
    app_raw = raw.get("app", {})
    routing_raw = raw.get("routing", {})

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
            )
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
            models=entries,
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
