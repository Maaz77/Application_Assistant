"""Decisions by TypeSafe's Jev, a System One model, through a gateway's System One API (user decisions 2026-09-24:
Jev decides; it is reached through Vercel AI Gateway, or OpenRouter — config models.jev_route).

Code asks typed questions about a state and branches on the typed answers:
  noul    the probability that a yes/no question is true           → Answer.noul
  choice  one option from a named set, with probabilities, confidence → Answer.choice
All questions of one call are answered in parallel and independently against the same state, so a caller asks
everything it may need about one page in one call. The thresholds live in code (THRESHOLDS below), never in a
prompt. Docs: https://docs.typesafe.ai · https://openrouter.ai/docs/guides/community/typesafe-sdk

One Decider per run (use()); every call is logged to decisions.jsonl next to calls.jsonl. A failed call raises
DecisionError: a job that cannot get a decision goes to Needs-Attention, it is never decided by a guess.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

# Jev's System One endpoint per route (TypeSafe's request and answer shapes on both). Model IDs differ per route:
# Vercel AI Gateway serves "typesafe-ai/jev", OpenRouter "typesafe/jev-1.13" (config models.jev).
ENDPOINTS = {"vercel": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
             "openrouter": "https://openrouter.ai/api/v1/systemone"}
SYSTEMONE = ENDPOINTS["openrouter"]
TIMEOUT = 30.0
BATCH = 60                  # questions per request; more are split (each is answered on its own anyway)
STATE_CHARS = 60_000        # Jev's context is 32K tokens: the state is cut to fit, text first
# Waits before each retry of a transient failure (no connection, 429, 5xx). Vercel's Jev answers "high demand"
# (429) and "Service temporarily unavailable" (503) now and then: 7 of 42 live tests on 2026-09-24 failed after
# one 2 s retry, and each such failure would send a job to Needs Attention.
RETRY_WAITS = (2.0, 5.0, 15.0)

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
    "starts_application": 0.7,   # the agent's Apply starts the application rather than submitting it
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


def fit_state(state: Any) -> Any:
    """Cut a dict state's longest string values until its JSON fits STATE_CHARS."""
    if not isinstance(state, dict):
        return state if len(json.dumps(state)) <= STATE_CHARS else json.dumps(state)[:STATE_CHARS]
    state = dict(state)
    while len(json.dumps(state, ensure_ascii=False)) > STATE_CHARS:
        key = max((k for k, v in state.items() if isinstance(v, str)), key=lambda k: len(state[k]), default=None)
        if key is None or len(state[key]) < 200:
            break
        state[key] = state[key][: len(state[key]) * 3 // 4]
    return state


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

    def __init__(self, key: str, model: str, *, route: str = "openrouter", log: Path | None = None,
                 post: Callable | None = None, sleep: Callable[[float], None] = time.sleep):
        self.key, self.model, self.log = key, model, log
        self.url = ENDPOINTS[route]
        self.post = post or _httpx_post
        self.sleep = sleep
        self.calls = 0
        self.cost = 0.0

    def ask(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        if not questions:
            return {}
        ids = list(questions)
        out: dict[str, Answer] = {}
        for i in range(0, len(ids), BATCH):
            chunk = {k: questions[k] for k in ids[i:i + BATCH]}
            out.update(self._one(topic, fit_state(state), chunk))
        return out

    def _one(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        t0 = time.monotonic()
        for wait in (*RETRY_WAITS, None):
            status, data = self.post(self.url, body, headers, TIMEOUT)
            if status == 200 or wait is None or not (status == 0 or status == 429 or status >= 500):
                break
            self.sleep(wait)
        ms = int((time.monotonic() - t0) * 1000)
        if status != 200:
            self._log(topic, questions, None, ms, f"HTTP {status}: {str(data)[:200]}")
            raise DecisionError(f"decision model HTTP {status}: {_error(data)}")
        raw = (data or {}).get("answers") or {}
        missing = [k for k in questions if k not in raw]
        if missing:
            self._log(topic, questions, raw, ms, f"no answer for {missing}")
            raise DecisionError(f"decision model left {len(missing)} question(s) unanswered: {missing[:3]}")
        answers = {k: Answer.parse(raw[k]) for k in questions}
        self.calls += 1
        self.cost += _cost(data)
        self._log(topic, questions, raw, ms, None, (data or {}).get("usage"))
        return answers

    def _log(self, topic, questions, answers, ms, error, usage=None) -> None:
        if not self.log:
            return
        compact = {k: ({"p": v.get("noul")} if v.get("type") == "noul" else
                       {"c": v.get("choice"), "conf": v.get("confidence")}) for k, v in (answers or {}).items()}
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "topic": topic, "ms": ms, "questions": list(questions),
                "answers": compact, "usage": usage, "error": error}
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def for_config(cfg, log: Path | None = None) -> Decider:
    """The run's Decider: config models.jev on models.jev_route, with that route's key from .env."""
    from assistant import config
    return Decider(config.jev_key(cfg), cfg.models.jev, route=cfg.models.jev_route, log=log)


_current: Decider | None = None


def use(decider: Decider | None) -> None:
    """Set the run's Decider (cli.run / preflight; tests install a stand-in)."""
    global _current
    _current = decider


def current() -> Decider:
    if _current is None:
        raise DecisionError("no decision model configured (decide.use)")
    return _current
