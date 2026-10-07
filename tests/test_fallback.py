"""decide.ChatDecider and the Decider's use of it (P1 T4, D19).

Jev is asked first. When a System One request fails after all of the Gateway's attempts, the same typed questions go
once to a chat model, which returns the same answer shapes. The breaker counts the pair as one failure only when
both fail, so a decision the fallback rescued is not evidence that the provider is out.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assistant import decide
from assistant import gateway as G

pytestmark = pytest.mark.unit


def gw(**kw) -> G.Gateway:
    return G.Gateway(limits=SimpleNamespace(max_in_flight=1, min_interval_s=0.0, max_attempts=3),
                     sleep=lambda _: None, **kw)


def chat_reply(answers: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"answers": answers})}}]}


def router(**by_host):
    """post() that answers by URL: "systemone" for the System One route, "chat" for chat/completions."""
    calls = []

    def post(url, body, headers, timeout):
        kind = "chat" if "chat/completions" in url else "systemone"
        calls.append((kind, body))
        reply = by_host[kind]
        return reply(body) if callable(reply) else reply
    return post, calls


QS = {"x": decide.noul("Is it?"), "k": decide.choice("Which?", {"a": "A", "b": "B"})}
BOTH = {"x": {"type": "noul", "noul": 0.8},
        "k": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.9}}


def wired(post, *, fallback=True):
    gateway = gw(post=post)
    chat = decide.ChatDecider("k", ["chat-a"], url="https://x/v1/chat/completions", gateway=gateway) \
        if fallback else None
    d = decide.Decider("k", "kev-latest", url="http://127.0.0.1:8009/v1/systemone",
                       fallback=chat, gateway=gateway)
    return d, gateway


# ------------------------------------------------------------------ ChatDecider on its own


def test_the_chat_fallback_answers_the_same_typed_questions():
    post, calls = router(chat=(200, chat_reply(BOTH)))
    chat = decide.ChatDecider("k", ["chat-a"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    a = chat.ask("page", "the state", QS)
    assert a["x"].yes(0.5) and not a["x"].yes(0.95)
    assert a["k"].choice == "a" and a["k"].confidence == 0.9 and a["k"].probabilities == {"a": 0.9, "b": 0.1}
    body = calls[0][1]
    assert body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}
    assert "the state" in body["messages"][1]["content"] and "noul" in body["messages"][0]["content"]


def test_the_question_types_reach_the_chat_model():
    post, calls = router(chat=(200, chat_reply(BOTH)))
    decide.ChatDecider("k", ["m"], url="https://x/v1/chat/completions", gateway=gw(post=post)) \
        .ask("page", "s", QS)
    sent = json.loads(calls[0][1]["messages"][1]["content"])["questions"]
    assert sent["x"]["type"] == "noul" and sent["k"]["type"] == "choice"


def test_a_missing_answer_is_an_error_never_a_guess():
    post, _ = router(chat=(200, chat_reply({"x": {"type": "noul", "noul": 0.8}})))
    chat = decide.ChatDecider("k", ["m"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    with pytest.raises(decide.DecisionError, match="unanswered"):
        chat.ask("page", "s", QS)


def test_a_malformed_answer_is_an_error_never_a_guess():
    post, _ = router(chat=(200, chat_reply({"x": {"type": "noul"}, "k": BOTH["k"]})))
    chat = decide.ChatDecider("k", ["m"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    with pytest.raises(decide.DecisionError, match="malformed"):
        chat.ask("page", "s", QS)


def test_output_that_is_not_the_json_asked_for_moves_to_the_next_model():
    replies = [(200, {"choices": [{"message": {"content": "sorry, I cannot"}}]}), (200, chat_reply(BOTH))]
    seen = []

    def post(url, body, headers, timeout):
        seen.append(body["model"])
        return replies[min(len(seen), len(replies)) - 1]
    chat = decide.ChatDecider("k", ["a", "b"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    assert chat.ask("page", "s", QS)["x"].yes(0.5) and seen == ["a", "b"]


def test_fenced_json_is_accepted():
    fenced = {"choices": [{"message": {"content": "```json\n" + json.dumps({"answers": BOTH}) + "\n```"}}]}
    post, _ = router(chat=(200, fenced))
    chat = decide.ChatDecider("k", ["m"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    assert chat.ask("page", "s", QS)["k"].choice == "a"


def test_when_no_chat_model_answers_it_is_a_decision_error():
    post, _ = router(chat=(500, {"error": "boom"}))
    chat = decide.ChatDecider("k", ["a", "b"], url="https://x/v1/chat/completions", gateway=gw(post=post))
    with pytest.raises(decide.DecisionError, match="chat fallback did not answer"):
        chat.ask("page", "s", QS)


# ------------------------------------------------------------------ the Decider's use of it


def test_system_one_is_asked_first_and_the_fallback_is_not_touched():
    post, calls = router(systemone=(200, {"answers": BOTH}), chat=(200, chat_reply(BOTH)))
    d, gateway = wired(post)
    assert d.ask("page", "s", QS)["x"].yes(0.5)
    assert [c[0] for c in calls] == ["systemone"] and d.by_fallback == 0 and gateway.run.fallbacks == 0


def test_when_system_one_fails_the_fallback_answers_and_it_is_counted():
    post, calls = router(systemone=(503, {"error": "down"}), chat=(200, chat_reply(BOTH)))
    d, gateway = wired(post)
    a = d.ask("page", "s", QS)
    assert a["k"].choice == "a"
    assert [c[0] for c in calls] == ["systemone"] * 3 + ["chat"]      # the Gateway's attempts, then one fallback
    assert d.by_fallback == 1 and gateway.run.fallbacks == 1


def test_a_rescued_request_is_not_a_failure_for_the_breaker():
    """Three System One requests in a row fail, but the fallback answers each: the run goes on (T4)."""
    post, _ = router(systemone=(503, {"error": "down"}), chat=(200, chat_reply(BOTH)))
    d, gateway = wired(post)
    for i in range(5):
        d.ask("page", f"state-{i}", QS)
    assert gateway.tripped is None and gateway.run.failures == 0 and d.by_fallback == 5


def test_both_failing_is_one_failure_and_trips_the_breaker_after_three():
    post, _ = router(systemone=(503, {"error": "down"}), chat=(500, {"error": "also down"}))
    d, gateway = wired(post)
    for _ in range(2):
        with pytest.raises(decide.DecisionError, match="chat fallback also failed"):
            d.ask("page", "s", QS)
    assert gateway.run.failures == 2
    with pytest.raises(G.ProviderOutage):
        d.ask("page", "s", QS)


def test_the_fallback_can_be_turned_off():
    post, calls = router(systemone=(503, {"error": "down"}), chat=(200, chat_reply(BOTH)))
    d, gateway = wired(post, fallback=False)
    with pytest.raises(decide.DecisionError, match="HTTP 503"):
        d.ask("page", "s", QS)
    assert [c[0] for c in calls] == ["systemone"] * 3 and gateway.run.failures == 1


def test_the_fallback_does_not_deadlock_the_single_slot():
    """The fallback sends while the System One request still holds the only slot; `Gateway.release()` is what keeps
    this from waiting on itself for ever. A failure here hangs rather than fails, so it is worth its own test."""
    post, _ = router(systemone=(503, {"error": "down"}), chat=(200, chat_reply(BOTH)))
    d, gateway = wired(post)
    assert d.ask("page", "s", QS)["x"].yes(0.5) and gateway._live == 0


def test_a_malformed_system_one_answer_is_not_an_outage():
    """The model answered; a malformed answer is a bug or a weak model, not a provider being out."""
    post, _ = router(systemone=(200, {"answers": {"x": {"type": "noul"}, "k": BOTH["k"]}}))
    d, gateway = wired(post, fallback=False)
    with pytest.raises(decide.DecisionError, match="malformed"):
        d.ask("page", "s", QS)
    assert gateway.run.failures == 0


# ------------------------------------------------------------------ config


def test_for_config_wires_the_fallback_only_when_it_is_asked_for(monkeypatch, tmp_path):
    from assistant import config
    monkeypatch.setattr(config, "DEFAULT_ENV", tmp_path / "no.env")
    cfg = config.load()
    assert cfg.decider.fallback == "chat"
    assert isinstance(decide.for_config(cfg).fallback, decide.ChatDecider)
    off = cfg.model_copy(update={"decider": config.Decider(fallback="none")})
    assert decide.for_config(off).fallback is None


def test_the_fallback_uses_the_chat_route_and_its_primary_model(monkeypatch, tmp_path):
    from assistant import config
    monkeypatch.setattr(config, "DEFAULT_ENV", tmp_path / "no.env")
    cfg = config.load()
    chat = decide.for_config(cfg).fallback
    assert chat.url == config.chat_url(cfg) and chat.models == list(cfg.models.llm_inference)


# ------------------------------------------------------------------ cache


def test_cache_keys_on_full_question_body_not_just_ids():
    """Same state, same IDs, different question body must produce two requests, not a cache hit."""
    calls = []

    def counting_post(url, body, headers, timeout):
        calls.append(body)
        return 200, {"answers": BOTH}

    d, _ = wired(counting_post, fallback=False)
    q1 = {"x": decide.noul("Is the sky blue?")}
    q2 = {"x": decide.noul("Is the ocean green?")}
    d.ask("page", "same-state", q1)
    d.ask("page", "same-state", q2)
    assert len(calls) == 2, "different question body with same ID should not cache-hit"
    d.ask("page", "same-state", q1)
    assert len(calls) == 2, "identical repeat should cache-hit"
