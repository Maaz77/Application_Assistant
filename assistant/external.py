"""P5: drive a full-page / multi-step external ATS form (Greenhouse, Ashby, Lever, or any readable host).

No host adapters. The generic pipeline already reads these forms: `pages.judge`/`pages.classify` classify every
page with code rules (no page-kind model call), `fill.fill_page` extracts the questions, the chat model answers
from the user's files, code fills and reads back, and the never-submit guard makes submission impossible. The
LinkedIn Easy Apply loop (`fill.run_pages`) is only a *navigation shell* for a modal dialog; this module is the
same shell for a full page: hand off to the external tab, then observe → classify → fill/advance → gate → park.

Two safety facts external hosts have and LinkedIn does not:
  * an "Apply" control can be the form's own submit, so a *pre-fill* apply click is only allowed when there is
    no form yet and the control is not a form's own control (`_safe_apply`, guard-checked);
  * a page can show non-application fields that look like a form (a job-alert email box — DISCOVERY ~198), so a
    page with fields is filled only when `looks_like_application` says it is a real application; otherwise the
    host is reported `unsupported_ats`, and nothing on it is clicked.
"""
from __future__ import annotations

import re
import time

from assistant import decide, fill, guard, google_signin, navigate, pages
from assistant.blockers import NeedsAttention, Parked, ParkedAtQuestion, StopRun
from assistant.browser import DriverError, DriverTimeout
from assistant.decide import DecisionError

APPLY_RE = re.compile(r"\bapply\b", re.I)
_WITH_RE = re.compile(r"\bwith\b", re.I)        # excludes "Continue with Google/LinkedIn/Indeed" from advance

_NAME_FIELD_RE = re.compile(r"\bname\b", re.I)
_EMAIL_FIELD_RE = re.compile(r"e-?mail", re.I)
# A "name" field that is not the candidate's identity (so a lone "Company name" box is not read as an application).
_NOT_IDENTITY_RE = re.compile(r"file\s*name|user\s*name|username|company|employer|organi[sz]ation|"
                              r"school|university|college|reference", re.I)


# ------------------------------------------------------------------ fill-vs-stop predicate (safety)

def looks_like_application(p: pages.Page) -> bool:
    """Whether this page is a real job-application form the program may fill, as opposed to a page that merely
    has input fields (a newsletter/job-alert box, a search, a survey — the Mastercard bug, DISCOVERY ~198).
    Fill only when this is True; otherwise the host is reported unsupported_ats and nothing is clicked.

    Signal (deterministic, no model call): a résumé/CV upload control is present, OR both an identity name
    field and an email field are among the real application fields. A lone email box (job alert) fails; a
    Greenhouse/Lever/Ashby form (résumé upload, or name + email) passes."""
    if pages.judge(p).resume_ref is not None:
        return True
    fields = pages.real_fields(p)

    def label(e) -> str:
        return f"{e.name or ''} {e.label or ''}"

    has_name = any(_NAME_FIELD_RE.search(label(e)) and not _NOT_IDENTITY_RE.search(label(e)) for e in fields)
    has_email = any(_EMAIL_FIELD_RE.search(label(e)) for e in fields)
    return has_name and has_email


# ------------------------------------------------------------------ navigation helpers (page-level)

def _page_signature(p: pages.Page) -> tuple:
    """A whole-page change detector: the URL plus the form fields and buttons. A step advance changes it
    (an Ashby multi-step keeps the same URL, so the fields must be part of the signature)."""
    return (pages.norm_label(p.url),
            tuple(sorted((e.role, pages.norm_label(e.name)) for e in p.elements
                         if e.role in pages.FORM_ROLES | {"button"})))


def _advance_button(p: pages.Page):
    """A page-level allowlisted advance control (Next / Continue / Review / Save and continue), excluding a
    refused label and "Continue with <provider>" (which is a sign-in, not a step advance)."""
    for e in pages.buttons(p):
        label = f"{e.name or ''} {e.value or ''}"
        if guard.ADVANCE_RE.search(label) and not guard.REFUSE_LABEL_RE.search(label) and not _WITH_RE.search(label):
            return e
    return None


def _safe_apply(p: pages.Page):
    """A pre-fill "Apply" control that is navigation, not a submit: an apply-labelled button/link that is not a
    form's own control and that the never-submit guard allows (so a structural submit or a form-member button is
    excluded). Used only on a page that has no application form yet."""
    for e in p.elements:
        if (e.role in {"button", "link"} and APPLY_RE.search(e.name or "")
                and not (getattr(e, "form", "") or "") and guard.never_click_element(e) is None):
            return e
    return None


def _click_forward(ctx, p: pages.Page, *, allow_apply: bool) -> bool:
    """Click a forward control (an advance button, or — only when `allow_apply` — a safe pre-fill apply) and
    wait for the page signature to change. True if the page moved on; False if there was nothing to click or
    the click changed nothing (the caller then treats the page as final and runs the gate)."""
    ctrl = _advance_button(p) or (_safe_apply(p) if allow_apply else None)
    if ctrl is None:
        return False
    before = _page_signature(p)
    known = ctx.book.handles()
    ctx.browser.act([{"op": "click", "ref": ctrl.ref}], ctx.session, p.table, stop_on_error=False)
    deadline = time.monotonic() + navigate.ADVANCE_WAIT
    while time.monotonic() < deadline:
        if ctx.book.handles() - known - ctx.baseline:        # the forward click opened the form in a new tab
            if ctx.book.hand_off(ctx.session, ctx.baseline, known):
                return True
        q = ctx.read()
        if pages.is_alarm(q):
            raise StopRun(f"ALARM: confirmation text after a forward click on {q.url}")
        if _page_signature(q) != before:
            return True
        ctx.sleep(0.5)
    return False


_PROCEED_RE = re.compile(r"^\s*(continue|proceed|go to|apply on|visit)\b", re.I)


def _leaving_control(p: pages.Page):
    """A "You are leaving … / Continue to the company website" control that LinkedIn shows between the posting
    and the external tab. Matches Continue/Proceed only (never Cancel, never a submit, never "Continue with
    <provider>"), so clicking it proceeds rather than cancels or submits."""
    for e in p.elements:
        label = e.name or ""
        if (e.role in {"button", "link"} and _PROCEED_RE.search(label)
                and not _WITH_RE.search(label) and not guard.REFUSE_LABEL_RE.search(label)):
            return e
    return None


def _hand_off(ctx) -> pages.Page:
    """From the LinkedIn posting, click its external Apply and adopt the ATS tab. The click is a pre-fill apply
    (guard.FORM.started is False, so it is allowed). The ATS tab may open at once, a couple of seconds later, or
    only after a "You are leaving LinkedIn" interstitial's Continue — so poll: hand off as soon as a new tab
    appears, click a leaving/Continue interstitial when one is up, or return when the form opens in this tab
    (a same-tab navigation off LinkedIn). Returns the landed external page."""
    p = ctx.read()
    ctrl = navigate.external_apply(p)
    if ctrl is None:
        raise NeedsAttention("navigation", "expected an external Apply control on the posting but found none")
    known = ctx.book.handles()
    before = ctx.book.current_handle(ctx.session)
    ctx.browser.act([{"op": "click", "ref": ctrl.ref}], ctx.session, p.table, stop_on_error=False)
    deadline = time.monotonic() + fill.ENTRY_TAB_WAIT * 2
    clicked: set[str] = set()
    while time.monotonic() < deadline:
        if ctx.book.handles() - known - ctx.baseline:                   # a new tab opened (by this tab)
            if ctx.book.hand_off(ctx.session, ctx.baseline, known):
                return ctx.read()
        p = ctx.read()
        if ctx.book.current_handle(ctx.session) != before:              # a tab was already adopted
            return p
        if pages.form_is_here(p) and "linkedin" not in p.host:          # same-tab navigation to the form
            return p
        il = _leaving_control(p)
        if il is not None and il.ref not in clicked:
            clicked.add(il.ref)
            ctx.browser.act([{"op": "click", "ref": il.ref}], ctx.session, p.table, stop_on_error=False)
        ctx.sleep(0.5)
    raise NeedsAttention("navigation", "the apply click did not open the external application")


FORM_WAIT = 12          # s: an external form can render after a "Fetching application form" placeholder (Ashby)


def _wait_for_form(ctx, secs: int = FORM_WAIT) -> pages.Page:
    """Re-read until an application form's fields appear (Ashby renders its form a beat after the page, behind
    'Fetching application form'), or until a blocker / sign-in / confirmation shows. Returns the latest page so
    the caller can re-classify it. Bounded; no model call."""
    deadline = time.monotonic() + secs
    p = ctx.read()
    while time.monotonic() < deadline:
        if pages.judge(p).app_fields or pages.is_alarm(p):
            return p
        if pages.classify(p).kind in ("blocker", "google", "google_wall"):
            return p
        ctx.sleep(0.8)
        p = ctx.read()
    return p


# ------------------------------------------------------------------ the external loop

def run_external(ctx) -> Parked:
    """Drive an external ATS application to the parked final step. Mirrors the exception handling of
    `fill.run_pages` exactly, because `process()` catches this through `except GoExternal:` (a sibling of its
    `except RestartFromEntry:`), so any exception that escapes here is NOT caught by the Easy-Apply handlers and
    would crash the whole run. Never clicks a transmit control (the guard refuses it)."""
    guard.FORM.started = False
    guard.FORM.final = False
    ctx._jev_start = decide._current.calls if decide._current else 0
    ctx.jev_budget = ctx.browser.cfg.jev.max_requests_per_job
    filled = False
    rounds = 0
    signed_in = False
    try:
        _hand_off(ctx)
        while True:
            rounds += 1
            if rounds > ctx.max_pages * 4 + 12:
                raise NeedsAttention("broken_form", "the external page loop is not making progress")
            try:
                # settle, not a bare read: an external SPA (Toast's careers page) loads in stages — blank, then
                # a transient blocker-looking state, then the real page — and classifying a transient thrashes
                # the loop. settle re-reads until the page is no longer `unsettled` (an Apply control or real
                # field is present), so classify runs on the stable page.
                p = pages.settle(ctx.read, sleep=ctx.sleep)
                v = pages.classify(p)
                if v.kind == "alarm":
                    raise StopRun(f"ALARM: confirmation text on {p.url}")
                if v.kind in ("google", "google_wall"):
                    if signed_in:
                        raise NeedsAttention("credentials", "Google sign-in was needed more than once")
                    google_signin.sign_in(ctx.browser, ctx.session, ctx.book, ctx.google_email, ctx.baseline)
                    signed_in, filled = True, False
                    continue
                if v.kind == "blocker":
                    cls = v.detail.split(":", 1)[0].strip()
                    if cls == "load_failure":
                        ctx.attempts.fail("load_failure", v.detail)     # raises NeedsAttention when exhausted
                        fill._reload(ctx)
                    else:
                        fill._attempt2(ctx, p, cls, v.detail)           # captcha wait-reload / signup guest link
                    filled = False
                    continue
                if v.kind == "iframe":
                    ctx.browser.open(v.detail, ctx.session)             # hosted-form hop (T2 frame-attach deferred)
                    filled = False
                    continue
                if v.kind in ("form", "final"):
                    if not looks_like_application(p):
                        raise NeedsAttention("unsupported_ats",
                                             f"fields on {p.host} but not a job-application form")
                    if not filled:
                        guard.FORM.started = True
                        fill._check_jev_budget(ctx)
                        fill.fill_page(ctx, p)      # raises ParkedAtQuestion / NeedsAttention / _Refill
                        filled = True
                        continue
                    if _click_forward(ctx, p, allow_apply=False):       # a further step
                        filled = False
                        ctx.pages += 1
                        if ctx.pages > ctx.max_pages:
                            raise NeedsAttention("broken_form", f"no final step after {ctx.max_pages} steps")
                        continue
                    guard.FORM.final = True
                    why = pages.gate(p, ctx.resume_pdf.name, ctx.typed)
                    if why is None:
                        break
                    raise NeedsAttention("gate", why)
                # v.kind == "navigate": no application form yet — click a safe apply/advance; if there is nothing
                # to click, wait for a late-rendering form (Ashby shows "Fetching application form" first) before
                # giving up as unsupported.
                if not _click_forward(ctx, p, allow_apply=True):
                    if pages.judge(_wait_for_form(ctx)).app_fields:
                        filled = False
                        continue
                    raise NeedsAttention("unsupported_ats", f"no application form on {p.host} ({v.detail})")
                filled = False
            except fill._Refill as rf:
                guard.FORM.started = guard.FORM.final = False
                ctx.browser.open(rf.url or ctx.last.url, ctx.session)
                filled = False
            except DriverTimeout:
                raise                                                   # outer handler → StopRun (exit 3)
            except DriverError as exc:
                ctx.attempts.fail("load_failure", str(exc)[:160])       # raises NeedsAttention when exhausted
                fill._reload(ctx)
                filled = False
    except NeedsAttention as exc:
        raise fill._where(exc, ctx)
    except DecisionError as exc:
        raise fill._where(NeedsAttention("decision", str(exc)), ctx) from exc
    except DriverTimeout as exc:
        raise StopRun(f"a browser call hung and was abandoned: {exc}")  # exit 3 (CLAUDE.md: hung browser call)
    except DriverError as exc:
        # A driver/CDP error outside the loop body (e.g. observing a freshly-adopted tab in _hand_off) must be a
        # job outcome, not an uncaught traceback that aborts the whole run.
        raise fill._where(NeedsAttention("load_failure", str(exc)[:160]), ctx) from exc
    except ParkedAtQuestion as exc:
        return fill.park_at_question(ctx, exc)
    return fill._park(ctx)
