"""Session management and guarded execution — the owned browser engine.

Ported from the vendored browser package (0.1.5+aa6, MIT; see LICENSE) `browser.py` (MIT; see LICENSE). Owned by this project (P2).

What changed from the vendored version:
  * one guarded mouse-press path (`_press`): every click — a click op, a toggle, a file-chooser
    upload, and the focus click of a `type` — runs `refuse_click` on the element's LIVE descriptor
    first, so the never-submit rule is enforced in the driver as defence in depth (00_common §4.1).
  * no `keys` op and no `type … submit` Enter: the driver has no way to send Enter/Escape (§4.1).
  * macros, the browser_goal support, the domain envelope and the secret-typing confirmation are gone.
  * settings come from a `Settings` object (constructor), not `JEVMCP_*`.
  * a hung CDP call raises `DriverTimeout` and keeps the process alive; nothing closes tabs at exit.
  * a tab opened by the page is identified through `Target.targetCreated` openers, not a helper tab.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cdp import Cdp, CdpError, DriverError, DriverTimeout, ChromeLaunchError, attach_chrome
from .observe import Observation

HELPER_SRC = (Path(__file__).with_name("observer.js")).read_text(encoding="utf-8")
HELPER_VERSION = 14


def chrome_data_dirs() -> list[Path]:
    """Where a Chromium-family browser keeps `DevToolsActivePort` (Chrome 144+ serves debugging over a
    WebSocket-only endpoint, so the port and browser WebSocket path are read from this file). Most likely
    first. Ported from the vendored config.py."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        names = ["Google/Chrome", "Chromium", "Google/Chrome Beta", "Microsoft Edge",
                 "BraveSoftware/Brave-Browser"]
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        names = ["Google/Chrome/User Data", "Chromium/User Data", "Microsoft/Edge/User Data",
                 "BraveSoftware/Brave-Browser/User Data"]
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        names = ["google-chrome", "chromium", "microsoft-edge", "BraveSoftware/Brave-Browser"]
    return [base / name for name in names]

# The only ops the driver executes (build spec §3.4, P2 keep-list). Everything else raises.
CLICKABLE_KINDS = {"click", "type", "select", "toggle", "upload"}
REQUIRES_REF = CLICKABLE_KINDS

# Geometry failures a scroll can fix (frame reasons included: an element can be placed inside its own
# frame yet below the top-level fold).
SCROLLABLE_REASONS = {
    "out_of_viewport", "occluded",
    "frame_out_of_viewport", "frame_occluded", "frame_hidden",
}


class PageStale(DriverError):
    """A ref no longer refers to the page state that was observed."""


class ClickRefused(DriverError):
    """The never-submit guard refused a click/press on this element (returned as needs_confirmation)."""


@dataclass
class Settings:
    """Driver settings. `cdp_url` and `max_actions` come from config.toml's [browser]; the rest are
    constants that do not vary for this program."""
    cdp_url: str
    data_dirs: list[Path] = field(default_factory=chrome_data_dirs)
    window: tuple[int, int] = (1280, 860)
    max_actions: int = 250
    max_text: int = 6000
    nav_timeout: float = 20.0
    call_timeout: float = 30.0
    settle_timeout: float = 4.0
    settle_poll_ms: int = 120
    allow_uploads: bool = True
    allow_js: bool = True          # the program runs read-only eval probes (probes.py)
    foreground: bool = False
    state_dir: Path = field(default_factory=lambda: Path.home() / ".application-assistant-driver")


@dataclass
class Step:
    op: str
    ok: bool
    ref: str | None = None
    target: str | None = None
    error: str | None = None
    detail: str | None = None
    ms: int = 0
    page_changed: bool | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Session:
    name: str
    cfg: Settings
    cdp: Cdp
    refuse_click: "callable | None" = None      # (descriptor:dict) -> reason:str | None
    background: bool = True

    target_id: str = ""
    page_session: str = ""
    sequence: int = 0
    last: Observation | None = None
    history: list[Step] = field(default_factory=list)
    tabs: list[dict] = field(default_factory=list)
    _known_targets: set[str] = field(default_factory=set)
    _no_change_streak: int = 0
    _fresh: bool = False
    _strict: bool = True

    # ------------------------------------------------------------- life cycle

    def start(self, url: str = "about:blank") -> "Session":
        self._attach_page("about:blank")
        if url and url != "about:blank":
            self.navigate(url)
        return self

    def _attach_page(self, url: str) -> None:
        result = self.cdp.call("Target.createTarget", url=url, background=self.background)
        self.target_id = result["targetId"]
        self.page_session = self.cdp.call(
            "Target.attachToTarget", targetId=self.target_id, flatten=True
        )["sessionId"]
        self.cdp.call("Page.enable", session_id=self.page_session)
        self.cdp.call("Runtime.enable", session_id=self.page_session)
        width, height = self.cfg.window
        self.cdp.call("Emulation.setDeviceMetricsOverride", session_id=self.page_session,
                      width=width, height=height, deviceScaleFactor=1, mobile=False)
        self.cdp.call("Emulation.setFocusEmulationEnabled", session_id=self.page_session, enabled=True)
        self.cdp.call("Page.addScriptToEvaluateOnNewDocument", session_id=self.page_session,
                      source=HELPER_SRC)
        if not self.background:
            try:
                self.cdp.call("Target.activateTarget", targetId=self.target_id)
            except CdpError:
                pass
        self._refresh_tabs()
        self._known_targets = {tab["target_id"] for tab in self.tabs}

    def adopt(self, target_id: str) -> None:
        """Attach this session to an existing page target (used to drive a tab the page opened).

        Mirrors _attach_page's capability setup: without the focus emulation and the device-metrics override a
        background adopted tab is treated as unfocused/zero-size, and React inputs there silently drop typed
        values (read-back mismatch → broken_form on every external ATS form). addScriptToEvaluateOnNewDocument
        keeps the observer present across the tab's own later navigations (P5)."""
        self.target_id = target_id
        self.page_session = self.cdp.call(
            "Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
        self.cdp.call("Page.enable", session_id=self.page_session)
        self.cdp.call("Runtime.enable", session_id=self.page_session)
        width, height = self.cfg.window
        self.cdp.call("Emulation.setDeviceMetricsOverride", session_id=self.page_session,
                      width=width, height=height, deviceScaleFactor=1, mobile=False)
        self.cdp.call("Emulation.setFocusEmulationEnabled", session_id=self.page_session, enabled=True)
        self.cdp.call("Page.addScriptToEvaluateOnNewDocument", session_id=self.page_session, source=HELPER_SRC)
        self._ensure_helper()
        self.last = None
        self._refresh_tabs()

    def close(self) -> None:
        if self.target_id:
            try:
                self.cdp.call("Target.closeTarget", targetId=self.target_id)
            except CdpError:
                pass
            self.target_id = ""

    # ------------------------------------------------------------- primitives

    def _ensure_helper(self) -> None:
        version = self._safe_eval("(window.__jevMcp && window.__jevMcp.version) || 0")
        if version != HELPER_VERSION:
            self.cdp.evaluate(HELPER_SRC, self.page_session, timeout=15)

    def _safe_eval(self, expression: str, *, await_promise: bool = False, timeout: float = 8.0):
        """Evaluate, returning None when the execution context is mid-navigation."""
        try:
            return self.cdp.evaluate(expression, self.page_session,
                                     await_promise=await_promise, timeout=timeout)
        except CdpError:
            return None

    def evaluate_js(self, expression: str):
        if not self.cfg.allow_js:
            raise ValueError("JS evaluation is disabled")
        return self.cdp.evaluate(expression, self.page_session)

    def _call(self, method: str, timeout: float | None = None, **params):
        return self.cdp.call(method, session_id=self.page_session, timeout=timeout, **params)

    def _refresh_tabs(self) -> list[dict]:
        try:
            targets = self.cdp.call("Target.getTargets")["targetInfos"]
        except CdpError:
            return self.tabs
        tabs = []
        for info in targets:
            if info.get("type") != "page" or info.get("url", "").startswith(("devtools://",)):
                continue
            tabs.append({
                "index": len(tabs),
                "target_id": info["targetId"],
                "opener_id": info.get("openerId") or self.cdp.openers.get(info["targetId"]),
                "url": info.get("url", ""),
                "title": info.get("title", ""),
                "active": info["targetId"] == self.target_id,
            })
        self.tabs = tabs
        return tabs

    # ----------------------------------------------------------- navigation

    def navigate(self, url: str, *, timeout: float | None = None) -> None:
        self.cdp.events.clear()
        self._call("Page.navigate", url=url)
        self._wait_loaded(timeout)

    def _wait_loaded(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout or self.cfg.nav_timeout)
        settled = False
        while time.monotonic() < deadline:
            if not settled:
                self._call("Runtime.evaluate", expression="1", returnByValue=True, timeout=2)
                settled = True
            if any(message.get("method") == "Page.loadEventFired" for message in self.cdp.events):
                break
            if self._safe_eval("document.readyState") == "complete":
                break
            time.sleep(0.02)
        self._ensure_helper()
        return self._safe_eval("document.readyState") == "complete"

    def _resolve_tab(self, index: int | None = None, target_id: str | None = None) -> dict:
        tabs = self._refresh_tabs()
        if target_id:
            match = next((tab for tab in tabs if tab["target_id"] == target_id), None)
            if match is None:
                raise CdpError(f"No tab with that target_id any more; {len(tabs)} tab(s) open")
            return match
        if index is None:
            raise CdpError("A tab action needs either index or target_id")
        if index < 0 or index >= len(tabs):
            raise CdpError(f"No tab at index {index}; {len(tabs)} tab(s) open")
        return tabs[index]

    def switch_tab(self, index: int | None = None, *, target_id: str | None = None) -> None:
        target = self._resolve_tab(index, target_id)
        if target["target_id"] == self.target_id:
            return
        self.adopt(target["target_id"])

    def _await_tab(self, *, exclude: str = "", timeout: float = 3.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = [tab for tab in self._refresh_tabs() if tab["target_id"] != exclude]
            if remaining:
                return remaining[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def close_tab(self, index: int | None = None, *, target_id: str | None = None) -> None:
        if not self._refresh_tabs():
            return
        if target_id or index is not None:
            target = self._resolve_tab(index, target_id)["target_id"]
        else:
            target = self.target_id
        if target == self.target_id:
            self.close()
            survivor = self._await_tab(exclude=target)
            if survivor is not None:
                self.switch_tab(target_id=survivor["target_id"])
        else:
            self.cdp.call("Target.closeTarget", targetId=target)
        self._refresh_tabs()

    # -------------------------------------------------------------- observe

    def _read_state(self, *, include_text: bool) -> dict:
        options = {
            "maxActions": self.cfg.max_actions,
            "maxText": self.cfg.max_text if include_text else 0,
            "includeText": include_text,
        }
        raw = self.cdp.evaluate(
            f"window.__jevMcp.readState({json.dumps(options)})",
            self.page_session, timeout=20,
        )
        if raw is None:
            raise PageStale("Page produced no snapshot (still navigating?)")
        return json.loads(raw)

    def _page_has_nodes(self) -> bool:
        count = self._safe_eval("document.body ? document.body.childElementCount : 0")
        return isinstance(count, int) and count > 0

    def observe(self, *, include_text: bool = True, full: bool = False,
                focus: list[str] | None = None) -> Observation:
        self._ensure_helper()
        data = self._read_state(include_text=include_text)
        if not data.get("actions") and self._page_has_nodes():
            # Content but nothing actionable — a client-rendered page whose JS has not filled it in yet.
            deadline = time.monotonic() + self.cfg.settle_timeout
            while time.monotonic() < deadline:
                time.sleep(self.cfg.settle_poll_ms / 1000.0)
                data = self._read_state(include_text=include_text)
                if data.get("actions"):
                    break
        observation = Observation.from_raw(data)
        self.sequence += 1
        observation.sequence = self.sequence

        previous = self.last
        observation.previous = (
            previous if (not full and previous is not None and previous.url == observation.url) else None
        )

        tabs = self._refresh_tabs()
        seen = {tab["target_id"] for tab in tabs}
        if previous is not None:
            # Only tabs THIS tab opened (openerId), never ones the user opened, so junk-closing is safe.
            observation.new_tabs = [tab for tab in tabs if tab["target_id"] not in self._known_targets
                                    and tab.get("opener_id") == self.target_id]
        observation.tabs = tabs
        self._known_targets = seen
        self.last = observation
        self._fresh = True
        return observation

    # ---------------------------------------------------------------- actions

    def act(self, ops: list[dict], *, dry_run: bool = False, stop_on_error: bool = True,
            observe_after: bool = True) -> dict:
        if not isinstance(ops, list) or not ops:
            raise ValueError("act() needs a non-empty list of ops")
        if self.last is None:
            self.observe()

        results: list[Step] = []
        strict = self._fresh
        for raw_op in ops:
            step = self._run_op(raw_op, dry_run=dry_run, strict=strict)
            results.append(step)
            self.history.append(step)
            if step.ok and not dry_run:
                strict = False
                self._fresh = False
            if not step.ok and stop_on_error:
                break

        payload: dict = {
            "ops": [step.to_dict() for step in results],
            "ok": all(step.ok for step in results),
            "steps": len(self.history),
        }
        if observe_after and not dry_run:
            try:
                observation = self.observe()
            except PageStale:
                observation = None
            if observation is not None:
                previous = observation.previous
                changed = previous is None or observation.digest != previous.digest
                self._no_change_streak = 0 if changed else self._no_change_streak + 1
                payload["page_changed"] = changed
                payload["observation"] = observation
        if self._no_change_streak >= 3:
            payload["stuck"] = (
                "Three consecutive actions changed nothing. Do not retry the same ref: "
                "re-read the observation, look for a covering dialog, or change strategy."
            )
        return payload

    def _resolve_ref(self, ref: str) -> tuple[str, str | None]:
        if ":" in ref:
            head, _, tail = ref.partition(":")
            return (head if head.startswith("e") else "e" + head), tail
        return (ref if ref.startswith("e") else "e" + ref), None

    def _guard(self, ref: str, strict: bool | None = None) -> dict:
        strict = self._strict if strict is None else strict
        if strict:
            page_key = self.last.page_key if self.last else ""
            result = self._safe_eval(
                "window.__jevMcp.verify(%s, %s)" % (json.dumps(ref), json.dumps(page_key))
            )
        else:
            result = self._safe_eval("window.__jevMcp.reinspect(%s)" % json.dumps(ref))
        if not isinstance(result, dict):
            return {"ok": False, "reason": "page_unavailable"}
        return result

    def _geometry(self, ref: str) -> dict:
        result = self._safe_eval("window.__jevMcp.resolve(%s)" % json.dumps(ref))
        if not isinstance(result, dict):
            return {"ok": False, "reason": "page_unavailable"}
        return result

    def _ensure_reachable(self, ref: str, strict: bool | None = None) -> dict:
        guard = self._guard(ref, strict)
        if not guard.get("ok"):
            return {"ok": False, "reason": guard.get("reason", "stale")}
        geometry = self._geometry(ref)
        if geometry.get("ok"):
            return geometry
        if geometry.get("reason") in SCROLLABLE_REASONS:
            self._safe_eval("window.__jevMcp.scrollTo(%s)" % json.dumps(ref))
            self._safe_eval("window.__jevMcp.settle('fast')", await_promise=True, timeout=4)
            geometry = self._geometry(ref)
            if geometry.get("ok"):
                return geometry
        return {"ok": False, "reason": geometry.get("reason", "unreachable")}

    def _label(self, ref: str) -> str:
        value = self._safe_eval("window.__jevMcp.label(%s)" % json.dumps(ref))
        return value if isinstance(value, str) else ""

    def _descriptor(self, ref: str) -> dict:
        """The element's live guard descriptor, read just before a press (never from self.last)."""
        value = self._safe_eval("window.__jevMcp.descriptor(%s)" % json.dumps(ref))
        return value if isinstance(value, dict) else {"ref": ref, "name": self._label(ref)}

    # ------------------------------------------------------------ op dispatch

    def _run_op(self, raw_op: dict, *, dry_run: bool, strict: bool = True) -> Step:
        self._strict = strict
        started = time.monotonic()
        if not isinstance(raw_op, dict):
            return Step(op="?", ok=False, error="bad_op", detail="each op must be an object")
        op = str(raw_op.get("op") or "").strip().lower()
        ref_raw = raw_op.get("ref")
        ref = None
        target_label = None

        try:
            if op in REQUIRES_REF:
                if not ref_raw:
                    raise ValueError(f"op {op!r} needs a ref")
                ref, _ = self._resolve_ref(str(ref_raw))
                guard = self._guard(ref)
                if not guard.get("ok"):
                    raise PageStale(guard.get("reason", "stale"))

            if op == "click":
                target_label = self._label(ref)
                if dry_run:
                    return Step(op=op, ref=ref, target=target_label, ok=True, detail="dry run")
                self._do_click(ref)
                self._after_input("options" if raw_op.get("kind") == "combobox" else "fast")

            elif op == "type":
                target_label = self._label(ref)
                if dry_run:
                    return Step(op=op, ref=ref, target=target_label, ok=True, detail="dry run")
                # The focus click routes through _press, so a `type` on a submit control is refused too.
                self._do_type(ref, str(raw_op.get("text") or ""), raw_op.get("clear", True),
                              raw_op.get("slow"))
                self._after_input("fast")

            elif op == "select":
                wanted = raw_op.get("value", raw_op.get("label"))
                if wanted is None:
                    raise ValueError("select needs 'value' or 'label'")
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                selected = self._safe_eval(
                    "window.__jevMcp.selectOption(%s, %s)"
                    % (json.dumps(ref), json.dumps(str(wanted)))
                )
                if not isinstance(selected, dict) or not selected.get("ok"):
                    reason = (selected or {}).get("reason", "select_failed")
                    raise ValueError(f"could not select {wanted!r}: {reason}")
                target_label = str(wanted)
                self._after_input("fast")

            elif op == "toggle":
                current = self._safe_eval(
                    # aa6: an ARIA radio/checkbox has no .checked; read aria-checked so a ticked box is not un-ticked
                    "(() => { const e=window.__jevRefs.nodes.get(%d); return e ? (typeof e.checked === 'boolean' "
                    "? e.checked : e.getAttribute('aria-checked') === 'true') : null; })()"
                    % int(ref[1:])
                )
                want = raw_op.get("state")
                if want is not None and bool(want) == bool(current):
                    return Step(op=op, ref=ref, ok=True, detail="already in requested state")
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                self._do_click(ref)
                self._after_input("fast")

            elif op == "upload":
                if not self.cfg.allow_uploads:
                    raise ValueError("uploads are disabled")
                paths = raw_op.get("paths") or ([raw_op["path"]] if raw_op.get("path") else [])
                if not paths:
                    raise ValueError("upload needs 'path' or 'paths'")
                missing = [p for p in paths if not Path(p).expanduser().exists()]
                if missing:
                    raise ValueError(f"file not found: {missing[0]}")
                resolved = [str(Path(p).expanduser().resolve()) for p in paths]
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                self._do_upload(ref, resolved)
                target_label = ", ".join(Path(p).name for p in resolved)
                self._after_input("fast")

            elif op == "reload":
                if not dry_run:
                    self.cdp.events.clear()
                    self._call("Page.reload")
                    self._wait_loaded()
                    self.last = None

            elif op == "wait_for_load":
                if not dry_run:
                    self._wait_loaded(float(raw_op.get("timeout_ms") or 20000) / 1000)

            elif op == "screenshot":
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                path = self._do_screenshot(raw_op)
                target_label = str(path)

            elif op == "eval":
                if not self.cfg.allow_js:
                    raise ValueError("eval is disabled")
                expression = str(raw_op.get("js") or "")
                if not expression:
                    raise ValueError("eval needs 'js'")
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                value = self._safe_eval(expression)
                # P2: no 200-char cap (a 256 KB sanity bound only).
                target_label = json.dumps(value)[:256 * 1024]

            else:
                raise ValueError(f"unsupported op {op!r}")

        except ClickRefused as exc:
            return Step(op=op, ref=ref, target=target_label, ok=False,
                        error="needs_confirmation", detail=str(exc)[:300],
                        ms=int((time.monotonic() - started) * 1000))
        except (PageStale, CdpError, ValueError) as exc:
            return Step(op=op, ref=ref, target=target_label, ok=False,
                        error=_error_code(exc), detail=str(exc)[:300],
                        ms=int((time.monotonic() - started) * 1000))

        return Step(op=op, ref=ref, target=target_label, ok=True,
                    ms=int((time.monotonic() - started) * 1000))

    # ------------------------------------------------------- input mechanics

    def _press(self, ref: str, x: float, y: float) -> None:
        """The one mouse-press path. Refuse a press the guard forbids (defence in depth, §4.1),
        reading the element's LIVE descriptor so a batch's earlier ops can't stale the check."""
        if self.refuse_click is not None:
            reason = self.refuse_click(self._descriptor(ref))
            if reason:
                raise ClickRefused(reason)
        self._call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for event in ("mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)

    def _do_click(self, ref: str) -> None:
        geometry = self._ensure_reachable(ref)
        if not geometry.get("ok"):
            raise PageStale(geometry.get("reason", "unreachable"))
        self._press(ref, geometry["x"], geometry["y"])

    def _do_type(self, ref: str, text: str, clear: bool, slow: bool | None) -> None:
        geometry = self._ensure_reachable(ref)
        if not geometry.get("ok"):
            raise PageStale(geometry.get("reason", "unreachable"))
        self._press(ref, geometry["x"], geometry["y"])
        if clear:
            self._select_all()
        if slow:
            for char in text:
                self._call("Input.dispatchKeyEvent", type="keyDown", text=char)
                self._call("Input.dispatchKeyEvent", type="keyUp")
            return
        if text:
            self._call("Input.insertText", text=text)

    def _do_upload(self, ref: str, paths: list[str]) -> None:
        node_id = int(ref[1:])
        # aa6: a button that opens the file chooser, with no <input type=file> until it is clicked
        # (LinkedIn Easy Apply "Upload resume"): intercept the chooser and fill its input.
        is_file = self._safe_eval(
            "(() => { const e = window.__jevRefs.nodes.get(%d); "
            "return !!e && e.tagName === 'INPUT' && (e.type || '').toLowerCase() === 'file'; })()" % node_id)
        if not is_file:
            self._upload_via_chooser(ref, paths)
            return
        result = self._call(
            "Runtime.evaluate",
            expression=f"window.__jevRefs.nodes.get({node_id})",
            returnByValue=False,
        )
        remote = result.get("result", {})
        if not remote.get("objectId"):
            raise PageStale("file input is gone")
        self._call("DOM.setFileInputFiles", files=paths, objectId=remote["objectId"])

    def _upload_via_chooser(self, ref: str, paths: list[str]) -> None:
        """aa6: click `ref` (guarded) with file-chooser interception on, then fill the input it opened."""
        self._call("Page.setInterceptFileChooserDialog", enabled=True)
        try:
            before = {id(m) for m in self.cdp.events}
            self._do_click(ref)
            deadline = time.monotonic() + 8
            event = None
            while event is None and time.monotonic() < deadline:
                event = next((m for m in list(self.cdp.events) if id(m) not in before
                              and m.get("method") == "Page.fileChooserOpened"
                              and m.get("sessionId") == self.page_session), None)
                if event is None:
                    time.sleep(0.1)
                    self._call("Runtime.evaluate", expression="0", returnByValue=True)   # pump events
            if event is None:
                raise PageStale("clicking it opened no file chooser")
            backend = (event.get("params") or {}).get("backendNodeId")
            if not backend:
                raise PageStale("the file chooser has no input to fill")
            self._call("DOM.setFileInputFiles", files=paths, backendNodeId=backend)
        finally:
            try:
                self._call("Page.setInterceptFileChooserDialog", enabled=False)
            except CdpError:
                pass

    def _do_screenshot(self, raw_op: dict) -> Path:
        directory = self.cfg.state_dir / "shots"
        directory.mkdir(parents=True, exist_ok=True)
        fmt = str(raw_op.get("format") or "jpeg").lower()
        params: dict = {
            "format": "png" if fmt == "png" else "jpeg",
            "captureBeyondViewport": bool(raw_op.get("full")),
        }
        if fmt != "png":
            params["quality"] = 80
        result = self._call("Page.captureScreenshot", **params)
        name = raw_op.get("path") or f"shot-{int(time.time() * 1000)}.{'png' if fmt == 'png' else 'jpg'}"
        path = Path(name)
        if not path.is_absolute():
            path = directory / path.name
        path.write_bytes(base64.b64decode(result["data"]))
        return path

    def _select_all(self) -> None:
        import sys
        modifier = 4 if sys.platform == "darwin" else 2
        for event in ("keyDown", "keyUp"):
            self._call("Input.dispatchKeyEvent", type=event, key="a", code="KeyA",
                       windowsVirtualKeyCode=65, modifiers=modifier,
                       **({"commands": ["selectAll"]} if event == "keyDown" else {}))

    def _after_input(self, settle_kind: str) -> None:
        """Settle in-page, then absorb a navigation if one was triggered."""
        self.cdp.events.clear()
        self._safe_eval(
            "window.__jevMcp.settle(%s)" % json.dumps(settle_kind),
            await_promise=True, timeout=5,
        )
        if self._safe_eval("document.readyState") == "complete":
            return
        deadline = time.monotonic() + self.cfg.nav_timeout
        while time.monotonic() < deadline:
            if any(message.get("method") == "Page.loadEventFired" for message in self.cdp.events):
                break
            if self._safe_eval("document.readyState") == "complete":
                break
            time.sleep(0.03)
        self._ensure_helper()


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PageStale):
        return exc.args[0] if exc.args else "stale"
    if isinstance(exc, CdpError):
        return "browser_error"
    return "invalid_request"


# --------------------------------------------------------------------- manager


class BrowserManager:
    """Owns the one CDP connection (attach only, D13) and the named sessions on top of it."""

    def __init__(self, cfg: Settings, *, refuse_click=None):
        self.cfg = cfg
        self.refuse_click = refuse_click
        self._cdp: Cdp | None = None
        self._sessions: dict[str, Session] = {}

    def connect(self, open_timeout: float) -> None:
        """Open the run's ONE CDP connection now (D13), with a human-sized handshake budget for
        Chrome's "Allow remote debugging?" prompt. Idempotent."""
        if self._cdp is None:
            self._cdp = attach_chrome(self.cfg.cdp_url, timeout=self.cfg.call_timeout,
                                      data_dirs=self.cfg.data_dirs, open_timeout=open_timeout)

    @property
    def cdp(self) -> Cdp:
        if self._cdp is None:
            if not self.cfg.cdp_url:
                raise ChromeLaunchError("a cdp_url is required to attach")
            self.connect(max(60.0, self.cfg.call_timeout))
        return self._cdp

    def session(self, name: str = "default") -> Session:
        session = self._sessions.get(name)
        if session is None:
            session = Session(name=name, cfg=self.cfg, cdp=self.cdp,
                              refuse_click=self.refuse_click, background=not self.cfg.foreground)
            session.start("about:blank")
            self._sessions[name] = session
        return session

    def close(self, name: str | None = None) -> list[str]:
        closed = []
        for key, session in list(self._sessions.items()):
            if name and key != name:
                continue
            session.close()
            self._sessions.pop(key, None)
            closed.append(key)
        return closed

    def forget(self, name: str) -> None:
        """Drop a session's bookkeeping without closing its tab (D13: a job tab stays open)."""
        self._sessions.pop(name, None)

    def detach(self) -> None:
        """Drop the sessions' bookkeeping and close the socket. Never closes the user's tabs (D13);
        never calls Browser.close. Nothing is closed at process exit either (no atexit hook, P2)."""
        self._sessions.clear()
        if self._cdp is not None:
            self._cdp.close()
            self._cdp = None

    def doctor(self) -> dict:
        report: dict = {
            "mode": "attach",
            "connected": self._cdp is not None,
            "sessions": sorted(self._sessions),
            "js_eval": self.cfg.allow_js,
            "uploads": self.cfg.allow_uploads,
        }
        if self._cdp is not None:
            try:
                version = self._cdp.call("Browser.getVersion")
                report["browser"] = version.get("product")
                report["protocol"] = version.get("protocolVersion")
            except CdpError as exc:
                report["browser_error"] = str(exc)
        return report
