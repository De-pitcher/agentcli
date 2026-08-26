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

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "google/gemma-4-31b-it:free"

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
class Config:
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    app: AppConfig = field(default_factory=AppConfig)


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


def load_config(path: Path | None = None) -> Config:
    path = path or find_config_path()
    if not path.exists():
        return Config()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    or_raw = raw.get("openrouter", {})
    app_raw = raw.get("app", {})

    return Config(
        openrouter=OpenRouterConfig(
            api_key_env=or_raw.get("api_key_env", "OPENROUTER_API_KEY"),
            default_model=or_raw.get("default_model", DEFAULT_MODEL),
            timeout_seconds=or_raw.get("timeout_seconds", 30.0),
            max_retries=or_raw.get("max_retries", 3),
            base_url=or_raw.get("base_url", "https://openrouter.ai/api/v1"),
        ),
        app=AppConfig(
            stream=app_raw.get("stream", True),
            history_turns=app_raw.get("history_turns", 20),
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
