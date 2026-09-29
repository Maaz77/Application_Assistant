"""Fill-loop unit tests (P2) on FakeBrowser: the deterministic Easy Apply loop (entry → fill each step →
advance → final → park), the §7 fill mapping, and never-submit. The old browser_goal navigation tests are
replaced by tests/test_navigate.py (which drives the real driver against the Easy Apply fixtures)."""
import pytest

from assistant.llm_inference import PageAnswers
from assistant.blockers import NeedsAttention
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
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, answers, tmp_path))
    assert exc.value.cls == "unanswered" and fake.sent == []


def test_required_widget_is_a_broken_form(tmp_path):
    """P2: no page-goal fallback. A required answer that no op can set (a custom widget) is a blocker."""
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q("Work model", "Hybrid", kind="choice", ref="auto"),       # a combobox with no listed options
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    site["s1"].els.insert(1, El("combobox", "Work model", required=True, dialog="d"))   # no options => a widget
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, answers, tmp_path))
    assert exc.value.cls == "broken_form" and "widget" in exc.value.what and fake.sent == []


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
