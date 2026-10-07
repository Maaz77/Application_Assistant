"""One path for every model request (P1 T2).

Before this, three senders each had their own queue, retries and timeouts: `decide.Decider` (batches of 4, four in
flight, five retry waits), `llm_inference._ask_model` (one Retry-After wait) and the vendored package's
`policy._post` (three attempts of its own, inside one call). Nested, they multiplied: one run sent 222 System One
requests in 19 minutes and up to ~18 HTTP requests per goal step (spec §13.1).

Every model request now goes through `Gateway.send`, which is the only place that holds a queue slot, waits, retries
or logs. The counters it keeps are what `report.md` prints, and the two stop conditions (D21) are raised from
here so the run ends cleanly instead of recording a job that never had a chance.

Cost accounting was removed on 2026-10-07: both servers run on this machine and neither reports a price, so every
request was costed at $0 and the spend cap (D15, `BudgetExceeded`, `[budget]`, `[prices]`) guarded nothing. A paid
route would have to bring it back.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import urlparse
from typing import Any, Callable

import httpx

from assistant import inference_log
from assistant.blockers import StopRun

# What a request is for. It picks the timeout and which log the attempt goes to.
JEV, CHAT = "jev", "chat"

RETRY_AFTER_MAX = 30.0          # a longer Retry-After is the provider telling us to stop, not to wait
BACKOFF = (2.0, 6.0)            # the waits before attempts 2 and 3 when no Retry-After is given
RETRY_STATUS = {429, 529}       # plus every 5xx, plus 0 (no connection) — see `_retryable`
# 401 a rejected key — which is what the FreeLLMAPI router answers without a FREELLMAPI_KEY — plus 402 no credit
# and 403 a refused account, kept because any OpenAI-compatible server may answer them. None of the three is cured
# by waiting or by another model, so each stops the run instead of rotating.
CREDIT_STATUS = {401, 402, 403}


class GatewayStop(StopRun):
    """Base of the two clean stops (T3). A StopRun, so `cli.run` already writes the report, skips the record and
    exits 3; nothing in fill.py or cli.py catches StopRun as NeedsAttention."""


class ProviderOutage(GatewayStop):
    pass


class CreditOrKey(GatewayStop):
    pass


@dataclass
class Counters:
    """Per run and per job. `in_flight` is the highest number seen at once, which is what proves max_in_flight."""
    requests: int = 0
    attempts: int = 0
    failures: int = 0
    in_flight: int = 0
    jev_requests: int = 0
    chat_requests: int = 0
    fallbacks: int = 0

    def row(self) -> str:
        return (f"{self.jev_requests} System One, {self.chat_requests} LLM, {self.attempts} attempts, "
                f"{self.failures} failures")


@dataclass
class Outcome:
    """What one `send` returned. `ok` is false for a status the caller must treat as a failed request."""
    status: int
    body: Any
    ok: bool
    attempts: int


def _httpx_post(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, Any]:
    """The default sender. Status 0 means no HTTP answer at all; the body then names the transport error."""
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return 0, {"error": {"message": f"{type(exc).__name__}: {exc}"}}
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:500]}
    if isinstance(body, dict) and r.headers.get("retry-after"):
        body["_retry_after"] = r.headers["retry-after"]
    return r.status_code, body


def _retryable(status: int) -> bool:
    return status == 0 or status in RETRY_STATUS or status >= 500


def _retry_after(body: Any) -> float | None:
    try:
        return float(body.get("_retry_after")) if isinstance(body, dict) else None
    except (TypeError, ValueError):
        return None


class Gateway:
    """`send(kind, url, body, headers, model=…)` is the only way a model request leaves this program.

    Failures are counted per request, not per attempt: the breaker is about a provider being out, and three
    attempts of one request are one piece of evidence. `note_failure` lets the Decider report the verdict after its
    chat fallback, so a request the fallback rescued does not count (T4).
    """

    def __init__(self, *, limits, timeouts: dict[str, float] | None = None, post: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.timeouts = {JEV: 20.0, CHAT: 45.0, **(timeouts or {})}
        self.post = post or _httpx_post
        self.sleep, self.monotonic = sleep, monotonic
        self._slots = threading.BoundedSemaphore(max(1, limits.max_in_flight))
        self._lock = threading.Lock()
        self._live = 0                      # requests holding a slot right now
        self._next_start = 0.0
        self.run = Counters()
        self.job = Counters()
        self._recent: list[bool] = []       # the last 10 request verdicts, True = failed
        self.tripped: GatewayStop | None = None   # sticky: set once, re-raised at every later entry point
        self._last_failure = ""                   # "<route> HTTP <status>", for the outage message (T3)

    # -------------------------------------------------------------- scope

    def start_job(self) -> Counters:
        self.job = Counters()
        return self.job

    def _both(self) -> tuple[Counters, Counters]:
        return self.run, self.job

    # -------------------------------------------------------------- the one send

    def send(self, kind: str, url: str, body: dict, headers: dict, *, model: str = "",
             timeout: float | None = None, log: bool = True, defer_verdict: bool = False,
             attempts: int | None = None) -> Outcome:
        """One request: at most `max_attempts` attempts, `min_interval_s` apart, one at a time. Every attempt is
        logged (§6.4) before anything is returned. Raises a GatewayStop for the two stop conditions.

        The queue slot is held only for the attempts of this one request and is free again when `send` returns, so
        a caller may send again (the chat fallback) without waiting on a slot it is itself holding.

        `attempts` caps this request below `limits.max_attempts`. A caller with an untried model behind it passes 1:
        another model is a cheaper answer to "this one is rate-limited" than waiting 8 s to ask the same one again,
        and it is still one retry layer — the rotation's next model is a different request, not a retry of this one.

        `defer_verdict` leaves the breaker unfed: the caller reports the verdict itself with `note_failure`,
        which is how a System One request the chat fallback rescued avoids counting as a failure (T4)."""
        self.check()
        model = model or str(body.get("model") or "")
        with self._slot():
            return self._attempts(kind, url, body, headers, model, timeout, log, defer_verdict, attempts)

    def _attempts(self, kind: str, url: str, body: dict, headers: dict, model: str,
                  timeout: float | None, log: bool, defer: bool = False, cap: int | None = None) -> Outcome:
        limit = max(1, min(self.limits.max_attempts, cap or self.limits.max_attempts))
        timeout_s = timeout if timeout is not None else self.timeouts.get(kind, 45.0)
        for counter in self._both():
            counter.requests += 1
            setattr(counter, f"{kind}_requests", getattr(counter, f"{kind}_requests", 0) + 1)
        status, response, used = 0, None, 0
        for attempt in range(1, limit + 1):
            self._space()
            try:
                status, response = self.post(url, body, headers, timeout_s)
            except httpx.HTTPError as exc:
                # `_httpx_post` reports a transport failure as status 0, but an injected sender (a test, or a
                # client of its own) may raise instead. One shape from here on, so the retry rule and the log do
                # not depend on which sender is in use.
                status, response = 0, {"error": {"message": f"{type(exc).__name__}: {exc}"}}
            used = attempt
            for counter in self._both():
                counter.attempts += 1
            if log:
                self._log(kind, url, body, status, response, timeout_s)
            if status in CREDIT_STATUS:
                if not defer:
                    self.note_failure(True)
                raise self._trip(CreditOrKey(self._credit_message(url, status, response)))
            if status == 200:
                if not defer:
                    self.note_failure(False)
                return Outcome(status, response, True, used)
            if attempt == limit or not _retryable(status):
                break
            self.sleep(self._wait(attempt, response))
        where = f"{inference_log.gateway_of(url)} HTTP {status}" if status else \
            f"{inference_log.gateway_of(url)} unreachable"
        self._last_failure = where
        if not defer:
            self.note_failure(True, where)
        return Outcome(status, response, False, used)

    # -------------------------------------------------------------- queue

    def _slot(self):
        gateway = self

        class _Slot:
            def __enter__(self):
                gateway._slots.acquire()
                with gateway._lock:
                    gateway._live += 1
                    for counter in gateway._both():
                        counter.in_flight = max(counter.in_flight, gateway._live)
                return self

            def __exit__(self, *exc):
                with gateway._lock:
                    gateway._live -= 1
                gateway._slots.release()
                return False

        return _Slot()

    def _space(self) -> None:
        """At least min_interval_s between the starts of two attempts. Tracked as the next time an attempt may
        start, so a slow request does not add its own duration on top of the interval."""
        with self._lock:
            now = self.monotonic()
            gap = self._next_start - now
            self._next_start = max(now, self._next_start) + self.limits.min_interval_s
        if gap > 0:
            self.sleep(gap)

    def _wait(self, attempt: int, response: Any) -> float:
        after = _retry_after(response)
        if after is not None and after <= RETRY_AFTER_MAX:
            return after
        return BACKOFF[min(attempt, len(BACKOFF)) - 1]

    # -------------------------------------------------------------- logs, the breaker

    def _log(self, kind: str, url: str, body: dict, status: int, response: Any, timeout: float) -> None:
        """The single logging point (§6.4, D4): the same body shape test `jev._log_package_request` used, so the
        package's own requests land in the right file too."""
        reason, logged = None, response
        if status == 0:
            reason, logged = _transport_reason(response), None
        elif response is None:
            reason = f"HTTP {status}"
        if kind == JEV:
            inference_log.log_jev(body, logged, reason)
        else:
            inference_log.log_llm(inference_log.gateway_of(url), body, logged, reason)

    def note_failure(self, failed: bool, where: str = "") -> None:
        """One request's verdict. D21: three failures in a row, or five of the last ten, is a provider outage."""
        if where:
            self._last_failure = where
        with self._lock:
            if failed:
                for counter in self._both():
                    counter.failures += 1
            self._recent.append(failed)
            del self._recent[:-10]
            row = len(self._recent) >= 3 and all(self._recent[-3:])
            of_ten = len(self._recent) >= 10 and sum(self._recent) >= 5
        if failed and (row or of_ten):
            rule = "3 requests in a row failed" if row else "5 of the last 10 failed"
            raise self._trip(ProviderOutage(f"provider outage — {self._last_failure or 'model requests'}: {rule}"))

    def note_fallback(self) -> None:
        for counter in self._both():
            counter.fallbacks += 1

    def _credit_message(self, url: str, status: int, response: Any = None) -> str:
        host = urlparse(url).hostname or inference_log.gateway_of(url)
        gateway = inference_log.gateway_of(url)
        said = _provider_message(response)
        verb = "refused the account" if status == 403 else "rejected the key"
        return (f"{host} {verb} (HTTP {status}{': ' + said if said else ''}) — "
                f"check the key / add credit on {gateway}")

    def _trip(self, exc: GatewayStop) -> GatewayStop:
        """Sticky, because a stop raised on the package's worker thread may be swallowed there: `Jev.checked` asks
        `check()` after every browser call and raises it on the run's own thread instead."""
        self.tripped = self.tripped or exc
        return self.tripped

    def check(self) -> None:
        if self.tripped is not None:
            raise self.tripped


def _provider_message(response: Any) -> str:
    """What the provider itself said, which is the actionable half of a 401/402/403."""
    if not isinstance(response, dict):
        return ""
    err = response.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")[:160]
    return str(err or "")[:160]


def _transport_reason(response: Any) -> str:
    if isinstance(response, dict):
        err = response.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return "request error"


# ------------------------------------------------------------------ the run's gateway

_current: Gateway | None = None


def use(gateway: Gateway | None) -> None:
    global _current
    _current = gateway


def current() -> Gateway | None:
    return _current


def required() -> Gateway:
    """For a sender that must not fall back to sending on its own (T7 A2)."""
    if _current is None:
        raise RuntimeError("no Gateway configured (gateway.use): a model request may not bypass it")
    return _current


def private(post: Callable | None = None, *, sleep: Callable[[float], None] = time.sleep,
            max_attempts: int = 3, **kwargs) -> Gateway:
    """A Gateway that is not the run's, for one sender handed its own `post`: what a test injects, so that even a
    stubbed send goes through the queue, the retry rule and the log rather than around them (A2). No minimum
    interval — the only reason for one is sharing a provider with the rest of a run, and this shares nothing."""
    limits = SimpleNamespace(max_in_flight=1, min_interval_s=0.0, max_attempts=max_attempts)
    return Gateway(limits=limits, post=post, sleep=sleep, **kwargs)


def for_config(cfg, **kwargs) -> Gateway:
    """The run's Gateway. The System One model runs on this machine and answers in seconds, not in a data-centre's
    milliseconds, so it gets models.local.timeout rather than the 20 s default a cloud route was given."""
    return Gateway(limits=cfg.limits, timeouts={JEV: cfg.models.local.timeout}, **kwargs)
