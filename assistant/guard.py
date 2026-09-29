"""The never-submit guard (00_common §4.1).

Two generations live here during the P2 migration:
  * v1 — `never_click(name, role)` and `check(op, table)` — the label/role rule the vendored package
    applied (only "Submit"/"Send", and "Apply" once filling started). Still used by `jev.py` and its
    tests until they are removed.
  * v2 — `never_click_element(el, page)` and `looks_final(elements)` — the absolute element-level rule
    P2 enforces in the owned driver's press path: the full refused-label set, structural submits, the
    advance allowlist, the final-page judgment, and the one cookie-consent exemption.

`Browser` injects `never_click_element` into the driver, so a click, a toggle, a file-chooser upload
and the focus click of a `type` are all refused before the mouse is dispatched (defence in depth).
The rule is absolute: no code path may click, press or trigger a control that sends an application.
"""
from __future__ import annotations

import re

from assistant import probes


class GuardError(RuntimeError):
    pass


# --- v1 (label/role only; kept until jev.py is removed) ---------------------
SUBMIT_RE = re.compile(r"\bsubmit", re.I)
SEND_RE = re.compile(r"\bsend\b", re.I)
APPLY_RE = re.compile(r"\bapply\b", re.I)
CLICK_ROLES = {"button", "link", "menuitem", "tab"}

# --- v2 (element-level, absolute) -------------------------------------------
# A label that names a submit is refused on every page (whole words, case-insensitive).
REFUSE_LABEL_RE = re.compile(r"\b(submit|send|confirm|done|finish|complete)\b", re.I)
# The cookie-consent exemption is denied only for these three (a consent control may say "confirm").
SUBMIT_SEND_APPLY_RE = re.compile(r"\b(submit|send|apply)\b", re.I)
# A structural-submit control is allowed only when its label is one of these AND the page is not final.
ADVANCE_RE = re.compile(r"^(next|continue to next step|continue|review your application|review|"
                        r"save and continue)\b", re.I)
# Cookie-consent containers (kept in sync with observer.js CONSENT_SELECTOR).
CONSENT_CONTAINERS = (
    "#onetrust-banner-sdk", "#onetrust-consent-sdk", "#CybotCookiebotDialog", "#didomi-host",
    "#usercentrics-root", "#truste-consent-track", ".qc-cmp2-container",
)


class _FormStage:
    started = False      # set when the program starts filling the job's form; reset per job
    final = False        # set when the page is judged final (looks_final); reset per page


FORM = _FormStage()


def never_click(name: str, role: str = "") -> str | None:
    """v1: why a click on this control is refused, or None (label/role only)."""
    if role and role.lower() not in CLICK_ROLES:
        return None
    if SUBMIT_RE.search(name or ""):
        return "the program never clicks Submit"
    if SEND_RE.search(name or ""):
        return "the program never clicks Send"
    if FORM.started and APPLY_RE.search(name or ""):
        return "the program never clicks Apply once the form is being filled"
    return None


def _field(el, name: str):
    """Read a field from either a driver descriptor (dict) or a pydantic Element (object)."""
    if isinstance(el, dict):
        return el.get(name)
    return getattr(el, name, None)


def never_click_element(el, page: _FormStage | None = None) -> str | None:
    """v2: why a click/press on this element is refused, or None. `el` is a driver descriptor or an
    Element; `page` carries `.started` and `.final` (defaults to the module `FORM`)."""
    page = page or FORM
    name = _field(el, "name") or ""
    value = _field(el, "value") or ""
    role = (_field(el, "role") or "").lower()
    tag = (_field(el, "tag") or "").upper()
    typ = (_field(el, "type") or "").lower()
    form = _field(el, "form") or ""
    dialog = _field(el, "dialog") or ""
    consent = _field(el, "consent") or ""
    label = f"{name} {value}".strip()
    # The label, apply and final rules test click targets only. A textbox "Confirm email address" or a
    # checkbox "I confirm the details are correct" reaches `_press` too (type/toggle focus-click) and must
    # not be refused for its label. The structural rule below applies to every element, so an
    # <input type=submit> — role "button" — is still refused whatever else it is.
    is_click_target = (not role) or role in CLICK_ROLES

    # The one exemption: a cookie-consent control, outside any application form and the application
    # dialog, whose label is not submit/send/apply — e.g. "Accept all", "Reject", "Confirm my choices".
    if is_click_target and consent and not form and not dialog and not SUBMIT_SEND_APPLY_RE.search(label):
        return None

    if is_click_target and REFUSE_LABEL_RE.search(label):
        return f"the program never clicks a control labelled like a submit ({label!r})"
    if is_click_target and page.started and APPLY_RE.search(label):
        return "the program never clicks Apply once the form is being filled"

    structural = typ == "submit" or (tag == "INPUT" and typ == "image") or (tag == "BUTTON" and not typ and bool(form))
    if structural:
        if ADVANCE_RE.search(label) and not page.final:
            return None      # an allowlisted advance control on a page that is not final
        return f"the program never clicks a control that would submit a form ({label!r})"

    if is_click_target and page.final:
        return "the page is final; the program parks rather than click further"
    return None


def _submit_like(el) -> bool:
    """True if this element is a submit control by label or structure (used to judge finality)."""
    name = _field(el, "name") or ""
    value = _field(el, "value") or ""
    tag = (_field(el, "tag") or "").upper()
    typ = (_field(el, "type") or "").lower()
    form = _field(el, "form") or ""
    label = f"{name} {value}".strip()
    if REFUSE_LABEL_RE.search(label):
        return True
    return typ == "submit" or (tag == "INPUT" and typ == "image") or (tag == "BUTTON" and not typ and bool(form))


def looks_final(elements) -> bool:
    """The deterministic final-page judgment: a submit-like control is present and no allowlisted advance
    control (Next / Continue / Review / Save and continue) is. Read from the table, so it never depends
    on a refused click (which would be circular now that the driver refuses submits itself)."""
    has_submit = False
    has_advance = False
    for e in elements:
        label = f"{_field(e, 'name') or ''} {_field(e, 'value') or ''}".strip()
        if ADVANCE_RE.search(label) and not REFUSE_LABEL_RE.search(label):
            has_advance = True
        if _submit_like(e):
            has_submit = True
    return has_submit and not has_advance


def label_of(ref: str, table) -> str | None:
    """The label the guard would test for a ref (option refs 'e2:1' resolve to the option label)."""
    base, _, opt = str(ref).partition(":")
    for e in table.elements:
        if e.ref == base:
            if not opt:
                return e.name
            idx = int(opt) - 1
            return e.options[idx].label if 0 <= idx < len(e.options) else None
    return None


def element_of(ref: str, table):
    """The Element a base ref points at (option refs resolve to their base element), or None."""
    base = str(ref).partition(":")[0]
    for e in table.elements:
        if e.ref == base:
            return e
    return None


def check(op: dict, table) -> None:
    """Raise GuardError if `op` leaves our intended paths. `table` is the latest table."""
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
        # The package/driver opens the file chooser of a non-input upload target by clicking it (aa6):
        # the same rule applies to an upload target as to a click.
        if kind == "upload" and never_click(label):
            raise GuardError(f"upload on {label!r}: {never_click(label)}")
