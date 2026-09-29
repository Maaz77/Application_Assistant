"""A2: no model request bypasses the Gateway (P1 T7).

Both HTTP clients that could carry a model request — `httpx.post` and `httpx.Client.post`, which our senders could call — are replaced with one that fails the test if it is ever used. Every sender is
then exercised. A sender that still had a way around the Gateway would reach one of them and fail here.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from assistant import decide, gateway as G, llm_inference

pytestmark = pytest.mark.unit

ANSWERS = {"x": {"type": "noul", "noul": 0.9}}
PAGE_ANSWERS = json.dumps({"questions": []})


class Bypassed(AssertionError):
    """Raised by the clients nothing may use any more."""


@pytest.fixture
def sealed(monkeypatch):
    """No HTTP client is usable; one Gateway with an injected sender is."""
    def forbidden(*a, **kw):
        raise Bypassed("a model request was sent without going through the Gateway")
    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, body))
        if "chat/completions" not in url:
            return 200, {"answers": {k: ANSWERS["x"] for k in body.get("questions", {})}}
        # PageAnswers forbids extra keys and ChatDecider wants an "answers" object, so answer by who asked:
        # the fallback names its questions in the user message, LLM inference sends a page.
        try:
            asked = json.loads(body["messages"][-1]["content"])
        except ValueError:                      # the package's text helper sends plain text
            asked = {}
        content = json.dumps({"answers": {k: ANSWERS["x"] for k in asked["questions"]}}) \
            if "questions" in asked and isinstance(asked.get("questions"), dict) else PAGE_ANSWERS
        return 200, {"choices": [{"message": {"content": content}}]}
    gateway = G.Gateway(limits=SimpleNamespace(max_in_flight=1, min_interval_s=0.0, max_attempts=3),
                        budget=SimpleNamespace(max_usd_per_run=0.0), post=post)
    G.use(gateway)
    yield SimpleNamespace(gateway=gateway, sent=sent)
    G.use(None)


def test_the_decider_sends_only_through_the_gateway(sealed):
    decide.Decider("k", "m", url="http://x/v1/systemone").ask("t", "s", {"x": decide.noul("?")})
    assert sealed.gateway.run.requests == 1 and len(sealed.sent) == 1


def test_llm_inference_sends_only_through_the_gateway(sealed):
    llm_inference.call_engine(key="k", models="m", system="s", user={},
                              url="https://openrouter.ai/api/v1/chat/completions")
    assert sealed.gateway.run.chat_requests == 1


def test_the_chat_fallback_sends_only_through_the_gateway(sealed):
    decide.ChatDecider("k", ["m"], url="https://x/v1/chat/completions") \
        .ask("t", "s", {"x": decide.noul("?")})
    assert sealed.gateway.run.chat_requests == 1


def test_a_sender_with_no_gateway_refuses_instead_of_sending_itself(monkeypatch):
    def forbidden(*a, **kw):
        raise Bypassed("sent without a Gateway")
    monkeypatch.setattr(httpx, "post", forbidden)
    G.use(None)
    with pytest.raises(RuntimeError, match="may not bypass"):
        decide.Decider("k", "m", url="http://x/v1/systemone").ask("t", "s", {"x": decide.noul("?")})
    with pytest.raises(RuntimeError, match="may not bypass"):
        llm_inference.call_engine(key="k", models="m", system="s", user={})


def test_only_the_gateway_names_an_http_client_for_a_model_request():
    """A static check, so a new sender added later cannot quietly reintroduce a second path: the only `httpx.post`
    in a model-sending module is the Gateway's own default sender."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "assistant"
    for module in ("decide.py", "llm_inference.py"):
        text = (root / module).read_text()
        assert "httpx.post" not in text, module
