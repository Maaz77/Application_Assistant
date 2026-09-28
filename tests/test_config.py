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


# ------------------------------------------------------------------ P1 (T1, T7)


def test_the_p1_sections_have_the_defaults_p1_asks_for():
    cfg = config.load()
    assert (cfg.limits.max_in_flight, cfg.limits.min_interval_s, cfg.limits.max_attempts) == (1, 0.25, 3)
    assert cfg.budget.max_usd_per_run == 1.00
    assert cfg.jev.max_questions_per_request == 24
    assert cfg.decider.fallback == "chat"


def test_no_free_model_is_configured_on_any_route():
    """D14. A free model shares its provider's capacity with everyone: 429s and overloaded bodies in bursts."""
    cfg = config.load()
    for route in ("openrouter", "vercel"):
        table = getattr(cfg.models, route)
        named = list(table.llm_inference) + list(table.text_helper) + [table.system_one_decision_model]
        assert not [m for m in named if ":free" in m], (route, named)


def test_every_configured_chat_model_has_a_price_to_estimate_from():
    """[prices] is only a fallback for a gateway that reports no cost, but a missing entry silently estimates $0."""
    cfg = config.load()
    for route in ("openrouter", "vercel"):
        for model in getattr(cfg.models, route).llm_inference:
            assert model in cfg.prices, model


def test_an_unknown_p1_key_is_still_an_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[paths]\nbase = "."\n[limits]\nmax_in_flight = 1\nmax_parallel = 4\n')
    with pytest.raises(Exception, match="max_parallel"):
        config.load(p)


def test_the_llm_inference_timeout_is_the_p1_value():
    from assistant import llm_inference
    assert llm_inference.LLM_INFERENCE_TIMEOUT == 45.0
