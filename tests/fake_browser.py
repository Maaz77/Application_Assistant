"""FakeBrowser: an in-memory stand-in for the owned driver (P2), injected as a Browser `manager=`.

It returns real driver `Observation`s built from scripted `FakePage`s and applies act ops to their state,
so the REAL Browser facade runs on top — guard.check, never_click_element (the driver press path), the
renderers and probes.parse_eval_results all run unchanged. Navigation is deterministic now, so there is no
goal to simulate. `sent` records any transmit that got through (must stay empty)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from assistant import config, probes
from assistant.browser import Browser
from assistant.driver import Settings
from assistant.driver.observe import Observation


@dataclass
class El:
    role: str
    name: str
    value: str = ""
    required: bool = False
    maxlength: int = 0
    goto: str | None = None          # click → switch to this page
    group: str | None = None         # radio: the group question
    options: list[str] = field(default_factory=list)
    checked: bool | None = None
    context: str = ""
    refuse_typing: bool = False      # a masked/rewriting input: a typed value does not stick
    readonly: bool = False
    submits: bool = False            # a control that would send the form: a click on it lands in `sent`
    occluded: bool = False
    ref: str | None = None
    label: str = ""
    tag: str = ""                    # defaults derived from role/submits below
    type: str = ""
    form: str = ""
    dialog: str = ""
    consent: str = ""

    def tag_of(self) -> str:
        if self.tag:
            return self.tag
        return {"button": "BUTTON", "link": "A", "textbox": "INPUT", "searchbox": "INPUT",
                "spinbutton": "INPUT", "combobox": "SELECT", "checkbox": "INPUT", "radio": "INPUT",
                "file": "INPUT"}.get(self.role, "DIV")

    def type_of(self) -> str:
        if self.type:
            return self.type
        if self.submits:
            return "submit"
        return {"file": "file", "checkbox": "checkbox", "radio": "radio"}.get(self.role, "")


@dataclass
class FakePage:
    url: str
    title: str
    text: str
    els: list[El]
    captcha: bool = False
    iframes: list[str] = field(default_factory=list)
    modal: str | None = None


class FakeSession:
    def __init__(self, mgr: "FakeManager", name: str):
        self.mgr, self.name = mgr, name
        self.target_id = f"T-{name}"
        self.cfg = mgr.settings
        self.cdp = mgr.cdp
        self.last: Observation | None = None

    # -- reading -------------------------------------------------------------
    @property
    def _page(self) -> FakePage:
        return self.mgr.site[self.mgr.cur]

    def _refs(self):
        return [(e.ref or f"e{i + 1}", e) for i, e in enumerate(self._page.els)]

    def _by_ref(self, ref: str):
        base = str(ref).partition(":")[0]
        return dict(self._refs()).get(base)

    def _raw(self) -> dict:
        actions = []
        for ref, e in self._refs():
            editable = e.role in ("textbox", "searchbox", "spinbutton") and not e.readonly
            d = {"ref": ref, "role": e.role, "name": e.name, "editable": editable,
                 "occluded": e.occluded, "checked": e.checked, "context": e.context, "label": e.label,
                 "value": "" if e.role == "combobox" else e.value, "current": None,
                 "tag": e.tag_of(), "type": e.type_of(), "required": e.required,
                 "maxlength": e.maxlength or None, "form": e.form, "dialog": e.dialog, "consent": e.consent,
                 "scope": e.dialog or e.form, "group": e.group or ""}
            if e.role == "combobox":
                d["current"] = e.value or "Select"
                d["options"] = [{"ref": f"{ref}:{j + 1}", "label": o, "value": o, "selected": o == e.value}
                                for j, o in enumerate(e.options)]
                d["opts_total"] = len(e.options)
            actions.append(d)
        files = " ".join(e.value for e in self._page.els if e.role == "file" and e.value)
        return {"url": self._page.url, "title": self._page.title, "text": self._page.text + " " + files,
                "actions": actions, "reachable": len(actions)}

    def observe(self, *, include_text: bool = True, full: bool = False, focus=None) -> Observation:
        obs = Observation.from_raw(self._raw())
        obs.previous = self.last if (not full and self.last and self.last.url == obs.url) else None
        obs.tabs = self._refresh_tabs()
        self.last = obs
        return obs

    def navigate(self, url, *, timeout=None):
        """Go to the first scripted page with this url (several share the posting url) — like a real reopen,
        which lands back on the posting. Used when the fill loop reopens after a _Refill."""
        match = next((k for k, pg in self.mgr.site.items() if pg.url == url), None)
        if match is not None:
            self.mgr.cur = match
        self.last = None

    def _refresh_tabs(self):
        return [{"index": 0, "target_id": self.target_id, "opener_id": None,
                 "url": self._page.url, "title": self._page.title, "active": True}]

    def evaluate_js(self, expr: str):
        return self.mgr.probe_value(expr, self._page)

    # -- acting --------------------------------------------------------------
    def _click(self, e: El) -> str | None:
        reason = self.mgr.refuse_click(_descriptor(e)) if self.mgr.refuse_click else None
        if reason:
            return reason
        if e.submits:
            self.mgr.sent.append(e.name)                # a form went out: must never happen
        if e.role == "radio":
            for x in self._page.els:
                if x.group == e.group:
                    x.checked = x is e
        if e.goto:
            self.mgr.cur = e.goto
        return None

    def act(self, ops, *, dry_run=False, observe_after=True, stop_on_error=True) -> dict:
        results, ok = [], True
        for op in ops:
            kind, ref = op["op"], op.get("ref", "")
            self.mgr.log.append((kind, op))
            e = self._by_ref(ref) if ref else None
            step = {"op": kind, "ref": ref or None, "ok": True, "ms": 0}
            if kind == "eval":
                step["target"] = json.dumps(self.mgr.probe_value(op["js"], self._page))
            elif kind in {"click", "type", "upload", "select", "toggle"} and e is None:
                step.update(ok=False, error="unknown_ref", detail="")
            elif kind == "click":
                step["target"] = e.name
                reason = self._click(e)
                if reason:
                    step.update(ok=False, error="needs_confirmation", detail=reason)
            elif kind == "type":
                step["target"] = e.name
                reason = self.mgr.refuse_click(_descriptor(e)) if self.mgr.refuse_click else None
                if reason:
                    step.update(ok=False, error="needs_confirmation", detail=reason)
                elif not e.refuse_typing:
                    e.value = op["text"][: e.maxlength or None]
            elif kind == "upload":
                reason = self.mgr.refuse_click(_descriptor(e)) if self.mgr.refuse_click else None
                if reason:
                    step.update(ok=False, error="needs_confirmation", detail=reason)
                else:
                    e.value = Path(op["path"]).name
            elif kind == "toggle":
                if e.role == "radio":
                    self._click(e)
                else:
                    e.checked = bool(op.get("state", not e.checked))
            elif kind == "select":
                if op.get("value") in e.options:
                    e.value = op["value"]
                else:
                    step.update(ok=False, error="no_such_option", detail="")
            step_ok = step["ok"]
            step = {k: v for k, v in step.items() if v is not None}
            results.append(step)
            ok = ok and step_ok
            if not step_ok and stop_on_error:
                break
        payload = {"ops": results, "ok": ok, "steps": len(results)}
        if observe_after and not dry_run:
            payload["observation"] = self.observe()
        return payload

    def close(self):
        pass


def _descriptor(e: El) -> dict:
    return {"ref": e.ref or "", "role": e.role, "tag": e.tag_of(), "type": e.type_of(),
            "name": e.name, "value": e.value, "form": e.form, "dialog": e.dialog, "consent": e.consent}


class _FakeCdp:
    def __init__(self, mgr):
        self.mgr, self.openers = mgr, {}

    def call(self, method, **kw):
        if method == "Target.getTargets":
            p = self.mgr.site[self.mgr.cur]
            return {"targetInfos": [{"targetId": f"T-{self.mgr.name}", "type": "page", "url": p.url,
                                     "title": p.title}]}
        if method == "Target.closeTarget":
            return {}
        return {}


class FakeManager:
    def __init__(self, site: dict[str, FakePage], start: str, *, refuse_click=None):
        self.site, self.cur, self.name = site, start, "job"
        self.refuse_click = refuse_click
        self.sent: list[str] = []
        self.log: list[tuple[str, dict]] = []           # (op-kind, {ref,...}) for each act op, for assertions
        self.settings = Settings(cdp_url="fake://")     # provides allow_js=True for Browser.assert_
        self.cdp = _FakeCdp(self)
        self._sessions: dict[str, FakeSession] = {}

    @property
    def page(self) -> FakePage:
        return self.site[self.cur]

    def connect(self, open_timeout):  # noqa: D401
        pass

    def session(self, name="default") -> FakeSession:
        s = self._sessions.get(name)
        if s is None:
            s = FakeSession(self, name)
            self._sessions[name] = s
        return s

    def doctor(self):
        return {"mode": "attach", "connected": True, "js_eval": True, "uploads": True}

    def close(self, name=None):
        self._sessions.pop(name, None)
        return [name] if name else []

    def forget(self, name):
        self._sessions.pop(name, None)

    def detach(self):
        self._sessions.clear()

    def probe_value(self, js: str, page: FakePage):
        empty = []
        for e in page.els:
            if not e.required:
                continue
            if e.role == "radio":
                if not any(x.checked for x in page.els if x.group == e.group) and [e.group, "radio"] not in empty:
                    empty.append([e.group, "radio"])
            elif not e.value:
                empty.append([e.name[:28], "file" if e.role == "file" else "text"])
        if js == probes.REQUIRED_EMPTY:
            return {"n": len(empty), "more": False, "items": empty}
        if js == probes.MAXLENGTHS:
            ml = [[e.name, e.maxlength] for e in page.els if e.maxlength]
            return {"n": len(ml), "more": False, "items": ml, "min": min((m for _, m in ml), default=None)}
        if js == probes.IFRAME_SRCS:
            return {"n": len(page.iframes), "more": False, "items": page.iframes, "long": 0}
        if js == probes.CAPTCHA_PRESENT:
            return bool(page.captcha)
        if js in probes.EVAL_PROBES.values():
            return {"n": 0, "more": False, "items": []}
        return None


class FakeBook:
    """TabBook stand-in: one tab, nothing to hand off."""
    def __init__(self, browser=None):
        pass

    def handles(self):
        return {"T-job"}

    def current_handle(self, session):
        return "T-job"

    current = current_handle

    def switch(self, session, h):
        pass

    def hand_off(self, session, baseline, known):
        return None

    def close_junk(self, session, baseline, keep):
        return []

    def release(self, session):
        pass

    def close(self):
        pass


def fake_browser(site: dict[str, FakePage], start: str, key: str = "") -> tuple[Browser, FakeManager]:
    """A Browser backed by FakeBrowser (real guard injected into the fake press path), and its manager
    (`.page`, `.sent`, `.cur`, `.site`, `.log` for assertions). Returns (browser, manager)."""
    from assistant import guard
    cfg = config.load()
    mgr = FakeManager(site, start)
    mgr.refuse_click = lambda el: guard.never_click_element(el, guard.FORM)
    return Browser(cfg, key, manager=mgr), mgr
