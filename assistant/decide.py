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
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from assistant import gateway as gateway_mod
from assistant import inference_log

# Jev's System One endpoint per route (TypeSafe's request and answer shapes on both). The model ID per route is
# config models.<route>.system_one_decision_model: Vercel AI Gateway serves "typesafe-ai/jev"; OpenRouter's alpha/decisions API serves a
# TypeSafe-compatible System One model, e.g. "respan/span-01-lite:free" (2026-09-28).
ENDPOINTS = {"vercel": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
             "openrouter": "https://openrouter.ai/api/alpha/decisions"}
SYSTEM_ONE_PATH = "/v1/systemone"   # TypeSafe's own path, which a Kev server on this machine serves (README, API)
# Questions per request. TypeSafe fails a whole request when any one question fails, so a big request rarely gets
# through: on Vercel, 2026-09-24, 44 questions per request failed 5 of 6 times, 11 per request 7 of 12, 4 per
# request 4 of 33 (503 "Service temporarily unavailable" from the provider). Above MAX_QUESTIONS a judgment is
# split and the parts go one after another — never side by side: P1 T2 allows one request in flight at a time.
MAX_QUESTIONS = 24
STATE_CHARS = 60_000        # Jev's context is 32K tokens: the state is cut to fit, text first

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
    """ask(topic, state, questions) → {question id: Answer}.

    Every request leaves through the Gateway (P1 T2): the queue, the one retry layer, the timeout, the cost and the
    log all live there, so this class only shapes the request and reads the answers. A judgment is one request;
    above MAX_QUESTIONS questions it is split and the parts go one after another.
    """

    def __init__(self, key: str, model: str, *, route: str = "openrouter", url: str | None = None,
                 state_chars: int = STATE_CHARS, keep_object_state: bool = False,
                 max_questions: int = MAX_QUESTIONS, fallback: "ChatDecider | None" = None,
                 gateway: "gateway_mod.Gateway | None" = None, post: Callable | None = None,
                 timeout: float | None = None, sleep: Callable[[float], None] = time.sleep):
        self.key, self.model = key, model
        self.url = url or ENDPOINTS[route]
        self.state_chars = state_chars
        self.keep_object_state = keep_object_state
        self.max_questions = max(1, max_questions)
        self.fallback = fallback
        if fallback is not None:
            # T4: the pair is one request as far as the breaker is concerned, and this Decider reports its verdict.
            fallback.defer_verdict = True
        self.timeout = timeout
        # `post` is for a caller that sends with its own client (the tests). It does not bypass the Gateway: it gets
        # a Gateway of its own, so the queue, the retry rule and the log apply to it as well (A2).
        self._gateway = gateway or (gateway_mod.private(post, sleep=sleep) if post else None)
        self.calls = 0
        self.cost = 0.0
        self.by_fallback = 0
        self._cache: dict[str, dict[str, Answer]] = {}
        self._lock = threading.Lock()         # a goal runs on a worker thread: the counters are shared

    @property
    def gateway(self) -> "gateway_mod.Gateway":
        return self._gateway or gateway_mod.required()

    @staticmethod
    def _cache_key(topic: str, fitted: Any, questions: dict[str, dict]) -> str:
        import hashlib
        raw = topic + "\0" + json.dumps(fitted, ensure_ascii=False, sort_keys=True) + "\0" + json.dumps(
            questions, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def ask(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        """Every question answered, or DecisionError. Cached by topic + fitted state + full questions."""
        if not questions:
            return {}
        ids, fitted = list(questions), fit_state(state, self.state_chars, self.keep_object_state)
        key = self._cache_key(topic, fitted, questions)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        out: dict[str, Answer] = {}
        for i in range(0, len(ids), self.max_questions):
            out.update(self._one(topic, fitted, {k: questions[k] for k in ids[i:i + self.max_questions]}))
        self._cache[key] = out
        return out

    def _one(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        adapt = "alpha/decisions" in self.url and adapt_questions_for(self.model)
        body = {"model": self.model, "state": state, "questions": respan_questions(questions) if adapt else questions}
        # A Kev server started without KEV_API_KEY is open and ignores the header; the cloud routes need it.
        headers = {"Content-Type": "application/json",
                   **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        gateway = self.gateway
        # The Gateway counts this request's verdict only when we say so: a request the chat fallback answered is not
        # evidence that the provider is out (T4), so the verdict is deferred until the fallback has had its turn.
        out = gateway.send(gateway_mod.JEV, self.url, body, headers, model=self.model,
                           timeout=self.timeout, defer_verdict=True)
        if out.ok:
            try:
                answers = self._answers(out.body, questions)
            except DecisionError:
                gateway.note_failure(False)      # the model answered; a malformed answer is not an outage
                raise
            gateway.note_failure(False)
            with self._lock:
                self.calls += 1
                self.cost += _cost(out.body)
            return answers
        if self.fallback is not None:
            try:
                # The queue slot was released when `send` returned, so the fallback's own send is not nested
                # inside this request and cannot wait on a slot we are holding.
                answers = self.fallback.ask(topic, state, questions)
            except DecisionError as exc:
                gateway.note_failure(True)       # both failed: one failure for the breaker
                raise DecisionError(f"decision model HTTP {out.status}: {_error(out.body)}; "
                                    f"the chat fallback also failed ({exc})") from None
            gateway.note_failure(False)
            gateway.note_fallback()
            with self._lock:
                self.calls += 1
                self.by_fallback += 1
            return answers
        gateway.note_failure(True)
        raise DecisionError(f"decision model HTTP {out.status}: {_error(out.body)}")

    @staticmethod
    def _answers(data: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        raw = (data or {}).get("answers") or {}
        missing = [k for k in questions if k not in raw]
        if missing:
            raise DecisionError(f"decision model left {len(missing)} question(s) unanswered: {missing[:3]}")
        return {k: Answer.parse(raw[k]) for k in questions}


class ChatDecider:
    """The same typed questions, answered by a chat model (D19, P1 T4).

    A System One model returns a probability per question natively; a chat model has to be asked for one. Each
    question goes out as its own JSON object in one request, and the answers come back in the shape `Answer.parse`
    already reads, so nothing downstream knows which backend answered. Its calls are chat-model calls, so the
    Gateway logs them to llm_inference_logs.json (D4).
    """

    SYSTEM = ("You answer typed decision questions about a state. Reply with JSON only: "
              '{"answers": {"<question id>": {...}}}. For a question of type "noul", the object is '
              '{"type": "noul", "noul": <probability between 0 and 1 that the statement is true>}. For type '
              '"choice", it is {"type": "choice", "choice": "<one key of that question\'s criteria, exactly as '
              'spelled>", "probabilities": {"<each criteria key>": <probability>}, "confidence": <0 to 1>}. '
              "Answer every question. Judge only from the state; never guess a fact the state does not contain — "
              "an even spread of probabilities is the honest answer when the state does not say.")

    def __init__(self, key: str, models, *, url: str, max_tokens: int = 4096,
                 gateway: "gateway_mod.Gateway | None" = None, defer_verdict: bool = False):
        self.key, self.models, self.url = key, models, url
        self.max_tokens = max_tokens
        self._gateway = gateway
        # As a fallback, its verdict belongs to the System One request it is rescuing: the Decider reports the pair
        # once, so a rescued decision is not a failure and a lost one is not two (T4).
        self.defer_verdict = defer_verdict
        self.calls = 0

    @property
    def gateway(self) -> "gateway_mod.Gateway":
        return self._gateway or gateway_mod.required()

    def ask(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        if not questions:
            return {}
        from assistant.rotation import NoModelAvailable, Rotation
        rotation = self.models if isinstance(self.models, Rotation) else Rotation(self.models)
        try:
            data = rotation.call(lambda m: self._one(m, topic, state, questions), _ChatAttemptFailed)
        except NoModelAvailable as exc:
            raise DecisionError(f"the chat fallback did not answer: {exc}") from None
        self.calls += 1
        return Decider._answers(data, questions)

    def _one(self, model: str, topic: str, state: Any, questions: dict[str, dict]) -> dict:
        user = {"topic": topic, "state": state,
                "questions": {qid: {"type": _kind(q), **{k: _plain(v) for k, v in q.items() if k != "type"}}
                              for qid, q in questions.items()}}
        body = {"model": model, "temperature": 0, "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"}, "reasoning": {"enabled": False},
                "messages": [{"role": "system", "content": self.SYSTEM},
                             {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        out = self.gateway.send(gateway_mod.CHAT, self.url, body, headers, model=model,
                                defer_verdict=self.defer_verdict)
        if not out.ok:
            raise _ChatAttemptFailed(f"HTTP {out.status}: {_error(out.body)}")
        try:
            content = out.body["choices"][0]["message"]["content"]
            parsed = json.loads(_fences.sub(r"\1", (content or "").strip()))
        except (KeyError, IndexError, TypeError, ValueError):
            raise _ChatAttemptFailed("output is not the JSON asked for") from None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), dict):
            raise _ChatAttemptFailed("no answers object in the output")
        return parsed


class _ChatAttemptFailed(RuntimeError):
    """One chat model did not answer; the rotation tries the next."""


_fences = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def _kind(question: dict) -> str:
    """noul or choice, from the shape the question was built with (noul() / choice())."""
    return str(question.get("type") or ("choice" if "criteria" in question else "noul"))



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


def for_config(cfg, gateway: "gateway_mod.Gateway | None" = None) -> Decider:
    """The run's Decider: config models.system_one_decision_model on models.system_one_decision_provider, with that
    route's URL and its key from .env (a local Kev server usually has none). The chat fallback (D19) answers when a
    System One request fails after all of the Gateway's attempts; decider.fallback = "none" turns it off."""
    from assistant import config
    local = cfg.models.system_one_decision_provider == "local"
    fallback = None
    if cfg.decider.fallback == "chat" and cfg.models.llm_inference:
        fallback = ChatDecider(config.chat_key(cfg), list(cfg.models.llm_inference),
                               url=config.chat_url(cfg), gateway=gateway)
    return Decider(config.system_one_decision_key(cfg), cfg.models.system_one_decision_model,
                   route=cfg.models.system_one_decision_provider, url=endpoint(cfg),
                   state_chars=cfg.models.local.state_chars if local else STATE_CHARS,
                   max_questions=cfg.jev.max_questions_per_request,
                   keep_object_state=local, fallback=fallback, gateway=gateway)


_current: Decider | None = None


def use(decider: Decider | None) -> None:
    """Set the run's Decider (cli.run / preflight; tests install a stand-in)."""
    global _current
    _current = decider


def current() -> Decider:
    if _current is None:
        raise DecisionError("no decision model configured (decide.use)")
    return _current
