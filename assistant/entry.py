"""The entry click (§4.2) — the only code that may send "confirm": true."""
from __future__ import annotations

import re
from typing import Callable

from assistant.guard import ENTRY, is_entry, is_guest, is_transmit
from assistant import pages
from assistant.jev import Jev, Table

class EntryRefused(RuntimeError):
    pass


def _fields(table: Table):
    """Form fields other than site chrome (search box, language picker): pages.real_fields on a bare table."""
    return pages.real_fields(pages.Page(url=table.url, title=table.title, text="", table=table))


def entry_button(table: Table):
    """The single button/link whose label matches ENTRY_RE (a link wrapping a same-label button counts once)."""
    return pages.single_entry(table.elements)


def entry_allowed(table: Table) -> str | None:
    """None if §4.2 permits the entry click on this page, else the reason it does not."""
    fields = _fields(table)
    if any((e.value or "").strip() or e.checked for e in fields):
        return "a form field on the page already holds a value"
    if fields and not re.search(r"/jobs/view/", table.url):
        return "page has form fields and is not a /jobs/view/ page"
    if entry_button(table) is None:
        return "no single Easy Apply / Apply button"
    return None


STALE_RE = re.compile(r"\b(detached|page_changed|target_changed|unknown_ref|stale)\b")


def entry_click(browser: Jev, session: str, table: Table, reread: Callable[[], Table] | None = None) -> str:
    """Click the entry button and check that the click happened. A stale ref — the page re-rendered the button
    between the snapshot and the click (LinkedIn, live 2026-09-23: `x click e16  detached`) — is re-read with
    `reread` and retried once, with the §4.2 checks run again on the fresh table."""
    for attempt in (1, 2):
        reason = entry_allowed(table)
        if reason:
            raise EntryRefused(reason)
        btn = entry_button(table)
        out = browser.act([{"op": "click", "ref": btn.ref, "confirm": True}], session, table, token=ENTRY,
                          stop_on_error=False)
        if out.splitlines()[0].strip().startswith("1/1 ops ok"):
            return out
        if attempt == 1 and reread is not None and STALE_RE.search(out):
            table = reread()
            continue
        raise EntryRefused("the entry click did not go through: " + " ".join(out.splitlines()[1:2]).strip())
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ guest link (§6.3 signup, attempt 2)

def guest_link(table: Table):
    hits = [e for e in table.elements if e.role in {"button", "link"} and is_guest(e.name)]
    return hits[0] if len(hits) == 1 else None


def guest_allowed(table: Table) -> str | None:
    """None if the guest-link click is permitted (user decision 2026-09-23), else why not: a sign-up wall
    (a password field), one guest link, and no field holding a value — nothing typed may go out with it."""
    fields = _fields(table)
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
