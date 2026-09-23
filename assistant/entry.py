"""The entry click (§4.2) — the only code that may send "confirm": true."""
from __future__ import annotations

import re

from assistant.guard import ENTRY, is_entry, is_guest, is_transmit
from assistant.jev import Jev, Table

FORM_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "file"}


class EntryRefused(RuntimeError):
    pass


def entry_button(table: Table):
    """The single button/link whose label matches ENTRY_RE, else None."""
    hits = [e for e in table.elements if e.role in {"button", "link"} and is_entry(e.name)]
    return hits[0] if len(hits) == 1 else None


def entry_allowed(table: Table) -> str | None:
    """None if §4.2 permits the entry click on this page, else the reason it does not."""
    fields = [e for e in table.elements if e.role in FORM_ROLES]
    if any((e.value or "").strip() or e.checked for e in fields):
        return "a form field on the page already holds a value"
    if fields and not re.search(r"/jobs/view/", table.url):
        return "page has form fields and is not a /jobs/view/ page"
    if entry_button(table) is None:
        return "no single Easy Apply / Apply button"
    return None


def entry_click(browser: Jev, session: str, table: Table) -> str:
    reason = entry_allowed(table)
    if reason:
        raise EntryRefused(reason)
    btn = entry_button(table)
    return browser.act([{"op": "click", "ref": btn.ref, "confirm": True}], session, table, token=ENTRY)


# ------------------------------------------------------------------ guest link (§6.3 signup, attempt 2)

def guest_link(table: Table):
    hits = [e for e in table.elements if e.role in {"button", "link"} and is_guest(e.name)]
    return hits[0] if len(hits) == 1 else None


def guest_allowed(table: Table) -> str | None:
    """None if the guest-link click is permitted (user decision 2026-09-23), else why not: a sign-up wall
    (a password field), one guest link, and no field holding a value — nothing typed may go out with it."""
    fields = [e for e in table.elements if e.role in FORM_ROLES]
    if not any(e.role == "textbox" and re.search(r"pass(word|code)", e.name, re.I) for e in fields):
        return "not a sign-up wall (no password field)"
    if any((e.value or "").strip() or e.checked for e in fields):
        return "a form field on the page already holds a value"
    if guest_link(table) is None:
        return "no single 'apply without an account' / 'continue as guest' link"
    return None


def guest_click(browser: Jev, session: str, table: Table) -> str:
    reason = guest_allowed(table)
    if reason:
        raise EntryRefused(reason)
    link = guest_link(table)
    op = {"op": "click", "ref": link.ref}
    if is_transmit(link.name):                 # "Apply without an account" needs the server's confirm (B1)
        op["confirm"] = True
    return browser.act([op, {"op": "wait_for_load", "timeout_ms": 20000}], session, table, token=ENTRY,
                         stop_on_error=False)
