"""Outcomes of a job and the two-attempt rule (§6.3)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenQuestion:
    question: str
    kind: str
    options: list[str] | None = None


class NeedsAttention(Exception):
    """The job goes to Needs-Attention; its tab stays open (C8)."""

    def __init__(self, cls: str, what: str, *, questions: list[OpenQuestion] | None = None,
                 optional: list[OpenQuestion] | None = None):
        super().__init__(f"{cls}: {what}")
        self.cls, self.what = cls, what
        self.questions = questions or []
        self.optional = optional or []
        # filled in by the loop as it unwinds
        self.url = self.title = self.stage = ""
        self.page = 0
        self.filled = 0


class StopRun(Exception):
    """Stop the whole run now, exit 3 (§4.6 alarms, C19 signed out, tracker changed, folder clash)."""


class ParkedAtQuestion(Exception):
    """A required question had no answer. Catch at run_pages — park the job, don't raise NeedsAttention."""

    def __init__(self, missing: list[OpenQuestion], *, what: str = ""):
        super().__init__(what or f"{len(missing)} required question(s) have no answer in the files")
        self.what = what or self.args[0]
        self.missing = missing


class RestartFromEntry(Exception):
    """Load failure, attempt 2: reopen the job from its LinkedIn URL."""


class GoExternal(Exception):
    """The LinkedIn posting's apply control leads off LinkedIn (P5). Signal (not a blocker): the caller hands
    the job off to the external tab and drives it with external.run_external instead of the Easy Apply loop."""


@dataclass
class Parked:
    url: str
    title: str
    pages: int
    generated: list[tuple[str, str]] = field(default_factory=list)   # (question, text)
    prefills: list[tuple[str, str]] = field(default_factory=list)    # (question, kept value)
    optional_empty: list[OpenQuestion] = field(default_factory=list)
    parked_at: list[OpenQuestion] = field(default_factory=list)      # unanswered required at park (T5)
    screenshot: str = ""


# blocker class → what attempt 2 does (§6.3)
ATTEMPT2 = {
    "captcha": "wait 5 s, reload",
    "signup": "click 'apply without an account' / 'continue as guest'",
    "credentials": None,
    "broken_form": "reload, refill the page",
    "load_failure": "reopen from the LinkedIn URL",
}


class Attempts:
    """Counts failures per blocker class within one job. The second failure is final."""

    def __init__(self):
        self.seen: dict[str, int] = {}

    def fail(self, cls: str, what: str) -> None:
        """Record a failure. Raises NeedsAttention when no attempt 2 is left."""
        n = self.seen.get(cls, 0) + 1
        self.seen[cls] = n
        if n >= 2 or ATTEMPT2.get(cls) is None:
            raise NeedsAttention(cls, what if n == 1 else f"{what} (after 2 attempts)")
