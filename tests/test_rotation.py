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
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.openrouter]\nllm_inference = "x/one"\n'
                 f'text_helper = ["y/a", "y/b"]\n')
    m = config.load(f).models
    assert m.llm_inference == ("x/one",) and m.text_helper == ("y/a", "y/b")


def test_chat_route_picks_the_provider_its_models_url_and_key(tmp_path, monkeypatch):
    """models.chat_route switches the LLM inference and the text helper between OpenRouter and Vercel AI Gateway
    (for when OpenRouter's free quota is out), each with its own model table and key."""
    from assistant import config
    monkeypatch.setattr(config, "api_key", lambda *a: "or-key")
    monkeypatch.setattr(config, "gateway_key", lambda *a: "gw-key")
    f = tmp_path / "c.toml"
    body = (f'[paths]\nbase = "{tmp_path}"\n[models]\nchat_route = "ROUTE"\n'
            '[models.openrouter]\nllm_inference = ["q:free"]\ntext_helper = ["q:free"]\n'
            '[models.vercel]\nllm_inference = ["mistral/mistral-nemo"]\ntext_helper = ["mistral/mistral-nemo"]\n')
    f.write_text(body.replace("ROUTE", "vercel"))
    cfg = config.load(f)
    assert cfg.models.llm_inference == ("mistral/mistral-nemo",)
    assert config.chat_url(cfg) == "https://ai-gateway.vercel.sh/v1/chat/completions"
    assert config.chat_key(cfg) == "gw-key"
    f.write_text(body.replace("ROUTE", "openrouter"))
    cfg = config.load(f)
    assert cfg.models.llm_inference == ("q:free",) and config.chat_key(cfg) == "or-key"
    assert config.chat_url(cfg) == "https://openrouter.ai/api/v1/chat/completions"
    f.write_text(body.replace("ROUTE", "vercel").replace('llm_inference = ["mistral/mistral-nemo"]\n', ""))
    assert any("models.vercel.llm_inference is empty" in p for p in config.load(f).problems())


# The package text helper and jev.rotate_text_helper are gone in P2 (the package is removed); its rotation
# test is dropped. The Gateway owns the one retry/rotation layer now — see tests/test_gateway.py.
