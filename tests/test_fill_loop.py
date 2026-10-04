"""Fill-loop unit tests (P2) on FakeBrowser: the deterministic Easy Apply loop (entry → fill each step →
advance → final → park), the §7 fill mapping, and never-submit. The old browser_goal navigation tests are
replaced by tests/test_navigate.py (which drives the real driver against the Easy Apply fixtures)."""
import pytest

from assistant.llm_inference import PageAnswers
from assistant.blockers import NeedsAttention, Parked
from assistant.fill import JobCtx, run_pages
from tests.fake_browser import El, FakeBook, FakePage, fake_browser

pytestmark = pytest.mark.unit
PDF = "Amin_Acme_Data_Engineer.pdf"
LI = "https://www.linkedin.com/jobs/view/4012345601/"


def Q(question, answer, *, kind="text", ref=None, option_ref=None, source="profile", required=True, **kw):
    return dict(id="q", question=question, kind=kind, ref=ref, option_ref=option_ref, options=kw.get("options"),
               required=required, answer=answer, source=source, quote=kw.get("quote", "x"),
               relies_on=kw.get("relies_on"))


def engine(by_title: dict):
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


def ctx_for(browser, answers, tmp_path, **kw):
    pdf = tmp_path / PDF
    pdf.write_bytes(b"%PDF")
    return JobCtx(browser=browser, session="job", book=FakeBook(), resume_pdf=pdf, answer_fn=engine(answers),
                  baseline={"T0"}, answers_log=tmp_path / "answers.json", shots_dir=tmp_path / "shots",
                  folder="1_Acme", sleep=lambda _: None, **kw)


def posting(goto="s1", easy="Easy Apply to Data Engineer at Acme"):
    return FakePage(LI, "Data Engineer | Acme | LinkedIn", "Data Engineer | Acme. About the job.",
                    [El("button", easy, goto=goto), El("button", "Save")])


def single_dialog():
    """A LinkedIn posting whose Easy Apply opens a one-step dialog ending in Submit application."""
    return {
        "job": posting(),
        "s1": FakePage(LI, "Data Engineer | Acme | LinkedIn", "Apply to Acme. City Why Acme? Resume", [
            El("textbox", "City", required=True, dialog="d"),
            El("textbox", "Why Acme?", maxlength=500, dialog="d"),
            El("file", "Resume", required=True, dialog="d"),
            El("button", "Submit application", submits=True, dialog="d")])}


SINGLE_ANSWERS = {"Data Engineer | Acme | LinkedIn": [
    Q("City", "Milan", ref="auto"),
    Q("Why Acme?", "I build perception systems.", kind="longtext", ref="auto", source="generated",
      relies_on=["fact"]),
    Q("Resume", None, kind="file", ref="auto", source=None)]}


def test_single_step_easy_apply_is_filled_and_parked(tmp_path):
    browser, fake = fake_browser(single_dialog(), "job")
    parked = run_pages(ctx_for(browser, SINGLE_ANSWERS, tmp_path))
    els = {e.name: e.value for e in fake.site["s1"].els}
    assert els["City"] == "Milan" and els["Why Acme?"] == "I build perception systems." and els["Resume"] == PDF
    assert fake.sent == []                                          # never submitted
    assert parked.generated == [("Why Acme?", "I build perception systems.")]
    kinds = [k for k, _ in fake.log]
    assert "upload" in kinds and "type" in kinds and "screenshot" in kinds
    assert not any(op.get("confirm") for _, op in fake.log)


def steps_site():
    return {
        "job": posting(),
        "s1": FakePage(LI, "Contact", "Contact info", [
            El("textbox", "Phone", required=True, dialog="d"),
            El("button", "Continue to next step", goto="s2", dialog="d")]),
        "s2": FakePage(LI, "Questions", "Questions. Do you need a visa?", [
            El("radio", "Yes", group="Do you need a visa?", required=True, dialog="d"),
            El("radio", "No", group="Do you need a visa?", dialog="d"),
            El("button", "Review your application", goto="s3", dialog="d")]),
        "s3": FakePage(LI, "Review", "Review. Resume", [
            El("file", "Resume", required=True, dialog="d"),
            El("button", "Submit application", submits=True, dialog="d")])}


STEPS_ANSWERS = {"Contact": [Q("Phone", "+39 333", ref="auto")],
                 "Questions": [Q("Do you need a visa?", "No", kind="choice", option_ref="auto",
                                 options=["Yes", "No"])],
                 "Review": [Q("Resume", None, kind="file", ref="auto", source=None)]}


def test_multi_step_easy_apply_walks_to_the_final_step(tmp_path):
    browser, fake = fake_browser(steps_site(), "job")
    parked = run_pages(ctx_for(browser, STEPS_ANSWERS, tmp_path))
    assert fake.cur == "s3" and fake.sent == []
    assert next(e for e in fake.site["s2"].els if e.name == "No").checked is True


def test_mismatches_holds_a_verbatim_field_without_asking_the_model(tmp_path):
    """P3: mismatches is all code, no model call. Verbatim match, reformatted phone (digits-suffix), and
    genuinely different values are all decided by _fuzzy_holds."""
    from assistant import decide, fill, pages
    from assistant.browser import Element, Table
    from assistant.llm_inference import PageAnswers
    q = PageAnswers.model_validate({"questions": [
        Q("Mobile phone number*", "+39 351 935 8813", ref="e1")]}).questions[0]
    p = pages.Page(url="x", title="t", text="",
                   table=Table(url="x", elements=[Element(ref="e1", role="textbox", name="Mobile phone number*",
                                                          value="+39 351 935 8813")]))

    class Boom:
        def ask(self, *a, **k):
            raise AssertionError("mismatches must not call the model at all (P3)")
    decide.use(Boom())
    try:
        assert fill.mismatches([q], p) == []                     # verbatim: holds
        p.elements[0].value = "+393519358813"
        assert fill.mismatches([q], p) == []                     # reformatted phone: digits match, holds
        p.elements[0].value = "+1 555 000 0000"
        assert fill.mismatches([q], p) == [q]                    # genuinely different: mismatch
        # short digit suffix must NOT match: "5" vs "15" is not a phone reformat
        q2 = PageAnswers.model_validate({"questions": [Q("Years", "5", ref="e1")]}).questions[0]
        p.elements[0].value = "15"
        assert fill.mismatches([q2], p) == [q2]                  # short digits: mismatch, not suffix
    finally:
        decide.use(None)


def reminder_site():
    """Easy Apply opens a "Job search safety reminder" modal over the form. Its buttons, in DOM order, are
    Dismiss (cancels), report it, Review job post, Continue applying (proceeds) — live 2026-09-29, Linda AI.
    The program must click "Continue applying", NOT "Dismiss" (which is first in DOM and cancels)."""
    return {
        "job": posting(goto="reminder"),
        "reminder": FakePage(LI, "Data Engineer | Acme | LinkedIn", "Job search safety reminder", [
            El("button", "Dismiss", goto="job", dialog="d"),          # first; clicking it cancels (back to posting)
            El("link", "report it", dialog="d"),
            El("button", "Review job post", goto="job", dialog="d"),
            El("link", "Continue applying", goto="s1", dialog="d")]),  # last; the real proceed control
        "s1": FakePage(LI, "Data Engineer | Acme | LinkedIn", "Apply to Acme. City Resume", [
            El("textbox", "City", required=True, dialog="d"),
            El("file", "Resume", required=True, dialog="d"),
            El("button", "Submit application", submits=True, dialog="d")])}


def test_easy_apply_safety_reminder_is_passed_with_continue_applying(tmp_path):
    answers = {"Data Engineer | Acme | LinkedIn": [Q("City", "Milan", ref="auto"),
                                                    Q("Resume", None, kind="file", ref="auto", source=None)]}
    browser, fake = fake_browser(reminder_site(), "job")
    run_pages(ctx_for(browser, answers, tmp_path))
    # reached the form only by clicking "Continue applying"; "Dismiss"/"Review job post" (goto "job") would loop.
    assert fake.cur == "s1" and fake.sent == []
    assert next(e for e in fake.site["s1"].els if e.name == "City").value == "Milan"


def test_refill_reenters_and_easy_apply_stays_clickable(tmp_path):
    """A field that will not hold triggers _Refill (broken_form attempt 2). Re-entry from the posting must reset
    guard.FORM.started, or the re-entry Easy Apply click is refused as "Apply while filling" and the job wrongly
    reports `navigation` instead of `broken_form` (live 2026-09-29, Linda AI's phone field)."""
    site = single_dialog()
    site["s1"].els.insert(0, El("textbox", "Mobile phone number", required=True, dialog="d", refuse_typing=True))
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("Mobile phone number", "351 935 8813", ref="auto"),
        Q("City", "Milan", ref="auto"),
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, answers, tmp_path))
    assert exc.value.cls == "broken_form"        # the phone field — NOT "navigation" (the bug's symptom)
    assert fake.sent == []
    # Easy Apply was clicked more than once (initial + re-entry) and never refused for "Apply while filling".
    assert not any("Apply once the form is being filled" in (op.get("detail", "") or "")
                   for _, op in fake.log)


def test_external_apply_is_external_ats(tmp_path):
    site = {"job": FakePage(LI, "Data Engineer | Acme | LinkedIn", "Data Engineer | Acme.",
                            [El("link", "Apply"), El("button", "Save")])}
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, {}, tmp_path))
    assert exc.value.cls == "external_ats" and fake.sent == []


def test_closed_posting_is_needs_attention(tmp_path):
    site = {"job": FakePage(LI, "Data Engineer | Acme | LinkedIn",
                            "Data Engineer | Acme. No longer accepting applications", [El("button", "Save")])}
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, {}, tmp_path))
    assert exc.value.cls == "closed"


def test_uncovered_required_question_goes_to_needs_attention(tmp_path):
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q("Salary expectation", None, ref="auto", source=None, required=True),
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    site["s1"].els.insert(1, El("textbox", "Salary expectation", required=True, dialog="d"))
    browser, fake = fake_browser(site, "job")
    result = run_pages(ctx_for(browser, answers, tmp_path))
    assert isinstance(result, Parked) and result.parked_at and fake.sent == []
    assert len(result.parked_at) == 1 and result.parked_at[0].question == "Salary expectation"


def test_required_widget_is_a_broken_form(tmp_path):
    """P4: a required widget the handlers don't support is a broken_form blocker."""
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q("Work model", "Hybrid", kind="choice", ref="auto"),       # combobox with no options → widget
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    site["s1"].els.insert(1, El("combobox", "Work model", required=True, dialog="d"))   # no options => widget
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, answers, tmp_path, widget_poll_secs=0))
    assert exc.value.cls == "broken_form" and "widget" in exc.value.what and fake.sent == []


def test_typeahead_widget_fills_combobox(tmp_path):
    """P4 T1b: typeahead combobox is filled via type → poll for role=option → click match."""
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q("Work model", "Hybrid", kind="choice", ref="auto"),
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    site["s1"].els.insert(1, El("combobox", "Work model", required=True, dialog="d"))
    site["s1"].els.insert(2, El("option", "Hybrid", dialog="d"))   # typeahead suggestion
    site["s1"].els.insert(3, El("option", "Remote", dialog="d"))
    browser, fake = fake_browser(site, "job")
    parked = run_pages(ctx_for(browser, answers, tmp_path, widget_poll_secs=0))
    assert isinstance(parked, Parked) and fake.sent == []


def test_mismatches_holds_after_radio_rerender(tmp_path):
    """P4 T1a: React re-render invalidates option_ref; label-scan fallback reports holds correctly."""
    from assistant.fill import mismatches
    from assistant.llm_inference import PageAnswers
    from assistant.browser import Element, Table
    from assistant import pages

    q = PageAnswers.model_validate({"questions": [
        Q("Do you need a visa?", "No", kind="choice", ref=None, option_ref="e2")]}).questions[0]
    # e2 gone after re-render; e5/e6 are the new refs, "No" is checked
    p = pages.Page(url="x", title="t", text="",
                   table=Table(url="x", elements=[
                       Element(ref="e5", role="radio", name="Yes"),
                       Element(ref="e6", role="radio", name="No", checked=True),
                   ]))
    assert mismatches([q], p) == []           # "No" is checked → holds via label scan
    p.table.elements[1].checked = False
    assert mismatches([q], p) == [q]          # nothing checked → mismatch


def test_save_application_dialog_raises_dialog_closed(tmp_path):
    """P4 T4: 'Save this application?' triggers dialog_closed NeedsAttention without any click."""
    site = {
        "job": posting(),
        "s1": FakePage(LI, "Data Engineer | Acme | LinkedIn",
                       "Save this application? You can continue later.",
                       [El("button", "Save", dialog="d"),
                        El("button", "Discard", dialog="d")])}
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, {}, tmp_path))
    assert exc.value.cls == "dialog_closed" and fake.sent == []


def test_required_cover_letter_file_is_a_blocker(tmp_path):
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("Cover letter", None, kind="file", ref="auto", source=None, required=True, cover=True)]}
    site = single_dialog()
    site["s1"].els = [El("file", "Cover letter", required=True, dialog="d"),
                      El("button", "Submit application", submits=True, dialog="d")]
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, answers, tmp_path))
    assert exc.value.cls == "broken_form" and fake.sent == []


def test_llm_inference_failure_is_a_needs_attention_blocker(tmp_path):
    from assistant.llm_inference import LLMInferenceError

    def boom(page):
        raise LLMInferenceError("model out of quota")
    browser, fake = fake_browser(single_dialog(), "job")
    ctx = ctx_for(browser, {}, tmp_path)
    ctx.answer_fn = boom
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx)
    assert exc.value.cls == "llm_inference" and fake.sent == []


def test_the_submit_button_is_never_clicked(tmp_path):
    browser, fake = fake_browser(single_dialog(), "job")
    run_pages(ctx_for(browser, SINGLE_ANSWERS, tmp_path))
    assert fake.sent == []
    assert not any(k == "click" and "Submit" in (op.get("target", "") or "")
                   for k, op in fake.log)
