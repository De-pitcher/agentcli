from agentcli.config import DEFAULT_MODEL, Config, init_config, load_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTCLI_CONFIG", raising=False)
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert cfg.openrouter.default_model == DEFAULT_MODEL
    assert cfg.app.history_turns == 20


def test_init_config_writes_file(tmp_path):
    path = tmp_path / "config.toml"
    result = init_config(path)
    assert result == path
    assert path.exists()
    assert "openrouter" in path.read_text()


def test_init_config_no_overwrite_by_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# custom, do not clobber")
    init_config(path)
    assert path.read_text() == "# custom, do not clobber"


def test_load_config_reads_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[openrouter]\ndefault_model = "some/other-model:free"\n'
        "[app]\nhistory_turns = 5\n"
    )
    cfg = load_config(path)
    assert cfg.openrouter.default_model == "some/other-model:free"
    assert cfg.app.history_turns == 5


def test_api_key_reads_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
    cfg = Config()
    assert cfg.openrouter.api_key == "sk-or-test123"


def test_api_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = Config()
    assert cfg.openrouter.api_key is None
