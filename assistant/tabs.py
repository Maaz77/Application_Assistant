"""Tab bookkeeping (§4.5, P2). The owned driver reports full target ids and each new tab's openerId
(Target.targetCreated), so the vendored helper tab that learned 8-char handles is gone, and so are the
release scratch tab and the exit hook. A job's tab is never closed: its session's bookkeeping is dropped
(Browser.forget) and the tab stays open (D13). A tab the job tab opened (openerId == the job tab) that we
are not keeping is junk and is closed; a tab the user opened is never touched.
"""
from __future__ import annotations

from assistant.browser import Browser


class TabError(RuntimeError):
    pass


class TabBook:
    def __init__(self, browser: Browser):
        self.browser = browser

    def handles(self) -> set[str]:
        """Every page tab, by full target id."""
        return {t["target_id"] for t in self.browser.list_tabs()}

    def current_handle(self, session: str) -> str:
        """The full target id of the tab this session is driving."""
        tid = self.browser.session_target(session)
        if not tid:
            raise TabError(f"session {session!r} has no tab")
        return tid

    # The driver's ids are already full, so `current` and `current_handle` are the same now.
    def current(self, session: str) -> str:
        return self.current_handle(session)

    def switch(self, session: str, target_id: str) -> None:
        self.browser.tabs(session, "switch", target_id=target_id)
        if self.current_handle(session) != target_id:
            raise TabError(f"session {session!r} did not move to {target_id}")

    def hand_off(self, session: str, baseline: set[str], known: set[str]) -> str | None:
        """If a click opened exactly one new tab (opened by this tab), move the session to it and close the
        old tab (unless it is the user's baseline). Used by the external-ATS path, not the Easy Apply dialog."""
        job = self.browser.session_target(session)
        opened = [t["target_id"] for t in self.browser.list_tabs()
                  if t["target_id"] not in known and t["target_id"] not in baseline
                  and t.get("opener_id") == job]
        if not opened:
            return None
        if len(opened) > 1:
            raise TabError(f"a click opened {len(opened)} tabs")
        old = self.current_handle(session)
        target = opened[0]
        self.switch(session, target)
        if old not in baseline:
            self.browser.close_tab_id(session, old)
        return target

    def close_junk(self, session: str, baseline: set[str], keep: set[str]) -> list[str]:
        """Close tabs the job tab opened (openerId) that are neither baseline nor kept. Never a user tab."""
        job = self.browser.session_target(session)
        closed = []
        for t in self.browser.list_tabs():
            tid = t["target_id"]
            if tid in baseline or tid in keep:
                continue
            if t.get("opener_id") == job:
                self.browser.close_tab_id(session, tid)
                closed.append(tid)
        return closed

    def release(self, session: str) -> None:
        """Drop the session's bookkeeping and leave its tab open (D13). No scratch tab, no close (P2)."""
        self.browser.forget(session)

    def close(self) -> None:
        """No helper tab to close (P2)."""
        return None
