"""The page loop of one job (§5 from the LinkedIn posting to the gate) and the fill mapping of §7.

Navigation is deterministic (navigate.py): from the LinkedIn posting into the Easy Apply dialog, and from one
step to the next by its allowlisted Next/Review button — no browser-agent goal. Code keeps what must not be left
to a model: the answers (llm_inference.py, checked against the files), the resume upload, the final-step gate, and
the one never-submit rule (guard.never_click_element): no submit-labelled click, no structural submit, no "Apply"
once the form is being filled. A refused click is never made; in the form it means the last step."""
from __future__ import annotations

import time
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

from assistant import decide, navigate, pages, tabs, guard
from assistant.llm_inference import (LONG_TEXT, LLMInferenceError, PageAnswers, Question, judge_questions,
                               uncovered_optional, uncovered_required)
from assistant.decide import DecisionError
from assistant.blockers import Attempts, NeedsAttention, OpenQuestion, Parked, RestartFromEntry, StopRun
from assistant.guard import label_of
from assistant.browser import Browser, DriverError, DriverTimeout, Table
from assistant.pages import Page

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
STALE = re.compile(r"\b(detached|page_changed|target_changed|unknown_ref|stale)\b")


# ------------------------------------------------------------------ job context

AnswerFn = Callable[[Page], PageAnswers]


@dataclass
class JobCtx:
    browser: Browser
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
    jev_budget: int = 40
    _jev_start: int = 0

    def read(self) -> Page:
        """The current page. Read directly, without pages.settle: settle calls judge()/kev on every read,
        and the LinkedIn Easy Apply path must not (navigate.py does its own bounded waits)."""
        self.last = pages.read_page(self.browser, self.session)
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
    """The control that uploads the resume. Code rules pick it; when unsure among several file inputs,
    the LLM inference's ref (`engine_pick`) settles it. Only when neither settles it is it a blocker."""
    j = pages.judge(p)
    if j.resume_ref is None:
        return None
    files = [e for e in p.elements if e.role == "file"]
    if len(files) <= 1 or j.resume_confidence >= RESUME_CONFIDENCE:
        return j.resume_ref
    finalists = set(sorted(j.resume_probabilities, key=j.resume_probabilities.get, reverse=True)[:2]) - {"none"}
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


def _code_how(q: Question, by_ref: dict) -> str | None:
    """Fill operation from element role. None when refs are missing or element unrecognised."""
    opt = by_ref.get(q.option_ref or "")
    if opt is not None and opt.role in TOGGLES:
        return "check"
    if q.option_ref and OPTION_REF_RE.match(q.option_ref):
        return "select"
    el = by_ref.get(q.ref or "")
    if el is not None:
        if el.role in LISTS:
            return "select"
        if el.role in TYPEABLE and el.editable:
            return "type"
    return None


def plan_fill(items: list[Question], p: Page) -> list[dict | None]:
    """Code decides how each answer goes in from element roles and LLM refs.
    Jev asked only for option mapping (answer != visible option) or items without refs."""
    if not items:
        return []
    by_ref = {e.ref: e for e in p.elements}
    result: list[dict | None] = [None] * len(items)
    need_option_map: list[int] = []
    need_full: list[int] = []

    for i, q in enumerate(items):
        how = _code_how(q, by_ref)
        if how is None:
            need_full.append(i)
            continue
        op = _carry_out(q, how, q.ref, q.option_ref, by_ref)
        if op is not None:
            result[i] = op
        elif how == "select":
            need_option_map.append(i)

    qs: dict[str, dict] = {}
    fields_map, options_map = _field_choices(p), _option_choices(p)
    for idx in need_option_map:
        q = items[idx]
        about = {"question": q.question, "answer": q.answer}
        if options_map:
            qs[f"option_{idx}"] = decide.choice(
                {**about, "ask": "Which option is `answer` for `question`?"},
                {**options_map, "none": "None of these options."})
    for idx in need_full:
        q = items[idx]
        about = {"question": q.question, "answer": q.answer}
        about.update({k: v for k, v in (("llm_inference_ref", q.ref), ("llm_inference_option_ref", q.option_ref)) if v})
        qs[f"op_{idx}"] = decide.choice({**about, "ask": "How does `answer` go into the form for `question`?"}, FILL_OPS)
        if fields_map:
            qs[f"field_{idx}"] = decide.choice({**about, "ask": "Which field is the one for `question`?"},
                                               {**fields_map, "none": "None of these fields."})
        if options_map:
            qs[f"option_{idx}"] = decide.choice({**about, "ask": "Which option is `answer` for `question`?"},
                                                {**options_map, "none": "None of these options."})
    if not qs:
        return result

    a = decide.current().ask("fill", pages.page_state(p), qs)

    def chosen(key: str) -> str | None:
        return a[key].choice if key in a and a[key].choice != "none" else None

    for idx in need_option_map:
        picked = chosen(f"option_{idx}")
        if picked:
            q = items[idx]
            op = _carry_out(q, "select", q.ref, picked, by_ref)
            if op is None:
                op = _carry_out(q, "check", q.ref, picked, by_ref)
            result[idx] = op
    for idx in need_full:
        q = items[idx]
        result[idx] = _carry_out(q, a[f"op_{idx}"].choice, chosen(f"field_{idx}"), chosen(f"option_{idx}"), by_ref)

    return result


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


_NON_DIGIT = re.compile(r"\D")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _fuzzy_holds(answer: str, held: str) -> bool:
    """Does `held` contain the same answer after page reformatting?  Handles phone country-code prepend,
    whitespace/punctuation/case differences.  No model call — the Linda AI false-flag (see DISCOVERY.md)
    showed kev-0.8b cannot reliably answer this; code is both faster and more correct."""
    na, nh = pages.norm_label(answer), pages.norm_label(held)
    if na == nh:
        return True
    da, dh = _NON_DIGIT.sub("", answer), _NON_DIGIT.sub("", held)
    if da and dh and min(len(da), len(dh)) >= 7 and (dh.endswith(da) or da.endswith(dh)):
        return True
    sa, sh = _NON_ALNUM.sub("", na), _NON_ALNUM.sub("", nh)
    return sa == sh and sa != ""


def mismatches(items: list[Question], p: Page) -> list[Question]:
    """Questions whose field does not hold the answer. All code, no model call: exact match, then fuzzy
    (phone digits suffix, stripped punctuation). A toggle holds when checked; a text field when its shown
    value fuzzy-matches the answer."""
    if not items:
        return []
    by_ref = {e.ref: e for e in p.elements}

    def holds(q: Question) -> bool:
        opt = by_ref.get(q.option_ref or "")
        if opt is not None:
            return bool(opt.checked)
        e = by_ref.get(q.ref or "")
        if e is None:
            return False
        held = (e.current or e.value or "").strip()
        return bool(held) and _fuzzy_holds(q.answer or "", held)

    return [q for q in items if not holds(q)]


def fill_page(ctx: JobCtx, p: Page) -> None:
    ctx.stage = "fill"
    guard.FORM.started = True         # from here on "Apply" is never clicked either (guard.never_click_element)
    try:
        pa = ctx.answer_fn(p)
    except LLMInferenceError as exc:
        raise NeedsAttention("llm_inference", str(exc)) from exc
    log_answers(ctx, p, pa)
    judge_questions(pa, p)
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
    widget_items = [q for q, op in zip(items, plan) if op is None]               # not click/type/select/toggle/upload
    if direct:                                                                   # 3a: the code acts
        ctx.browser.act(direct, ctx.session, p.table, stop_on_error=False)
    # P2: the page-goal fallback is gone. A widget answer (a custom date picker, autocomplete, …) stays empty
    # and is listed in the note; a REQUIRED widget is a blocker (P4 adds the widget handlers).
    for q in widget_items:
        if q.required and not _wrapper_types(q):
            raise NeedsAttention("broken_form", f"widget not supported yet: {q.question!r}")
        ctx.optional_empty.append(_open_q(q))
    ctx.filled_count += len(direct)
    ctx.optional_empty += [_open_q(q) for q in uncovered_optional(pa)]
    missing = [q for q in uncovered_required(pa) if not (resume_in_place and is_resume_question(q))]
    if missing:                                                                  # D2
        raise NeedsAttention("unanswered", f"{len(missing)} required question(s) have no answer in the files",
                             questions=[_open_q(q) for q in missing])
    if items:                                                                    # read-back
        q = ctx.read()
        bad = mismatches(items, q)
        if bad:
            # One retry, planned again on the fresh page. Any field the plan can carry out (type/toggle/select)
            # is re-acted; a field the page keeps refusing (a mask, a widget) is a broken_form attempt.
            replan = plan_fill(bad, q)
            again = [op for op in replan if op]
            if again:
                ctx.browser.act(again, ctx.session, q.table, stop_on_error=False)
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


def _check_jev_budget(ctx: JobCtx) -> None:
    d = decide._current
    if d is None:
        return
    used = d.calls - ctx._jev_start
    if used > ctx.jev_budget:
        raise NeedsAttention("decision_budget",
                             f"decision request count ({used}) exceeds per-job budget ({ctx.jev_budget})")


# ------------------------------------------------------------------ the loop (deterministic, §5.1)

def run_pages(ctx: JobCtx) -> Parked:
    """From the LinkedIn posting to the parked final step, with no page-kind model call (navigate.py).

    Entry: click Easy Apply and wait for the dialog — or Needs Attention for a closed / already-applied
    posting or an external ATS. Form: fill each Easy Apply step, advance by its allowlisted Next / Review
    button, until the final step (only a refused Submit remains) → gate → park.
    Raises NeedsAttention / StopRun / RestartFromEntry. Never clicks a transmit label (the guard refuses)."""
    guard.FORM.started = False        # a new job starts at its posting, where "Apply" starts the application
    guard.FORM.final = False
    ctx._jev_start = decide._current.calls if decide._current else 0
    ctx.jev_budget = ctx.browser.cfg.jev.max_requests_per_job
    filled, gate_retry, entered, guard_rounds = False, False, False, 0
    try:
        while True:
            guard_rounds += 1
            if guard_rounds > ctx.max_pages * 4 + 12:
                raise NeedsAttention("broken_form", "the page loop is not making progress")
            p = ctx.read()
            if entered and pages.ALARM_RE.search(p.text):        # §4.6 safety floor, only after we have acted
                raise StopRun(f"ALARM: confirmation text on {p.url}")
            if not entered:
                if navigate.enter(ctx, p) == "form":             # raises closed/applied/external_ats/navigation
                    entered, filled = True, False
                continue                                         # a cookie was declined, or the dialog is opening
            if not navigate.dialog_is_open(p):
                ctx.attempts.fail("broken_form", "the Easy Apply dialog is not open")   # raises when no attempt left
                guard.FORM.started = guard.FORM.final = False       # re-entry from the posting: Apply is clickable again
                entered, filled = False, False
                ctx.browser.open(ctx.last.url, ctx.session)
                continue
            if navigate.dialog_fields(p) and not filled:
                _check_jev_budget(ctx)
                try:
                    fill_page(ctx, p)
                except _Refill as rf:
                    # broken_form attempt 2: reopen the posting and re-enter. Reset the fill stage, or the
                    # re-entry Easy Apply click is refused as "Apply once the form is being filled" (live
                    # 2026-09-29, Linda AI — that turned a phone-field broken_form into a bogus "navigation").
                    guard.FORM.started = guard.FORM.final = False
                    ctx.browser.open(rf.url or ctx.last.url, ctx.session)
                    entered, filled = False, False
                    continue
                filled = True
                continue
            if navigate.advance_button(p) is None and guard.looks_final(navigate.in_dialog(p)):
                guard.FORM.final = True
                ctx.stage = "final"
                why = pages.gate(p, ctx.resume_pdf.name, ctx.typed)
                if why is None:
                    break
                if gate_retry:
                    raise NeedsAttention("gate", why)
                gate_retry, filled, guard.FORM.final = True, False, False     # the refill types into fields
                continue
            step = navigate.advance(ctx, p)
            if step == "final":
                filled = False                     # the gate runs on the next read
                continue
            if step == "stuck":
                ctx.attempts.fail("broken_form", "the Easy Apply step did not advance")
                filled = False                     # attempt 2: refill this step, then advance again
                continue
            filled = False
            ctx.pages += 1
            if ctx.pages > ctx.max_pages:
                raise NeedsAttention("broken_form", f"no final step after {ctx.max_pages} steps")
    except NeedsAttention as exc:
        raise _where(exc, ctx)
    except DecisionError as exc:
        raise _where(NeedsAttention("decision", str(exc)), ctx) from exc
    except DriverTimeout as exc:
        raise StopRun(f"a browser call hung and was abandoned: {exc}")    # exit 3 (CLAUDE.md: hung browser call)
    except DriverError as exc:
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
