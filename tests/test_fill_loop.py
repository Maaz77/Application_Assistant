"""Loop unit tests on FakeMCP: fill, upload, typing, goal, read-back, advance, final step, gate, blockers."""
import pytest

from assistant.answers import PageAnswers
from assistant.blockers import NeedsAttention, StopRun
from assistant.fill import NEXT_STEP_GOAL, JobCtx, parse_goal, run_pages
from tests.fake_mcp import El, FakeBook, FakeMCP, FakePage

pytestmark = pytest.mark.unit
PDF = "Amin_Acme_Data_Engineer.pdf"


def Q(question, answer, *, kind="text", ref=None, option_ref=None, source="profile", required=True, **kw):
    return dict(id="q", question=question, kind=kind, ref=ref, option_ref=option_ref, options=kw.get("options"),
                required=required, answer=answer, source=source, quote=kw.get("quote", "x"),
                relies_on=kw.get("relies_on"))


def engine(by_title: dict):
    """Canned answer engine: page title → list of question dicts (refs resolved by name at call time)."""
    def fn(page):
        names = {e.name: e.ref for e in page.elements}
        qs = []
        for q in by_title.get(page.title, []):
            q = dict(q)
            if q["ref"] == "auto":
                q["ref"] = names.get(q["question"])
            if q["option_ref"] == "auto":
                q["option_ref"] = names.get(q["answer"])
            qs.append(q)
        return PageAnswers.model_validate({"questions": qs})
    return fn


def ctx_for(fake, answers, tmp_path, **kw):
    pdf = tmp_path / PDF
    pdf.write_bytes(b"%PDF")

    def no_sleep(_):
        return None
    return JobCtx(browser=fake, session="job", book=FakeBook(), resume_pdf=pdf, answer_fn=engine(answers),
                  baseline={"T0"}, answers_log=tmp_path / "answers.json", shots_dir=tmp_path / "shots",
                  folder="1_Acme", sleep=no_sleep, **kw)


def _goals(fake):
    """The page goals (the model setting fields); the agent's navigate and next-step goals are not counted."""
    return [a for n, a in fake.log if n == "browser_goal" and a["goal"].startswith("Set each field")]


def single_page():
    return {"p1": FakePage("https://acme.io/apply", "Apply", "Apply to Acme. Resume Why Acme? City", [
        El("textbox", "City", required=True),
        El("textbox", "Why Acme?", maxlength=500),
        El("file", "Resume", required=True),
        El("button", "Submit application")])}


def widget_page():
    """single_page plus a custom dropdown (no option list): only the page goal can set it."""
    site = single_page()
    site["p1"].els.insert(1, El("combobox", "Work model", required=True))
    return site


WIDGET_ANSWERS = {"Apply": [
    Q("City", "Milan", ref="auto"),
    Q("Work model", "Hybrid", kind="choice", ref="auto"),
    Q("Resume", None, kind="file", ref="auto", source=None)]}


SINGLE_ANSWERS = {"Apply": [
    Q("City", "Milan", ref="auto"),
    Q("Why Acme?", "I build perception systems.", kind="longtext", ref="auto", source="generated",
      relies_on=["fact"]),
    Q("Resume", None, kind="file", ref="auto", source=None)]}


def test_single_page_is_filled_and_parked(tmp_path):
    fake = FakeMCP(single_page(), "p1")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    els = {e.name: e.value for e in fake.page.els}
    assert els == {"City": "Milan", "Why Acme?": "I build perception systems.", "Resume": PDF,
                   "Submit application": ""}
    assert parked.generated == [("Why Acme?", "I build perception systems.")] and parked.pages == 1
    assert fake.sent == []
    ops = [op["op"] for name, a in fake.log if name == "browser_act" for op in a["ops"]]
    assert "upload" in ops and "type" in ops and "screenshot" in ops
    assert not any(op.get("confirm") for name, a in fake.log if name == "browser_act" for op in a["ops"])
    assert (tmp_path / "answers.json").exists()


def steps_site():
    return {
        "s1": FakePage("https://acme.io/a", "Contact", "Contact info", [
            El("textbox", "Phone", required=True), El("button", "Next", goto="s2")]),
        "s2": FakePage("https://acme.io/a", "Questions", "Questions. Do you need a visa?", [
            El("radio", "Yes", group="Do you need a visa?", required=True),
            El("radio", "No", group="Do you need a visa?"), El("button", "Review", goto="s3")]),
        "s3": FakePage("https://acme.io/a", "Review", "Review. Resume", [
            El("file", "Resume", required=True), El("button", "Submit application")]),
    }


STEPS_ANSWERS = {"Contact": [Q("Phone", "+39 333", ref="auto")],
                 "Questions": [Q("Do you need a visa?", "No", kind="choice", option_ref="auto",
                                 options=["Yes", "No"])],
                 "Review": [Q("Resume", None, kind="file", ref="auto", source=None)]}


def test_multi_step_form_walks_to_the_final_step(tmp_path):
    fake = FakeMCP(steps_site(), "s1")
    parked = run_pages(ctx_for(fake, STEPS_ANSWERS, tmp_path))
    assert parked.pages == 3 and fake.cur == "s3" and fake.sent == []
    no = next(e for e in fake.site["s2"].els if e.name == "No")
    assert no.checked is True


def test_uncovered_required_question_goes_to_needs_attention(tmp_path):
    answers = {"Apply": [Q("City", "Milan", ref="auto"), Q("Why Acme?", None, ref="auto", source=None, required=False),
                         Q("Salary", None, source=None)]}
    site = single_page()
    site["p1"].els.insert(0, El("textbox", "Salary", required=True))
    fake = FakeMCP(site, "p1")
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(fake, answers, tmp_path))
    na = ei.value
    assert na.cls == "unanswered" and [q.question for q in na.questions] == ["Salary"]
    assert [q.question for q in na.optional] == ["Why Acme?"]     # the other nulls are listed, not blocking
    assert na.filled >= 2 and fake.site["p1"].els[1].value == "Milan"       # the rest was filled first


def test_field_that_refuses_its_value_twice_is_a_blocker(tmp_path):
    site = single_page()
    site["p1"].els[0].refuse_typing = True              # City: direct typing does not stick …
    fake = FakeMCP(site, "p1", goal_refuses={"City"})   # … and the per-field goal cannot set it either
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert ei.value.cls == "broken_form" and "after 2 attempts" in ei.value.what
    reopens = [a for n, a in fake.log if n == "browser_open"]
    assert len(reopens) == 1                                           # attempt 2 reopened and refilled once


def test_goal_clicking_submit_stops_the_run(tmp_path):
    fake = FakeMCP(widget_page(), "p1", goal_clicks_submit=True)       # the widget needs a goal
    with pytest.raises(StopRun, match="ALARM"):
        run_pages(ctx_for(fake, WIDGET_ANSWERS, tmp_path))


def test_confirmation_text_after_advance_stops_the_run(tmp_path):
    site = steps_site()
    site["s2"] = FakePage("https://acme.io/done", "Thanks", "Thank you for applying to Acme!", [])
    fake = FakeMCP(site, "s1")
    with pytest.raises(StopRun, match="ALARM"):
        run_pages(ctx_for(fake, STEPS_ANSWERS, tmp_path))


def test_advance_that_changes_nothing_twice_is_a_blocker(tmp_path):
    site = {"s1": FakePage("https://acme.io/a", "Contact", "Contact info", [
        El("textbox", "Phone", required=True), El("button", "Next")])}       # Next goes nowhere
    fake = FakeMCP(site, "s1")
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(fake, STEPS_ANSWERS, tmp_path))
    assert ei.value.cls == "broken_form" and "advance changes nothing" in ei.value.what


def test_no_final_step_after_max_pages(tmp_path):
    site = {f"s{i}": FakePage(f"https://acme.io/{i}", "Contact", f"Step {i}", [
        El("textbox", f"Field {i}"), El("button", "Continue", goto=f"s{i + 1}")]) for i in range(1, 6)}
    fake = FakeMCP(site, "s1")
    with pytest.raises(NeedsAttention, match="no final step after 3 pages"):
        run_pages(ctx_for(fake, {}, tmp_path, max_pages=3))


def test_advance_is_the_agent_and_a_refused_submit_means_final(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply. Resume", [
        El("file", "Resume", required=True), El("button", "Submit application"),
        El("button", "Continue"), El("button", "Continue later")])}
    fake = FakeMCP(site, "p")
    parked = run_pages(ctx_for(fake, {"Apply": [Q("Resume", None, kind="file", ref="auto", source=None)]},
                                     tmp_path))
    assert parked.pages == 1 and fake.sent == []
    assert any(n == "browser_goal" and a["goal"] == NEXT_STEP_GOAL and a["max_steps"] == 1 for n, a in fake.log)


def test_captcha_gets_one_reload_then_needs_attention(tmp_path):
    site = {"c": FakePage("https://acme.io/a", "Just a moment", "Checking", [], captcha=True)}
    fake = FakeMCP(site, "c")
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(fake, {}, tmp_path))
    assert ei.value.cls == "captcha"


def test_required_cover_letter_file_is_a_blocker(tmp_path):
    site = single_page()
    site["p1"].els.insert(3, El("file", "Cover letter", required=True))
    fake = FakeMCP(site, "p1")
    with pytest.raises(NeedsAttention, match="cover-letter"):
        run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))


def test_unlabelled_file_inputs_on_a_resume_step_are_a_blocker(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Docs", "Upload your resume", [
        El("file", "Attachment 1"), El("file", "Attachment 2"), El("button", "Submit application")])}
    with pytest.raises(NeedsAttention, match="unclear which one takes the resume"):
        run_pages(ctx_for(FakeMCP(site, "p"), {}, tmp_path))


def test_gate_failure_is_retried_once(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply", [El("button", "Submit application")], form_here=True)}
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(FakeMCP(site, "p"), {}, tmp_path))
    assert ei.value.cls == "gate" and "resume file name" in ei.value.what


def test_cookie_banner_then_form(tmp_path):
    site = single_page()
    site["p1"].text = "We use cookies. " + site["p1"].text
    site["p1"].els += [El("button", "Accept all"), El("button", "Reject all")]
    fake = FakeMCP(site, "p1")

    def reject(e):                                   # clicking Reject all removes the banner
        if e.name == "Reject all":
            site["p1"].text = site["p1"].text.replace("We use cookies. ", "")
            site["p1"].els = [x for x in site["p1"].els if x.name not in ("Accept all", "Reject all")]
        return None
    orig = fake._click
    fake._click = lambda e, confirm=False: reject(e) or orig(e, confirm)
    run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    clicked = [fake.by_ref(op["ref"]) for n, a in fake.log if n == "browser_act" for op in a["ops"]
               if op["op"] == "click"]
    assert fake.sent == []


def test_parse_goal_formats():
    g = parse_goal("goal: x\nstatus: failed:needs_confirmation\nsteps: 1\ntrace:\n"
                   "  1. CLICK e9 Submit application → needs_confirmation (1ms model / 0ms browser)\n\n[delta#2] …")
    assert g.status == "failed:needs_confirmation" and len(g.trace) == 1
    assert parse_goal("no status here").status == "failed:no_status"


def test_answer_engine_failure_is_a_needs_attention_blocker(tmp_path):
    from assistant.answers import AnswerEngineError

    def broken(page):
        raise AnswerEngineError("answer engine output invalid")
    ctx = ctx_for(FakeMCP(single_page(), "p1"), {}, tmp_path)
    ctx.answer_fn = broken
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx)
    assert ei.value.cls == "answer_engine" and ei.value.stage == "fill"


def test_the_read_back_is_one_jev_question_per_field_on_the_fresh_page():
    """Jev judges whether each field holds its answer (2026-09-24): no rules per kind of control in the program."""
    from assistant import decide
    from assistant.answers import Question
    from assistant.fill import mismatches
    from assistant.jev import Element, Table
    from assistant.pages import Page
    asked = []

    class Jev:
        def ask(self, topic, state, questions):
            asked.append((topic, state, questions))
            return {k: decide.Answer("choice", choice="holds" if q["instructions"]["answer"] == "Hybrid" else "empty",
                                     confidence=0.9) for k, q in questions.items()}
    decide.use(Jev())
    hybrid = Question.model_validate(Q("Work model", "Hybrid", kind="choice", ref="e1"))
    phone = Question.model_validate(Q("Phone", "351 935 8813", ref="e2"))
    el = [Element(ref="e1", role="combobox", name="Work model"), Element(ref="e2", role="textbox", name="Phone")]
    after = Page(url="u", title="t", text="Work model Hybrid", table=Table(url="u", elements=el))
    assert mismatches([hybrid, phone], after) == [phone]
    topic, state, questions = asked[0]
    assert topic == "readback" and [c["ref"] for c in state["controls"]] == ["e1", "e2"]
    assert questions["held_0"]["instructions"]["answer"] == "Hybrid" and questions["held_0"]["instructions"]["ref"] == "e1"
    assert mismatches([], after) == [] and len(asked) == 1


def _wanderer(fake, times):
    orig, left = fake._browser_goal, {"n": times}

    def wandering(goal, **kw):                          # the model clicks Next instead of filling
        out = orig(goal, **kw)
        if goal.startswith("Set each field") and left["n"] > 0:
            left["n"] -= 1
            fake.cur = "s2"
        return out
    fake._browser_goal = wandering


def widget_steps():
    """steps_site with a custom dropdown on step 1, so step 1 needs a page goal."""
    site = steps_site()
    site["s1"].els.insert(1, El("combobox", "Work model", required=True))
    return site


WIDGET_STEPS_ANSWERS = dict(STEPS_ANSWERS, Contact=STEPS_ANSWERS["Contact"] + [
    Q("Work model", "Hybrid", kind="choice", ref="auto")])


def test_fill_goal_that_leaves_the_page_once_is_recovered(tmp_path):
    fake = FakeMCP(widget_steps(), "s1", reset_on_open=True)
    _wanderer(fake, 1)
    parked = run_pages(ctx_for(fake, WIDGET_STEPS_ANSWERS, tmp_path))
    assert parked.pages == 3 and fake.site["s1"].els[0].value == "+39 333"


def test_fill_goal_that_always_leaves_the_page_is_a_broken_form(tmp_path):
    fake = FakeMCP(widget_steps(), "s1", reset_on_open=True)
    _wanderer(fake, 99)
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(fake, WIDGET_STEPS_ANSWERS, tmp_path))
    assert ei.value.cls == "broken_form" and "moved off the page" in ei.value.what


def test_text_helper_failure_is_a_failed_goal_not_a_crash(tmp_path):
    fake = FakeMCP(widget_page(), "p1")
    calls = {"n": 0}
    orig = fake._browser_goal

    def flaky(goal, **kw):                              # the first page goal dies in the text helper (B5)
        if goal.startswith("Set each field"):
            calls["n"] += 1
            if calls["n"] == 1:
                return "turbo_unavailable: Text helper returned no usable value; nothing typed."
        return orig(goal, **kw)
    fake._browser_goal = flaky
    parked = run_pages(ctx_for(fake, WIDGET_ANSWERS, tmp_path))
    assert parked.pages == 1 and calls["n"] == 2                      # the read-back retry set the widget


def test_radios_and_native_selects_are_set_without_the_model(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply. Resume. Relocate? Country", [
        El("radio", "Yes", group="Relocate?", required=True), El("radio", "No", group="Relocate?"),
        El("combobox", "Country", options=["Italy", "Iran"], required=True),
        El("file", "Resume", required=True), El("button", "Submit application")])}
    fake = FakeMCP(site, "p")
    answers = {"Apply": [Q("Relocate?", "Yes", kind="choice", option_ref="auto", options=["Yes", "No"]),
                         Q("Country", "Iran", kind="choice", ref="auto", options=["Italy", "Iran"]),
                         Q("Resume", None, kind="file", ref="auto", source=None)]}
    run_pages(ctx_for(fake, answers, tmp_path))
    assert _goals(fake) == []                                          # no page goal: no model set a field
    ops = [op["op"] for n, a in fake.log if n == "browser_act" for op in a["ops"]]
    assert "toggle" in ops and "select" in ops
    assert site["p"].els[0].checked is True and site["p"].els[2].value == "Iran"


def test_follow_new_tab_does_not_wait_when_the_form_is_already_here(tmp_path):
    from assistant.fill import follow_new_tab
    slept = []
    ctx = ctx_for(FakeMCP(single_page(), "p1"), {}, tmp_path)
    ctx.sleep = slept.append
    assert follow_new_tab(ctx, ctx.book.handles(), 8) is False and slept == []      # Easy Apply: fields at once


def test_follow_new_tab_gives_up_after_its_budget(tmp_path):
    from assistant.fill import follow_new_tab
    site = {"j": FakePage("https://www.linkedin.com/jobs/view/1/", "Job", "Data Engineer", [El("button", "Save")])}
    slept = []
    ctx = ctx_for(FakeMCP(site, "j"), {}, tmp_path)
    ctx.sleep = slept.append
    assert follow_new_tab(ctx, ctx.book.handles(), 3) is False and slept == [1.0, 1.0, 1.0]


def test_follow_new_tab_waits_for_the_page_to_change_not_just_for_a_field(tmp_path):
    """LinkedIn: the job page already has a field when Easy Apply is clicked; the dialog comes a moment later."""
    from assistant.fill import follow_new_tab
    site = {"job": FakePage("https://www.linkedin.com/jobs/view/1/", "Job", "Data Engineer", [
                El("textbox", "Notes to self"), El("button", "Easy Apply")]),
            "dlg": FakePage("https://www.linkedin.com/jobs/view/1/", "Job", "Apply to Acme", [
                El("textbox", "Mobile phone number*"), El("button", "Continue to next step")])}
    fake = FakeMCP(site, "job")
    reads = {"n": 0}
    orig = fake._browser_observe

    def observe(**kw):                            # the dialog appears on the third read
        reads["n"] += 1
        if reads["n"] == 3:
            fake.cur = "dlg"
        return orig(**kw)
    fake._browser_observe = observe
    ctx = ctx_for(fake, {}, tmp_path)
    slept = []
    ctx.sleep = slept.append
    before = __import__("assistant.pages", fromlist=["read_page"]).read_page(fake, "job")
    reads["n"] = 0
    assert follow_new_tab(ctx, ctx.book.handles(), 8, before) is False
    assert fake.cur == "dlg" and slept == [1.0, 1.0]                             # waited through the unchanged reads


def test_an_engine_answer_for_an_older_resume_card_is_dropped(tmp_path):
    from assistant.answers import Question
    from assistant.fill import _other_resume_card, resume_input
    site = {"r": FakePage("https://www.linkedin.com/jobs/view/1/", "Apply", "Resume* Select or upload a resume", [
        El("radio", "Amin_Old-Company_Old-Role.pdf", checked=True), El("radio", PDF, checked=False),
        El("button", "Upload resume"), El("button", "Next")])}
    fake = FakeMCP(site, "r")
    ctx = ctx_for(fake, {}, tmp_path)
    p = __import__("assistant.pages", fromlist=["read_page"]).read_page(fake, "r")
    old, ours = p.elements[0], p.elements[1]
    pick = lambda ref: Question.model_validate(Q("Resume", "x", kind="choice", option_ref=ref))
    assert _other_resume_card(pick(old.ref), p, ctx) and not _other_resume_card(pick(ours.ref), p, ctx)
    assert resume_input(p) == p.elements[2].ref                           # the "Upload resume" trigger


def test_upload_on_a_transmit_label_is_refused():
    from assistant.guard import GuardError, check
    from assistant.jev import Element, Table
    t = Table(url="u", elements=[Element(ref="e1", role="button", name="Submit application"),
                                 Element(ref="e2", role="button", name="Upload resume")])
    with pytest.raises(GuardError):
        check({"op": "upload", "ref": "e1", "path": "/tmp/x.pdf"}, t)
    check({"op": "upload", "ref": "e2", "path": "/tmp/x.pdf"}, t)



# ------------------------------------------------------------------ direct typing (user decision, 2026-09-23)

def test_short_text_answers_are_typed_without_the_model(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply. Resume", [
        El("textbox", "Full name", required=True), El("textbox", "Email", required=True),
        El("spinbutton", "Years of experience"), El("file", "Resume", required=True),
        El("button", "Submit application")])}
    fake = FakeMCP(site, "p")
    answers = {"Apply": [Q("Full name", "Amin Abbaszadeh", ref="auto"), Q("Email", "amin@example.com", ref="auto"),
                         Q("Years of experience", "2", ref="auto"),
                         Q("Resume", None, kind="file", ref="auto", source=None)]}
    parked = run_pages(ctx_for(fake, answers, tmp_path))
    assert parked.pages == 1 and _goals(fake) == []                        # no page goal, no model call
    vals = {e.name: e.value for e in site["p"].els}
    assert vals["Full name"] == "Amin Abbaszadeh" and vals["Email"] == "amin@example.com"


def test_a_refused_typed_value_gets_one_goal_for_that_field_only(tmp_path):
    site = single_page()
    site["p1"].els[0].refuse_typing = True               # City rewrites what is typed; the model can set it
    fake = FakeMCP(site, "p1")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    goals = _goals(fake)
    assert parked.pages == 1 and len(goals) == 1 and '"City" → "Milan"' in goals[0]["goal"]
    assert site["p1"].els[0].value == "Milan"


def test_read_only_fields_and_custom_widgets_still_go_to_the_goal(tmp_path):
    from assistant.answers import Question
    from assistant.fill import plan_fill
    from assistant.jev import Element, Table
    from assistant.pages import Page
    t = Table(url="u", elements=[Element(ref="e1", role="textbox", name="City", editable=True),
                                 Element(ref="e2", role="textbox", name="Start date"),          # read-only
                                 Element(ref="e3", role="combobox", name="Work model", editable=True)])
    p = Page(url="u", title="t", text="", table=t)
    plan = plan_fill([Question.model_validate(Q("x", "v", ref=ref)) for ref in ("e1", "e2", "e3")], p)
    assert plan == [{"op": "type", "ref": "e1", "text": "v", "clear": True, "submit": False}, None, None]


def test_the_fill_plan_is_jevs_pick_and_the_code_only_checks_it_can_be_done():
    """Jev picks how and where (2026-09-24); the code refuses a pick the page does not allow (typing into a
    read-only field, checking something that is not a radio or checkbox) and sends it to the page goal instead."""
    from assistant import decide
    from assistant.answers import Question
    from assistant.fill import plan_fill
    from assistant.jev import Element, Option, Table
    from assistant.pages import Page
    els = [Element(ref="e1", role="textbox", name="Phone", editable=True),
           Element(ref="e2", role="textbox", name="Start date"),                       # read-only
           Element(ref="e3", role="radio", name="Relocate?", label="Yes"),
           Element(ref="e4", role="combobox", name="Based in Ireland?", options=[Option(ref="e4:1", label="No")])]
    p = Page(url="u", title="t", text="", table=Table(url="u", elements=els))
    picks = {"Phone": ("type", "e1", "none"), "Start date": ("type", "e2", "none"),
             "Relocate?": ("check", "none", "e3"), "Based in Ireland?": ("select", "e4", "e4:1"),
             "Nonsense": ("check", "none", "e1")}
    asked = []

    class Jev:
        def ask(self, topic, state, questions):
            asked.append((topic, questions))
            out = {}
            for k, q in questions.items():
                how, field, option = picks[q["instructions"]["question"]]
                c = how if k.startswith("op_") else field if k.startswith("field_") else option
                out[k] = decide.Answer("choice", choice=c, confidence=0.9)
            return out
    decide.use(Jev())
    qs = [Question.model_validate(Q(name, "x")) for name in picks]
    plan = plan_fill(qs, p)
    assert plan[0] == {"op": "type", "ref": "e1", "text": "x", "clear": True, "submit": False}
    assert plan[1] is None                                                        # read-only: to the page goal
    assert plan[2] == {"op": "toggle", "ref": "e3", "state": True}
    assert plan[3] == {"op": "select", "ref": "e4", "value": "No"}
    assert plan[4] is None                                                        # a textbox cannot be checked
    topic, questions = asked[0]
    assert topic == "fill" and set(questions["option_0"]["criteria"]) == {"e3", "e4:1", "none"}


def test_a_resume_question_is_covered_once_the_resume_is_in_place(tmp_path):
    """Live 2026-09-23: the engine turned LinkedIn's resume cards into a required "Resume*" question and its
    answer was dropped — that must not send the job to Needs-Attention when the upload took care of it."""
    site = {"r": FakePage("https://www.linkedin.com/jobs/view/1/", "Apply", f"Resume* Select or upload a resume {PDF}", [
        El("radio", "Amin_Old-Company_Old-Role.pdf", checked=False), El("radio", PDF, checked=True),
        El("button", "Upload resume"), El("button", "Submit application")])}
    fake = FakeMCP(site, "r")
    answers = {"Apply": [Q("Resume*", None, kind="choice", options=["Amin_Old-Company_Old-Role.pdf", PDF],
                           source=None)]}
    parked = run_pages(ctx_for(fake, answers, tmp_path))
    assert parked.pages == 1


def test_is_resume_question_is_the_deciders_verdict():
    from assistant.answers import judge_questions
    from assistant.fill import is_resume_question
    from assistant.jev import Table
    from assistant.pages import Page
    pa = PageAnswers.model_validate({"questions": [
        Q("Resume*", None, kind="choice", source=None),
        Q("Select one", None, kind="choice", source=None, options=["Amin_A.pdf", "Amin_B.pdf"]),
        Q("Cover letter or resume notes", None, source=None),               # a cover letter is not covered
        Q("Mobile phone number*", None, source=None)]})
    judge_questions(pa, Page(url="u", title="Apply", text="", table=Table(url="u")))
    assert [is_resume_question(q) for q in pa.questions] == [True, True, False, False]


# ------------------------------------------------------------------ the browser agent (user decision 2026-09-23)
# One test per job of the live run runs/20260923-224945, where the rule-based driver sent all seven to Needs-Attention.

LI_JOB = "https://www.linkedin.com/jobs/view/4470454940/"


def _clicked(fake) -> list[str]:
    """Labels the fake clicked, through a goal or an act (a wrapper over _click installed by the test)."""
    return fake.clicked


def _record_clicks(fake):
    fake.clicked = []
    orig = fake._click

    def click(e, confirm=False):
        fake.clicked.append(e.name)
        return orig(e, confirm)
    fake._click = click


def test_a_safety_reminder_after_easy_apply_is_passed_with_continue_applying(tmp_path):
    """Linda AI: Easy Apply opened "Job search safety reminder"; the old rule clicked "Review job post" (it starts
    with "Review") and looped twelve times. The agent continues the application instead."""
    site = {
        "job": FakePage(LI_JOB, "Founding Software Engineer | Linda AI | LinkedIn", "Founding Software Engineer",
                        [El("button", "Easy Apply to this job", goto="safety"), El("button", "Save the job")]),
        "safety": FakePage(LI_JOB, "Founding Software Engineer | Linda AI | LinkedIn", "Job search safety reminder",
                           [El("button", "Dismiss", goto="job"), El("button", "Review job post", goto="job"),
                            El("link", "Continue applying", goto="s1")], modal="Job search safety reminder"),
        **steps_site()}
    fake = FakeMCP(site, "job")
    _record_clicks(fake)
    parked = run_pages(ctx_for(fake, STEPS_ANSWERS, tmp_path))
    assert parked.pages == 3 and fake.cur == "s3" and fake.sent == []
    assert "Review job post" not in fake.clicked and "Continue applying" in fake.clicked
    assert not any(op.get("confirm") for n, a in fake.log if n == "browser_act" for op in a["ops"])


def test_a_cookie_pop_up_over_the_form_is_declined_and_the_form_submit_never_confirmed(tmp_path):
    """Toast: the pop-up's "I do not accept / I accept" matched none of the old cookie rules; it covered the form
    and every typed value was refused. The form's own submit is "Apply now!", an Apply label that must never be
    taken for the entry click."""
    site = {"t": FakePage("https://careers.toasttab.com/jobs/1", "Software Engineer II", "We use cookies. City Resume",
                          [El("link", "APPLY NOW"), El("textbox", "City", required=True, occluded=True),
                           El("file", "Resume", required=True, occluded=True), El("button", "I do not accept"),
                           El("button", "I accept"), El("button", "Apply now!", submits=True)],
                          modal="Cookie consent")}
    fake = FakeMCP(site, "t")
    _record_clicks(fake)
    orig = fake._click

    def click(e, confirm=False):
        if e.name == "I do not accept":                             # declining removes the pop-up
            page = site["t"]
            page.modal, page.text = None, page.text.replace("We use cookies. ", "")
            page.els = [x for x in page.els if x.name not in ("I do not accept", "I accept")]
            for x in page.els:
                x.occluded = False
        return orig(e, confirm)
    fake._click = click
    answers = {"Software Engineer II": [Q("City", "Milan", ref="auto"),
                                        Q("Resume", None, kind="file", ref="auto", source=None)]}
    parked = run_pages(ctx_for(fake, answers, tmp_path))
    assert parked.pages == 1 and fake.sent == [] and "I accept" not in fake.clicked
    assert {e.name: e.value for e in site["t"].els}["City"] == "Milan"
    assert not any(op.get("confirm") for n, a in fake.log if n == "browser_act" for op in a["ops"])


def test_the_never_submit_rule_on_the_server_refuses_submit_always_and_apply_while_filling():
    """The package applies jev.never_click to every click (FakeMCP does the same): Submit is refused on any page,
    Apply only once the form is being filled."""
    from assistant import jev
    from assistant.pages import read_page
    site = {"t": FakePage("https://careers.example/jobs/1", "Job", "Apply", [
        El("textbox", "City"), El("button", "Apply now!", submits=True), El("button", "Submit application", submits=True)])}
    fake = FakeMCP(site, "t")
    p = read_page(fake, "s")
    click = lambda ref: fake.act([{"op": "click", "ref": ref}], "s", p.table, stop_on_error=False)
    assert "needs_confirmation" in click("e3")                     # Submit, even before filling
    jev.FORM.started = True
    assert "needs_confirmation" in click("e2")                     # Apply, once filling started
    assert fake.sent == []


def test_a_job_alert_box_is_not_taken_for_the_application_form(tmp_path):
    """Mastercard: the job page's job-alert box ("Please enter your email address") was filled as if it were the
    application, and "Apply Now" was never clicked. The agent's first "the form is here" is checked, and questioned
    once when the page does not look like an application."""
    from assistant import decide
    from assistant.fill import NOT_THE_FORM
    from tests.rule_decider import RuleDecider
    decide.use(RuleDecider(kinds={"https://careers.mastercard.com/job/1": "job_posting"}))   # what Jev says
    site = {"job": FakePage("https://careers.mastercard.com/job/1", "Senior ML Ops", "Senior ML Ops. Job alerts", [
                El("button", "Apply Now for R-286222", goto="p1"), El("textbox", "Please enter your email address"),
                El("button", "Submit")], form_here=True),
            **single_page()}
    fake = FakeMCP(site, "job")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and fake.cur == "p1" and fake.sent == []
    assert site["job"].els[1].value == ""                              # the job-alert box was left alone
    navigates = [a["goal"] for n, a in fake.log if n == "browser_goal" and a["goal"].startswith("Bring")]
    assert NOT_THE_FORM not in navigates[0] and NOT_THE_FORM in navigates[1]


def test_a_radio_group_redrawn_after_the_toggle_is_read_back_by_its_label(tmp_path):
    """Digital Manufacturing Ireland: LinkedIn redraws a radio group when an option is picked; the new radios have
    new refs (e221 → e231, checked), the old ref read as "not set", and a correctly filled page was given up."""
    q = "Are you currently a resident of Ireland?"
    site = {"s": FakePage("https://www.linkedin.com/jobs/view/1/", "Additional Questions",
                          f"Additional Questions {q} Yes No Resume", [
                              El("radio", q, group=q, label="Yes", ref="e220"),
                              El("radio", q, group=q, label="No", ref="e221"),
                              El("file", "Resume", required=True, value=PDF), El("button", "Submit application")])}
    fake = FakeMCP(site, "s")
    orig = fake._browser_act

    def act(ops, **kw):
        out = orig(ops, **kw)
        if any(op["op"] == "toggle" for op in ops):                     # the redraw: new radios, new refs
            old = site["s"].els
            site["s"].els = [El("radio", q, group=q, label=e.label, checked=e.checked, ref=f"e23{i}")
                             for i, e in enumerate(old[:2])] + old[2:]
        return out
    fake._browser_act = act
    answers = {"Additional Questions": [Q(q, "No", kind="choice", option_ref="e221", options=["Yes", "No"])]}
    parked = run_pages(ctx_for(fake, answers, tmp_path))
    assert parked.pages == 1 and [e.checked for e in site["s"].els[:2]] == [False, True]
    assert not [a for n, a in fake.log if n == "browser_open"]         # never reopened: the answer was seen as set


def test_the_agents_entry_click_is_tried_again_while_the_posting_re_renders(tmp_path):
    """Genesys: the posting was still re-rendering — `detached`, then `page_changed` — and the old code gave the
    job up after one retry, three seconds in. A click the page changed under counts as movement: look again."""
    site = {"j": FakePage(LI_JOB, "Job", "Software Engineer",
                          [El("link", "Apply on company website", goto="p1")]), **single_page()}
    fake = FakeMCP(site, "j")
    orig, n = fake._click, {"clicks": 0}

    def click(e, confirm=False):
        if e.name == "Apply on company website":
            n["clicks"] += 1
            if n["clicks"] < 3:
                return "detached" if n["clicks"] == 1 else "page_changed"
        return orig(e, confirm)
    fake._click = click
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and n["clicks"] == 3 and fake.cur == "p1"


def test_an_agent_that_finds_no_way_forward_is_needs_attention(tmp_path):
    site = {"j": FakePage("https://acme.io/jobs/1", "Job", "No longer open", [El("button", "Save")])}
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(FakeMCP(site, "j"), {}, tmp_path))
    assert ei.value.cls == "navigation" and "no way forward" in ei.value.what


def test_an_advance_goal_that_only_scrolled_is_asked_again(tmp_path):
    fake = FakeMCP(steps_site(), "s1")
    orig, first = fake._browser_goal, {"n": 0}

    def goal(goal, **kw):
        if goal == NEXT_STEP_GOAL and first["n"] == 0:
            first["n"] += 1
            return ("goal: x\nstatus: stopped: hit max_steps=1\nsteps: 1\ntrace:\n"
                    "  1. SCROLL  → ok (1ms model / 1ms browser)\n\n" + fake.view())
        return orig(goal, **kw)
    fake._browser_goal = goal
    parked = run_pages(ctx_for(fake, STEPS_ANSWERS, tmp_path))
    assert parked.pages == 3 and first["n"] == 1


def test_an_answer_naming_a_select_option_by_its_ref_is_set_directly_and_read_back():
    """Toast (Greenhouse), live 2026-09-24: the answer named option "e31:3" of a native <select>; no element has that
    ref, so the select went to a page goal and the read-back failed although "No" was selected."""
    from assistant.answers import Question
    from assistant.fill import mismatches, plan_fill
    from assistant.jev import Element, Option, Table
    from assistant.pages import Page

    def page(current):
        sel = Element(ref="e31", role="combobox", name="Are you currently based in Ireland? (required) 8ab4c6f4",
                      current=current, options=[Option(ref="e31:1", label=""), Option(ref="e31:2", label="Yes"),
                                                Option(ref="e31:3", label="No", selected=current == "No")])
        return Page(url="https://x.test", title="t", text="", table=Table(url="https://x.test", elements=[sel]))
    base = dict(id="a", question="Are you currently based in Ireland? (required)", kind="choice", options=None,
                answer="No", required=True, source="profile", quote="x", relies_on=None)
    for q in (Question(**base, ref="e31", option_ref="e31:3"), Question(**base, ref="e31:3", option_ref=None)):
        assert plan_fill([q], page(None)) == [{"op": "select", "ref": "e31", "value": "No"}]  # RuleDecider
        assert mismatches([q], page("No")) == [] and mismatches([q], page(None)) == [q]      # RuleDecider


def test_an_unsure_resume_pick_is_asked_again_narrowly_then_settled_by_the_answer_engine():
    """Several uploads and Jev unsure (The Flex, live 2026-09-24): no fixed-threshold stop. Jev is asked again over its
    two likeliest picks; still unsure, the answer engine's pick settles it if it is one of those two."""
    from assistant import decide
    from assistant.fill import resume_input
    from assistant.jev import Element, Table
    from assistant.pages import Page, Judgment
    els = [Element(ref="e16", role="file", name=""), Element(ref="e29", role="file", name="Resume"),
           Element(ref="e30", role="file", name="Cover letter")]

    def page():
        p = Page(url="u", title="t", text="", table=Table(url="u", elements=els))
        p.judgment = Judgment(kind="application_form", kind_confidence=1, submitted=False, applied=False,
                              covered=False, loading=False, validation=False, registration=False, account="none",
                              submit_button=False, app_fields=set(), resume_ref="e29", resume_confidence=0.4,
                              resume_probabilities={"e29": 0.4, "e16": 0.35, "e30": 0.2, "none": 0.05},
                              cover_letters=[], google_ref=None, google_step=None, form_iframe=None)
        return p
    asked = []

    class Unsure:
        def __init__(self, pick, conf):
            self.pick, self.conf = pick, conf

        def ask(self, topic, state, questions):
            asked.append(set(questions["resume_input"]["criteria"]))
            return {"resume_input": decide.Answer("choice", choice=self.pick, confidence=self.conf)}
    decide.use(Unsure("e29", 0.9))
    assert resume_input(page()) == "e29" and asked[-1] == {"e29", "e16", "none"}     # the two likeliest, and none
    decide.use(Unsure("e29", 0.45))
    assert resume_input(page(), engine_pick="e16") == "e16"                          # the LLM settles a close call
    with pytest.raises(NeedsAttention, match="unclear which one takes the resume"):
        resume_input(page(), engine_pick="e30")                                     # not a finalist: no guess
