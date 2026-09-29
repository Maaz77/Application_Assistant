"""Deterministic LinkedIn Easy Apply navigation (P2 T5) — replaces browser_goal.

kev cannot classify a real LinkedIn posting (DISCOVERY: it answers kind="other" at 0.12), so the entry
and the Easy Apply advance are decided from page text, button labels and the dialog's field set, with
**no page-kind model call**. The only model call is the last-resort "which control starts the
application?", one Jev choice, made only when the deterministic signals find nothing.

The never-submit rule still guards every click (guard.check in Browser.act, and the driver's press
path). Navigation only ever clicks Easy Apply, a cookie reject, or an allowlisted advance button.
"""
from __future__ import annotations

import re
import time

from assistant import decide, guard, pages
from assistant.blockers import NeedsAttention

# Entry signals (top-card controls + page text). Matched on the observer's name (aria-label wins).
EASY_APPLY_RE = re.compile(r"^\s*easy apply\b", re.I)
APPLY_RE = re.compile(r"^\s*apply\b", re.I)
CLOSED_RE = re.compile(r"no longer accepting applications|this job is (closed|no longer)", re.I)
APPLIED_RE = re.compile(r"\bapplied\b|application submitted|see application", re.I)
COOKIE_REJECT_RE = re.compile(r"^\s*(reject|decline|only necessary|refuse)\b", re.I)

DIALOG_WAIT = 8            # s: LinkedIn shows the Easy Apply dialog a moment after the click
ADVANCE_WAIT = 8          # s: wait for the dialog to change after an advance click
ENTRY_CONFIDENCE = 0.6    # the Jev entry fallback must be at least this sure


def _read(ctx) -> pages.Page:
    """Read the page WITHOUT pages.settle — settle calls judge()/kev, which navigation must avoid."""
    return pages.read_page(ctx.browser, ctx.session)


# ------------------------------------------------------------------ element helpers (deterministic)

def easy_apply_button(p: pages.Page):
    """A top-card button whose label starts 'Easy Apply' (601: 'Easy Apply to …', 609/610: '… to this job')."""
    return next((e for e in p.elements
                 if e.role == "button" and not e.dialog and EASY_APPLY_RE.search(e.name or "")), None)


def external_apply(p: pages.Page):
    """An apply control that is not Easy Apply — a bare 'Apply' button or an off-LinkedIn 'Apply' link."""
    return next((e for e in p.elements
                 if e.role in {"button", "link"} and not e.dialog
                 and APPLY_RE.search(e.name or "") and not EASY_APPLY_RE.search(e.name or "")), None)


def cookie_reject(p: pages.Page):
    """A cookie-consent reject/decline control (inside a known consent container, per the observer)."""
    return next((e for e in p.elements
                 if e.role in {"button", "link"} and e.consent and COOKIE_REJECT_RE.search(e.name or "")), None)


def in_dialog(p: pages.Page) -> list:
    """Elements inside the application dialog (a non-empty `dialog` field). Robust whether LinkedIn uses a
    real <dialog> (aa2 lists only its content) or a div role=dialog (page buttons stay listed too)."""
    return [e for e in p.elements if e.dialog]


def dialog_fields(p: pages.Page) -> list:
    """The application dialog's own form fields — what tells us the Easy Apply step has opened."""
    return [e for e in in_dialog(p) if e.role in pages.FORM_ROLES]


def advance_button(p: pages.Page):
    """A dialog button whose label is on the advance allowlist (Next / Continue / Review / Save and continue)."""
    for e in in_dialog(p):
        if e.role == "button" and guard.ADVANCE_RE.search(e.name or "") and not guard.REFUSE_LABEL_RE.search(e.name or ""):
            return e
    return None


def dialog_signature(p: pages.Page) -> tuple:
    """A change detector for the dialog: its fields and buttons. A step advance changes this."""
    els = in_dialog(p) or p.elements
    return tuple(sorted((e.role, pages.norm_label(e.name)) for e in els
                        if e.role in pages.FORM_ROLES | {"button"}))


def dialog_is_open(p: pages.Page) -> bool:
    return bool(dialog_fields(p))


# ------------------------------------------------------------------ entry (§5.1)

def enter(ctx, p: pages.Page) -> str:
    """Decide the LinkedIn posting deterministically. Returns:
      "form"   — Easy Apply was clicked and the application dialog is open;
      "cookie" — a cookie banner was declined (the caller re-reads and calls enter again).
    Raises NeedsAttention for closed / applied / external ATS / an unrecognised page."""
    ctx.stage = "entry"
    # 1. Easy Apply: click it and wait for the dialog. A closed/applied posting has no Easy Apply button.
    b = easy_apply_button(p)
    if b is not None:
        ctx.browser.act([{"op": "click", "ref": b.ref}], ctx.session, p.table, stop_on_error=False)
        q = wait_for_dialog(ctx)
        if q is not None:
            return "form"
        raise NeedsAttention("navigation", "clicked Easy Apply but no application dialog appeared")
    # 2/3. closed or already applied (text; checked before any §4.6 alarm, which applies only after we act).
    if CLOSED_RE.search(p.text):
        raise NeedsAttention("closed", "LinkedIn says this job is no longer accepting applications")
    if APPLIED_RE.search(p.text):
        raise NeedsAttention("applied", "LinkedIn says you have already applied to this job")
    # 4. external ATS (an Apply that is not Easy Apply): never click it (D12).
    if external_apply(p) is not None:
        raise NeedsAttention("external_ats", "external ATS, not yet supported")
    # 5. a cookie banner: decline it, then look again.
    c = cookie_reject(p)
    if c is not None:
        ctx.browser.act([{"op": "click", "ref": c.ref}], ctx.session, p.table, stop_on_error=False)
        return "cookie"
    # 6. last resort: one Jev choice over the visible, guard-allowed buttons.
    return _entry_choice(ctx, p)


def _entry_choice(ctx, p: pages.Page) -> str:
    buttons = {e.ref: (e.name or e.role)[:90] for e in p.elements
               if e.role in {"button", "link"} and not guard.never_click_element(e)}
    if buttons:
        q = decide.choice("Which control starts the job application?", {**buttons, "none": "None of these."})
        a = decide.current().ask("navigate", pages.page_state(p), {"start": q})["start"]
        if a.choice and a.choice != "none" and (a.confidence or 0.0) >= ENTRY_CONFIDENCE:
            el = next((e for e in p.elements if e.ref == a.choice), None)
            if el is not None:
                ctx.browser.act([{"op": "click", "ref": el.ref}], ctx.session, p.table, stop_on_error=False)
                if wait_for_dialog(ctx) is not None:
                    return "form"
                return "cookie"      # something changed; let the caller re-read and decide again
    raise NeedsAttention("navigation", f"no way to start the application on {p.title or p.url!r}")


# An interstitial that LinkedIn puts over/before the Easy Apply form — most often the "Job search safety
# reminder" modal (live 2026-09-29, Linda AI). Its control must be clicked to reveal the form. "Continue applying"
# is preferred; a plain "Dismiss"/"Got it" is accepted (it dismisses the reminder, not the application). Guarded
# by never_click_element like every click — none of these are submit/structural, and Apply is allowed pre-fill.
INTERSTITIAL_RE = re.compile(r"^\s*(continue applying|continue|got it|i understand|dismiss|okay|ok)\b", re.I)


def _interstitial_control(p: pages.Page):
    """A control inside an open dialog that has no form fields yet — a reminder to click through, not the form."""
    if dialog_fields(p):
        return None
    for e in in_dialog(p):
        if e.role in {"button", "link"} and INTERSTITIAL_RE.search(e.name or ""):
            return e
    return None


def wait_for_dialog(ctx, seconds: int = DIALOG_WAIT) -> pages.Page | None:
    """Read (no settle) until the Easy Apply dialog's fields appear, at most `seconds`. Click through an
    interstitial dialog (e.g. the "Job search safety reminder") that sits over the form, once per control."""
    deadline = time.monotonic() + seconds
    clicked: set[str] = set()
    while True:
        p = _read(ctx)
        if dialog_is_open(p):
            return p
        control = _interstitial_control(p)
        if control is not None and control.ref not in clicked:
            clicked.add(control.ref)
            ctx.browser.act([{"op": "click", "ref": control.ref}], ctx.session, p.table, stop_on_error=False)
            continue                                   # look again at once — the form may be underneath
        if time.monotonic() >= deadline:
            return None
        ctx.sleep(0.5)


# ------------------------------------------------------------------ advance (§5.1)

def advance(ctx, p: pages.Page) -> str:
    """Advance one Easy Apply step. Returns 'moved' | 'final' | 'stuck'.

    Click the dialog's allowlisted advance button and wait for the dialog to change. No advance button
    and a refused Submit means the final step; an inline error or no change means stuck (broken_form)."""
    ctx.stage = "advance"
    button = advance_button(p)
    if button is None:
        return "final"      # no Next/Continue/Review left — the last step (a refused Submit is all that remains)
    before = dialog_signature(p)
    ctx.browser.act([{"op": "click", "ref": button.ref}], ctx.session, p.table, stop_on_error=False)
    deadline = time.monotonic() + ADVANCE_WAIT
    while True:
        q = _read(ctx)
        if pages.is_alarm(q):
            from assistant.blockers import StopRun
            raise StopRun(f"ALARM: confirmation text after advance on {q.url}")
        if dialog_signature(q) != before:
            return "moved"
        if inline_error(q) or time.monotonic() >= deadline:
            return "stuck"
        ctx.sleep(0.5)


INLINE_ERROR_RE = re.compile(r"please (enter|select|fix)|is required|required field|invalid|"
                            r"enter a valid|this field", re.I)


def inline_error(p: pages.Page) -> bool:
    """A validation error in the dialog: the observer does not list role=alert, so match the dialog text."""
    dialog_text = p.text or ""
    return bool(INLINE_ERROR_RE.search(dialog_text))
