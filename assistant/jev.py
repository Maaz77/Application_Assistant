"""jev-ultrafast-mcp 0.1.5, called directly in Python (spec v2 §3). The only module that imports the package.

Order matters: the package reads its config and builds its browser manager when `server` is imported
(server.py l.61–63), so apply_env() must run first. Only the public `server.browser_*` functions are called.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from assistant import guard, probes
from assistant.config import Config
from assistant.guard import TRANSMIT

STRIPPED_PREFIXES = ("JEVMCP_", "TYPESAFE_", "TEXT_MODEL_", "OPENROUTER_")
DEFAULT_TIMEOUT = 60.0
GOAL_TIMEOUT = 300.0
EXIT_STOPPED = 3

# Result prefixes the package uses for failures (server._error and friends).
ERROR_PREFIXES = ("browser_unavailable:", "blocked_by_policy:", "stale:", "browser_error:",
                  "turbo_unavailable:", "error(")


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
    """The §3 table. Importing jev_ultrafast_mcp.config is safe: it does not load the server."""
    from jev_ultrafast_mcp.config import DEFAULT_DENY_PATTERNS

    patterns = list(DEFAULT_DENY_PATTERNS) + TRANSMIT
    for p in patterns:
        assert "," not in p and ";" not in p, f"confirm pattern would be split: {p!r}"
    return {
        "JEVMCP_MODE": "attach",
        "JEVMCP_CDP_URL": cdp_url or cfg.browser.cdp_url,
        "JEVMCP_FOREGROUND": "0",
        "JEVMCP_MAX_ACTIONS": str(cfg.browser.max_actions),
        "JEVMCP_ALLOW_UPLOADS": "1",
        "JEVMCP_ALLOW_JS": "1",
        "JEVMCP_CONFIRM_PATTERNS": ",".join(patterns),
        "TYPESAFE_BASE_URL": "https://openrouter.ai/api/alpha/decisions",
        "TYPESAFE_MODEL": cfg.models.jev,
        "OPENROUTER_API_KEY": key,
        "TEXT_MODEL_API_KEY": key,
        "TEXT_MODEL_BASE_URL": "https://openrouter.ai/api/v1",
        "TEXT_MODEL": cfg.models.text_helper,
    }


_server = None
_applied: dict[str, str] | None = None


def apply_env(cfg: Config, key: str, cdp_url: str | None = None) -> None:
    """Scrub stray package variables from os.environ, then set the §3 values. Must precede load()."""
    global _applied
    if _server is not None:
        raise RuntimeError("apply_env() after the package was loaded has no effect (config is fixed per process)")
    for k in [k for k in os.environ if k.startswith(STRIPPED_PREFIXES)]:
        del os.environ[k]
    _applied = env_values(cfg, key, cdp_url)
    os.environ.update(_applied)


def load():
    """Import jev_ultrafast_mcp.server (once per process), after apply_env()."""
    global _server
    if _server is None:
        if _applied is None:
            raise RuntimeError("call jev.apply_env() before jev.load()")
        from jev_ultrafast_mcp import server
        _server = server
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
                 timeouts: tuple[float, float] = (DEFAULT_TIMEOUT, GOAL_TIMEOUT)):
        self.cfg, self._key, self.calls_log = cfg, key, calls_log
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

    def call(self, name: str, **kwargs: Any) -> str:
        """getattr(server, name)(**kwargs) on the worker thread. A hung call cannot be cancelled: on timeout
        the report is written and the process exits 3 without running exit hooks (the job's tab stays open)."""
        fn = getattr(self.server, name)
        timeout = self.timeouts[1] if name == "browser_goal" else self.timeouts[0]
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
                os._exit(EXIT_STOPPED)
        except Exception as exc:  # noqa: BLE001 - a raising function is reported like an error result
            text = f"error({type(exc).__name__}): {exc}"
        text = text if isinstance(text, str) else str(text)
        self._log(name, kwargs, text, int((time.monotonic() - t0) * 1000))
        return text

    def checked(self, name: str, **kwargs: Any) -> str:
        text = self.call(name, **kwargs)
        if text.startswith(ERROR_PREFIXES):
            raise JevError(text)
        return text

    # ---------------------------------------------------------------- typed calls

    def doctor(self) -> dict:
        return json.loads(self.checked("browser_doctor"))

    def open(self, url: str, session: str) -> str:
        return self.checked("browser_open", url=url, session=session)

    def observe(self, session: str, *, mode: str = "full", include_json: bool = True,
                include_text: bool = True) -> str:
        return self.checked("browser_observe", session=session, mode=mode, include_json=include_json,
                            include_text=include_text)

    def table(self, session: str) -> tuple[str, Table]:
        return split_json(self.observe(session))

    def act(self, ops: list[dict], session: str, table: Table, *, token: object = None, **kw) -> str:
        """The only path to browser_act: every op passes guard.check against `table` first (§4.1)."""
        for op in ops:
            guard.check(op, table, token)
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
        # verbose=True: the trace is the only place a needs_confirmation label shows (B1). No url= (0.1.5).
        return self.call("browser_goal", goal=goal, session=session, max_steps=max_steps, verify=verify,
                         verbose=True)

    def tabs(self, session: str, action: str = "list", *, target_id: str = "", url: str = "about:blank") -> str:
        return self.checked("browser_tabs", session=session, action=action, target_id=target_id, url=url)

    def close(self, session: str) -> str:
        return self.checked("browser_close", session=session)
