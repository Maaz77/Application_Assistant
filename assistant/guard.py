"""Checks on every browser_act op our code sends (§4.1), before it reaches the server.

The never-submit rule (never_click, user decision 2026-09-24: only "Submit" and "Send", and "Apply" once the form
is being filled) is applied by the package to every click, the agent's and ours (jev.guard_clicks), and here to an upload,
which clicks a non-input target to open its file chooser. The other checks keep our own ops on their intended
paths: scripts are only the read-only probes (model text never becomes JavaScript), tabs go through tabs.py,
navigation through browser_open, and nothing asks the server to override a refusal.
"""
from __future__ import annotations

import re

from assistant import probes


class GuardError(RuntimeError):
    pass


# User decision 2026-09-24: the one guard against sending an application is this rule, with no model call. A
# button or link whose label says "Submit" or "Send" is never clicked; one that says "Apply" is not clicked once the
# application form is being filled (before that, "Apply" / "Easy Apply" is how the application starts).
SUBMIT_RE = re.compile(r"\bsubmit", re.I)
SEND_RE = re.compile(r"\bsend\b", re.I)
APPLY_RE = re.compile(r"\bapply\b", re.I)
CLICK_ROLES = {"button", "link", "menuitem", "tab"}


class _FormStage:
    started = False      # set when the program starts filling the job's form (fill.fill_page), reset per job


FORM = _FormStage()


def never_click(name: str, role: str = "") -> str | None:
    """Why a click on this control is refused, or None."""
    if role and role.lower() not in CLICK_ROLES:
        return None
    if SUBMIT_RE.search(name or ""):
        return "the program never clicks Submit"
    if SEND_RE.search(name or ""):
        return "the program never clicks Send"
    if FORM.started and APPLY_RE.search(name or ""):
        return "the program never clicks Apply once the form is being filled"
    return None


def label_of(ref: str, table) -> str | None:
    """The label the server will test for a ref (option refs 'e2:1' resolve to the option label)."""
    base, _, opt = str(ref).partition(":")
    for e in table.elements:
        if e.ref == base:
            if not opt:
                return e.name
            idx = int(opt) - 1
            return e.options[idx].label if 0 <= idx < len(e.options) else None
    return None


def check(op: dict, table) -> None:
    """Raise GuardError if `op` leaves our intended paths. `table` is the latest jev.Table."""
    kind = str(op.get("op") or "").strip().lower()
    if "confirm" in op:
        raise GuardError(f"'confirm' would override the server's refusal (op={kind})")
    if kind == "eval" and op.get("js") not in probes.EVAL_PROBES.values():
        raise GuardError("eval js must be a probes.py constant")
    if kind == "tab" and str(op.get("action") or "list") != "list":
        raise GuardError("tab ops go through browser_tabs (tabs.py), not browser_act")
    if kind in {"nav", "back", "forward"}:
        raise GuardError(f"'{kind}' is not a wrapper op; navigation goes through browser_open")
    if kind in {"click", "upload"}:
        label = label_of(op.get("ref", ""), table)
        if label is None:
            raise GuardError(f"{kind} target {op.get('ref')!r} is not in the latest element table")
        # The package opens the file chooser of a non-input upload target by clicking it (aa6): the same rule.
        if kind == "upload" and never_click(label):
            raise GuardError(f"upload on {label!r}: {never_click(label)}")
