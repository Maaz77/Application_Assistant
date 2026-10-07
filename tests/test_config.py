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
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.local]\n{extra}')
    return config.load(f)


def test_the_decision_server_defaults_to_a_kev_server_on_this_machine(tmp_path, monkeypatch):
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    cfg = local_toml(tmp_path)
    assert cfg.models.local.base_url == "http://127.0.0.1:8009"
    assert cfg.models.system_one_decision_model == "kev-latest"      # [models.local] is the only route there is
    assert config.local_key(tmp_path / "no.env") == ""               # an open server needs no key
    assert config.LOCAL_KEY_NAME == "KEV_API_KEY"


def test_the_decision_server_reads_kev_api_key_when_it_asks_for_one(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEV_API_KEY=kev-secret\n")
    assert config.local_key(env) == "kev-secret"


def test_the_chat_route_is_the_freellmapi_router_keyed_by_freellmapi_key(tmp_path):
    """One chat route: the FreeLLMAPI router on this machine. The URL comes from the config, never a table of
    hosts, and the key is always FREELLMAPI_KEY (the router answers HTTP 401 without it)."""
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\n'
                 f'base_url = "http://127.0.0.1:31415/v1"\nllm_inference = "kimi-k3"\n')
    cfg = config.load(f)
    assert cfg.models.llm_inference == ("kimi-k3",)                  # a single ID is a rotation of one
    assert config.chat_url(cfg) == "http://127.0.0.1:31415/v1/chat/completions"
    env = tmp_path / ".env"
    env.write_text("FREELLMAPI_KEY=fl-secret\n")
    assert config.chat_key(env) == "fl-secret" and config.CHAT_KEY_NAME == "FREELLMAPI_KEY"


def test_a_trailing_slash_on_the_base_url_does_not_double_up(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\n'
                 f'base_url = "http://127.0.0.1:31415/v1/"\nllm_inference = "kimi-k3"\n')
    assert config.chat_url(config.load(f)) == "http://127.0.0.1:31415/v1/chat/completions"


def test_an_empty_freellmapi_base_url_is_a_problem(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\n'
                 f'base_url = ""\nllm_inference = "kimi-k3"\n')
    assert any("models.freellmapi.base_url is empty" in p for p in config.load(f).problems())


def test_a_config_written_for_a_removed_route_says_what_replaced_it(tmp_path):
    """The paid routes went on 2026-10-07. A config still naming one must say where its keys moved, not fail with
    the strict schema's generic "extra fields not permitted"."""
    for gone in ('chat_route = "openrouter"', '[models.openrouter]\nllm_inference = ["x"]',
                 '[models.vercel]\nllm_inference = ["x"]', 'system_one_decision_provider = "vercel"'):
        f = tmp_path / "c.toml"
        f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models]\n{gone}\n')
        with pytest.raises(Exception, match="removed on 2026-10-07"):
            config.load(f)


def test_an_empty_local_base_url_is_a_problem(tmp_path):
    problems = local_toml(tmp_path, 'base_url = ""\n').problems()
    assert any("models.local.base_url is empty" in p for p in problems)


def test_the_shipped_config_names_a_reachable_decision_route():
    cfg = config.load()
    assert cfg.models.system_one_decision_model == "kev-latest"
    assert cfg.models.local.base_url.startswith("http://127.0.0.1")  # the only route: a server on this machine


# ------------------------------------------------------------------ P1 (T1, T7)


def test_the_p1_sections_have_the_defaults_p1_asks_for():
    cfg = config.load()
    assert (cfg.limits.max_in_flight, cfg.limits.min_interval_s, cfg.limits.max_attempts) == (1, 0.25, 3)
    assert cfg.jev.max_questions_per_request == 24
    assert cfg.decider.fallback == "chat"


ROUTING_MODES = ("auto", "auto:fast", "auto:smart", "fusion")


def test_the_shipped_config_lets_the_router_choose_the_model():
    """User decision 2026-10-07: model choice and failover are the router's job, not this program's — so the
    shipped config names a routing mode rather than a hand-picked list. `auto` honours the strict json_schema
    (9/9 across the three strategies, live) and routes around a free tier that is spent, which a fixed list
    cannot. A concrete ID is still allowed for pinning one deliberately."""
    named = list(config.load().models.llm_inference)
    assert named == ["auto"], named
    assert not [m for m in named if ":free" in m], named          # D14 still stands for concrete IDs


def test_an_unknown_p1_key_is_still_an_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[paths]\nbase = "."\n[limits]\nmax_in_flight = 1\nmax_parallel = 4\n')
    with pytest.raises(Exception, match="max_parallel"):
        config.load(p)


def test_the_llm_inference_timeout_is_the_p1_value():
    from assistant import llm_inference
    assert llm_inference.LLM_INFERENCE_TIMEOUT == 45.0
