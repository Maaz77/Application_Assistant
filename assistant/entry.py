"""The entry click (§4.2) — the only code that may send "confirm": true.

The browser agent picks the control; the server refuses it as submit-like (B1) and the click comes here. It is made
only when every check agrees: the guard's entry or guest label list (the safety floor), Jev's judgment that the
control starts the application rather than submits one, no application field holding a value, and — for a button —
the read-only ENTRY_SUBMITS probe finding that it does not submit a form with fields.
"""
from __future__ import annotations

import re
from typing import Callable

from assistant import decide, pages
from assistant.decide import THRESHOLDS as T
from assistant.guard import ENTRY, is_entry, is_guest, is_transmit
from assistant.jev import Jev
from assistant.pages import Page


class EntryRefused(RuntimeError):
    pass


STALE_RE = re.compile(r"\b(detached|page_changed|target_changed|unknown_ref|stale)\b")
CONFIRM_TRIES = 3


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (label or "").lower())[:16]


def _holds_value(p: Page) -> bool:
    return any((e.value or "").strip() or e.checked for e in pages.real_fields(p))


def starts_application(p: Page, label: str) -> bool:
    """Jev: does clicking `label` open or start the application, rather than submit a finished one?"""
    a = decide.current().ask("entry", pages.page_state(p), {"starts": decide.noul(
        {"control": label, "question": "Does clicking `control` start the job application or open its form, rather "
                                       "than submit an application?"},
        true="It opens the application: Easy Apply, Apply now on a job description, Apply manually, Continue.",
        false="It sends an application that has been filled in, or does something else.")})
    return a["starts"].yes(T["starts_application"])


def confirm_click(browser: Jev, session: str, label: str, read: Callable[[], Page],
                  sleep: Callable[[float], None]) -> str:
    """Make the click the browser agent chose and the server refused (B1), when every check above agrees.
    The control is found by label in a fresh read (a re-render renumbers refs; Genesys, live 2026-09-23:
    `detached`, then `page_changed`); a stale click is re-read and retried, up to CONFIRM_TRIES times."""
    if not (is_entry(label) or is_guest(label)):
        raise EntryRefused(f"{label!r} does not start an application")
    out = ""
    for attempt in range(CONFIRM_TRIES):
        p = read()
        if _holds_value(p):
            raise EntryRefused("a form field on the page already holds a value")
        hits = [e for e in p.elements if e.role in {"button", "link"} and not e.occluded
                and pages.norm_label(e.name) == pages.norm_label(label)]
        if not hits:
            raise EntryRefused(f"{label!r} is not on the page any more")
        target = hits[0]
        if attempt == 0 and not starts_application(p, label):
            raise EntryRefused(f"{label!r} does not look like it starts the application")
        if target.role == "button":
            sub = browser.probe(session, "ENTRY_SUBMITS")["ENTRY_SUBMITS"]
            if sub.get("more") or any(_key(i) == _key(target.name) for i in sub.get("items", [])):
                raise EntryRefused(f"{label!r} submits the form on this page")
        out = browser.act([{"op": "click", "ref": target.ref, "confirm": True}], session, p.table, token=ENTRY,
                          stop_on_error=False)
        if out.splitlines()[0].strip().startswith("1/1 ops ok"):
            return out
        if not STALE_RE.search(out):
            break
        sleep(1.0)
    raise EntryRefused("the entry click did not go through: " + " ".join(out.splitlines()[1:2]).strip())


# ------------------------------------------------------------------ guest link (§6.3 signup, attempt 2)

def guest_allowed(p: Page) -> str | None:
    """None if the guest-link click is permitted (user decision 2026-09-23), else why not: a sign-up wall (Jev),
    one guest link, and no field holding a value — nothing typed may go out with it."""
    j = pages.judge(p)
    if not (j.kind == "account_wall" and j.account == "create_account"):
        return "not a sign-up wall"
    if _holds_value(p):
        return "a form field on the page already holds a value"
    if pages.guest_link(p) is None:
        return "no 'apply without an account' / 'continue as guest' link"
    return None


def guest_click(browser: Jev, session: str, p: Page) -> str:
    reason = guest_allowed(p)
    if reason:
        raise EntryRefused(reason)
    link = pages.guest_link(p)
    op = {"op": "click", "ref": link.ref}
    if is_transmit(link.name):                 # "Apply without an account" needs the server's confirm (B1)
        op["confirm"] = True
    return browser.act([op, {"op": "wait_for_load", "timeout_ms": 20000}], session, p.table, token=ENTRY,
                       stop_on_error=False)
