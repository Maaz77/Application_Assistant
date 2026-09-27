"""Per-run, per-job logs of every HTTP attempt to a chat model and to Jev (00_common §6).

Stdlib only. The orchestrator calls start_run() once, then set_scope()/scope() around each job; the senders
(decide.py, llm_inference.py, and jev.py's package hook) call log_jev()/log_llm() after every attempt. Each log is
rewritten atomically after every append, so the file on disk is always valid JSON at all times (§6.4). The scope is
a module-level value: jobs run strictly in sequence and browser work may run on another thread, so a contextvar
would put those threads in the wrong scope (§6.4).
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_RUN = "_run"
_LLM_FILE = "llm_inference_logs.json"
_JEV_FILE = "jev_inference_logs.json"
_SURROGATE = re.compile("[\ud800-\udfff]")   # lone UTF-16 halves from JS-cut page text; cf. jev.clean_text
_JEV_BUCKET = {"score": "Score", "noul": "Noul", "choice": "Choice"}

_lock = threading.Lock()
_run_dir: Path | None = None
_scope: str = _RUN
_buffers: dict[tuple[str, str], list] = {}


# ------------------------------------------------------------------ run and scope

def start_run(run_dir: Path) -> None:
    """Begin a run: logs go under run_dir/<scope>/. Resets the scope to _run and the in-memory buffers."""
    global _run_dir, _scope, _buffers
    with _lock:
        _run_dir = Path(run_dir)
        _scope = _RUN
        _buffers = {}


def set_scope(name: str | None) -> None:
    """The folder logs land in until the next call: a job folder name, or _run for work outside a job."""
    global _scope
    _scope = name or _RUN


@contextmanager
def scope(name: str | None):
    global _scope
    prev = _scope
    _scope = name or _RUN
    try:
        yield
    finally:
        _scope = prev


def gateway_of(url: str) -> str:
    """The gateway a chat/Jev URL points at, from its host (00_common §6.2): openrouter.ai -> openrouter,
    ai-gateway.vercel.sh -> vercel."""
    host = urlparse(url).hostname or ""
    if "openrouter" in host:
        return "openrouter"
    if "vercel" in host:
        return "vercel"
    return host or "unknown"


# ------------------------------------------------------------------ writing

def _clean(value: Any) -> Any:
    """§6.4: every string passes clean_text (lone UTF-16 surrogates -> '?') before it is written."""
    if isinstance(value, str):
        return _SURROGATE.sub("?", value)
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _append(filename: str, entry: dict) -> None:
    """Append one entry to (scope, filename)'s list and rewrite the whole file atomically (§6.4)."""
    if _run_dir is None:      # no run started (e.g. a unit test that does not exercise logging): a no-op
        return
    with _lock:
        folder = _run_dir / _scope
        buf = _buffers.setdefault((_scope, filename), [])
        buf.append(_clean(entry))
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (filename + ".tmp")
        tmp.write_text(json.dumps(buf, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, folder / filename)


# ------------------------------------------------------------------ chat models (§6.2)

def _provider(gateway: str, response: Any) -> str:
    """The 'provider' field of a §6.2 entry: f"{gateway}/{upstream}" where `upstream` is the provider the gateway
    reports it actually routed to, or just `gateway` when none is reported."""
    if not isinstance(response, dict):          # None, or a non-JSON error body
        return gateway
    upstream = response.get("provider")         # OpenRouter: top level
    if not upstream:                            # Vercel chat: choices[0].message.provider_metadata (live 2026-09-27)
        try:
            meta = response.get("provider_metadata") or response["choices"][0]["message"]["provider_metadata"]
            upstream = meta["gateway"]["routing"]["finalProvider"]     # who served it, after any fallbacks
        except (KeyError, IndexError, TypeError):
            upstream = None
    return f"{gateway}/{upstream}" if isinstance(upstream, str) and upstream else gateway


def _completion(response: dict | str | None, reason: str | None) -> str:
    """§6.2 completion: choices[0].message.content (text parts joined); a raw error/non-JSON body as-is; and
    "<no response body: <reason>>" when there was none."""
    if response is None:
        return f"<no response body: {reason or 'unknown'}>"
    if isinstance(response, str):
        return response
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return json.dumps(response, ensure_ascii=False)     # an error body, or a 200 that carries one
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def log_llm(gateway: str, request_body: dict, response: dict | str | None, reason: str | None = None) -> None:
    """One §6.2 entry per HTTP attempt to a chat model (LLM inference, regenerations, the package's text helper,
    the Jev fallback backend). `response` is the parsed JSON body, the raw text if it was not JSON, or None."""
    model = (response.get("model") if isinstance(response, dict) else None) or request_body.get("model")
    _append(_LLM_FILE, {
        "model": model,
        "provider": _provider(gateway, response),
        "parameters": {k: v for k, v in request_body.items() if k not in ("model", "messages")},
        "messages": request_body.get("messages", []),
        "completion": _completion(response, reason),
    })


# ------------------------------------------------------------------ Jev (§6.3)

def log_jev(request_body: dict, response: dict | str | None, reason: str | None = None) -> None:
    """One §6.3 entry per HTTP attempt to Jev. Questions are grouped by their `type` into Score/Noul/Choice, each
    logged as {"id": <key>, ...fields as sent}; a type with no questions is []."""
    grouped: dict[str, list] = {"Score": [], "Noul": [], "Choice": []}
    questions = request_body.get("questions") or {}
    items = questions.items() if isinstance(questions, dict) else enumerate(questions)
    for qid, q in items:
        if isinstance(q, dict) and (bucket := _JEV_BUCKET.get(q.get("type"))):
            grouped[bucket].append({"id": qid, **q})
    _append(_JEV_FILE, {
        "State": request_body.get("state"),
        "Score": grouped["Score"],
        "Noul": grouped["Noul"],
        "Choice": grouped["Choice"],
        "Response": response if response is not None else f"<no response body: {reason or 'unknown'}>",
    })
