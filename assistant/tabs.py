"""Tab bookkeeping (§4.5, §5): release a session without closing its tab, hand off to a new tab, close junk.

Only public browser_* functions are used — no /json/list. Chrome's `chrome://inspect/#remote-debugging` switch
(Chrome 136+, the user's normal profile) serves only the DevTools websocket, so that HTTP endpoint answers 404.

jev 0.1.5 lists tabs with 8-char handles, while switch/close need the full target ID (DISCOVERY.md). A helper
session on its own about:blank tab learns full IDs: every tab created after it starts is announced in its next
browser_observe as `NEW TAB opened … 'target_id':'<full id>'`. The user's older tabs never need a full ID —
we never switch to or close them. If the helper's tab is closed from outside, a new helper is started; a tab
it cannot name is switched to by its position in a fresh list and the move is verified before anything is
closed (Chrome renumbers tabs), and it is never closed by us.
"""
from __future__ import annotations

import re

from assistant.jev import Jev, JevError

HELPER = "tabbook"
LIST_RE = re.compile(r"^\s*\[(\d+)\]\s+(\*?)\s*#([0-9A-Fa-f]{8})\s+(\S*)", re.M)
NEW_TAB_RE = re.compile(r"'target_id':'([0-9A-Fa-f]{32})'")


class TabError(RuntimeError):
    pass


def handle(target_id: str) -> str:
    return target_id[:8].upper()


class TabBook:
    """Handles (8 chars) for sets and diffs; full IDs, learned from the helper's observations, for actions."""

    def __init__(self, browser: Jev):
        self.browser = browser
        self.full: dict[str, str] = {}
        browser.open("about:blank", HELPER)
        self.helper_tab = self.current_handle(HELPER)
        browser.observe(HELPER, mode="delta", include_json=False, include_text=False)   # baseline for NEW TAB

    # ---------------------------------------------------------------- reading
    def _list(self, session: str) -> list[tuple[str, bool, str]]:
        text = self.browser.tabs(session, "list")
        return [(h.upper(), star == "*", url) for _, star, h, url in LIST_RE.findall(text)]

    def handles(self) -> set[str]:
        """Every page tab in the browser, as handles (the helper's own tab excluded)."""
        return {h for h, _, _ in self._list(HELPER)} - {self.helper_tab}

    def current_handle(self, session: str) -> str:
        cur = [h for h, star, _ in self._list(session) if star]
        if len(cur) != 1:
            raise TabError(f"session {session!r} has no single current tab")
        return cur[0]

    def _learn_ids(self) -> None:
        """Read full IDs from the helper's NEW TAB lines. If the helper's tab is gone (closed from outside — live
        2026-09-23 it vanished mid-run), start a new helper; tabs made before it keep only their handle."""
        try:
            out = self.browser.observe(HELPER, mode="delta", include_json=False, include_text=False)
        except JevError:
            self._revive()
            return
        for tid in NEW_TAB_RE.findall(out):
            self.full[handle(tid)] = tid.upper()

    def _revive(self) -> None:
        try:
            self.browser.close(HELPER)
        except JevError:
            pass
        self.browser.open("about:blank", HELPER)
        self.helper_tab = self.current_handle(HELPER)
        self.browser.observe(HELPER, mode="delta", include_json=False, include_text=False)

    def full_id(self, h: str) -> str | None:
        """The full target ID of a tab created after the helper started, else None."""
        if h not in self.full:
            self._learn_ids()
        return self.full.get(h)

    def current(self, session: str) -> str:
        h = self.current_handle(session)
        fid = self.full_id(h)
        if fid is None:
            raise TabError(f"no full target ID known for tab #{h}")
        return fid

    # ---------------------------------------------------------------- acting
    def switch(self, session: str, h: str) -> None:
        """Move `session` to tab `h`: by full ID when known, else by its position in a fresh list. Either way the
        result is verified, so a renumbered list can never leave the session on a different tab unnoticed."""
        fid = self.full_id(h)
        if fid is not None:
            self.browser.tabs(session, "switch", target_id=fid)
        else:
            idx = next((i for i, (x, _, _) in enumerate(self._list(session)) if x == h), None)
            if idx is None:
                raise TabError(f"tab #{h} is gone")
            self.browser.tabs(session, "switch", index=idx)
        if self.current_handle(session) != h:
            raise TabError(f"session {session!r} did not move to tab #{h}")

    def release(self, session: str) -> None:
        """Detach `session` from its tab and leave that tab open (B3: the exit hook and browser_close close
        only a session's current tab). The session moves to a new scratch tab, verified, then closes that."""
        before = self.handles()
        self.browser.tabs(session, "new")
        scratch = self.handles() - before
        if len(scratch) != 1:
            raise TabError(f"release: expected one new scratch tab, saw {len(scratch)}")
        h = scratch.pop()
        self.switch(session, h)
        if self.current_handle(session) != h:            # never close anything but our own scratch tab
            raise TabError("release: session is not on its scratch tab; nothing closed")
        self.browser.close(session)

    def hand_off(self, session: str, baseline: set[str], known: set[str]) -> str | None:
        """If the entry click opened exactly one new tab, move `session` to it and close the old tab (when its
        full ID is known; else it is left open). `known` = handles present just before the click."""
        new = self.handles() - known - baseline
        if not new:
            return None
        if len(new) > 1:
            raise TabError(f"entry click opened {len(new)} tabs")
        old = self.current_handle(session)
        target = new.pop()
        self.switch(session, target)
        fid = self.full_id(old)
        if old not in baseline and fid is not None:
            self.browser.tabs(session, "close", target_id=fid)
        return target

    def close_junk(self, session: str, baseline: set[str], keep: set[str]) -> list[str]:
        """Close tabs opened during this job that are neither in the baseline nor kept, when their full ID is
        known (a tab that cannot be identified for sure is left open). Returns the closed handles."""
        closed = []
        for h in sorted(self.handles() - baseline - keep):
            fid = self.full_id(h)
            if fid is not None:
                self.browser.tabs(session, "close", target_id=fid)
                closed.append(h)
        return closed

    def close(self) -> None:
        """Close the helper's own about:blank tab."""
        self.browser.close(HELPER)
