"""Manual check: is the FreeLLMAPI key valid, and can each configured model generate text?

The models checked are the ones in config.toml (models.freellmapi.llm_inference), so this follows the config
instead of a list that drifts from it. Run it (free — every model behind the router is a free tier):

    .venv/bin/pytest tests/test_model_access.py -s

Each model gets a report (shown with -s, and in the failure message):
  key     — is the key accepted at all (the router answers HTTP 401 without one)
  model   — is the ID in the router's catalogue, is it available, does it list `response_format` (the LLM
            inference asks for a strict json_schema, and a model that does not list it answers prose at HTTP 200)
  reply   — one real completion, sent with the same max_tokens the LLM inference reserves, plus which free
            platform the router actually routed to (`_routed_via`)
The key itself is never printed. There is deliberately no `live_model` marker: a plain pytest run checks this.
"""
import time

import httpx
import pytest

from assistant import config
from assistant.llm_inference import MAX_TOKENS, REASONING_EFFORT

ROUTING_MODES = ("auto", "auto:fast", "auto:smart", "fusion")   # the router chooses the model behind these

TIMEOUT = 90.0


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:300]}


def _error(body: dict) -> str:
    """The router's errors: {"error": {"message", "type", "code", "retryAtMs"}}."""
    err = body.get("error") or {}
    if not err:
        return str(body)[:300]
    return f"{err.get('type') or err.get('code')} {str(err.get('message'))[:220]}"


@pytest.fixture(scope="module")
def catalogue(chat_key, cfg):
    """GET /v1/models once, with the key: {model id: entry}. Skips when the router is not running."""
    url = cfg.models.freellmapi.base_url.rstrip("/") + "/models"
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {chat_key}"}, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        pytest.skip(f"no FreeLLMAPI router answers on {url} ({type(exc).__name__}: {exc}). Start it first.")
    assert r.status_code == 200, f"key    HTTP {r.status_code} · {_error(_json(r))}"
    return {m.get("id"): m for m in (_json(r).get("data") or [])}


def test_the_configured_models_are_in_the_catalogue(catalogue, cfg):
    """Every models.freellmapi.llm_inference entry exists, is available, and honours response_format. A typo or a
    retired ID would otherwise only show up as "none of N models answered" mid-run.

    A routing mode (`auto`, `auto:*`, `fusion`) is checked only for being listed and available: the router picks
    the underlying model per request, so its catalogue entry carries no `supported_parameters` to inspect. A
    concrete ID must additionally list `response_format`, or it cannot honour the strict json_schema."""
    problems = []
    for model in cfg.models.llm_inference:
        entry = catalogue.get(model)
        if entry is None:
            problems.append(f"{model}: NOT in the catalogue — check the spelling against GET /v1/models")
            continue
        if not entry.get("available"):
            problems.append(f"{model}: listed but not available ({entry.get('unavailable_reason')})")
        if model in ROUTING_MODES:
            continue
        if "response_format" not in (entry.get("supported_parameters") or []):
            problems.append(f"{model}: does not list response_format, so it will not honour the strict "
                            f"json_schema the LLM inference asks for")
    assert not problems, "\n".join(problems)


def test_each_configured_model_replies(chat_key, cfg, catalogue):
    lines = []
    ok = []
    url = config.chat_url(cfg)
    for model in cfg.models.llm_inference:
        entry = catalogue.get(model) or {}
        lines.append(f"== {model}")
        lines.append(f"model    listed {entry.get('id') is not None} · available {entry.get('available')} · "
                     f"context {entry.get('context_window')}")
        t0 = time.monotonic()
        try:
            r = httpx.post(url, timeout=TIMEOUT,
                           headers={"Authorization": f"Bearer {chat_key}", "Content-Type": "application/json"},
                           json={"model": model, "temperature": 0, "max_tokens": MAX_TOKENS,
                                 "reasoning_effort": REASONING_EFFORT,
                                 "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}]})
            status, body = r.status_code, _json(r)
        except httpx.HTTPError as exc:
            status, body = 0, {"error": {"code": "network", "message": str(exc)}}
        secs = time.monotonic() - t0
        if status == 200 and body.get("choices"):
            choice = body["choices"][0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            usage = body.get("usage") or {}
            routed = body.get("_routed_via") or {}
            ok.append(model)
            lines.append(f"reply    HTTP 200 · {secs:.1f}s · routed to {routed.get('platform')}/"
                         f"{routed.get('model')} · finish {choice.get('finish_reason')} · tokens in "
                         f"{usage.get('prompt_tokens')} / out {usage.get('completion_tokens')} · "
                         f"text {text[:60]!r}"
                         + ("  (empty: a reasoning model may have spent its tokens thinking)" if not text else ""))
        else:
            lines.append(f"reply    HTTP {status} · {secs:.1f}s · {_error(body)}")
            if status == 429:
                lines.append("         → this model's free tier is exhausted; the rotation would hand over to "
                             "the next configured model")
    report = "\n".join(lines)
    print("\n" + report)
    # A free tier that is out answers 429, and the rotation exists for exactly that: one model answering is the
    # bar, not all of them. A run with none answering is what sends a job to Needs Attention.
    assert ok, report
