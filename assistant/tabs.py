"""Tab bookkeeping (§4.5, §5): release a session without closing its tab, hand off to a new tab, close junk.

jev 0.1.5 lists tabs with 8-char handles but switch/close need the full target ID, and `new` does not
switch the session (DISCOVERY.md) — so full IDs come from the CDP endpoint's /json/list.
"""
from __future__ import annotations

import httpx

from assistant.jev import Jev


def page_tabs(cdp_url: str) -> list[dict]:
    """[{id, url, title}] of every page tab, straight from Chrome (read-only)."""
    r = httpx.get(cdp_url.rstrip("/") + "/json/list", timeout=5)
    r.raise_for_status()
    return [{"id": t["id"], "url": t.get("url", ""), "title": t.get("title", "")}
            for t in r.json() if t.get("type") == "page"]


def tab_ids(cdp_url: str) -> set[str]:
    return {t["id"] for t in page_tabs(cdp_url)}


def release(browser: Jev, session: str, cdp_url: str) -> None:
    """Detach `session` from its tab and leave that tab open (B3: close() closes only the current tab)."""
    before = tab_ids(cdp_url)
    browser.tabs(session, "new")
    scratch = tab_ids(cdp_url) - before
    if len(scratch) != 1:
        raise RuntimeError(f"release: expected one new scratch tab, saw {len(scratch)}")
    browser.tabs(session, "switch", target_id=scratch.pop())
    browser.close(session)


def current_tab(browser: Jev, session: str, cdp_url: str) -> str:
    """Full target ID of the session's current tab (the '*' row of browser_tabs list)."""
    listing = browser.tabs(session, "list")
    star = next((l for l in listing.splitlines() if l.strip().startswith("[") and "] * #" in l), None)
    if not star:
        raise RuntimeError(f"no current tab in listing:\n{listing}")
    handle = star.split("#", 1)[1].split()[0]
    matches = [i for i in tab_ids(cdp_url) if i.startswith(handle)]
    if len(matches) != 1:
        raise RuntimeError(f"tab handle #{handle} is ambiguous or gone")
    return matches[0]


def hand_off(browser: Jev, session: str, cdp_url: str, baseline: set[str], known: set[str]) -> str | None:
    """If the entry click opened exactly one new tab, move `session` to it and close the old tab.

    `known` = tabs present just before the click (baseline + our scratch/job tabs). Returns the new tab ID.
    """
    new = tab_ids(cdp_url) - known - baseline
    if not new:
        return None
    if len(new) > 1:
        raise RuntimeError(f"entry click opened {len(new)} tabs")
    old = current_tab(browser, session, cdp_url)
    target = new.pop()
    browser.tabs(session, "switch", target_id=target)
    if old not in baseline:
        browser.tabs(session, "close", target_id=old)
    return target


def close_junk(browser: Jev, session: str, cdp_url: str, baseline: set[str], keep: set[str]) -> list[str]:
    """Close tabs opened during this job that are neither in the baseline nor kept. Returns closed IDs."""
    junk = tab_ids(cdp_url) - baseline - keep
    for t in junk:
        browser.tabs(session, "close", target_id=t)
    return sorted(junk)
