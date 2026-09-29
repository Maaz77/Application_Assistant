"""decide.py, offline: the System One request, batching, retries, errors and the decisions log."""
import json

import pytest

from assistant import decide
from assistant import gateway as G

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


def test_many_questions_are_split_and_the_parts_go_one_after_another():
    """TypeSafe fails a whole request when one question fails (Vercel, live 2026-09-24), so a judgment above
    jev.max_questions_per_request is split. P1 T2: the parts go one after another, never side by side."""
    order, live, peak = [], [0], [0]

    def post(url, body, headers, timeout):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        order.append(len(body["questions"]))
        live[0] -= 1
        return 200, {"answers": {k: {"type": "noul", "noul": 0.9} for k in body["questions"]}}

    qs = {f"q{i}": decide.noul(f"Q{i}?") for i in range(10)}
    d = decide.Decider("k", "m", post=post, max_questions=4)
    got = d.ask("page", "s", qs)
    assert set(got) == set(qs) and all(a.yes(0.5) for a in got.values())
    assert order == [4, 4, 2] and peak[0] == 1 and d.calls == 3


def test_a_judgment_under_the_limit_is_one_request():
    post, calls = fake_post((200, None))
    qs = {f"q{i}": decide.noul(f"Q{i}?") for i in range(decide.MAX_QUESTIONS)}
    decide.Decider("k", "m", post=post).ask("page", "s", qs)
    assert len(calls) == 1


def test_transient_errors_are_retried_by_the_gateway_then_a_decision_error():
    """P1 T2: the Gateway is the only retry layer — at most 3 attempts, waiting 2 s then 6 s."""
    from assistant import gateway as G
    slept = []
    post, calls = fake_post((503, {"error": "busy"}), (429, {"error": "high demand"}), (200, None))
    decide.Decider("k", "m", post=post, sleep=slept.append).ask("t", "s", {"x": decide.noul("?")})
    assert len(calls) == 3 and slept == list(G.BACKOFF)
    slept.clear()
    post, calls = fake_post((429, {"error": "rate"}))
    with pytest.raises(decide.DecisionError, match="HTTP 429"):
        decide.Decider("k", "m", post=post, sleep=slept.append).ask("t", "s", {"x": decide.noul("?")})
    assert len(calls) == 3 and slept == list(G.BACKOFF)          # three attempts, not the old five-wait ladder
    post, calls = fake_post((500, {"error": "boom"}))
    with pytest.raises(decide.DecisionError, match="HTTP 500"):
        decide.Decider("k", "m", post=post, sleep=lambda _: None).ask("t", "s", {"x": decide.noul("?")})
    assert len(calls) == 3


def test_no_credit_stops_the_run_instead_of_failing_one_job():
    """P1 T3: 401, 402 and 403 are CreditOrKey — a clean stop, not a retry and not one job's Needs Attention."""
    from assistant import gateway as G
    for status in (401, 402, 403):
        post, calls = fake_post((status, {"error": {"message": "credits"}}))
        with pytest.raises(G.CreditOrKey):
            decide.Decider("k", "m", post=post).ask("t", "s", {"x": decide.noul("?")})
        assert len(calls) == 1                                   # never retried


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
    # P1 T3: 403 is CreditOrKey — a clean stop, not one job's failure. The provider's own words are kept.
    with pytest.raises(G.CreditOrKey, match="HTTP 403: AI Gateway requires a valid credit card"):
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


# The browser-agent env-route test (jev.env_values) is dropped: the package is gone in P2, and the System One
# route per provider is covered by test_for_config_picks_the_route_url_and_model above.


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


def test_a_server_that_is_down_fails_after_the_gateways_attempts():
    """A server on this machine is never "in high demand": a failure is a server that is down or a model out of
    memory, and neither is cured by waiting. The Gateway's three attempts are the whole ladder now (P1 T2)."""
    post, calls = fake_post((0, {"error": "ConnectError"}))
    waits = []
    d = decide.Decider("", "kev-latest", url="http://127.0.0.1:8009/v1/systemone", post=post, sleep=waits.append)
    with pytest.raises(decide.DecisionError):
        d.ask("page", "x", {"q": decide.noul("own?")})
    assert len(calls) == 3 and waits == list(G.BACKOFF)


def test_for_config_wires_the_local_server_its_url_size_and_timeout(monkeypatch, tmp_path):
    monkeypatch.delenv("KEV_API_KEY", raising=False)
    from assistant import config
    monkeypatch.setattr(config, "DEFAULT_ENV", tmp_path / "no.env")
    d = decide.for_config(local_config(base_url="http://127.0.0.1:8010/", state_chars=5000, timeout=90.0))
    assert d.url == "http://127.0.0.1:8010/v1/systemone" and d.model == "kev-latest" and d.key == ""
    assert d.state_chars == 5000
    # a System One model on this Mac answers in seconds, not a data-centre's milliseconds: its own timeout, not 20 s
    assert G.for_config(local_config(base_url="http://x", state_chars=5000, timeout=90.0)).timeouts[G.JEV] == 90.0


def test_the_cloud_routes_are_unchanged():
    from assistant import config
    cfg = config.Config(paths=config.Paths(base="."),
                        models=config.Models(system_one_decision_provider="vercel",
                                             vercel=config.ChatModels(system_one_decision_model="typesafe-ai/jev")))
    assert decide.endpoint(cfg) == decide.ENDPOINTS["vercel"] and decide.start_hint(cfg) == ""
    d = decide.Decider("k", "typesafe-ai/jev", route="vercel")
    assert d.state_chars == decide.STATE_CHARS and d.timeout is None   # the Gateway holds the timeout
    assert G.for_config(cfg).timeouts[G.JEV] == 20.0                   # a cloud System One route


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
