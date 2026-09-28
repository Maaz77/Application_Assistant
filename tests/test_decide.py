"""decide.py, offline: the System One request, batching, retries, errors and the decisions log."""
import json

import pytest

from assistant import decide

pytestmark = pytest.mark.unit


def fake_post(*replies):
    """post(url, body, headers, timeout) → (status, body); answers every question unless a reply says otherwise."""
    calls = []

    def post(url, body, headers, timeout):
        calls.append((url, body, headers))
        status, data = replies[min(len(calls), len(replies)) - 1]
        if status == 200 and data is None:
            data = {"answers": {k: ({"type": "noul", "noul": 0.9} if q["type"] == "noul" else
                                    {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 0.8,
                                     "probabilities": {}}) for k, q in body["questions"].items()},
                    "usage": {"cost": 0.00002}}
        return status, data
    return post, calls


def test_request_shape_answers_and_cost():
    post, calls = fake_post((200, None))
    d = decide.Decider("sk-secret", "typesafe/jev-1.13", post=post)
    a = d.ask("page", {"url": "u"}, {"x": decide.noul("Is it?", true="yes", false="no"),
                                    "k": decide.choice("Which?", {"a": "A", "b": None})})
    url, body, headers = calls[0]
    assert url == "https://openrouter.ai/api/alpha/decisions" and body["model"] == "typesafe/jev-1.13"
    assert body["questions"]["x"] == {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}}
    assert a["x"].yes(0.5) and not a["x"].yes(0.95) and a["k"].choice == "a" and a["k"].confidence == 0.8
    assert headers["Authorization"] == "Bearer sk-secret" and d.calls == 1 and d.cost == pytest.approx(0.00002)


def test_many_questions_are_split_into_small_batches_that_retry_on_their_own():
    """TypeSafe fails a whole request when one question fails (Vercel, live 2026-09-24): small batches, each
    retried alone, all answered in the end."""
    import threading
    lock, calls, failed = threading.Lock(), [], set()

    def post(url, body, headers, timeout):
        first = next(iter(body["questions"]))
        with lock:
            calls.append(first)
            if first == "q4" and first not in failed:            # the second batch fails once
                failed.add(first)
                return 503, {"error": {"message": "Service temporarily unavailable"}}
        return 200, {"answers": {k: {"type": "noul", "noul": 0.9} for k in body["questions"]}}
    n = decide.BATCH * 3 + 1
    qs = {f"q{i}": decide.noul(f"Q{i}?") for i in range(n)}
    slept = []
    d = decide.Decider("k", "m", post=post, sleep=slept.append)
    got = d.ask("page", "s", qs)
    assert set(got) == set(qs) and all(a.yes(0.5) for a in got.values())
    assert len(calls) == 4 + 1 and calls.count("q4") == 2 and slept == [decide.RETRY_WAITS[0]] and d.calls == 4


def test_transient_errors_are_retried_with_backoff_then_a_decision_error():
    slept = []
    post, calls = fake_post((503, {"error": "busy"}), (429, {"error": "high demand"}), (200, None))
    decide.Decider("k", "m", post=post, sleep=slept.append).ask("t", "s", {"x": decide.noul("?")})
    assert len(calls) == 3 and slept == list(decide.RETRY_WAITS[:2])
    slept.clear()
    post, calls = fake_post((429, {"error": "rate"}))
    with pytest.raises(decide.DecisionError, match="HTTP 429"):
        decide.Decider("k", "m", post=post, sleep=slept.append).ask("t", "s", {"x": decide.noul("?")})
    assert slept == list(decide.RETRY_WAITS) and len(calls) == len(decide.RETRY_WAITS) + 1
    post, calls = fake_post((402, {"error": {"message": "credits"}}))
    with pytest.raises(decide.DecisionError, match="HTTP 402"):             # not transient: no retry
        decide.Decider("k", "m", post=post).ask("t", "s", {"x": decide.noul("?")})
    assert len(calls) == 1


def test_missing_or_malformed_answers_are_errors_never_guesses():
    post, _ = fake_post((200, {"answers": {}}))
    with pytest.raises(decide.DecisionError, match="unanswered"):
        decide.Decider("k", "m", post=post).ask("t", "s", {"x": decide.noul("?")})
    post, _ = fake_post((200, {"answers": {"x": {"type": "noul"}}}))
    with pytest.raises(decide.DecisionError, match="malformed"):
        decide.Decider("k", "m", post=post).ask("t", "s", {"x": decide.noul("?")})


def test_no_decider_configured_is_an_error():
    decide.use(None)
    with pytest.raises(decide.DecisionError, match="no decision model"):
        decide.current()


def test_a_long_state_is_cut_to_fit_and_returned_as_a_string():
    state = decide.fit_state({"url": "u", "text": "x" * (decide.STATE_CHARS * 2)})
    assert isinstance(state, str) and len(state) <= decide.STATE_CHARS and '"url": "u"' in state


def test_the_vercel_route_endpoint_cost_and_errors():
    """Vercel AI Gateway's TypeSafe-compatible API: its own endpoint and model ID, cost in provider_metadata, and
    errors as {"error": {"message", "type"}} (live 2026-09-24: 403 until a card is on file)."""
    replies = [(200, {"answers": {"x": {"type": "noul", "noul": 0.9}},
                      "provider_metadata": {"gateway": {"cost": "0.00001155"}}})]
    post, calls = fake_post(*replies)
    d = decide.Decider("gw-key", "typesafe-ai/jev", route="vercel", post=post)
    d.ask("t", "s", {"x": decide.noul("?")})
    assert calls[0][0] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone" and calls[0][1]["model"] == "typesafe-ai/jev"
    assert d.cost == pytest.approx(0.00001155)
    post, _ = fake_post((403, {"error": {"message": "AI Gateway requires a valid credit card on file",
                                         "type": "customer_verification_required"}}))
    with pytest.raises(decide.DecisionError, match="HTTP 403: AI Gateway requires a valid credit card"):
        decide.Decider("k", "m", route="vercel", post=post).ask("t", "s", {"x": decide.noul("?")})


def test_the_decider_follows_the_config(monkeypatch):
    from assistant import config
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw")
    cfg = config.load()
    vercel = cfg.model_copy(update={"models": cfg.models.model_copy(update={
        "system_one_decision_provider": "vercel", "vercel": cfg.models.vercel.model_copy(update={"system_one_decision_model": "typesafe-ai/jev"})})})
    d = decide.for_config(vercel)
    assert d.url.startswith("https://ai-gateway.vercel.sh/") and d.model == "typesafe-ai/jev"
    orouter = cfg.model_copy(update={"models": cfg.models.model_copy(update={
        "system_one_decision_provider": "openrouter", "openrouter": cfg.models.openrouter.model_copy(update={"system_one_decision_model": "respan/span-01-lite:free"})})})
    ora = decide.for_config(orouter)
    assert ora.url == "https://openrouter.ai/api/alpha/decisions" and ora.model == "respan/span-01-lite:free"


def test_the_browser_agent_follows_the_same_route(monkeypatch):
    from assistant import config, jev
    monkeypatch.setattr(config, "gateway_key", lambda *a: "gw-key")
    cfg = config.load()
    vercel = cfg.model_copy(update={"models": cfg.models.model_copy(update={
        "system_one_decision_provider": "vercel", "vercel": cfg.models.vercel.model_copy(update={"system_one_decision_model": "typesafe-ai/jev"})})})
    env = jev.env_values(vercel, "or-key")
    assert env["TYPESAFE_BASE_URL"] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert env["TYPESAFE_API_KEY"] == "gw-key" and env["TYPESAFE_MODEL"] == "typesafe-ai/jev"
    assert env["TEXT_MODEL_API_KEY"] == "or-key" and env["TEXT_MODEL_REASONING"] == "none"
    orouter = cfg.model_copy(update={"models": cfg.models.model_copy(update={"system_one_decision_provider": "openrouter"})})
    env = jev.env_values(orouter, "or-key")
    assert env["TYPESAFE_BASE_URL"] == "https://openrouter.ai/api/alpha/decisions" and "TYPESAFE_API_KEY" not in env


def test_respan_questions_flattens_instructions_and_criteria_to_strings():
    qs = {"a": decide.noul({"form_question": "Q", "question": "ask?"}, true="yes", false="no"),
          "b": decide.choice("plain?", {"x": "X", "y": {"nested": 1}})}
    out = decide.respan_questions(qs)
    assert isinstance(out["a"]["instructions"], str) and '"form_question"' in out["a"]["instructions"]
    assert out["a"]["criteria"] == {"true": "yes", "false": "no"}      # already strings: unchanged
    assert out["a"]["type"] == "noul" and out["b"]["type"] == "choice"  # type/ids preserved
    assert out["b"]["instructions"] == "plain?"                         # a plain string is left alone
    assert out["b"]["criteria"]["x"] == "X" and out["b"]["criteria"]["y"] == '{"nested": 1}'


def test_decider_sends_string_state_and_plain_questions_on_the_openrouter_route():
    post, calls = fake_post((200, None))
    d = decide.Decider("k", "respan/span-01-lite:free", route="openrouter", post=post)
    d.ask("t", {"page": "x"}, {"q": decide.noul({"field": "email", "question": "own?"})})
    body = calls[0][1]
    assert body["model"] == "respan/span-01-lite:free"
    assert isinstance(body["state"], str) and isinstance(body["questions"]["q"]["instructions"], str)


def test_jev_model_keeps_structured_questions_on_the_openrouter_route():
    post, calls = fake_post((200, None))
    d = decide.Decider("k", "typesafe/jev-1.13", route="openrouter", post=post)
    d.ask("t", {"page": "x"}, {"q": decide.noul({"field": "email", "question": "own?"})})
    assert calls[0][1]["questions"]["q"]["instructions"] == {"field": "email", "question": "own?"}  # not flattened
    assert decide.adapt_questions_for("respan/span-01-lite:free")
    assert not decide.adapt_questions_for("typesafe/jev-1.13") and not decide.adapt_questions_for("jev-latest")


# ------------------------------------------------------------------ a Kev server on this machine (models.local)

def local_post(*replies):
    """Like fake_post, but keeps the timeout each attempt was given."""
    post, calls = fake_post(*replies)
    seen = []

    def wrapper(url, body, headers, timeout):
        seen.append(timeout)
        return post(url, body, headers, timeout)
    return wrapper, calls, seen


def local_config(**kw):
    from assistant import config
    return config.Config(paths=config.Paths(base="."),
                         models=config.Models(system_one_decision_provider="local",
                                              local=config.LocalKev(**kw)))


def test_local_route_posts_system_one_to_the_kev_server_with_no_key():
    """A Kev server is open by default and ignores Authorization; the questions keep TypeSafe's structured shape,
    which its API takes as well (criteria values are JSONContent)."""
    post, calls, timeouts = local_post((200, None))
    d = decide.Decider("", "kev-latest", url="http://127.0.0.1:8009/v1/systemone", timeout=120.0, post=post)
    a = d.ask("page", {"page": "x"}, {"q": decide.noul({"field": "email", "question": "own?"})})
    url, body, headers = calls[0]
    assert url == "http://127.0.0.1:8009/v1/systemone" and body["model"] == "kev-latest"
    assert body["questions"]["q"]["instructions"] == {"field": "email", "question": "own?"}   # not flattened
    assert "Authorization" not in headers and timeouts == [120.0] and a["q"].yes(0.5)


def test_local_route_sends_the_key_when_the_server_was_started_with_one():
    post, calls = fake_post((200, None))
    decide.Decider("kev-secret", "kev-latest", url="http://127.0.0.1:8009/v1/systemone", post=post) \
        .ask("page", "x", {"q": decide.noul("own?")})
    assert calls[0][2]["Authorization"] == "Bearer kev-secret"


def test_a_short_state_is_cut_to_the_local_limit():
    post, calls = fake_post((200, None))
    d = decide.Decider("", "kev-latest", url="http://x/v1/systemone", state_chars=300, post=post)
    d.ask("page", {"page": {"text": "long " * 500}, "elements": []}, {"q": decide.noul("own?")})
    assert len(calls[0][1]["state"]) <= 300
    assert len(decide.fit_state("x" * 900, 300)) == 300 and len(decide.fit_state("x" * 900)) == 900


def test_a_server_that_is_down_fails_after_the_short_local_ladder():
    post, calls = fake_post((0, {"error": "ConnectError"}))
    waits = []
    d = decide.Decider("", "kev-latest", url="http://127.0.0.1:8009/v1/systemone", post=post,
                       retry_waits=decide.LOCAL_RETRY_WAITS, sleep=waits.append)
    with pytest.raises(decide.DecisionError):
        d.ask("page", "x", {"q": decide.noul("own?")})
    assert waits == list(decide.LOCAL_RETRY_WAITS) and len(calls) == len(decide.LOCAL_RETRY_WAITS) + 1


def test_for_config_wires_the_local_server_its_url_size_timeout_and_ladder(monkeypatch, tmp_path):
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    from assistant import config
    monkeypatch.setattr(config, "DEFAULT_ENV", tmp_path / "no.env")
    d = decide.for_config(local_config(base_url="http://127.0.0.1:8010/", state_chars=5000, timeout=90.0))
    assert d.url == "http://127.0.0.1:8010/v1/systemone" and d.model == "kev-latest" and d.key == ""
    assert (d.state_chars, d.timeout, d.retry_waits) == (5000, 90.0, decide.LOCAL_RETRY_WAITS)


def test_the_cloud_routes_are_unchanged():
    from assistant import config
    cfg = config.Config(paths=config.Paths(base="."),
                        models=config.Models(system_one_decision_provider="vercel",
                                             vercel=config.ChatModels(system_one_decision_model="typesafe-ai/jev")))
    assert decide.endpoint(cfg) == decide.ENDPOINTS["vercel"] and decide.start_hint(cfg) == ""
    d = decide.Decider("k", "typesafe-ai/jev", route="vercel")
    assert (d.state_chars, d.timeout, d.retry_waits) == (decide.STATE_CHARS, decide.TIMEOUT, decide.RETRY_WAITS)


def test_the_start_hint_names_the_command_and_the_configured_port():
    hint = decide.start_hint(local_config(base_url="http://127.0.0.1:8123"))
    assert "run_kev_server.command" in hint and "--port 8123" in hint


def test_server_card_reports_the_loaded_checkpoint(monkeypatch):
    import httpx

    def get(url, timeout=None, headers=None):
        assert url == "http://127.0.0.1:8009/v1/models" and not headers
        return httpx.Response(200, json={"models": [{"name": "kev-latest", "run": "jaredpalmer/kev-0.8b",
                                                     "backend": "mlx", "dtype": "bfloat16", "device": "mps"}]},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    from assistant import config
    monkeypatch.setattr(config, "local_key", lambda *a: "")
    assert decide.server_card(local_config())["run"] == "jaredpalmer/kev-0.8b"


def test_server_card_says_how_to_start_a_server_that_is_not_running(monkeypatch):
    import httpx

    def get(url, timeout=None, headers=None):
        raise httpx.ConnectError("Connection refused")
    monkeypatch.setattr(httpx, "get", get)
    from assistant import config
    monkeypatch.setattr(config, "local_key", lambda *a: "")
    with pytest.raises(decide.DecisionError, match="run_kev_server.command"):
        decide.server_card(local_config())


def test_the_local_route_keeps_a_dict_state_but_still_honours_the_limit():
    post, calls = fake_post((200, None), (200, None))
    d = decide.Decider("", "kev-latest", url="http://x/v1/systemone", state_chars=4000, keep_object_state=True,
                       post=post)
    d.ask("page", {"url": "u", "text": "x" * 9000}, {"q": decide.noul("own?")})
    state = calls[0][1]["state"]      # a dict whose long text was cut to fit stays a dict (Kev labels its fields)
    assert isinstance(state, dict) and len(json.dumps(state)) <= 4000 and state["url"] == "u"
    d.ask("page", {"controls": [{"ref": f"e{i}"} for i in range(500)]}, {"q": decide.noul("own?")})
    state = calls[1][1]["state"]      # the cut only shortens long string values: what is still over is serialised
    assert isinstance(state, str) and len(state) <= 4000
