"""jev-ultrafast-mcp 0.1.5, called directly in Python (spec v2 §3). The only module that imports the package.

Order matters: the package reads its config and builds its browser manager when `server` is imported
(server.py l.61–63), so apply_env() must run first. Only the public `server.browser_*` functions are called; the
three changes to the package's inside are wraps: the text helper's model rotation (rotate_text_helper), clean
request bodies (clean_requests), and the never-submit click rule (guard_clicks).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ConfigDict

from assistant import guard, inference_log, probes
from assistant.guard import FORM, SUBMIT_RE, never_click  # noqa: F401 - the rule the package applies
from assistant.decide import RETRY_WAITS, DecisionError, adapt_questions_for, respan_questions
from assistant import config
from assistant.config import CHAT_BASES, Config
from assistant.rotation import NoModelAvailable, Rotation

STRIPPED_PREFIXES = ("JEVMCP_", "TYPESAFE_", "TEXT_MODEL_", "OPENROUTER_")
# 0.1.5 plus three vendored observer fixes (vendor/*.patch): display:contents wrappers hide nothing; while a
# modal <dialog> is open only the topmost one's content is listed; opacity-0 native radio/checkbox/file inputs are listed. Stock 0.1.5 cannot see LinkedIn's job card or Easy Apply dialog.
EXPECTED_PACKAGE_VERSION = "0.1.5+aa6"
LOCAL_PLACEHOLDER_KEY = "local"      # an open Kev server wants no key; the package wants a non-empty one
DEFAULT_TIMEOUT = 60.0
GOAL_TIMEOUT = 300.0
EXIT_STOPPED = 3

# Result prefixes the package uses for failures (server._error and friends).
ERROR_PREFIXES = ("browser_unavailable:", "blocked_by_policy:", "stale:", "browser_error:",
                  "turbo_unavailable:", "error(")


# A goal whose decision model was briefly out and that took no step can simply be asked again. The package retries
# a 503 for only ~1.5 s; Vercel's Jev answered 503 through that on both of Mastercard's goals (live 2026-09-24),
# and the job was reported as "no way forward".
TRANSIENT_GOAL_RE = re.compile(r"^status: turbo_unavailable: Decision model (returned HTTP (429|5\d\d)|unreachable|"
                               r"returned a body that is not JSON|unavailable)", re.M)
NO_STEPS_RE = re.compile(r"^steps: 0$", re.M)


class JevError(RuntimeError):
    """A browser_* function answered with an error prefix (or raised)."""


class Option(BaseModel):
    model_config = ConfigDict(extra="allow")
    ref: str | None = None
    value: str | None = None
    label: str | None = None
    selected: bool | None = None


class Element(BaseModel):
    """One row of the element table (observe include_json=True). Shape recorded in DISCOVERY.md."""
    model_config = ConfigDict(extra="allow")
    ref: str
    role: str
    name: str = ""
    value: str = ""
    label: str = ""
    editable: bool = False
    occluded: bool = False
    checked: bool | None = None
    current: str | None = None
    expanded: str | None = None
    options: list[Option] = []
    secret: bool = False
    context: str = ""
    accept: str | None = None
    multiple: bool = False
    in_viewport: bool = True


class Table(BaseModel):
    """The json block of browser_observe(include_json=True): {url, title, elements}."""
    model_config = ConfigDict(extra="allow")
    url: str
    title: str = ""
    elements: list[Element] = []


def split_json(text: str) -> tuple[str, Table]:
    """Split browser_observe(include_json=True) output into (view text, table)."""
    head, sep, tail = text.rpartition("\n\njson: ")
    if not sep:
        raise JevError("observe output carries no json block")
    return head, Table.model_validate_json(tail)


# ------------------------------------------------------------------ environment and import

def env_values(cfg: Config, key: str, cdp_url: str | None = None) -> dict[str, str]:
    """The §3 table. `key` is the chat route's key (config.chat_key): the text helper goes where the LLM inference
    goes. Clicks are refused by never_click (guard_clicks), not by JEVMCP_CONFIRM_PATTERNS."""
    return {
        "JEVMCP_MODE": "attach",
        "JEVMCP_CDP_URL": cdp_url or cfg.browser.cdp_url,
        "JEVMCP_FOREGROUND": "0",
        "JEVMCP_MAX_ACTIONS": str(cfg.browser.max_actions),
        "JEVMCP_ALLOW_UPLOADS": "1",
        "JEVMCP_ALLOW_JS": "1",
        **agent_route(cfg, key),
        "TYPESAFE_MODEL": cfg.models.system_one_decision_model,
        # the package's OpenRouter key pays for Jev on system_one_decision_provider = "openrouter"
        "OPENROUTER_API_KEY": key if cfg.models.chat_route == "openrouter" else config.api_key(),
        "TEXT_MODEL_API_KEY": key,
        "TEXT_MODEL_BASE_URL": CHAT_BASES[cfg.models.chat_route],
        "TEXT_MODEL": cfg.models.text_helper[0],    # the rest rotate in through rotate_text_helper()
        "TEXT_MODEL_REASONING": "none",      # the text helper copies a value: no hidden reasoning (free Qwen too)
    }


def agent_route(cfg: Config, key: str) -> dict[str, str]:
    """Where the goal agent's decision model is reached. The package takes a non-OpenRouter TYPESAFE_BASE_URL
    as the full System One endpoint, with TYPESAFE_API_KEY (its config._turbo_backend); OpenRouter's route is its
    alpha decisions endpoint, paid with the OpenRouter key. A Kev server on this machine is just another such
    endpoint (same request and answer shapes), named by config models.local.base_url."""
    from assistant import decide
    provider = cfg.models.system_one_decision_provider
    if provider == "local":
        # The package refuses turbo mode on an empty key (policy.available / policy.choose), and a Kev server
        # started without KEV_API_KEY is open and ignores the header: send a placeholder in that case.
        return {"TYPESAFE_BASE_URL": decide.endpoint(cfg), "TYPESAFE_API_KEY": config.local_key() or LOCAL_PLACEHOLDER_KEY}
    if provider == "vercel":
        return {"TYPESAFE_BASE_URL": decide.ENDPOINTS["vercel"], "TYPESAFE_API_KEY": config.gateway_key()}
    return {"TYPESAFE_BASE_URL": "https://openrouter.ai/api/alpha/decisions"}


def clean_text(text: str) -> str:
    """Replace lone UTF-16 surrogates with "?". The package cuts page text in JavaScript, which counts UTF-16
    units, so a cut can split a pair such as LinkedIn's styled letters (U+1D400…: "\\ud835" + low half). A lone
    half cannot be encoded as UTF-8: writing calls.jsonl raised UnicodeEncodeError and ended the run (Genesys,
    live 2026-09-24). Everything the package returns passes here, so no later log, prompt or request sees one."""
    return _SURROGATE.sub("?", text)


_SURROGATE = re.compile("[\ud800-\udfff]")


# ------------------------------------------------------------------ the never-submit rule

def guard_clicks(browser_module) -> None:
    """Every click, the agent's and ours, goes through the package's `confirm_reason(cfg, name, role)` before it is
    made (browser.py, looked up at call time; checked by contract_check). It is replaced by never_click: a refused
    click comes back as needs_confirmation, and nothing in this program sends "confirm"."""
    original = getattr(browser_module.confirm_reason, "__wrapped__", browser_module.confirm_reason)

    def confirm_reason(cfg, name, role):
        return never_click(name, role)

    confirm_reason.__wrapped__ = original
    browser_module.confirm_reason = confirm_reason


def clean_json(value: Any) -> Any:
    """clean_text on every string in a JSON-shaped value."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean_json(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return value


def clean_requests(policy, string_state: bool = True) -> None:
    """Every request the package sends (the decisions, option picks, the text helper) goes through
    `policy._post(url, key, body)`, and httpx encodes the body as UTF-8: a page whose text was cut inside a
    surrogate pair made every browser_goal fail with UnicodeEncodeError (Genesys and Mastercard, live 2026-09-24).
    Wrap it so the body is cleaned first. Calls inside policy.py look `_post` up at call time (contract_check)."""
    original = getattr(policy._post, "__wrapped__", policy._post)

    def _post(url, key, body):
        cleaned = clean_json(body)
        if isinstance(cleaned, dict) and "questions" in cleaned:
            # OpenRouter decisions models (respan/span-01-lite) require a string state and plain-string question
            # instructions/criteria (HTTP 400 otherwise); TypeSafe accepts both too. cf. decide.fit_state /
            # respan_questions. Only the package's own System One bodies (state+questions) are touched.
            # A Kev server on this machine (string_state=False) is given the object: it renders one as labeled
            # text, so the escaped quotes of a JSON string would only cost a small model tokens (kev/api.py).
            if string_state and not isinstance(cleaned.get("state"), str):
                cleaned = {**cleaned, "state": json.dumps(cleaned.get("state"), ensure_ascii=False)}
            if ("alpha/decisions" in url and isinstance(cleaned.get("questions"), dict)
                    and adapt_questions_for(str(cleaned.get("model", "")))):
                cleaned = {**cleaned, "questions": respan_questions(cleaned["questions"])}
        try:
            resp = original(url, key, cleaned)
        except Exception as exc:              # the package raises TurboUnavailable on a failed attempt; log it, re-raise
            _log_package_request(url, cleaned, None, str(exc))
            raise
        _log_package_request(url, cleaned, resp, None)
        return resp

    _post.__wrapped__ = original
    policy._post = _post


def _log_package_request(url: str, body, resp, reason: str | None) -> None:
    """Route the package's own request to the right inference log (§6, D4). The package's `_post` retries internally
    (range(3)), so only the last attempt is visible here (DISCOVERY 2026-09-27). A Jev body carries `state` and
    `questions`; the text helper's body carries `messages`."""
    if not isinstance(body, dict):
        return
    if "state" in body and "questions" in body:
        inference_log.log_jev(body, resp, reason)
    elif "messages" in body:
        inference_log.log_llm(inference_log.gateway_of(url), body, resp, reason)

_server = None
_applied: dict[str, str] | None = None
_text_helpers: tuple[str, ...] = ()
_call_timeout: float | None = None
_string_state: bool = True


def apply_env(cfg: Config, key: str, cdp_url: str | None = None) -> None:
    """Scrub stray package variables from os.environ, then set the §3 values. Must precede load()."""
    global _applied, _text_helpers, _call_timeout, _string_state
    if _server is not None:
        raise RuntimeError("apply_env() after the package was loaded has no effect (config is fixed per process)")
    for k in [k for k in os.environ if k.startswith(STRIPPED_PREFIXES)]:
        del os.environ[k]
    _applied = env_values(cfg, key, cdp_url)
    _text_helpers = cfg.models.text_helper
    local = cfg.models.system_one_decision_provider == "local"
    _call_timeout = cfg.models.local.timeout if local else None
    _string_state = not local
    os.environ.update(_applied)


def rotate_text_helper(policy, models: tuple[str, ...]) -> None:
    """The package types a custom widget's value with ONE text model (TEXT_MODEL, read once at import). Wrap its
    `policy.text_for(cfg, ...)` so each call tries the models in turn (rotation.py): a model that fails
    (TurboUnavailable: rate-limited, overloaded, wrong shape) hands over to the next, and only when none answers does
    the package see TurboUnavailable, naming every model's failure. server.py calls it as `policy.text_for(CONFIG, …)`
    at call time, which is what makes the wrap take effect (checked by contract_check)."""
    original = getattr(policy.text_for, "__wrapped__", policy.text_for)
    rotation = Rotation(models)

    def text_for(cfg, *args, **kwargs):
        def attempt(model: str) -> str:
            return original(dataclasses.replace(cfg, text_model=model), *args, **kwargs)
        try:
            return rotation.call(attempt, policy.TurboUnavailable)
        except NoModelAvailable as exc:
            raise policy.TurboUnavailable(f"Text helper: {exc}; nothing typed.") from None

    text_for.__wrapped__ = original
    text_for.rotation = rotation
    policy.text_for = text_for


def package_version() -> str:
    from importlib.metadata import version
    return version("jev-ultrafast-mcp")


def set_call_timeout(policy, seconds: float) -> None:
    """The package sends every request through one httpx client with a fixed 30 s timeout (policy.CLIENT). A
    decision model on this Mac answers in seconds, not in the milliseconds of a data-centre GPU, and the first call
    after the server starts is the slowest, so the local route replaces that client with one of its own timeout
    (config models.local.timeout). Same flags as the package's, so only the deadline changes."""
    old = getattr(policy, "CLIENT", None)
    if not isinstance(old, httpx.Client):
        raise RuntimeError("jev_ultrafast_mcp.policy.CLIENT is not an httpx.Client: cannot set the call timeout")
    policy.CLIENT = httpx.Client(http2=True, timeout=seconds)
    old.close()


def load():
    """Import jev_ultrafast_mcp.server (once per process), after apply_env()."""
    global _server
    if _server is None:
        if _applied is None:
            raise RuntimeError("call jev.apply_env() before jev.load()")
        if package_version() != EXPECTED_PACKAGE_VERSION:
            raise RuntimeError(f"jev-ultrafast-mcp {package_version()} is installed; this project needs "
                               f"{EXPECTED_PACKAGE_VERSION} from vendor/ (see README, Setup)")
        from jev_ultrafast_mcp import server
        _server = server
        rotate_text_helper(server.policy, _text_helpers)
        clean_requests(server.policy, string_state=_string_state)
        if _call_timeout is not None:
            set_call_timeout(server.policy, _call_timeout)
        from jev_ultrafast_mcp import browser as package_browser
        guard_clicks(package_browser)
        # Importing the server turns on INFO logging, and httpx then prints every request ("HTTP Request: POST
        # https://openrouter.ai/…") into the run's output (live run 2026-09-23). Keep only its warnings.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
    return _server


def server_signatures() -> dict[str, Any]:
    """inspect.signature of every public browser_* function (for contract_check.py)."""
    import inspect
    srv = load()
    return {n: inspect.signature(getattr(srv, n)) for n in dir(srv)
            if n.startswith("browser_") and callable(getattr(srv, n))}


# ------------------------------------------------------------------ the client

_worker: ThreadPoolExecutor | None = None
_worker_lock = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    """One dedicated worker thread for the whole process: one call at a time (B7)."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev")
        return _worker


class Jev:
    """Typed calls to server.browser_*; every browser_act goes through the guard (§4.1)."""

    def __init__(self, cfg: Config, key: str, *, calls_log: Path | None = None,
                 on_timeout: Callable[[str], None] | None = None, server: Any = None,
                 timeouts: tuple[float, float] = (DEFAULT_TIMEOUT, GOAL_TIMEOUT),
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg, self._key, self.calls_log = cfg, key, calls_log
        self.sleep = sleep
        self.on_timeout = on_timeout            # writes the report before os._exit(3)
        self._server = server                   # a stub in tests; else the real package (load())
        self.timeouts = timeouts

    @property
    def server(self):
        return self._server if self._server is not None else load()

    def _log(self, name: str, args: dict, result: str, ms: int) -> None:
        if not self.calls_log:
            return
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "args": args,
                           "ms": ms, "result": result}, ensure_ascii=False, default=str)
        if self._key:
            line = line.replace(self._key, "***")
        self.calls_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.calls_log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def call(self, name: str, *, _timeout: float | None = None, **kwargs: Any) -> str:
        """getattr(server, name)(**kwargs) on the worker thread. A hung call cannot be cancelled: on timeout
        the report is written and the process exits 3 without running exit hooks (the job's tab stays open).
        `_timeout` overrides the default for one call (preflight's first connection waits for Chrome's
        "Allow remote debugging?" prompt, which a person has to click)."""
        fn = getattr(self.server, name)
        timeout = _timeout or (self.timeouts[1] if name == "browser_goal" else self.timeouts[0])
        t0 = time.monotonic()
        future = _executor().submit(fn, **kwargs)
        try:
            text = future.result(timeout=timeout)
        except FutureTimeout:
            msg = f"{name} did not return within {timeout:.0f}s"
            self._log(name, kwargs, f"timeout: {msg}", int((time.monotonic() - t0) * 1000))
            try:
                if self.on_timeout:
                    self.on_timeout(msg)
            finally:
                # os._exit skips interpreter shutdown, including flushing stdout: with output redirected to a
                # file, every line of the run would be lost (live run, 2026-09-23).
                for stream in (sys.stdout, sys.stderr):
                    try:
                        stream.flush()
                    except Exception:  # noqa: BLE001 - exiting anyway
                        pass
                os._exit(EXIT_STOPPED)
        except Exception as exc:  # noqa: BLE001 - a raising function is reported like an error result
            text = f"error({type(exc).__name__}): {exc}"
        text = clean_text(text if isinstance(text, str) else str(text))
        self._log(name, kwargs, text, int((time.monotonic() - t0) * 1000))
        return text

    def checked(self, name: str, *, _timeout: float | None = None, **kwargs: Any) -> str:
        text = self.call(name, _timeout=_timeout, **kwargs)
        if text.startswith(ERROR_PREFIXES):
            raise JevError(text)
        return text

    # ---------------------------------------------------------------- typed calls

    def doctor(self) -> dict:
        return json.loads(self.checked("browser_doctor"))

    def open(self, url: str, session: str, *, timeout: float | None = None) -> str:
        return self.checked("browser_open", url=url, session=session, _timeout=timeout)

    def observe(self, session: str, *, mode: str = "full", include_json: bool = True,
                include_text: bool = True) -> str:
        return self.checked("browser_observe", session=session, mode=mode, include_json=include_json,
                            include_text=include_text)

    def table(self, session: str) -> tuple[str, Table]:
        return split_json(self.observe(session))

    def act(self, ops: list[dict], session: str, table: Table, **kw) -> str:
        """The only path to browser_act: every op passes guard.check against `table` first (§4.1)."""
        for op in ops:
            guard.check(op, table)
        return self.checked("browser_act", ops=ops, session=session, **kw)

    def probe(self, session: str, *names: str) -> dict[str, dict]:
        """Run eval probes by name (probes.EVAL_PROBES) in one round trip."""
        ops = [{"op": "eval", "js": probes.EVAL_PROBES[n]} for n in names]
        text = self.act(ops, session, Table(url=""), observe_after=False, stop_on_error=False)
        values = probes.parse_eval_results(text)
        if len(values) != len(names):
            raise probes.ProbeError(f"expected {len(names)} probe results, got {len(values)}: {text[:200]}")
        return dict(zip(names, values))

    def assert_(self, checks: list[dict], session: str) -> str:
        for c in checks:
            if c.get("type") == "js" and c.get("expr") not in probes.ASSERT_PROBES.values():
                raise guard.GuardError("js checks must be probes.py constants")
        return self.checked("browser_assert", checks=checks, session=session)

    def captcha_present(self, session: str) -> bool:
        return self.assert_([{"type": "js", "expr": probes.CAPTCHA_PRESENT}], session).startswith("PASS")

    def goal(self, goal: str, session: str, *, max_steps: int = 20, verify: list[dict] | None = None) -> str:
        """browser_goal. A goal the decision model could not serve (429/5xx, unreachable) before any step is
        asked again after each of decide.RETRY_WAITS; still out, it is a DecisionError (the job goes to Needs
        Attention as `decision`), never a page with "no way forward"."""
        for wait in (*RETRY_WAITS, None):
            # verbose=True: the trace is the only place a needs_confirmation label shows (B1). No url= (0.1.5).
            out = self.call("browser_goal", goal=goal, session=session, max_steps=max_steps, verify=verify,
                            verbose=True)
            m = TRANSIENT_GOAL_RE.search(out)
            if not (m and NO_STEPS_RE.search(out)):
                return out
            if wait is None:
                raise DecisionError(f"the browser agent's decision model is unavailable: {m.group(0)[8:]}")
            self.sleep(wait)
        raise AssertionError("unreachable")

    def tabs(self, session: str, action: str = "list", *, target_id: str = "", url: str = "about:blank",
             index: int = -1) -> str:
        return self.checked("browser_tabs", session=session, action=action, index=index, target_id=target_id, url=url)

    def close(self, session: str) -> str:
        return self.checked("browser_close", session=session)
