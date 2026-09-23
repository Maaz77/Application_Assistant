"""Google one-click sign-in (§6.2). No model acts on accounts.google.com; every step is a wrapper click."""
from __future__ import annotations

import time

from assistant import pages, tabs
from assistant.blockers import NeedsAttention
from assistant.jev import Jev


def sign_in(browser: Jev, session: str, cdp_url: str, email: str, baseline: set[str]) -> None:
    """From a page offering Google (or already on accounts.google.com), pick `email` once and return to the site.
    Raises NeedsAttention(signup/credentials) whenever §6.2 says blocker."""
    if not email:
        raise NeedsAttention("credentials", "Google sign-in offered but no google.account_email is configured")
    p = pages.read_page(browser, session)
    site_tab = tabs.current_tab(browser, session, cdp_url)
    popup = None
    if not pages.is_google_page(p):
        btn = pages.google_button(p)
        if btn is None:
            raise NeedsAttention("credentials", "sign-in wall without a Google option")
        before = tabs.tab_ids(cdp_url)
        browser.act([{"op": "click", "ref": btn.ref}, {"op": "wait_for_load", "timeout_ms": 20000}],
                      session, p.table, stop_on_error=False)
        new = tabs.tab_ids(cdp_url) - before - baseline
        if len(new) == 1:                                   # Google opened a popup window
            popup = new.pop()
            browser.tabs(session, "switch", target_id=popup)
        p = pages.read_page(browser, session)
    if not pages.is_google_page(p):
        return _back_on_site(browser, session)            # already signed in: Google returned at once
    if why := pages.google_blocker(p):
        raise NeedsAttention("credentials", why)
    chooser = next((e for e in p.elements if e.role in {"button", "link"} and email.lower() in e.name.lower()), None)
    if chooser is None:
        raise NeedsAttention("credentials", f"Google account chooser does not offer {email}")
    browser.act([{"op": "click", "ref": chooser.ref}, {"op": "wait_for_load", "timeout_ms": 20000}],
                  session, p.table, stop_on_error=False)
    if popup:
        for _ in range(40):                                  # the popup closes itself after the choice
            if popup not in tabs.tab_ids(cdp_url):
                break
            time.sleep(0.25)
        else:
            q = pages.read_page(browser, session)
            raise NeedsAttention("credentials", pages.google_blocker(q) or "a second Google click is needed")
        browser.tabs(session, "switch", target_id=site_tab)
    else:
        q = pages.read_page(browser, session)
        if pages.is_google_page(q):
            raise NeedsAttention("credentials", pages.google_blocker(q) or "a second Google click is needed")
    _back_on_site(browser, session)


def _back_on_site(browser: Jev, session: str) -> None:
    p = pages.read_page(browser, session)
    if why := pages.after_google_blocker(p):
        raise NeedsAttention("signup", why)
