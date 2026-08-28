from agentcli.config import DEFAULT_MODEL, Config, init_config, load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTCLI_CONFIG", raising=False)
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert cfg.openrouter.default_model == DEFAULT_MODEL
    assert cfg.app.history_turns == 20


def test_init_config_writes_file(tmp_path):
    path = tmp_path / "config.toml"
    result, written = init_config(path)
    assert result == path
    assert written is True
    assert path.exists()
    assert "openrouter" in path.read_text()


def test_init_config_no_overwrite_by_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# custom, do not clobber")
    result, written = init_config(path)
    assert result == path
    assert written is False
    assert path.read_text() == "# custom, do not clobber"


def test_load_config_reads_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[openrouter]\ndefault_model = "some/other-model:free"\n[app]\nhistory_turns = 5\n'
    )
    cfg = load_config(path)
    assert cfg.openrouter.default_model == "some/other-model:free"
    assert cfg.app.history_turns == 5


def test_load_config_invalid_type_raises_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[routing]\nmax_fallbacks = "two"\n')
    import pytest

    from agentcli.config import ConfigError

    with pytest.raises(ConfigError, match="Invalid value for 'max_fallbacks'"):
        load_config(path)


def test_api_key_reads_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
    cfg = Config()
    assert cfg.openrouter.api_key == "sk-or-test123"


def test_api_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = Config()
    assert cfg.openrouter.api_key is None


def test_load_config_subagents_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("")
    cfg = load_config(path)
    assert cfg.subagents.enabled is True
    assert cfg.subagents.max_concurrent == 5
    assert cfg.subagents.idle_timeout_seconds == 300.0


def test_load_config_subagents_custom_and_models(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[subagents]
enabled = false
max_concurrent = 2
idle_timeout_seconds = 60.0
default_timeout_seconds = 15.0
max_output_bytes = 500000

[[subagents.models]]
id = "custom-agent"
command = "python"
args = ["-m", "custom"]
timeout_seconds = 20.0
env = { FOO = "bar" }

[[subagents.models]]
command = "missing_id"
"""
    )
    cfg = load_config(path)
    assert cfg.subagents.enabled is False
    assert cfg.subagents.max_concurrent == 2
    assert cfg.subagents.idle_timeout_seconds == 60.0
    assert cfg.subagents.default_timeout_seconds == 15.0
    assert cfg.subagents.max_output_bytes == 500000
    assert len(cfg.subagents.models) == 1
    assert cfg.subagents.models[0].id == "custom-agent"
    assert cfg.subagents.models[0].env == {"FOO": "bar"}
