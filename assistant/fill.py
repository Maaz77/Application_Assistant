"""The page loop of one job (§5 from the LinkedIn posting to the gate) and the fill mapping of §7.

The browser agent (the package's browser_goal) finds its way: from the posting to the application form past
pop-ups, cookie banners and job pages, and from one form step to the next. Code keeps what must not be left to a
model: the answers (llm_inference.py, checked against the files), the resume upload, the blockers, the final-step gate,
and the one never-submit rule (jev.never_click): no "Submit" click, and no "Apply" click once the form is being
filled. A refused click is never made; in the form it means the last step."""
from __future__ import annotations

import time
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

from assistant import decide, pages, tabs
from assistant.llm_inference import (LONG_TEXT, LLMInferenceError, PageAnswers, Question, judge_questions,
                               uncovered_optional, uncovered_required)
from assistant.decide import DecisionError
from assistant.blockers import Attempts, NeedsAttention, OpenQuestion, Parked, RestartFromEntry, StopRun
from assistant import jev
from assistant.guard import label_of
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
    """§4.6: a click the never-submit rule refuses that went through anyway ends the run (the rule is not in place)."""
    for label, result in goal_clicks(g, table):
        if result == "ok" and jev.never_click(label):
            raise StopRun(f"ALARM: a goal clicked {label!r}")


def blocked_click(g: GoalResult, table: Table) -> str | None:
    """The label the server refused (needs_confirmation: jev.never_click), if any."""
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


def resume_input(p: Page, engine_pick: str | None = None) -> str | None:
    """The control that uploads the resume (a file input, or LinkedIn's "Upload resume" button that opens the file
    chooser, aa6), as Jev picks it. Several uploads and Jev unsure: Jev is asked again over its two likeliest picks;
    still unsure, the LLM inference's pick (`engine_pick`, an LLM's ref for the resume question) settles it when it is
    one of those two. Only when neither settles it is it a blocker (C25)."""
    j = pages.judge(p)
    if j.resume_ref is None:
        return None
    files = [e for e in p.elements if e.role == "file"]
    if len(files) <= 1 or j.resume_confidence >= RESUME_CONFIDENCE:
        return j.resume_ref
    probs = j.resume_probabilities or {j.resume_ref: j.resume_confidence}
    again = decide.narrow("page", pages.page_state(p), "resume_input", pages.page_questions(p)["resume_input"], probs)
    if again.choice not in (None, "none") and (again.confidence or 0.0) >= RESUME_CONFIDENCE:
        return again.choice
    finalists = set(sorted(probs, key=probs.get, reverse=True)[:2]) - {"none"}
    if engine_pick in finalists:
        return engine_pick
    raise NeedsAttention("broken_form", "several file inputs and it is unclear which one takes the resume")


def _engine_resume_pick(pa: PageAnswers | None, p: Page) -> str | None:
    """The LLM inference's ref for the resume upload, when it names exactly one of the page's file inputs."""
    files = {e.ref for e in p.elements if e.role == "file"}
    picks = {q.ref for q in (pa.questions if pa else []) if q.kind == "file" and q.resume_upload and q.ref in files}
    return picks.pop() if len(picks) == 1 else None


def upload_resume(ctx: JobCtx, p: Page, pa: PageAnswers | None = None) -> bool:
    """Upload the tailored resume on this page if it has a resume input. True if the resume is now in place."""
    ref = resume_input(p, _engine_resume_pick(pa, p))
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
    """Answers still to set: non-null, not wrapper-typed long text, differing from the current value. Jev plans how
    each goes in (plan_fill); the code sets most, a page goal the rest."""
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


OPTION_REF_RE = re.compile(r"^(e\d+):\d+$")     # an option of a native <select>, as the table lists it ("e31:3")


TOGGLES = {"radio", "checkbox", "switch"}
LISTS = {"combobox", "listbox"}
FILL_OPS = {
    "type": "Type the answer into the field: a text box, a text area or a number field.",
    "select": "Choose the answer in a dropdown list whose options are listed on the page.",
    "check": "Check the radio button or checkbox that is the answer.",
    "widget": "Anything else, which needs clicks: a dropdown with no listed options, a date picker, an autocomplete.",
}
MAX_CHOICES = 250          # Jev takes up to 255 options per choice


def _field_choices(p: Page) -> dict[str, str]:
    return dict(list({e.ref: (e.name or e.role)[:90] for e in p.elements
                      if e.role in TYPEABLE | LISTS}.items())[:MAX_CHOICES])


def _option_choices(p: Page) -> dict[str, str]:
    out = {}
    for e in p.elements:
        if e.role in TOGGLES:
            out[e.ref] = f"{e.name[:70]}: {e.label[:40]}" if e.label and e.label != e.name else e.name[:90]
        elif e.role in LISTS:
            out.update({o.ref: f"{e.name[:70]}: {o.label[:40]}" for o in e.options if o.ref and o.label})
    return dict(list(out.items())[:MAX_CHOICES])


def plan_fill(items: list[Question], p: Page) -> list[dict | None]:
    """How and where each answer goes in, as Jev decides, in one round (jev-ultrafast's speculative fan-out: the
    operation, and a target for each kind of operation). The code then only checks that the pick can be carried out
    and acts with the LLM inference's own text, so no rule chooses by the kind of control (it replaces direct_op,
    2026-09-24). None: a page goal sets that answer (custom widgets)."""
    if not items:
        return []
    fields, options = _field_choices(p), _option_choices(p)
    qs: dict[str, dict] = {}
    for i, q in enumerate(items):
        about = {"question": q.question, "answer": q.answer}
        about.update({k: v for k, v in (("llm_inference_ref", q.ref), ("llm_inference_option_ref", q.option_ref)) if v})
        qs[f"op_{i}"] = decide.choice({**about, "ask": "How does `answer` go into the form for `question`?"}, FILL_OPS)
        if fields:
            qs[f"field_{i}"] = decide.choice({**about, "ask": "Which field is the one for `question`?"},
                                             {**fields, "none": "None of these fields."})
        if options:
            qs[f"option_{i}"] = decide.choice({**about, "ask": "Which option is `answer` for `question`?"},
                                              {**options, "none": "None of these options."})
    a = decide.current().ask("fill", pages.page_state(p), qs)
    by_ref = {e.ref: e for e in p.elements}

    def chosen(key: str) -> str | None:
        return a[key].choice if key in a and a[key].choice != "none" else None
    return [_carry_out(q, a[f"op_{i}"].choice, chosen(f"field_{i}"), chosen(f"option_{i}"), by_ref)
            for i, q in enumerate(items)]


def _carry_out(q: Question, how: str | None, field: str | None, option: str | None, by_ref: dict) -> dict | None:
    """The browser op for Jev's pick, when the page allows it; else None (a page goal)."""
    f = by_ref.get(field or "")
    if how == "type" and f is not None and f.role in TYPEABLE and f.editable:
        return {"op": "type", "ref": f.ref, "text": q.answer, "clear": True, "submit": False}
    if how == "select":
        m = OPTION_REF_RE.match(option or "")
        base = by_ref.get(m.group(1)) if m else (f if f is not None and f.options else None)
        if base is not None and base.options:
            label = next((o.label for o in base.options if o.ref == option and o.label), None) or next(
                (o.label for o in base.options if pages.norm_label(o.label) == pages.norm_label(q.answer)), None)
            if label:
                return {"op": "select", "ref": base.ref, "value": label}
    if how == "check":
        t = by_ref.get(option or "")
        if t is not None and t.role in TOGGLES:
            return {"op": "toggle", "ref": t.ref, "state": True}
    return None


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
        raise NeedsAttention("llm_inference", "page goal could not run: no decision-model key")
    # Anything else (text helper returned no value, a provider hiccup, a timeout) is a failed goal:
    # read-back decides what was set, and its retry/blocker rules apply (B5).
    check_goal_alarm(g, p.table)
    return g


HELD = {"holds": "The answer, maybe formatted by the page: other spacing or punctuation, a country code or area code "
                 "added in front of the same number, other letter case.",
        "different": "A different answer.",
        "empty": "Nothing, or a placeholder such as 'Select…'."}


def _held_question(q: Question) -> dict:
    field = {"question": q.question, "answer": q.answer}
    field.update({k: v for k, v in (("ref", q.ref), ("option_ref", q.option_ref)) if v})
    return decide.choice({**field, "ask": "What does the form's field for `question` hold now, compared with `answer`?"},
                         HELD)


def mismatches(items: list[Question], p: Page) -> list[Question]:
    """Read-back by Jev on the fresh page: the questions whose field does not hold the answer (Jev's top choice is not
    "holds"). It replaces rules per kind of control (option refs, redrawn radio groups, custom comboboxes that show the
    pick only as page text). Toast's Greenhouse form, live 2026-09-24: the select held "No" while those rules said it
    did not, and the phone widget showed the typed "351 935 8813" as "+393519358813". As a yes/no question Jev put the
    phone at 0.36–0.54; as this choice it picks "holds" at 0.92–0.95."""
    if not items:
        return []
    a = decide.current().ask("readback", pages.page_state(p),
                             {f"held_{i}": _held_question(q) for i, q in enumerate(items)})
    return [q for i, q in enumerate(items) if a[f"held_{i}"].choice != "holds"]


def fill_page(ctx: JobCtx, p: Page) -> None:
    ctx.stage = "fill"
    jev.FORM.started = True          # from here on "Apply" is never clicked either (jev.never_click)
    try:
        pa = ctx.answer_fn(p)
    except LLMInferenceError as exc:
        raise NeedsAttention("llm_inference", str(exc)) from exc
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
    resume_in_place = upload_resume(ctx, p, pa)                            # 1
    for q in pa.questions:                                                       # 2
        if q.answer is not None and _wrapper_types(q):
            type_long(ctx, p, q)
            if q.source == "generated":
                ctx.generated.append((q.question, q.answer))
    for q in pa.questions:
        if q.source == "linkedin-prefill" and q.answer is not None:
            ctx.prefills.append((q.question, q.answer))
    items = [q for q in _goal_items(pa, p) if not _other_resume_card(q, p, ctx)]
    plan = plan_fill(items, p)                                                   # 3: Jev picks how and where
    direct = [op for op in plan if op]
    goal_items = [q for q, op in zip(items, plan) if op is None]
    if direct:                                                                   # 3a: the code acts
        ctx.browser.act(direct, ctx.session, p.table, stop_on_error=False)
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
        bad = mismatches(items, q)
        if bad:
            # One retry, planned again on the fresh page: a toggle or select again (idempotent); everything else,
            # including a typed value the page rewrote or refused (masks, date widgets), gets one goal for that
            # field alone.
            replan = plan_fill(bad, q)
            again = [op for op in replan if op and op["op"] in ("toggle", "select")]
            if again:
                ctx.browser.act(again, ctx.session, q.table, stop_on_error=False)
            for b, op in zip(bad, replan):
                if not (op and op["op"] in ("toggle", "select")):
                    run_goal(ctx, [b], q)
                    q = ctx.read()
            q = ctx.read()
            bad = mismatches(bad, q)
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
              "ref": q.ref, "option_ref": q.option_ref, "source": q.source, "quote": q.quote, "relies_on": q.relies_on, "note": q.note} for q in pa.questions]
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
    or "stuck". Before the form is being filled, "Apply" / "Easy Apply" is the agent's own click (jev.never_click
    refuses only "Submit" here); a refused Submit on a page with fields means the form is here."""
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
    blocked = blocked_click(g, p.table)
    if blocked:
        if pages.has_fields(ctx.read()):
            return "form"
        raise NeedsAttention("navigation", f"the browser agent chose {blocked!r}, which the program never clicks")
    # An Apply may open the company site in a new tab, or LinkedIn's dialog, a few seconds later.
    if follow_new_tab(ctx, known, ENTRY_TAB_WAIT if acted(g) else 0, p):
        return "moved"
    if any(STALE.search(line) for line in g.trace):
        return "moved"               # the page changed under the click (Genesys re-rendered its posting): look again
    if g.done or any((m := TRACE_RE.match(line)) and m.group(1) in ("TYPE_TEXT", "SELECT", "TOGGLE")
                     for line in g.trace):
        return "form"                                         # it says so, or it began to answer the form
    return "moved" if acted(g) else "stuck"


def advance(ctx: JobCtx, p: Page) -> str:
    """'moved' | 'final' | 'stuck'. The agent clicks this step's Next / Continue / Review (NEXT_STEP_GOAL), one
    action per goal so it can never run past a step we have not filled; a goal that only scrolled or waited is
    asked again. A click refused by the never-submit rule (Submit, or Apply while filling) means this is the last
    step."""
    ctx.stage = "advance"
    before = _field_fingerprint(p)
    known = ctx.book.handles()
    for _ in range(ADVANCE_TRIES):
        g = parse_goal(ctx.browser.goal(NEXT_STEP_GOAL, ctx.session, max_steps=1))
        check_goal_alarm(g, p.table)
        if blocked_click(g, p.table):
            return "final"
        if acted(g) or g.status in ("done", "blocked"):
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
    jev.FORM.started = False         # a new job starts at its posting, where "Apply" starts the application
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
        why = guest_refused(p)
        if why:
            raise NeedsAttention("signup", f"{detail}; guest link not usable: {why}")
        ctx.browser.act([{"op": "click", "ref": pages.guest_link(p).ref},
                         {"op": "wait_for_load", "timeout_ms": 20000}], ctx.session, p.table, stop_on_error=False)
    elif cls == "broken_form":
        _reload(ctx)
    elif cls == "load_failure":
        raise RestartFromEntry(detail)


def guest_refused(p: Page) -> str | None:
    """Why the guest link of a sign-up wall is not clicked (user decision 2026-09-23), or None: it must be a
    sign-up wall (Jev) with an "apply without an account" / "continue as guest" link."""
    j = pages.judge(p)
    if not (j.kind == "account_wall" and j.account == "create_account"):
        return "not a sign-up wall"
    if pages.guest_link(p) is None:
        return "no 'apply without an account' / 'continue as guest' link"
    return None


def _reload(ctx: JobCtx) -> None:
    ctx.browser.act([{"op": "reload"}, {"op": "wait_for_load", "timeout_ms": 20000}], ctx.session,
                      Table(url=""), stop_on_error=False)


def _park(ctx: JobCtx) -> Parked:
    p = ctx.last
    shot = ""
    if ctx.shots_dir:
        ctx.shots_dir.mkdir(parents=True, exist_ok=True)
        shot = str(ctx.shots_dir / "screenshot.jpg")
        ctx.browser.act([{"op": "screenshot", "path": shot, "full": True}], ctx.session, p.table,
                          observe_after=False, stop_on_error=False)
    return Parked(url=p.url, title=p.title, pages=ctx.pages + 1, generated=ctx.generated, prefills=ctx.prefills,
                  optional_empty=ctx.optional_empty, screenshot=shot)
