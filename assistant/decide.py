"""Decisions by a System One decision model (config models.system_one_decision_model; e.g. TypeSafe's Jev instance),
through a System One API (user decisions 2026-09-24: a System One model decides; it is reached through Vercel AI
Gateway, OpenRouter, or a Kev server on this machine — config models.system_one_decision_provider).

Code asks typed questions about a state and branches on the typed answers:
  noul    the probability that a yes/no question is true           → Answer.noul
  choice  one option from a named set, with probabilities, confidence → Answer.choice
All questions of one call are answered in parallel and independently against the same state, so a caller asks
everything it may need about one page in one call. The thresholds live in code (THRESHOLDS below), never in a
prompt. Docs: https://docs.typesafe.ai · https://openrouter.ai/docs/guides/community/typesafe-sdk

One Decider per run (use()); every call is logged to jev_inference_logs.json (inference_log). A failed call raises
DecisionError: a job that cannot get a decision goes to Needs-Attention, it is never decided by a guess.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from assistant import inference_log

# Jev's System One endpoint per route (TypeSafe's request and answer shapes on both). The model ID per route is
# config models.<route>.system_one_decision_model: Vercel AI Gateway serves "typesafe-ai/jev"; OpenRouter's alpha/decisions API serves a
# TypeSafe-compatible System One model, e.g. "respan/span-01-lite:free" (2026-09-28).
ENDPOINTS = {"vercel": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
             "openrouter": "https://openrouter.ai/api/alpha/decisions"}
SYSTEM_ONE_PATH = "/v1/systemone"   # TypeSafe's own path, which a Kev server on this machine serves (README, API)
TIMEOUT = 30.0
# Questions per request. TypeSafe fails a whole request when any one question fails, so a big request rarely gets
# through: on Vercel, 2026-09-24, 44 questions per request failed 5 of 6 times, 11 per request 7 of 12, 4 per
# request 4 of 33 (503 "Service temporarily unavailable" from the provider). Small batches go out side by side.
BATCH = 4
PARALLEL = 4                # batches in flight at once
STATE_CHARS = 60_000        # Jev's context is 32K tokens: the state is cut to fit, text first
# Waits before each retry of a transient failure (no connection, 429, 5xx). Vercel's Jev answers "high demand"
# (429) and "Service temporarily unavailable" (503) in bursts: 7 of 42 live tests on 2026-09-24 failed after one
# 2 s retry, and in the live run that day a burst outlasted 2+5+15 s (23.7 s) and sent The Flex to Needs Attention.
RETRY_WAITS = (2.0, 5.0, 15.0, 30.0, 60.0)
# A server on this machine is never rate-limited and never "in high demand": a failure is a server that is down or
# a model that is out of memory, and neither is cured by waiting a minute. Say so quickly instead.
LOCAL_RETRY_WAITS = (2.0, 5.0)

# What each yes/no answer must reach before the code acts on it. Set by the cost of being wrong, per question.
THRESHOLDS = {
    "submitted": 0.7,       # confirmation text: the run stops (exit 3), so a miss is worse than a false alarm
    "applied": 0.7,         # LinkedIn says "Applied …": the job is not touched
    "covered": 0.5,         # a pop-up over the form: one agent action to clear it
    "actionable": 0.5,      # the page offers something to act on; below this it is not drawn yet
    "validation": 0.6,      # an error message on a field: the step is not done
    "app_field": 0.5,       # a field belongs to the application, not to the site around it
    "cover_letter": 0.6,    # a required cover-letter upload: a blocker (we have none)
    "submit_button": 0.5,   # the final step shows a button that submits the application
    "registration": 0.6,    # after Google sign-in the site still wants a registration or terms
    "placeholder": 0.6,     # a field's shown value is a prompt ("Select an option"), not an answer
    "must_not_generate": 0.4,    # a question asks for a fact only the candidate can give: never write one
    "resume_upload": 0.6,   # a question is answered by the resume upload itself
}


class DecisionError(RuntimeError):
    """The decision model could not answer (network, HTTP error, malformed reply, no decider configured)."""


def noul(instructions: Any, *, true: str | None = None, false: str | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true or "", "false": false or ""}
    return q


def choice(instructions: Any, criteria: dict[str, Any]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def narrow(topic: str, state: Any, qid: str, question: dict, probabilities: dict[str, float],
           keep: int = 2) -> "Answer":
    """Ask a choice again over only its `keep` likeliest options (and "none" when it was offered). Look-alike options
    split an answer (a file input and the button beside it, The Flex on Ashby, live 2026-09-24); fewer settle it."""
    top = sorted(probabilities, key=probabilities.get, reverse=True)[:keep]
    criteria = {k: v for k, v in question["criteria"].items() if k in top or k == "none"}
    return current().ask(topic, state, {qid: {**question, "criteria": criteria}})[qid]


@dataclass
class Answer:
    type: str
    noul: float | None = None
    choice: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None

    def yes(self, threshold: float) -> bool:
        return (self.noul or 0.0) >= threshold

    @classmethod
    def parse(cls, d: dict) -> "Answer":
        kind = d.get("type")
        if kind == "noul" and isinstance(d.get("noul"), (int, float)):
            return cls("noul", noul=float(d["noul"]))
        if kind == "choice" and isinstance(d.get("choice"), str):
            return cls("choice", choice=d["choice"], probabilities=dict(d.get("probabilities") or {}),
                       confidence=d.get("confidence"))
        raise DecisionError(f"malformed answer: {json.dumps(d)[:160]}")


def fit_state(state: Any, limit: int = STATE_CHARS, keep_object: bool = False) -> Any:
    """The System One `state` as a string that fits `limit` characters (STATE_CHARS on a cloud route;
    models.local.state_chars on a Kev server, which is small and was trained on short states). A dict's longest
    string values are cut first (text before structure), then the whole state is JSON-serialised. A string state is
    cut to the same limit.

    Sending a string, not an object, is required by OpenRouter decisions models such as respan/span-01-lite (which
    reject a bare JSON object: HTTP 400 "state must be a string or an object with only input … and output …",
    live 2026-09-28), and TypeSafe Jev accepts a string too (docs: state may be a string, object or array).
    `keep_object` (the local route) hands a dict over as it is: Kev renders an object as labeled text, which is
    what a JSON string turns into anyway, minus the escaped quotes it would spend tokens on (kev/api.py render()).
    A state that is still over the limit is serialised and cut, so the limit holds either way."""
    if isinstance(state, str):
        return state[:limit]
    if isinstance(state, dict):
        state = dict(state)
        while len(json.dumps(state, ensure_ascii=False)) > limit:
            key = max((k for k, v in state.items() if isinstance(v, str)), key=lambda k: len(state[k]), default=None)
            if key is None or len(state[key]) < 200:
                break
            state[key] = state[key][: len(state[key]) * 3 // 4]
    encoded = json.dumps(state, ensure_ascii=False)
    if keep_object and isinstance(state, dict) and len(encoded) <= limit:
        return state
    return encoded[:limit]


def adapt_questions_for(model: str) -> bool:
    """Whether a decisions model needs the flattened question form. TypeSafe Jev (typesafe/*, jev-*) takes its
    native structured instructions/criteria; other OpenRouter decisions models (respan/span-01-lite) need plain
    strings, so they are flattened (respan_questions)."""
    m = model.lower()
    return "jev" not in m and not m.startswith("typesafe")


def _plain(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def respan_questions(questions: dict) -> dict:
    """OpenRouter's decisions models (respan/span-01-lite) accept only plain-string question `instructions` and
    `criteria` values (HTTP 400 otherwise, live 2026-09-28). Flatten TypeSafe Jev's structured instructions and
    nested choice criteria to JSON strings — same content, as text. Question ids and types are unchanged."""
    out = {}
    for qid, q in questions.items():
        q = dict(q)
        if "instructions" in q:
            q["instructions"] = _plain(q["instructions"])
        crit = q.get("criteria")
        if isinstance(crit, dict):
            q["criteria"] = {k: _plain(v) for k, v in crit.items()}
        elif isinstance(crit, list):
            q["criteria"] = [_plain(v) for v in crit]
        out[qid] = q
    return out


def _httpx_post(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, Any]:
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:300]}


def _error(data: Any) -> str:
    """OpenRouter: {"error": {"message"}}; Vercel: {"error": {"message", "type"}} or {"message", "error_type"}."""
    err = data.get("error", data) if isinstance(data, dict) else data
    msg = err.get("message", err) if isinstance(err, dict) else err
    return str(msg)[:200]


def _cost(data: Any) -> float:
    """OpenRouter: usage.cost; Vercel: provider_metadata.gateway.cost (a string)."""
    data = data or {}
    cost = (data.get("usage") or {}).get("cost") or ((data.get("provider_metadata") or {}).get("gateway") or {}).get("cost")
    try:
        return float(cost or 0)
    except (TypeError, ValueError):
        return 0.0


class Decider:
    """ask(topic, state, questions) → {question id: Answer}. `post` is injectable for tests."""

    def __init__(self, key: str, model: str, *, route: str = "openrouter", url: str | None = None,
                 state_chars: int = STATE_CHARS, timeout: float = TIMEOUT, retry_waits: tuple[float, ...] = RETRY_WAITS,
                 keep_object_state: bool = False, post: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.key, self.model = key, model
        self.url = url or ENDPOINTS[route]
        self.state_chars, self.timeout, self.retry_waits = state_chars, timeout, retry_waits
        self.keep_object_state = keep_object_state
        self.post = post or _httpx_post
        self.sleep = sleep
        self.calls = 0
        self.cost = 0.0
        self._lock = threading.Lock()         # batches run on worker threads: counters and the log are shared

    def ask(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        """Every question answered, or DecisionError. Batches of BATCH questions, PARALLEL at a time; each batch
        retries on its own, so one failing batch does not cost the others their answers."""
        if not questions:
            return {}
        ids, fitted = list(questions), fit_state(state, self.state_chars, self.keep_object_state)
        chunks = [{k: questions[k] for k in ids[i:i + BATCH]} for i in range(0, len(ids), BATCH)]
        if len(chunks) == 1:
            return self._one(topic, fitted, chunks[0])
        out: dict[str, Answer] = {}
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            for part in pool.map(lambda c: self._one(topic, fitted, c), chunks):
                out.update(part)
        return out

    def _one(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        adapt = "alpha/decisions" in self.url and adapt_questions_for(self.model)
        body = {"model": self.model, "state": state, "questions": respan_questions(questions) if adapt else questions}
        # A Kev server started without KEV_API_KEY is open and ignores the header; the cloud routes need it.
        headers = {"Content-Type": "application/json",
                   **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        for wait in (*self.retry_waits, None):
            status, data = self.post(self.url, body, headers, self.timeout)
            inference_log.log_jev(body, data, None if status else "request error")   # §6.3: every attempt
            if status == 200 or wait is None or not (status == 0 or status == 429 or status >= 500):
                break
            self.sleep(wait)
        if status != 200:
            raise DecisionError(f"decision model HTTP {status}: {_error(data)}")
        raw = (data or {}).get("answers") or {}
        missing = [k for k in questions if k not in raw]
        if missing:
            raise DecisionError(f"decision model left {len(missing)} question(s) unanswered: {missing[:3]}")
        answers = {k: Answer.parse(raw[k]) for k in questions}
        with self._lock:
            self.calls += 1
            self.cost += _cost(data)
        return answers


def endpoint(cfg) -> str:
    """The System One URL of models.system_one_decision_provider. A Kev server on this machine is named by
    models.local.base_url and serves TypeSafe's own path."""
    if cfg.models.system_one_decision_provider == "local":
        return cfg.models.local.base_url.rstrip("/") + SYSTEM_ONE_PATH
    return ENDPOINTS[cfg.models.system_one_decision_provider]


def start_hint(cfg) -> str:
    """What to do about a local decision server that does not answer. Empty on a cloud route."""
    if cfg.models.system_one_decision_provider != "local":
        return ""
    port = urlparse(cfg.models.local.base_url).port or 8009
    return (f"no Kev server answers on {cfg.models.local.base_url}. Start it first: ./run_kev_server.command "
            f"(or, in your kev clone: uv run --extra serve python -m kev.serve "
            f"--run jaredpalmer/kev-0.8b --port {port})")


def server_card(cfg, timeout: float = 15.0) -> dict:
    """GET /v1/models on the local Kev server: which checkpoint it loaded, on which backend and in which precision.
    A cheap preflight check that names a server that is down before the first job does. DecisionError if it cannot
    be read."""
    from assistant import config
    url = cfg.models.local.base_url.rstrip("/") + "/v1/models"
    key = config.local_key()
    try:
        r = httpx.get(url, timeout=timeout, headers={"Authorization": f"Bearer {key}"} if key else {})
        r.raise_for_status()
        models = (r.json() or {}).get("models") or []
    except httpx.HTTPError as exc:
        raise DecisionError(f"{start_hint(cfg)} ({type(exc).__name__}: {exc})") from exc
    except ValueError as exc:
        raise DecisionError(f"{url} did not answer with JSON: is that a Kev server?") from exc
    if not models:
        raise DecisionError(f"{url} lists no model: the server has no checkpoint loaded")
    return models[0]


def for_config(cfg) -> Decider:
    """The run's Decider: config models.system_one_decision_model on models.system_one_decision_provider, with that
    route's URL and its key from .env (a local Kev server usually has none)."""
    from assistant import config
    local = cfg.models.system_one_decision_provider == "local"
    return Decider(config.system_one_decision_key(cfg), cfg.models.system_one_decision_model,
                   route=cfg.models.system_one_decision_provider, url=endpoint(cfg),
                   state_chars=cfg.models.local.state_chars if local else STATE_CHARS,
                   timeout=cfg.models.local.timeout if local else TIMEOUT,
                   retry_waits=LOCAL_RETRY_WAITS if local else RETRY_WAITS, keep_object_state=local)


_current: Decider | None = None


def use(decider: Decider | None) -> None:
    """Set the run's Decider (cli.run / preflight; tests install a stand-in)."""
    global _current
    _current = decider


def current() -> Decider:
    if _current is None:
        raise DecisionError("no decision model configured (decide.use)")
    return _current
