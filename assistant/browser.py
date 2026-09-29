"""The browser facade — `Browser` replaces `jev.py`'s `Jev` (P2).

`Browser` wraps the owned driver (`assistant.driver`), runs the never-submit guard before every op,
renders the driver's structured results into the text formats the callers parse (build spec §3.4 +
the P2 element fields), and logs every call and its full result to `browser_actions.jsonl`.

Only this module imports `assistant.driver`. There is no `goal` and no `call(name, …)` string dispatch:
navigation is deterministic (`assistant.navigate`), and every browser action is a typed method here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from assistant import guard, probes
from assistant.config import Config
from assistant.driver import BrowserManager, DriverError, Settings
from assistant.driver.observe import Observation


class Option(BaseModel):
    model_config = ConfigDict(extra="allow")
    ref: str | None = None
    value: str | None = None
    label: str | None = None
    selected: bool | None = None


class Element(BaseModel):
    """One row of the element table (driver Observation.to_dict()['json'])."""
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
    # P2 fields (guard v2 + the fill loop).
    type: str = ""
    tag: str = ""
    required: bool = False
    maxlength: int | None = None
    placeholder: str = ""
    form: str = ""
    dialog: str = ""
    consent: str = ""
    scope: str = ""


class Table(BaseModel):
    """The json block of a driver observation: {url, title, elements}."""
    model_config = ConfigDict(extra="allow")
    url: str
    title: str = ""
    elements: list[Element] = []


def split_json(text: str) -> tuple[str, Table]:
    """Split an observe result into (view text, table). The observe format ends with '\\n\\njson: <json>'."""
    head, sep, tail = text.rpartition("\n\njson: ")
    if not sep:
        raise DriverError("observe output carries no json block")
    return head, Table.model_validate_json(tail)


# ------------------------------------------------------------------ renderers (ported from the package's server.py)

def _brief(obs: Observation) -> str:
    return f"{obs.url} — {len(obs.elements)} elements, {obs.reachable} reachable, obs#{obs.sequence}"


def _view(obs: Observation, *, mode: str = "auto", include_text: bool = True, max_text: int = 6000) -> str:
    return obs.render(obs.previous, mode=mode, include_text=include_text, max_text=max_text)


def _render_act(payload: dict, view: str | None) -> str:
    lines = []
    ops = payload.get("ops", [])
    ok_count = sum(1 for op in ops if op.get("ok"))
    lines.append(f"{ok_count}/{len(ops)} ops ok" + ("" if payload.get("ok") else "  (stopped early)"))
    for op in ops:
        ref = op.get("ref") or ""
        target = op.get("target") or ""
        label = f"{op.get('op')} {ref}".strip()
        if target:
            label += f" → {target}"
        if op.get("ok"):
            detail = f"  [{op['detail']}]" if op.get("detail") else ""
            lines.append(f"  + {label}  {op.get('ms', 0)}ms{detail}")
        else:
            lines.append(f"  x {label}  {op.get('error')}: {op.get('detail', '')}")
    if payload.get("stuck"):
        lines.append(f"! {payload['stuck']}")
    if view:
        lines.append("")
        lines.append(view)
    return "\n".join(lines)


def _tabs_list(tabs: list[dict]) -> str:
    if not tabs:
        return "no tabs"
    lines = []
    for item in tabs:
        handle = "#" + item["target_id"][:8]
        lines.append(f"  [{item['index']}] {'*' if item['active'] else ' '} {handle}  "
                     f"{item['url']}  \"{item['title']}\"")
    return "\n".join(lines)


# ------------------------------------------------------------------ the facade

class Browser:
    """Typed browser control; every act goes through the guard (§4.1) and every call is logged."""

    def __init__(self, cfg: Config, key: str = "", *, actions_log: Path | None = None,
                 refuse_click=None, on_timeout=None, manager=None):
        self.cfg = cfg
        self.actions_log = actions_log
        # The driver's press path refuses a click the never-submit rule forbids (defence in depth). The
        # guard reads the module page-stage (guard.FORM), which fill/navigate keep current.
        refuse = refuse_click or (lambda el: guard.never_click_element(el, guard.FORM))
        self._settings = Settings(cdp_url=cfg.browser.cdp_url, max_actions=cfg.browser.max_actions)
        # `manager` is injected by the offline tests (FakeBrowser) and lets one connection be shared for a
        # whole run (D13). Only one Browser is built per run, so only one CDP handshake happens.
        self._mgr = manager if manager is not None else BrowserManager(self._settings, refuse_click=refuse)

    # -- connection ---------------------------------------------------------
    def connect(self, open_timeout: float) -> None:
        self._mgr.connect(open_timeout)

    def detach(self) -> None:
        self._mgr.detach()

    def _session(self, name: str):
        return self._mgr.session(name)

    # -- logging ------------------------------------------------------------
    def _log(self, name: str, args: dict, result: str, ms: int) -> None:
        if not self.actions_log:
            return
        self.actions_log.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "args": args,
                           "ms": ms, "result": result}, ensure_ascii=False, default=str)
        with open(self.actions_log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _observe_text(self, obs: Observation, *, mode: str, include_json: bool, include_text: bool) -> str:
        text = _view(obs, mode=mode, include_text=include_text, max_text=self._settings.max_text)
        if include_json:
            text += "\n\njson: " + json.dumps(obs.to_dict()["json"], ensure_ascii=False)
        return text

    # -- typed calls --------------------------------------------------------
    def doctor(self) -> dict:
        t0 = time.monotonic()
        report = self._mgr.doctor()
        self._log("doctor", {}, json.dumps(report), int((time.monotonic() - t0) * 1000))
        return report

    def open(self, url: str, session: str, *, timeout: float | None = None) -> str:
        t0 = time.monotonic()
        s = self._session(session)
        s.navigate(url, timeout=timeout)
        obs = s.observe()
        text = f"opened {_brief(obs)}\n\n" + self._observe_text(obs, mode="full", include_json=False,
                                                                include_text=True)
        self._log("open", {"url": url, "session": session}, text, int((time.monotonic() - t0) * 1000))
        return text

    def observe(self, session: str, *, mode: str = "full", include_json: bool = True,
                include_text: bool = True) -> str:
        t0 = time.monotonic()
        s = self._session(session)
        obs = s.observe(include_text=include_text, full=(mode == "full"))
        text = self._observe_text(obs, mode=mode, include_json=include_json, include_text=include_text)
        self._log("observe", {"session": session, "mode": mode, "include_json": include_json,
                              "include_text": include_text}, text, int((time.monotonic() - t0) * 1000))
        return text

    def table(self, session: str) -> tuple[str, Table]:
        return split_json(self.observe(session))

    def act(self, ops: list[dict], session: str, table: Table, **kw) -> str:
        """The only path to the driver's act: every op passes guard.check against `table` first (§4.1)."""
        for op in ops:
            guard.check(op, table)
        t0 = time.monotonic()
        s = self._session(session)
        payload = s.act(ops, **kw)
        obs = payload.pop("observation", None)
        view = self._observe_text(obs, mode="auto", include_json=False, include_text=True) if obs is not None else None
        text = _render_act(payload, view)
        self._log("act", {"ops": ops, "session": session, **kw}, text, int((time.monotonic() - t0) * 1000))
        return text

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
        t0 = time.monotonic()
        s = self._session(session)
        from assistant.driver import assertions
        obs = s.observe(include_text=True)
        result = assertions.run(checks, obs, allow_js=s.cfg.allow_js, eval_js=s.evaluate_js)
        lines = [f"{'PASS' if result['pass'] else 'FAIL'}  ({result['url']})"]
        for check in result["checks"]:
            lines.append(f"  {'ok' if check['ok'] else 'X '} {check['type']}: {check['detail']}")
        text = "\n".join(lines)
        self._log("assert", {"checks": checks, "session": session}, text, int((time.monotonic() - t0) * 1000))
        return text

    def captcha_present(self, session: str) -> bool:
        return self.assert_([{"type": "js", "expr": probes.CAPTCHA_PRESENT}], session).startswith("PASS")

    def tabs(self, session: str, action: str = "list", *, target_id: str = "", url: str = "about:blank",
             index: int = -1) -> str:
        t0 = time.monotonic()
        s = self._session(session)
        which = index if index >= 0 else None
        if action == "list":
            text = _tabs_list(s._refresh_tabs())
        elif action == "new":
            s.cdp.call("Target.createTarget", url=url, background=not s.cfg.foreground)
            text = "opened new tab\n" + _tabs_list(s._refresh_tabs())
        elif action == "switch":
            s.switch_tab(which, target_id=target_id or None)
            text = f"switched to tab {target_id or which}"
        elif action == "close":
            s.close_tab(which, target_id=target_id or None)
            text = f"closed tab {target_id or which or 'current'}"
        else:
            text = f"unknown tab action {action!r}"
        self._log("tabs", {"session": session, "action": action, "target_id": target_id, "index": index},
                  text, int((time.monotonic() - t0) * 1000))
        return text

    def new_tabs(self, session: str) -> list[dict]:
        """Tabs opened by this session's tab since the last observe (via Target.targetCreated openers)."""
        s = self._session(session)
        return list(getattr(s.last, "new_tabs", []) or [])

    def list_tabs(self) -> list[dict]:
        """Every page tab, with full target ids + openerIds — read from the connection, no tab created."""
        cdp = self._mgr.cdp
        out = []
        for info in cdp.call("Target.getTargets").get("targetInfos", []):
            if info.get("type") == "page" and not info.get("url", "").startswith("devtools://"):
                out.append({"target_id": info["targetId"],
                            "opener_id": info.get("openerId") or cdp.openers.get(info["targetId"]),
                            "url": info.get("url", ""), "title": info.get("title", "")})
        return out

    def session_target(self, session: str) -> str:
        """The full target id of the tab this session is driving, or '' if it has no tab yet (never creates one)."""
        s = self._mgr._sessions.get(session)
        return s.target_id if s is not None else ""

    def close_tab_id(self, session: str, target_id: str) -> None:
        self._session(session).cdp.call("Target.closeTarget", targetId=target_id)

    def close(self, session: str) -> str:
        """Close a session's tab. Use only for the program's own scratch tabs (preflight); never for a
        job tab — the job tab must stay open (D13). Use `forget` to drop a job session's bookkeeping."""
        t0 = time.monotonic()
        closed = self._mgr.close(session)
        text = f"closed {closed or 'nothing'}"
        self._log("close", {"session": session}, text, int((time.monotonic() - t0) * 1000))
        return text

    def forget(self, session: str) -> str:
        """Drop a session's bookkeeping without closing its tab (a job tab is left open, D13)."""
        self._mgr.forget(session)
        return f"forgot {session}"
