"""The page loop of one job (§5 from the entry click to the gate) and the fill mapping of §7."""
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
from assistant.answers import LONG_TEXT, AnswerEngineError, PageAnswers, Question, uncovered_optional, uncovered_required
from assistant.blockers import Attempts, NeedsAttention, OpenQuestion, Parked, RestartFromEntry, StopRun
from assistant.entry import EntryRefused, entry_click, guest_click
from assistant.guard import is_strong_transmit, is_transmit, label_of
from assistant.jev import Jev, JevError, Table
from assistant.pages import Page

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
STALE = re.compile(r"\b(detached|page_changed|target_changed|unknown_ref|stale)\b")
NEXT_STEP_GOAL = "Go to the next step of this application."


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


def resume_input(p: Page, resume_step: bool) -> str | None:
    """C25: the file input labelled resume/CV; a lone file input on a resume step; several unlabelled → blocker."""
    files = [e for e in p.elements if e.role == "file"]
    if not files:
        # No file input: an "Upload resume" button that opens the file chooser (LinkedIn Easy Apply, aa6).
        triggers = [e for e in p.elements if e.role == "button" and pages.UPLOAD_TRIGGER_RE.search(e.name)
                    and not is_transmit(e.name)]
        return triggers[0].ref if resume_step and len(triggers) == 1 else None
    named = [e for e in files if any(pages.RESUME_RE.search(x) for x in (e.name, e.label, e.context))]
    if named:
        return named[0].ref
    if len(files) == 1 and resume_step:
        return files[0].ref
    if len(files) > 1 and resume_step:
        raise NeedsAttention("broken_form", "several file inputs and none is labelled resume/CV")
    return None


def _required_file_labels(p: Page) -> list[str]:
    return [i[0] for i in p.required_empty.get("items", []) if len(i) > 1 and i[1] == "file"]


def upload_resume(ctx: JobCtx, p: Page) -> bool:
    """Upload the tailored resume on this page if it has a resume input. True if the resume is now in place."""
    ref = resume_input(p, bool(pages.RESUME_RE.search(p.text)))
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
    """A question the resume upload answers: about the resume/CV, or choosing among resume files (LinkedIn's
    "Resume*" cards, live 2026-09-23)."""
    return bool(pages.RESUME_RE.search(q.question)) and not re.search(r"cover", q.question, re.I) \
        or any(pages.RESUME_FILE_RE.search(o or "") for o in (q.options or []))


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


def _holds(q: Question, p: Page, before_text: str) -> bool:
    by_ref = {e.ref: e for e in p.elements}
    if q.option_ref:
        opt = by_ref.get(q.option_ref)
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
    for q in pa.questions:
        if q.kind == "file" and q.required and re.search(r"cover", q.question, re.I) \
                and not pages.RESUME_RE.search(q.question):
            raise NeedsAttention("broken_form", f"required cover-letter file: {q.question!r}")   # C15
    for label in _required_file_labels(p):
        if re.search(r"cover", label, re.I) and not pages.RESUME_RE.search(label):
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
    data += [{"page": ctx.pages + 1, "url": p.url, "question": q.question, "answer": q.answer, "source": q.source,
              "quote": q.quote, "relies_on": q.relies_on, "note": q.note} for q in pa.questions]
    ctx.answers_log.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ new tabs after a click

ENTRY_TAB_WAIT = 8          # s: LinkedIn may open the company site a few seconds after the Apply click
CLICK_TAB_WAIT = 3          # s: after an advance click (an interstitial "Continue" can open the site too)


def page_shape(p: Page) -> tuple:
    """What a click is expected to change: the page, its real fields and its advance buttons."""
    return (urlparse(p.url)._replace(query="", fragment="").geturl(),
            tuple(sorted((e.role, e.name) for e in pages.real_fields(p))),
            tuple(sorted(e.name for e in pages.advance_buttons(p))))


def follow_new_tab(ctx: JobCtx, known: set[str], seconds: int, before: Page | None = None) -> bool:
    """After a click: if a new tab appears within `seconds`, hand the job off to it (and close the tab we
    were on, unless it is the user's). Stops waiting as soon as this tab has visibly changed from `before`
    (the page at the click) and shows a real form field or an advance button, or shows confirmation text.
    LinkedIn opens its Easy Apply dialog a moment after the click, over a page that already has a switch
    (live 2026-09-23), so "any field" is not enough. Returns True if the job moved to a new tab."""
    shape = page_shape(before) if before is not None else None
    for i in range(seconds + 1):
        if ctx.book.handles() - known - ctx.baseline:
            ctx.book.hand_off(ctx.session, ctx.baseline, known)
            return True
        if i == seconds:
            break
        p = pages.read_page(ctx.browser, ctx.session)
        if pages.is_alarm(p):
            return False
        if (pages.real_fields(p) or pages.has_advance(p)) and (shape is None or page_shape(p) != shape):
            return False
        ctx.sleep(1.0)
    return False


# ------------------------------------------------------------------ advance and final (§4.3, §4.4)

def advance(ctx: JobCtx, p: Page) -> str:
    """'moved' | 'final' | 'stuck'."""
    ctx.stage = "advance"
    before = _field_fingerprint(p)
    known = ctx.book.handles()
    btns = pages.advance_buttons(p)
    if len(btns) == 1:
        out = ctx.browser.act([{"op": "click", "ref": btns[0].ref}, {"op": "wait_for_load", "timeout_ms": 20000}],
                              ctx.session, p.table, stop_on_error=False)
        if STALE.search(out.split("\n", 2)[1] if "\n" in out else out):     # re-rendered: re-map by name, once
            fresh = ctx.read()
            again = [b for b in pages.advance_buttons(fresh) if b.name == btns[0].name]
            if len(again) == 1:
                ctx.browser.act([{"op": "click", "ref": again[0].ref}, {"op": "wait_for_load", "timeout_ms": 20000}],
                                ctx.session, fresh.table, stop_on_error=False)
    else:
        g = parse_goal(ctx.browser.goal(NEXT_STEP_GOAL, ctx.session, max_steps=4))
        check_goal_alarm(g, p.table)
        blocked = blocked_transmit(g, p.table)
        if blocked and is_strong_transmit(blocked):
            return "final"
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
    """From the application's first page to the parked final step. Raises NeedsAttention / StopRun /
    RestartFromEntry. Never clicks a transmit label (the guard would refuse)."""
    filled, gate_retry, iframe_hops, guard_rounds, final_hint = False, False, 0, 0, False
    try:
        while True:
            guard_rounds += 1
            if guard_rounds > ctx.max_pages * 4:
                raise NeedsAttention("broken_form", "the page loop is not making progress")
            p = ctx.read()
            v = pages.classify(p)
            if v.kind == "alarm":
                raise StopRun(f"ALARM: {v.detail!r} on {p.url}")
            if v.kind == "blocker":
                _attempt2(ctx, p, v.detail.split(":", 1)[0], v.detail)
                filled = False
                continue
            if v.kind in ("google", "google_wall"):
                from assistant.google_signin import sign_in
                ctx.stage = "google sign-in"
                sign_in(ctx.browser, ctx.session, ctx.book, ctx.google_email, ctx.baseline)
                continue
            if v.kind == "cookie":
                ctx.browser.act([{"op": "click", "ref": v.ref}], ctx.session, p.table, stop_on_error=False)
                continue
            if v.kind == "ats_entry":
                ctx.stage = "entry"
                known = ctx.book.handles()
                try:
                    entry_click(ctx.browser, ctx.session, p.table, reread=lambda: ctx.read().table)
                except EntryRefused as exc:
                    raise NeedsAttention("load_failure", f"company-site Apply: {exc}") from exc
                follow_new_tab(ctx, known, ENTRY_TAB_WAIT, p)
                continue
            if v.kind == "iframe":
                iframe_hops += 1
                if iframe_hops > 2:
                    raise NeedsAttention("load_failure", "the embedded application form did not load")
                ctx.browser.open(v.detail, ctx.session)
                continue
            if pages.has_fields(p) and not filled:
                try:
                    fill_page(ctx, p)
                except _Refill as r:
                    if r.url:
                        ctx.browser.open(r.url, ctx.session)
                    else:
                        _reload(ctx)
                    continue
                filled = True
                continue
            if pages.is_final(p) or (final_hint and pages.has_transmit(p)):
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
            guest_click(ctx.browser, ctx.session, p.table)
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
