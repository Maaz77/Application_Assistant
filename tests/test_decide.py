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
