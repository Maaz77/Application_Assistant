"""rotation.py: the order of a call, handing over on an unavailable model, and the error when none answers."""
import pytest

from assistant.rotation import NoModelAvailable, Rotation

pytestmark = pytest.mark.unit


class Busy(Exception):
    pass


def test_a_call_starts_at_the_model_that_answered_last_then_the_rest_as_configured():
    r = Rotation(["a", "b", "c"])
    assert r.order() == ["a", "b", "c"]
    tried = []

    def attempt(m):
        tried.append(m)
        if m != "c":
            raise Busy(f"{m} busy")
        return m.upper()
    assert r.call(attempt, Busy) == "C" and tried == ["a", "b", "c"] and r.last == "c"
    assert r.order() == ["c", "a", "b"]


def test_none_answered_names_every_failure_and_keeps_the_last_good_model():
    r = Rotation(["a", "b"])
    r.last = "b"

    def attempt(m):
        raise Busy(f"HTTP 429 {m}")
    with pytest.raises(NoModelAvailable, match=r"none of 2 models answered \(b: HTTP 429 b; a: HTTP 429 a\)"):
        r.call(attempt, Busy)
    assert r.last == "b"


def test_other_errors_are_not_skipped():
    def attempt(m):
        raise KeyError("bad key")
    with pytest.raises(KeyError):
        Rotation(["a", "b"]).call(attempt, Busy)


def test_one_model_is_a_rotation_of_one_and_none_is_an_error():
    assert Rotation("a").models == ["a"]
    with pytest.raises(ValueError):
        Rotation([])


def test_config_takes_one_model_or_a_list(tmp_path):
    from assistant import config
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.openrouter]\nanswer_engine = "x/one"\n'
                 f'text_helper = ["y/a", "y/b"]\n')
    m = config.load(f).models
    assert m.answer_engine == ("x/one",) and m.text_helper == ("y/a", "y/b")


def test_chat_route_picks_the_provider_its_models_url_and_key(tmp_path, monkeypatch):
    """models.chat_route switches the answer engine and the text helper between OpenRouter and Vercel AI Gateway
    (for when OpenRouter's free quota is out), each with its own model table and key."""
    from assistant import config, jev
    monkeypatch.setattr(config, "api_key", lambda *a: "or-key")
    monkeypatch.setattr(config, "gateway_key", lambda *a: "gw-key")
    f = tmp_path / "c.toml"
    body = (f'[paths]\nbase = "{tmp_path}"\n[models]\nchat_route = "ROUTE"\n'
            '[models.openrouter]\nanswer_engine = ["q:free"]\ntext_helper = ["q:free"]\n'
            '[models.vercel]\nanswer_engine = ["mistral/mistral-nemo"]\ntext_helper = ["mistral/mistral-nemo"]\n')
    f.write_text(body.replace("ROUTE", "vercel"))
    cfg = config.load(f)
    assert cfg.models.answer_engine == ("mistral/mistral-nemo",)
    assert config.chat_url(cfg) == "https://ai-gateway.vercel.sh/v1/chat/completions"
    assert config.chat_key(cfg) == "gw-key"
    env = jev.env_values(cfg, config.chat_key(cfg))
    assert env["TEXT_MODEL_BASE_URL"] == "https://ai-gateway.vercel.sh/v1" and env["TEXT_MODEL_API_KEY"] == "gw-key"
    assert env["TEXT_MODEL"] == "mistral/mistral-nemo" and env["OPENROUTER_API_KEY"] == "or-key"
    f.write_text(body.replace("ROUTE", "openrouter"))
    cfg = config.load(f)
    assert cfg.models.answer_engine == ("q:free",) and config.chat_key(cfg) == "or-key"
    assert config.chat_url(cfg) == "https://openrouter.ai/api/v1/chat/completions"
    f.write_text(body.replace("ROUTE", "vercel").replace('answer_engine = ["mistral/mistral-nemo"]\n', ""))
    assert any("models.vercel.answer_engine is empty" in p for p in config.load(f).problems())


def test_the_text_helper_rotates_inside_the_package():
    """jev.rotate_text_helper wraps the package's policy.text_for(cfg, …) with the model from each turn."""
    import dataclasses
    import types

    from assistant import jev

    class TurboUnavailable(RuntimeError):
        pass

    @dataclasses.dataclass
    class Cfg:
        text_model: str = "first"

    seen = []

    def text_for(cfg, goal, element, observation, history):
        seen.append(cfg.text_model)
        if cfg.text_model == "busy":
            raise TurboUnavailable("Decision model returned HTTP 429; no action executed.")
        return f"{goal} by {cfg.text_model}"

    policy = types.SimpleNamespace(text_for=text_for, TurboUnavailable=TurboUnavailable)
    jev.rotate_text_helper(policy, ("busy", "ok"))
    assert policy.text_for(Cfg(), "Dublin", None, None, []) == "Dublin by ok" and seen == ["busy", "ok"]
    assert policy.text_for(Cfg(), "Cork", None, None, []) == "Cork by ok" and seen[-1] == "ok"    # last good first
    jev.rotate_text_helper(policy, ("busy",))                  # wrapping again replaces the rotation, never nests
    with pytest.raises(TurboUnavailable, match=r"Text helper: none of 1 models answered \(busy: .*HTTP 429"):
        policy.text_for(Cfg(), "Galway", None, None, [])
