"""Tab bookkeeping (§4.5, P2). The owned driver reports full target ids and each new tab's openerId
(Target.targetCreated), so the vendored helper tab that learned 8-char handles is gone, and so are the
release scratch tab and the exit hook. A job's tab is never closed: its session's bookkeeping is dropped
(Browser.forget) and the tab stays open (D13). A tab the job tab opened (openerId == the job tab) that we
are not keeping is junk and is closed; a tab the user opened is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

from assistant.browser import Browser, DriverError


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

    def close_stale(self, session: str, keep: str, *, job_key: str = "", remembered: str = "") -> list[str]:
        """Close the tabs an EARLIER run of THIS job left open, and nothing else.

        Two narrow rules, because the two kinds of leftover look different: `remembered` is the exact tab id
        TabMemory recorded when the job was last released (the only way to recognise an adopted ATS tab, whose
        URL carries no job id), and a LinkedIn posting tab is recognised by `/jobs/view/<job_key>` in its URL,
        which needs no state and so works on the first run after this fix. `keep` — the tab this run is
        driving — is never closed. A failure is ignored: the user may have closed the tab already."""
        closed = []
        for t in self.browser.list_tabs():
            tid = t["target_id"]
            if tid == keep or not tid:
                continue
            if tid == remembered or (job_key and f"/jobs/view/{job_key}" in (t.get("url") or "")):
                try:
                    self.browser.close_tab_id(session, tid)
                except (DriverError, RuntimeError):
                    continue
                closed.append(tid)
        return closed

    def release(self, session: str) -> None:
        """Drop the session's bookkeeping and leave its tab open (D13). No scratch tab, no close (P2)."""
        self.browser.forget(session)

    def close(self) -> None:
        """No helper tab to close (P2)."""
        return None


class TabMemory:
    """Which tab each job was last left on, remembered ACROSS runs.

    D13 keeps a parked job's tab open on purpose — it is the deliverable, the user submits from it. Nothing
    closed the tab the PREVIOUS run left, so every re-run added another live, filled form for the same job
    (live 2026-10-08: 7 jobs x 2 runs = 14 job tabs, and the two tabs for one Flex posting disagreed — the
    older one held an `Age` the user had typed by hand, the newer one did not). This is what the user saw as
    "always two tabs on the application".

    Only ids this program recorded as its own job tabs are ever closed, so a tab the user opened is never
    touched. The file lives beside the run directories (gitignored), not in the user's application folders.
    """

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> str:
        return str(self._read().get(key) or "")

    def remember(self, key: str, target_id: str) -> None:
        data = self._read()
        data[key] = target_id
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            pass            # losing the memory costs an extra tab next run; it must never fail a job
