import pytest

from assistant import config
from assistant.cli import build_parser

pytestmark = pytest.mark.unit


def test_shipped_config_loads():
    cfg = config.load()
    assert cfg.browser.max_actions == 2000
    assert (cfg.base_dir() / "Tools").is_dir()


def test_unknown_key_is_an_error(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[paths]\nbase = "x"\nbogus = 1\n')
    with pytest.raises(Exception):
        config.load(f)


def test_empty_llm_inference_is_a_problem(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n')
    assert any("llm_inference" in p for p in config.load(f).problems())


def test_cli_parses_run_flags():
    a = build_parser().parse_args(["run", "--dry-run", "--limit", "2"])
    assert a.dry_run and a.limit == 2


def local_toml(tmp_path, extra: str = "") -> "config.Config":
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models]\nsystem_one_decision_provider = "local"\n'
                 f'[models.local]\n{extra}')
    return config.load(f)


def test_the_local_route_defaults_to_a_kev_server_on_this_machine(tmp_path, monkeypatch):
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    cfg = local_toml(tmp_path)
    assert cfg.models.local.base_url == "http://127.0.0.1:8009"
    assert cfg.models.system_one_decision_model == "kev-latest"      # read from [models.local], not [models.vercel]
    assert config.system_one_decision_key(cfg, tmp_path / "no.env") == ""      # an open server needs no key
    assert "local" in config.KEYLESS_PROVIDERS and config.KEY_NAMES["local"] == "KEV_API_KEY"


def test_the_local_route_reads_kev_api_key_when_the_server_asks_for_one(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEV_API_KEY=kev-secret\n")
    assert config.system_one_decision_key(local_toml(tmp_path), env) == "kev-secret"


def test_an_empty_local_base_url_is_a_problem(tmp_path):
    problems = local_toml(tmp_path, 'base_url = ""\n').problems()
    assert any("models.local.base_url is empty" in p for p in problems)


def test_the_shipped_config_names_a_reachable_decision_route():
    cfg = config.load()
    assert cfg.models.system_one_decision_model                      # whichever provider is configured
    assert cfg.models.system_one_decision_provider in ("local", "vercel", "openrouter")
