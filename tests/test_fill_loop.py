"""Loop unit tests on FakeMCP: fill, upload, typing, goal, read-back, advance, final step, gate, blockers."""
import pytest

from assistant.answers import PageAnswers
from assistant.blockers import NeedsAttention, StopRun
from assistant.fill import JobCtx, parse_goal, run_pages
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


def test_two_advance_buttons_use_the_goal_and_strong_block_means_final(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply. Resume", [
        El("file", "Resume", required=True), El("button", "Submit application"),
        El("button", "Continue"), El("button", "Continue later")])}
    fake = FakeMCP(site, "p")
    parked = run_pages(ctx_for(fake, {"Apply": [Q("Resume", None, kind="file", ref="auto", source=None)]},
                                     tmp_path))
    assert parked.pages == 1 and fake.sent == []
    assert any(n == "browser_goal" and a["goal"].startswith("Go to the next step") for n, a in fake.log)


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
    with pytest.raises(NeedsAttention, match="none is labelled resume"):
        run_pages(ctx_for(FakeMCP(site, "p"), {}, tmp_path))


def test_gate_failure_is_retried_once(tmp_path):
    site = {"p": FakePage("https://acme.io/a", "Apply", "Apply", [El("button", "Submit application")])}
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


def test_custom_combobox_read_back_uses_new_page_text():
    from assistant.fill import mismatches
    from assistant.jev import Element, Table
    from assistant.pages import Page
    from assistant.answers import Question
    q = Question.model_validate(Q("Work model", "Hybrid", kind="choice", ref="e1"))
    el = [Element(ref="e1", role="combobox", name="Work model")]
    after = Page(url="u", title="t", text="Work model Hybrid", table=Table(url="u", elements=el))
    assert mismatches([q], after, before_text="Work model Select an option") == []
    assert mismatches([q], after, before_text="Work model Remote Hybrid On-site") == [q]   # was already visible


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

    def flaky(goal, **kw):                              # first goal dies in the text helper (B5), retry works
        calls["n"] += 1
        if calls["n"] == 1:
            return "turbo_unavailable: Text helper returned no usable value; nothing typed."
        return orig(goal, **kw)
    fake._browser_goal = flaky
    parked = run_pages(ctx_for(fake, WIDGET_ANSWERS, tmp_path))
    assert parked.pages == 1 and calls["n"] == 2


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
    assert not any(n == "browser_goal" for n, _ in fake.log)          # no model was needed
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
    assert resume_input(p, resume_step=True) == p.elements[2].ref          # the "Upload resume" trigger


def test_upload_on_a_transmit_label_is_refused():
    from assistant.guard import GuardError, check
    from assistant.jev import Element, Table
    t = Table(url="u", elements=[Element(ref="e1", role="button", name="Submit application"),
                                 Element(ref="e2", role="button", name="Upload resume")])
    with pytest.raises(GuardError):
        check({"op": "upload", "ref": "e1", "path": "/tmp/x.pdf"}, t)
    check({"op": "upload", "ref": "e2", "path": "/tmp/x.pdf"}, t)



# ------------------------------------------------------------------ direct typing (user decision, 2026-09-23)

def _goals(fake):
    return [a for n, a in fake.log if n == "browser_goal"]


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
    from assistant.fill import direct_op
    from assistant.jev import Element, Table
    from assistant.pages import Page
    t = Table(url="u", elements=[Element(ref="e1", role="textbox", name="City", editable=True),
                                 Element(ref="e2", role="textbox", name="Start date"),          # read-only
                                 Element(ref="e3", role="combobox", name="Work model", editable=True)])
    p = Page(url="u", title="t", text="", table=t)
    op = lambda ref: direct_op(Question.model_validate(Q("x", "v", ref=ref)), p)
    assert op("e1") == {"op": "type", "ref": "e1", "text": "v", "clear": True, "submit": False}
    assert op("e2") is None and op("e3") is None


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


def test_is_resume_question():
    from assistant.answers import Question
    from assistant.fill import is_resume_question
    Qm = lambda **k: Question.model_validate(Q(**{"answer": None, "source": None, **k}))
    assert is_resume_question(Qm(question="Resume*", kind="choice"))
    assert is_resume_question(Qm(question="Select one", kind="choice", options=["Amin_A.pdf", "Amin_B.pdf"]))
    assert not is_resume_question(Qm(question="Cover letter or resume notes"))       # a cover letter is not covered
    assert not is_resume_question(Qm(question="Mobile phone number*"))
