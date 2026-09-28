"""Manual check: is the OpenRouter key valid, and can it generate text with a given model?

Put the model IDs you want to check in the `parametrize` list below, then run (costs a few tokens per model):

    .venv/bin/pytest tests/test_model_access.py --live -s

Each model gets a report (shown with -s, and in the failure message):
  key     — is the key accepted, its own spending limit, what it has used
  account — credits bought vs used on the key's account (the 402 "can only afford N tokens" comes from here)
  model   — is the ID in OpenRouter's catalogue, its price, does it support structured outputs (the answer
            engine asks for json_schema)
  reply   — one real completion, sent with the same max_tokens the LLM inference reserves, so a too-small
            balance fails here exactly as it does in a run
The key itself is never printed.
"""
import time

import httpx
import pytest

from assistant.llm_inference import MAX_TOKENS, OPENROUTER_CHAT

#pytestmark = pytest.mark.live_model

API = "https://openrouter.ai/api/v1"
TIMEOUT = 60.0


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:300]}


def _get(path: str, key: str | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = httpx.get(API + path, headers=headers, timeout=TIMEOUT)
    return r.status_code, _json(r)


def _money(x) -> str:
    return "unlimited" if x is None else f"${float(x):.4f}"


def _per_million(price) -> str:
    return "?" if price is None else f"${float(price) * 1e6:.2f}/M"


def _error(body: dict) -> str:
    """OpenRouter errors: {"error": {"code", "message", "metadata": {"raw", "provider_name"}}}."""
    err = body.get("error") or {}
    meta = err.get("metadata") or {}
    extra = f" · provider {meta.get('provider_name')}" if meta.get("provider_name") else ""
    raw = f" · upstream: {str(meta.get('raw'))[:200]}" if meta.get("raw") else ""
    return f"{err.get('code')} {str(err.get('message'))[:200]}{extra}{raw}" if err else str(body)[:300]


@pytest.mark.parametrize("model", [
    "qwen/qwen3.7-flash",        # models.llm_inference in config.toml
    "deepseek/deepseek-chat",       # models.text_helper (the page goals' text helper)
])
def test_model_is_accessible(model, api_key):
    lines = [f"== {model}"]

    # 1. The key: accepted? its own limit and usage.
    status, body = _get("/key", api_key)
    data = body.get("data") or {}
    label = str(data.get("label") or "?")
    label = "(unnamed key)" if label.startswith("sk-") else label[:24]   # an unnamed key's label previews the key
    lines.append(f"key      HTTP {status} · label {label} · free tier {data.get('is_free_tier')} · "
                 f"key limit {_money(data.get('limit'))} (left {_money(data.get('limit_remaining'))}) · "
                 f"used {_money(data.get('usage'))}" if status == 200 else f"key      HTTP {status} · {_error(body)}")
    key_ok = status == 200

    # 2. The account behind the key: credits bought vs used.
    if key_ok:
        status, body = _get("/credits", api_key)
        data = body.get("data") or {}
        if status == 200:
            bought, used = float(data.get("total_credits") or 0), float(data.get("total_usage") or 0)
            lines.append(f"account  credits bought {_money(bought)} · used {_money(used)} · left {_money(bought - used)}")
        else:
            lines.append(f"account  HTTP {status} · {_error(body)}")

    # 3. The model in the public catalogue.
    status, body = _get("/models")
    entry = next((m for m in body.get("data", []) if m.get("id") == model), None)
    if entry is None:
        lines.append(f"model    NOT in the catalogue (HTTP {status}) — check the ID's spelling and variant (e.g. ':free')")
    else:
        price = entry.get("pricing") or {}
        params = entry.get("supported_parameters") or []
        lines.append(f"model    listed · context {entry.get('context_length')} · in {_per_million(price.get('prompt'))} · "
                     f"out {_per_million(price.get('completion'))} · structured outputs "
                     f"{'yes' if 'structured_outputs' in params else 'no'} · reasoning "
                     f"{'yes' if 'reasoning' in params else 'no'}")

    # 4. One real completion, reserving max_tokens like the LLM inference does.
    reply_ok = False
    if key_ok:
        t0 = time.monotonic()
        try:
            r = httpx.post(OPENROUTER_CHAT, timeout=TIMEOUT,
                           headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                           json={"model": model, "temperature": 0, "max_tokens": MAX_TOKENS,
                                 "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}]})
            status, body = r.status_code, _json(r)
        except httpx.HTTPError as exc:
            status, body = 0, {"error": {"code": "network", "message": str(exc)}}
        secs = time.monotonic() - t0
        if status == 200 and body.get("choices"):
            choice = body["choices"][0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            usage = body.get("usage") or {}
            reply_ok = True
            lines.append(f"reply    HTTP 200 · {secs:.1f}s · provider {body.get('provider', '?')} · finish "
                         f"{choice.get('finish_reason')} · tokens in {usage.get('prompt_tokens')} / out "
                         f"{usage.get('completion_tokens')} · text {text[:60]!r}"
                         + ("  (empty: a reasoning model may have spent its tokens thinking)" if not text else ""))
        else:
            lines.append(f"reply    HTTP {status} · {secs:.1f}s · {_error(body)}")
            if status == 402:
                lines.append(f"         → the account cannot cover max_tokens={MAX_TOKENS} for this model")
            elif status == 429:
                lines.append("         → rate-limited (free models: upstream provider or the daily free quota)")

    report = "\n".join(lines)
    print("\n" + report)
    assert key_ok, report
    assert reply_ok, report
