"""The page loop of one job (§5 from the LinkedIn posting to the gate) and the fill mapping of §7.

The browser agent (the package's browser_goal) finds its way: from the posting to the application form past
pop-ups, cookie banners and job pages, and from one form step to the next. Code keeps what must not be left to a
model: the answers (answers.py, checked against the files), the resume upload, the blockers, the final-step gate,
and every click the server refuses as submit-like — an entry label is clicked by entry.py, never anything else."""
from __future__ import annotations

import time
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

from assistant import pages, tabs
from assistant.answers import (LONG_TEXT, AnswerEngineError, PageAnswers, Question, judge_questions,
                               uncovered_optional, uncovered_required)
from assistant.decide import DecisionError
from assistant.blockers import Attempts, NeedsAttention, OpenQuestion, Parked, RestartFromEntry, StopRun
from assistant.entry import EntryRefused, confirm_click, guest_click
from assistant.guard import is_strong_transmit, is_transmit, label_of
from assistant.jev import Jev, JevError, Table
from assistant.pages import Page

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
STALE = re.compile(r"\b(detached|page_changed|target_changed|unknown_ref|stale)\b")
NAVIGATE_GOAL = (PROMPTS / "navigate_goal.md").read_text().strip()
NEXT_STEP_GOAL = (PROMPTS / "next_step_goal.md").read_text().strip()
NOT_THE_FORM = ("\nThe fields on this page are not the application form (they look like a search box or a "
                "job-alert sign-up). Start the application instead.")
NAVIGATE_ROUNDS = 12        # one-action navigate goals per job: cookies, pop-ups, job pages, start dialogs, and spare
ADVANCE_TRIES = 3           # one-action advance goals per step, while the agent only scrolls or waits


def page_goal(answers: list[tuple[str, str]]) -> tuple[str, int]:
    """(goal text, max_steps) for one goal that sets every (question, answer) pair."""
    lines = "\n".join(f'- "{q}" → "{a}"' for q, a in answers)
    goal = (PROMPTS / "page_goal.md").read_text().format(answers=lines).strip()
    return goal, 2 * len(answers) + 3


# ------------------------------------------------------------------ goal output (B8)

@dataclass
class GoalResult:
    status: str
    trace: list[str]
    verified: bool | None
    raw: str

    @property
    def done(self) -> bool:
        return self.status == "done"


TRACE_RE = re.compile(r"^\s*\d+\.\s+(CLICK|TYPE_TEXT|SELECT|TOGGLE|SCROLL|WAIT)\s+(e[\d:]+)?\s*(.*?)\s+→\s+(\S+)")


def parse_goal(out: str) -> GoalResult:
    m = re.search(r"^status: (.+)$", out, re.M)
    status = m.group(1).strip() if m else "failed:no_status"          # B8: no status line → failed
    trace = out.split("\ntrace:\n", 1)[1].split("\n\n", 1)[0].splitlines() if "\ntrace:\n" in out else []
    v = re.search(r"^verified: (PASS|FAIL)$", out, re.M)
    return GoalResult(status, trace, (v.group(1) == "PASS") if v else None, out)


def goal_clicks(g: GoalResult, table: Table) -> list[tuple[str, str]]:
    """(label, result) of every CLICK in the trace; the label comes from our table when the ref is known."""
    out = []
    for line in g.trace:
        m = TRACE_RE.match(line)
        if m and m.group(1) == "CLICK":
            label = (label_of(m.group(2), table) if m.group(2) else None) or m.group(3)
            out.append((label, m.group(4)))
    return out


def check_goal_alarm(g: GoalResult, table: Table) -> None:
    """§4.6: a successful click on a TRANSMIT label ends the run."""
    for label, result in goal_clicks(g, table):
        if result == "ok" and is_transmit(label):
            raise StopRun(f"ALARM: a goal clicked {label!r}")


def blocked_transmit(g: GoalResult, table: Table) -> str | None:
    """The label the server refused (needs_confirmation), if any."""
    for label, result in goal_clicks(g, table):
        if result.startswith("needs_confirmation"):
            return label
    return None


# ------------------------------------------------------------------ job context

AnswerFn = Callable[[Page], PageAnswers]


@dataclass
class JobCtx:
    browser: Jev
    session: str
    book: tabs.TabBook
    resume_pdf: Path
    answer_fn: AnswerFn
    baseline: set[str]
    google_email: str = ""
    max_pages: int = 15
    answers_log: Path | None = None
    shots_dir: Path | None = None
    folder: str = "job"
    # state
    pages: int = 0
    filled_count: int = 0
    typed: dict[str, str] = field(default_factory=dict)
    generated: list[tuple[str, str]] = field(default_factory=list)
    prefills: list[tuple[str, str]] = field(default_factory=list)
    optional_empty: list[OpenQuestion] = field(default_factory=list)
    attempts: Attempts = field(default_factory=Attempts)
    nav_rounds: int = 0
    stage: str = "entry"
    last: Page | None = None
    sleep: Callable[[float], None] = time.sleep

    def read(self) -> Page:
        """The current page, after it has drawn its content (pages.settle)."""
        self.last = pages.settle(lambda: pages.read_page(self.browser, self.session), self.sleep)
        return self.last


def _open_q(q: Question) -> OpenQuestion:
    return OpenQuestion(q.question, q.kind, q.options)


def _where(exc: NeedsAttention, ctx: JobCtx) -> NeedsAttention:
    if ctx.last and not exc.url:
        exc.url, exc.title = ctx.last.url, ctx.last.title
    exc.page, exc.stage, exc.filled = ctx.pages + 1, exc.stage or ctx.stage, ctx.filled_count
    exc.optional = exc.optional or ctx.optional_empty
    return exc


# ------------------------------------------------------------------ fill steps (§7 fill mapping)

def _field_fingerprint(p: Page) -> tuple:
    return tuple(sorted((e.role, e.name) for e in pages.fields(p)))


RESUME_CONFIDENCE = 0.5     # below this, Jev cannot tell which of several uploads is the resume


def resume_input(p: Page) -> str | None:
    """The control that uploads the resume (a file input, or LinkedIn's "Upload resume" button that opens the file
    chooser, aa6), as Jev picks it. Several uploads and no clear pick → a blocker (C25)."""
    j = pages.judge(p)
    files = [e for e in p.elements if e.role == "file"]
    if j.resume_ref is None:
        return None
    if len(files) > 1 and j.resume_confidence < RESUME_CONFIDENCE:
        raise NeedsAttention("broken_form", "several file inputs and it is unclear which one takes the resume")
    return j.resume_ref


def upload_resume(ctx: JobCtx, p: Page) -> bool:
    """Upload the tailored resume on this page if it has a resume input. True if the resume is now in place."""
    ref = resume_input(p)
    if ref is None:
        return False
    el = next(e for e in p.elements if e.ref == ref)
    stem = ctx.resume_pdf.name[:30]
    if stem in (el.value or "") or (el.role != "file" and stem in p.text):
        select_resume_card(ctx, p)
        return True
    out = ctx.browser.act([{"op": "upload", "ref": ref, "path": str(ctx.resume_pdf)}], ctx.session, p.table)
    if "1/1 ops ok" not in out:
        raise NeedsAttention("broken_form", f"resume upload failed: {out.splitlines()[1:2]}")
    ctx.filled_count += 1
    select_resume_card(ctx, ctx.read())
    return True


def is_resume_question(q: Question) -> bool:
    """A question the resume upload answers (Jev's verdict, answers.judge_questions): about the resume/CV, or a
    choice among resume files (LinkedIn's "Resume*" cards, live 2026-09-23)."""
    return q.resume_upload


def select_resume_card(ctx: JobCtx, p: Page) -> None:
    """LinkedIn lists earlier resumes as radio cards named by file name: make sure the tailored one is the
    selected card (never an older one)."""
    cards = [e for e in p.elements if e.role == "radio" and pages.RESUME_FILE_RE.search(e.name)]
    ours = [e for e in cards if e.name.startswith(ctx.resume_pdf.name[:30])]
    if ours and not ours[0].checked:
        ctx.browser.act([{"op": "toggle", "ref": ours[0].ref, "state": True}], ctx.session, p.table,
                        stop_on_error=False)


def type_long(ctx: JobCtx, p: Page, q: Question) -> None:
    """Wrapper-typed text (generated, or > LONG_TEXT chars). A stale ref: re-observe, re-map by question, once."""
    ref = q.ref
    for attempt in range(2):
        out = ctx.browser.act([{"op": "type", "ref": ref, "text": q.answer, "clear": True, "submit": False}],
                                ctx.session, p.table, stop_on_error=False)
        if "1/1 ops ok" in out:
            ctx.typed[ref] = q.answer
            ctx.filled_count += 1
            return
        if attempt == 0 and STALE.search(out):
            p = ctx.read()
            ref = next((e.ref for e in p.elements if pages.norm_label(e.name) == pages.norm_label(q.question)), None)
            if ref is None:
                break
            continue
        break
    raise NeedsAttention("broken_form", f"field would not accept its value: {q.question!r}")


def _goal_items(pa: PageAnswers, p: Page) -> list[Question]:
    """Answers still to set: non-null, not wrapper-typed long text, differing from the current value. Most are then
    set directly (direct_op); the rest go to the one page goal."""
    by_ref = {e.ref: e for e in p.elements}
    items = []
    for q in pa.questions:
        if q.answer is None or q.kind == "file" or _wrapper_types(q):
            continue
        el = by_ref.get(q.ref or "")
        opt = by_ref.get(q.option_ref or "")
        if opt is not None and opt.checked:
            continue
        if el is not None and opt is None and pages.norm_label(el.current or el.value) == pages.norm_label(q.answer):
            continue
        items.append(q)
    return items


TYPEABLE = {"textbox", "searchbox", "spinbutton"}


def direct_op(q: Question, p: Page) -> dict | None:
    """User decisions 2026-09-23: controls with a known ref are set by the wrapper, no model —
    `toggle` for a radio/checkbox option_ref, `select` for a native <select> (a combobox with options), and
    `type` for a text answer into a plain field the observer marks editable (textbox, searchbox, number input;
    not read-only, not a custom combobox). The page goal is left for custom widgets."""
    by_ref = {e.ref: e for e in p.elements}
    opt = by_ref.get(q.option_ref or "")
    if opt is not None and opt.role in {"radio", "checkbox", "switch"}:
        return {"op": "toggle", "ref": opt.ref, "state": True}
    el = by_ref.get(q.ref or "")
    if el is not None and el.role in {"combobox", "listbox"} and el.options:
        if any(pages.norm_label(o.label) == pages.norm_label(q.answer) for o in el.options):
            return {"op": "select", "ref": el.ref, "value": q.answer}
    if el is not None and el.role in TYPEABLE and el.editable and q.kind != "file" and q.answer is not None:
        return {"op": "type", "ref": el.ref, "text": q.answer, "clear": True, "submit": False}
    return None


def set_direct(ctx: JobCtx, p: Page, qs: list[Question]) -> None:
    ops = [direct_op(q, p) for q in qs]
    if ops:
        ctx.browser.act(ops, ctx.session, p.table, stop_on_error=False)


def _other_resume_card(q: Question, p: Page, ctx: JobCtx) -> bool:
    """An answer that would select an older resume card instead of the tailored resume."""
    by_ref = {e.ref: e for e in p.elements}
    opt = by_ref.get(q.option_ref or "")
    name = opt.name if opt is not None else (q.answer or "")
    return bool(pages.RESUME_FILE_RE.search(name)) and not name.startswith(ctx.resume_pdf.name[:30])


def _wrapper_types(q: Question) -> bool:
    return bool(q.ref) and q.kind in ("text", "longtext", "other") and (
        q.source == "generated" or len(q.answer or "") > LONG_TEXT)


def _verify(items: list[Question], p: Page) -> list[dict]:
    by_ref = {e.ref: e for e in p.elements}
    checks = []
    for q in items:
        if q.option_ref and q.option_ref in by_ref:
            checks.append({"type": "checked", "ref": q.option_ref, "state": True})
        elif q.ref in by_ref and by_ref[q.ref].role in {"textbox", "searchbox", "spinbutton"}:
            checks.append({"type": "value_equals", "ref": q.ref, "value": q.answer})
    return checks


def run_goal(ctx: JobCtx, items: list[Question], p: Page) -> GoalResult:
    goal, steps = page_goal([(q.question, q.answer) for q in items])
    g = parse_goal(ctx.browser.goal(goal, ctx.session, max_steps=steps, verify=_verify(items, p) or None))
    if "no decision-model key" in g.raw:
        raise NeedsAttention("answer_engine", "page goal could not run: no decision-model key")
    # Anything else (text helper returned no value, a provider hiccup, a timeout) is a failed goal:
    # read-back decides what was set, and its retry/blocker rules apply (B5).
    check_goal_alarm(g, p.table)
    return g


def _key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", pages.norm_label(s))


def _option_now(q: Question, p: Page):
    """The option a toggle set, found again after a re-render renumbered it: LinkedIn redraws its radio group on
    every change (live 2026-09-23: e221 became e231, checked), so the old ref reads as "not set". The one option
    named like the question whose own label is the answer, else the one named like the answer."""
    kinds = {"radio", "checkbox", "switch"}
    hits = [e for e in p.elements if e.role in kinds and _key(e.name) == _key(q.question) and e.label
            and _key(e.label) == _key(q.answer)]
    if not hits:
        hits = [e for e in p.elements if e.role in kinds and _key(e.name) == _key(q.answer)]
    return hits[0] if len(hits) == 1 else None


def _holds(q: Question, p: Page, before_text: str) -> bool:
    by_ref = {e.ref: e for e in p.elements}
    if q.option_ref:
        opt = by_ref.get(q.option_ref) or _option_now(q, p)
        return bool(opt and opt.checked)
    want = pages.norm_label(q.answer)
    el = by_ref.get(q.ref or "") or next((e for e in p.elements
                                          if pages.norm_label(e.name) == pages.norm_label(q.question)), None)
    if el is None:
        # a radio group without refs: the radio named like the answer, if it is the only one
        radios = [e for e in p.elements if e.role == "radio" and pages.norm_label(e.name) == want]
        return len(radios) == 1 and bool(radios[0].checked)
    if pages.norm_label(el.current or el.value) == want:
        return True
    # A custom (non-<select>) combobox reports no value (DISCOVERY.md); the picked label shows in the page
    # text instead. Accept only a label that appeared because of the fill.
    if el.role == "combobox" and not el.options and not (el.value or "").strip():
        return want in pages.norm_label(p.text) and want not in pages.norm_label(before_text)
    return False


def mismatches(items: list[Question], p: Page, before_text: str = "") -> list[Question]:
    """Read-back: questions whose field does not hold the answer."""
    return [q for q in items if not _holds(q, p, before_text)]


def fill_page(ctx: JobCtx, p: Page) -> None:
    ctx.stage = "fill"
    try:
        pa = ctx.answer_fn(p)
    except AnswerEngineError as exc:
        raise NeedsAttention("answer_engine", str(exc)) from exc
    log_answers(ctx, p, pa)
    try:
        judge_questions(pa, p)                                   # Jev: resume and cover-letter questions
    except DecisionError as exc:
        raise NeedsAttention("decision", str(exc)) from exc
    for q in pa.questions:
        if q.kind == "file" and q.required and q.cover_letter:
            raise NeedsAttention("broken_form", f"required cover-letter file: {q.question!r}")   # C15
    for label in pages.judge(p).cover_letters:
        raise NeedsAttention("broken_form", f"required cover-letter file: {label!r}")
    resume_in_place = upload_resume(ctx, p)                                # 1
    for q in pa.questions:                                                       # 2
        if q.answer is not None and _wrapper_types(q):
            type_long(ctx, p, q)
            if q.source == "generated":
                ctx.generated.append((q.question, q.answer))
    for q in pa.questions:
        if q.source == "linkedin-prefill" and q.answer is not None:
            ctx.prefills.append((q.question, q.answer))
    items = [q for q in _goal_items(pa, p) if not _other_resume_card(q, p, ctx)]
    direct = [q for q in items if direct_op(q, p)]
    goal_items = [q for q in items if q not in direct]
    set_direct(ctx, p, direct)                                             # 3a: no model
    if goal_items:                                                               # 3b: one goal
        run_goal(ctx, goal_items, p)
    ctx.filled_count += len(items)
    if items:
        after = ctx.read()
        if _field_fingerprint(after) != _field_fingerprint(p):                   # the goal left the page
            ctx.attempts.fail("broken_form", "the page goal moved off the page before it was filled")
            raise _Refill(p.url)
    ctx.optional_empty += [_open_q(q) for q in uncovered_optional(pa)]
    missing = [q for q in uncovered_required(pa) if not (resume_in_place and is_resume_question(q))]
    if missing:                                                                  # D2
        raise NeedsAttention("unanswered", f"{len(missing)} required question(s) have no answer in the files",
                             questions=[_open_q(q) for q in missing])
    if items:                                                                    # read-back
        q = ctx.read()
        bad = mismatches(items, q, p.text)
        if bad:
            # One retry: a toggle or select again (idempotent); everything else — including a directly typed
            # value the page rewrote or refused (masks, date widgets) — gets one goal for that field alone.
            again = [b for b in bad if (direct_op(b, q) or {}).get("op") in ("toggle", "select")]
            set_direct(ctx, q, again)
            for b in bad:
                if b not in again:
                    run_goal(ctx, [b], q)
                    q = ctx.read()
            q = ctx.read()
            bad = mismatches(bad, q, p.text)
            if bad:
                ctx.attempts.fail("broken_form", "field would not accept its value: "
                                  + ", ".join(repr(b.question) for b in bad))
                raise _Refill(p.url)


class _Refill(Exception):
    """broken_form attempt 2: reopen the page where filling started and fill it again."""

    def __init__(self, url: str = ""):
        super().__init__(url)
        self.url = url


def log_answers(ctx: JobCtx, p: Page, pa: PageAnswers) -> None:
    if not ctx.answers_log:
        return
    ctx.answers_log.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(ctx.answers_log.read_text()) if ctx.answers_log.exists() else []
    data += [{"page": ctx.pages + 1, "url": p.url, "model": pa.model, "question": q.question, "answer": q.answer,
              "source": q.source, "quote": q.quote, "relies_on": q.relies_on, "note": q.note} for q in pa.questions]
    ctx.answers_log.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ new tabs after a click

ENTRY_TAB_WAIT = 8          # s: LinkedIn may open the company site a few seconds after the Apply click
CLICK_TAB_WAIT = 3          # s: after an advance click (an interstitial "Continue" can open the site too)


def follow_new_tab(ctx: JobCtx, known: set[str], seconds: int, before: Page | None = None) -> bool:
    """After a click: if a new tab appears within `seconds`, hand the job off to it (and close the tab we
    were on, unless it is the user's). Stops waiting as soon as this tab has visibly changed from `before`
    (the page at the click) and shows a real form field or an advance button, or shows confirmation text.
    LinkedIn opens its Easy Apply dialog a moment after the click, over a page that already has a switch
    (live 2026-09-23), so "any field" is not enough. Returns True if the job moved to a new tab."""
    shape = pages.page_shape(before) if before is not None else None
    for i in range(seconds + 1):
        if ctx.book.handles() - known - ctx.baseline:
            ctx.book.hand_off(ctx.session, ctx.baseline, known)
            return True
        if i == seconds:
            break
        p = pages.read_page(ctx.browser, ctx.session)
        if pages.is_alarm(p):
            return False
        if pages.page_shape(p) != shape if shape is not None else pages.form_is_here(p):
            return False
        ctx.sleep(1.0)
    return False


# ------------------------------------------------------------------ the browser agent (browser_goal)

def acted(g: GoalResult) -> bool:
    """The agent changed something: a click, a choice, a toggle or typing went through."""
    return any((m := TRACE_RE.match(line)) and m.group(1) not in ("SCROLL", "WAIT") and m.group(4) == "ok"
               for line in g.trace)


def navigate(ctx: JobCtx, p: Page, hint: str = "") -> str:
    """One agent action from `p` towards the application form (NAVIGATE_GOAL, max_steps=1: the page is looked at
    again after every action, so the agent can never run through a form we have not filled). Returns "form" (the
    agent says the form is on screen, or starts answering it), "moved" (it, or our entry click, changed the page)
    or "stuck". A click the server refused as submit-like is made by entry.confirm_click when it starts the
    application; a refused Apply that belongs to a form already on the page means the form is here."""
    ctx.stage = "navigate"
    ctx.nav_rounds += 1
    if ctx.nav_rounds > NAVIGATE_ROUNDS:
        raise NeedsAttention("navigation", f"the browser agent did not reach the application form in "
                                           f"{NAVIGATE_ROUNDS} tries (last page: {p.title or p.url!r})")
    known = ctx.book.handles()
    out = ctx.browser.goal(NAVIGATE_GOAL + hint, ctx.session, max_steps=1)
    if "no decision-model key" in out:
        raise NeedsAttention("navigation", "the browser agent is not available: no decision-model key")
    g = parse_goal(out)
    check_goal_alarm(g, p.table)
    blocked = blocked_transmit(g, p.table)
    if blocked:
        try:
            confirm_click(ctx.browser, ctx.session, blocked, ctx.read, ctx.sleep)
        except EntryRefused as exc:
            if pages.has_fields(ctx.read()):
                return "form"
            raise NeedsAttention("navigation", f"the browser agent chose {blocked!r}, which the program does "
                                               f"not click: {exc}") from exc
        follow_new_tab(ctx, known, ENTRY_TAB_WAIT, p)       # a dialog or the company site may open a moment later
        return "moved"
    if follow_new_tab(ctx, known, 0):                         # the agent's own click opened a tab
        return "moved"
    if g.done or any((m := TRACE_RE.match(line)) and m.group(1) in ("TYPE_TEXT", "SELECT", "TOGGLE")
                     for line in g.trace):
        return "form"                                         # it says so, or it began to answer the form
    return "moved" if acted(g) else "stuck"


def advance(ctx: JobCtx, p: Page) -> str:
    """'moved' | 'final' | 'stuck'. The agent clicks this step's Next / Continue / Review (NEXT_STEP_GOAL), one
    action per goal so it can never run past a step we have not filled; a goal that only scrolled or waited is
    asked again. The server refusing a strong transmit label (Submit, Send, Apply) means this is the last step."""
    ctx.stage = "advance"
    before = _field_fingerprint(p)
    known = ctx.book.handles()
    for _ in range(ADVANCE_TRIES):
        g = parse_goal(ctx.browser.goal(NEXT_STEP_GOAL, ctx.session, max_steps=1))
        check_goal_alarm(g, p.table)
        blocked = blocked_transmit(g, p.table)
        if blocked and is_strong_transmit(blocked):
            return "final"
        if acted(g) or blocked or g.status in ("done", "blocked"):
            break
    if follow_new_tab(ctx, known, CLICK_TAB_WAIT, p):  # e.g. LinkedIn's "Continue" to the company site
        return "moved"
    q = ctx.read()
    if pages.is_alarm(q):
        raise StopRun(f"ALARM: confirmation text after advance on {q.url}")
    if pages.validation_error(q) or _field_fingerprint(q) == before and q.url == p.url and not pages.is_final(q):
        return "stuck"
    return "moved"


# ------------------------------------------------------------------ the loop

def run_pages(ctx: JobCtx) -> Parked:
    """From wherever the job's tab is (the LinkedIn posting) to the parked final step. Two stages: "navigate",
    where the agent looks for the application form, and "form", where each step is filled and advanced. A page
    that is covered by a pop-up, or shows neither fields nor a way forward, goes back to the agent.
    Raises NeedsAttention / StopRun / RestartFromEntry. Never clicks a transmit label (the guard would refuse)."""
    ctx.nav_rounds = 0
    stage, hint, stuck = "navigate", "", 0
    filled, gate_retry, iframe_hops, guard_rounds, final_hint = False, False, 0, 0, False
    try:
        while True:
            guard_rounds += 1
            if guard_rounds > ctx.max_pages * 4 + NAVIGATE_ROUNDS:
                raise NeedsAttention("broken_form", "the page loop is not making progress")
            p = ctx.read()
            v = pages.classify(p)
            if v.kind == "alarm":
                raise StopRun(f"ALARM: {v.detail!r} on {p.url}")
            if v.kind == "blocker":
                _attempt2(ctx, p, v.detail.split(":", 1)[0], v.detail)
                stage, filled = "navigate", False
                continue
            if v.kind in ("google", "google_wall"):
                from assistant.google_signin import sign_in
                ctx.stage = "google sign-in"
                sign_in(ctx.browser, ctx.session, ctx.book, ctx.google_email, ctx.baseline)
                stage = "navigate"
                continue
            if v.kind == "iframe":
                iframe_hops += 1
                if iframe_hops > 2:
                    raise NeedsAttention("load_failure", "the embedded application form did not load")
                ctx.browser.open(v.detail, ctx.session)
                continue
            if stage == "navigate" and pages.form_is_here(p):
                stage, hint = "form", ""
            if stage == "navigate" or not pages.form_is_here(p):
                r = navigate(ctx, p, hint)
                if r == "form" and stage == "navigate" and not hint and not pages.form_is_here(ctx.read()):
                    hint = NOT_THE_FORM                        # ask once more; a second "the form is here" stands
                    continue
                if r == "stuck" and hint:
                    r = "form"                     # asked for the application's start, it found nothing to click
                if r == "stuck":
                    stuck += 1
                    if stuck >= 2:
                        raise NeedsAttention("navigation", f"the browser agent found no way forward on "
                                                           f"{p.title or p.url!r}")
                    continue
                stuck, hint = 0, ""
                if r == "form":
                    stage = "form"
                continue
            if pages.has_fields(p) and not filled:
                try:
                    fill_page(ctx, p)
                except _Refill as r:
                    if r.url:
                        ctx.browser.open(r.url, ctx.session)
                    else:
                        _reload(ctx)
                    stage = "navigate"                        # a reopened LinkedIn job shows the posting again
                    continue
                filled = True
                continue
            if pages.is_final(p) or (final_hint and pages.judge(p).submit_button):
                ctx.stage = "final"
                why = pages.gate(p, ctx.resume_pdf.name, ctx.typed)
                if why is None:
                    break
                if gate_retry:
                    raise NeedsAttention("gate", why)
                gate_retry, filled = True, False
                continue
            step = advance(ctx, p)
            if step == "final":
                final_hint = True              # the server refused a strong transmit click: this is the last step
                continue
            if step == "stuck":
                ctx.attempts.fail("broken_form", pages.validation_error(ctx.last) or "advance changes nothing")
                filled = False                 # attempt 2: refill this page, then advance again
                continue
            filled, final_hint = False, False
            ctx.pages += 1
            if ctx.pages > ctx.max_pages:
                raise NeedsAttention("broken_form", f"no final step after {ctx.max_pages} pages")
    except NeedsAttention as exc:
        raise _where(exc, ctx)
    except DecisionError as exc:
        raise _where(NeedsAttention("decision", str(exc)), ctx) from exc
    except JevError as exc:
        try:
            ctx.attempts.fail("load_failure", str(exc)[:160])
        except NeedsAttention as na:
            raise _where(na, ctx) from exc
        raise RestartFromEntry(str(exc)) from exc
    return _park(ctx)


def _attempt2(ctx: JobCtx, p: Page, cls: str, detail: str) -> None:
    ctx.attempts.fail(cls, detail)                   # raises when no attempt is left
    if cls == "captcha":
        ctx.sleep(5)
        _reload(ctx)
    elif cls == "signup":
        try:
            guest_click(ctx.browser, ctx.session, p)
        except EntryRefused as exc:
            raise NeedsAttention("signup", f"{detail}; guest link not usable: {exc}") from exc
    elif cls == "broken_form":
        _reload(ctx)
    elif cls == "load_failure":
        raise RestartFromEntry(detail)


def _reload(ctx: JobCtx) -> None:
    ctx.browser.act([{"op": "reload"}, {"op": "wait_for_load", "timeout_ms": 20000}], ctx.session,
                      Table(url=""), stop_on_error=False)


def _park(ctx: JobCtx) -> Parked:
    p = ctx.last
    shot = ""
    if ctx.shots_dir:
        ctx.shots_dir.mkdir(parents=True, exist_ok=True)
        shot = str(ctx.shots_dir / f"{ctx.folder}.jpg")
        ctx.browser.act([{"op": "screenshot", "path": shot, "full": True}], ctx.session, p.table,
                          observe_after=False, stop_on_error=False)
    return Parked(url=p.url, title=p.title, pages=ctx.pages + 1, generated=ctx.generated, prefills=ctx.prefills,
                  optional_empty=ctx.optional_empty, screenshot=shot)
