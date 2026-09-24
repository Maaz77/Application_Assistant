"""FakeMCP: an in-memory stand-in for jev-ultrafast-browser at the text level (tool name + args → reply text).

It subclasses Jev and overrides only `call`, so the real guard, parsers, probes and loop run unchanged.
Output formats copy tests/golden/. `sent` records any transmit that got through (must stay empty).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from assistant import config, probes
from assistant.guard import is_entry, is_transmit
from assistant.fill import NOT_THE_FORM
from assistant.jev import Jev


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
    refuse_typing: bool = False      # a masked/rewriting input: direct typing does not stick, a goal must set it
    readonly: bool = False           # like the observer: a read-only field is not `editable`
    submits: bool = False            # a button that submits a form with fields (the ENTRY_SUBMITS probe lists it)
    occluded: bool = False           # covered by a pop-up
    ref: str | None = None           # a fixed ref (a redrawn element gets a new one); default: its position
    label: str = ""                  # a radio's own option text when its name is the question (LinkedIn)


@dataclass
class FakePage:
    url: str
    title: str
    text: str
    els: list[El]
    captcha: bool = False
    iframes: list[str] = field(default_factory=list)
    form_here: bool | None = None    # the navigate agent's verdict; None: "yes" when the page shows any field
    modal: str | None = None         # an open modal dialog's name (the view reports it)


FIELD_ROLES = {"textbox", "searchbox", "combobox", "radio", "checkbox", "file", "spinbutton", "switch"}
DECLINE = re.compile(r"^\s*(reject all|i do not accept|decline)\b", re.I)


class FakeMCP(Jev):
    def __init__(self, site: dict[str, FakePage], start: str, *, goal_refuses: set[str] = frozenset(),
                 goal_clicks_submit: bool = False, reset_on_open: bool = False):
        super().__init__(config.load(), "")
        self.site, self.cur = site, start
        self.goal_refuses = set(goal_refuses)       # questions the page goal fails to set
        self.goal_clicks_submit = goal_clicks_submit
        self.reset_on_open = reset_on_open              # a real reload/reopen clears typed values
        self.sent: list[str] = []
        self.log: list[tuple[str, dict]] = []
        self.obs = 0

    # ------------------------------------------------------------------ rendering
    @property
    def page(self) -> FakePage:
        return self.site[self.cur]

    def refs(self) -> list[tuple[str, El]]:
        return [(e.ref or f"e{i + 1}", e) for i, e in enumerate(self.page.els)]

    def by_ref(self, ref: str) -> El | None:
        return dict(self.refs()).get(ref)

    def table_json(self) -> dict:
        els = []
        for ref, e in self.refs():
            d = {"ref": ref, "role": e.role, "name": e.name, "value": e.value if e.role != "combobox" else "",
                 "editable": e.role in ("textbox", "searchbox", "spinbutton") and not e.readonly, "occluded": e.occluded, "checked": e.checked, "current": None,
                 "options": [], "context": e.context, "label": e.label}
            if e.role == "combobox":
                d["current"] = e.value or "Select"
                d["options"] = [{"ref": f"{ref}:{j + 1}", "label": o, "value": o, "selected": o == e.value}
                                for j, o in enumerate(e.options)]
            els.append(d)
        return {"url": self.page.url, "title": self.page.title, "elements": els}

    def view(self) -> str:
        self.obs += 1
        lines = [f"[obs#{self.obs}] {self.page.url}  \"{self.page.title}\""]
        if self.page.modal:
            lines.append(f"  ! dialog open: {self.page.modal} [modal]")
        lines += [f"{ref:<4} {e.role[:3]}  {e.name} ▸ \"{e.value}\"" for ref, e in self.refs()]
        return "\n".join(lines) + "\ntext:\n" + self.page.text + " " + " ".join(
            e.value for e in self.page.els if e.role == "file" and e.value)

    # ------------------------------------------------------------------ probes
    def probe_value(self, js: str):
        empty = []
        for e in self.page.els:
            if not e.required:
                continue
            if e.role == "radio":
                if not any(x.checked for x in self.page.els if x.group == e.group):
                    if [e.group, "radio"] not in empty:
                        empty.append([e.group, "radio"])
            elif not e.value:
                empty.append([e.name[:28], "file" if e.role == "file" else "text"])
        if js == probes.REQUIRED_EMPTY:
            return {"n": len(empty), "more": False, "items": empty}
        if js == probes.MAXLENGTHS:
            ml = [[e.name, e.maxlength] for e in self.page.els if e.maxlength]
            return {"n": len(ml), "more": False, "items": ml, "min": min((m for _, m in ml), default=None)}
        if js == probes.ENTRY_SUBMITS:
            subs = [e.name for e in self.page.els if e.submits]
            return {"n": len(subs), "more": False, "items": subs}
        if js == probes.IFRAME_SRCS:
            return {"n": len(self.page.iframes), "more": False, "items": self.page.iframes, "long": 0}
        return None

    # ------------------------------------------------------------------ tools
    def call(self, name: str, *, _timeout: float | None = None, **args) -> str:
        self.log.append((name, args))
        fn = getattr(self, "_" + name)
        return fn(**{k: v for k, v in args.items() if k != "session"})

    def _browser_doctor(self):
        return json.dumps({"connected": True, "text_model": True, "js_eval": True, "uploads": True})

    def _browser_open(self, url, hint=""):
        self.cur = next(k for k, p in self.site.items() if p.url == url)
        if self.reset_on_open:
            for page in self.site.values():
                for e in page.els:
                    e.value = "" if e.role != "button" else e.value
                    e.checked = False if e.checked is not None else None
        return f"opened {url}\n\n" + self.view()

    def _browser_observe(self, include_json=False, include_text=True, mode="auto"):
        out = self.view()
        return out + ("\n\njson: " + json.dumps(self.table_json()) if include_json else "")

    def _browser_assert(self, checks):
        ok = all((c["type"] == "js" and self.page.captcha) for c in checks)
        return f"{'PASS' if ok else 'FAIL'}  ({self.page.url})"

    def _browser_tabs(self, action="list", index=-1, target_id="", url="about:blank"):
        return "  [0] * #AAAAAAAA  " + self.page.url if action == "list" else f"{action} ok"

    def _browser_close(self, shutdown_browser=False):
        return "closed default"

    def _click(self, e: El, confirm: bool = False) -> str | None:
        if is_transmit(e.name) and not confirm:
            return "needs_confirmation"                         # the server rule (B1)
        if e.submits:
            self.sent.append(e.name)                            # a form went out: must never happen
        if e.role == "radio":
            for x in self.page.els:
                if x.group == e.group:
                    x.checked = x is e
        if e.goto:
            self.cur = e.goto
        return None

    def _browser_act(self, ops, dry_run=False, observe_after=True, stop_on_error=True):
        lines, ok = [], 0
        for op in ops:
            kind, ref = op["op"], op.get("ref", "")
            e = self.by_ref(ref) if ref else None
            err = None
            if kind == "eval":
                ok += 1
                lines.append(f"  + eval → {json.dumps(self.probe_value(op['js']))}  0ms")
                continue
            if kind in {"click", "type", "upload", "select", "toggle"} and e is None:
                err = "unknown_ref"
            elif kind == "click":
                err = self._click(e, bool(op.get("confirm")))
            elif kind == "type":
                if not e.refuse_typing:
                    e.value = op["text"][: e.maxlength or None]
            elif kind == "upload":
                e.value = Path(op["path"]).name
            elif kind == "toggle":
                if e.role == "radio":
                    self._click(e)
                else:
                    e.checked = bool(op.get("state", not e.checked))
            elif kind == "select":
                if op["value"] in e.options:
                    e.value = op["value"]
                else:
                    err = "no_such_option"
            elif kind == "keys" and "enter" in str(op).lower():
                self.sent.append("enter")
            if err:
                lines.append(f"  x {kind} {ref}  {err}: ")
                if stop_on_error:
                    break
            else:
                ok += 1
                lines.append(f"  + {kind} {ref}  0ms")
        return f"{ok}/{len(ops)} ops ok\n" + "\n".join(lines)

    def _browser_goal(self, goal, max_steps=20, verify=None, verbose=False):
        trace, status, step = [], "done", 0
        table_names = {e.name: (ref, e) for ref, e in self.refs()}
        if goal.startswith("Set each field"):
            for q, a in re.findall(r'^- "(.+?)" → "(.*)"$', goal, re.M):
                step += 1
                if q in self.goal_refuses:
                    trace.append(f"  {step}. TYPE_TEXT  {q} → ok (1ms model / 1ms browser)")
                    continue
                radios = [(r, e) for r, e in self.refs() if e.group == q and e.name == a]
                if radios:
                    self._click(radios[0][1])
                    trace.append(f"  {step}. TOGGLE {radios[0][0]} {a} → ok (1ms model / 1ms browser)")
                elif q in table_names:
                    ref, e = table_names[q]
                    e.value = a
                    trace.append(f"  {step}. TYPE_TEXT {ref} {q} → ok (1ms model / 1ms browser)")
            if self.goal_clicks_submit:
                sub = next((r, e) for r, e in self.refs() if is_transmit(e.name))
                self.sent.append(sub[1].name)
                trace.append(f"  {step + 1}. CLICK {sub[0]} {sub[1].name} → ok (1ms model / 1ms browser)")
        elif goal.startswith("Bring this job application"):     # the navigate goal (prompts/navigate_goal.md)
            status, step = self._navigate(trace, goal)
        else:                                                   # the next-step goal: the first button
            for ref, e in self.refs():
                if e.role == "button":
                    step += 1
                    err = self._click(e)
                    trace.append(f"  {step}. CLICK {ref} {e.name} → {err or 'ok'} (1ms model / 1ms browser)")
                    if err:
                        status = f"failed:{err}"
                    break
        out = [f"goal: {goal[:60]}", f"status: {status}", f"steps: {step}", "trace:", *trace]
        if verify:
            passed = all(self._check(c) for c in verify)
            out.append(f"verified: {'PASS' if passed else 'FAIL'}")
        return "\n".join(out) + "\n\n" + self.view()

    def _navigate(self, trace: list[str], goal: str) -> tuple[str, int]:
        """A stand-in for the agent: decline a cookie banner, continue past a pop-up, then say "done" when the
        form is here, or click the page's Apply (the server refuses it: needs_confirmation). Told that the page's
        fields are not the form (fill.NOT_THE_FORM), it looks for the Apply instead."""
        step = 0
        for pattern in (DECLINE, re.compile(r"^\s*continue applying\b", re.I)):
            hit = next(((r, e) for r, e in self.refs() if e.role in {"button", "link"} and pattern.search(e.name)),
                       None)
            if hit:
                step += 1
                err = self._click(hit[1])
                trace.append(f"  {step}. CLICK {hit[0]} {hit[1].name} → {err or 'ok'} (1ms model / 1ms browser)")
        here = self.page.form_here
        if here is None:
            here = any(e.role in FIELD_ROLES for e in self.page.els)
        if NOT_THE_FORM in goal:
            here = False
        if here:
            trace.append(f"  {step + 1}. DONE (conf 0.95)")
            return "done", step
        entry = next(((r, e) for r, e in self.refs() if e.role in {"button", "link"} and is_entry(e.name)), None)
        if entry:
            step += 1
            err = self._click(entry[1])
            trace.append(f"  {step}. CLICK {entry[0]} {entry[1].name} → {err or 'ok'} (1ms model / 1ms browser)")
            return (f"failed:{err}" if err else "done"), step
        trace.append(f"  {step + 1}. BLOCKED (conf 0.6)")
        return "blocked", step

    def _check(self, c: dict) -> bool:
        e = self.by_ref(c.get("ref", ""))
        if e is None:
            return False
        if c["type"] == "checked":
            return bool(e.checked) == c.get("state", True)
        return e.value == c.get("value")


class FakeBook:
    """TabBook stand-in for FakeMCP tests: one tab, no pop-ups, nothing to hand off."""

    def handles(self):
        return {"AAAAAAAA"}

    def current_handle(self, session):
        return "AAAAAAAA"

    def switch(self, session, h):
        pass

    def hand_off(self, session, baseline, known):
        return None
