"""Fill-loop unit tests (P2) on FakeBrowser: the deterministic Easy Apply loop (entry → fill each step →
advance → final → park), the §7 fill mapping, and never-submit. The old browser_goal navigation tests are
replaced by tests/test_navigate.py (which drives the real driver against the Easy Apply fixtures)."""
import pytest

from assistant.llm_inference import PageAnswers
from assistant.blockers import GoExternal, NeedsAttention, Parked
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
            if q["option_ref"] == "same":
                q["option_ref"] = q["ref"]      # what _set_option_refs does for a checkbox/switch
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


def test_external_apply_signals_go_external(tmp_path):
    """P5 (was test_external_apply_is_external_ats): a bare external Apply makes run_pages raise GoExternal,
    so process() hands the job to external.run_external instead of dead-ending as external_ats. The posting
    is not submitted and nothing is sent to the model from the Easy-Apply loop."""
    site = {"job": FakePage(LI, "Data Engineer | Acme | LinkedIn", "Data Engineer | Acme.",
                            [El("link", "Apply"), El("button", "Save")])}
    browser, fake = fake_browser(site, "job")
    with pytest.raises(GoExternal):
        run_pages(ctx_for(browser, {}, tmp_path))
    assert fake.sent == []


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


# P4 T1a: LinkedIn renders a Yes/No group as `div role=radio` whose accessible *name* is the question and whose
# *label* is the option ("the same_name branch" in llm_inference._radio_q). Several groups on one page therefore
# carry identical option labels, which is why the read-back after a re-render has to be scoped to one group.
_RERENDER_QS = ["Have you completed the following level of education: Bachelor's Degree?",
                "Are you comfortable working in an onsite setting?",
                "Are you legally authorized to work in Ireland?"]


def _rerender_page(checked: dict[str, str]):
    """A LinkedIn form page with the three Yes/No groups; `checked` maps group → the checked option label."""
    from assistant.browser import Element, Table
    from assistant import pages
    url = "https://www.linkedin.com/jobs/view/4470454940/"
    els = [Element(ref=f"n{i}{j}", role="radio", name=q, label=opt, tag="DIV", scope="form",
                   group=f"g{i}", checked=checked.get(f"g{i}") == opt)
           for i, q in enumerate(_RERENDER_QS) for j, opt in enumerate(("Yes", "No"))]
    return pages.Page(url=url, title="t", text="", table=Table(url=url, elements=els))


def _rerender_items():
    """The three questions as answered, with the pre-re-render option_refs that no longer exist on the page."""
    return PageAnswers.model_validate({"questions": [
        Q(q, "Yes", kind="choice", option_ref=ref)
        for q, ref in zip(_RERENDER_QS, ("e150", "e152", "e154"))]}).questions


def _typeahead_site(*suggestions):
    """A one-step dialog whose "Work model" is a combobox with no listed options: a typeahead."""
    site = single_dialog()
    site["s1"].els.insert(1, El("combobox", "Work model", required=True, dialog="d"))
    for i, label in enumerate(suggestions):
        site["s1"].els.insert(2 + i, El("option", label, dialog="d"))
    return site


_TYPEAHEAD_ANSWERS = {"Data Engineer | Acme | LinkedIn": [
    Q("City", "Milan", ref="auto"),
    Q("Work model", "Hybrid", kind="choice", ref="auto"),
    Q("Resume", None, kind="file", ref="auto", source=None)]}


def test_typeahead_picks_the_suggestion_that_starts_with_the_answer(tmp_path):
    """P4 T1b: no suggestion equals "Hybrid", so the one that starts with it wins — never just the first one.
    "Remote" is listed first on purpose: picking it would fill the application with the wrong answer."""
    site = _typeahead_site("Remote", "Hybrid (3 days onsite)")
    browser, fake = fake_browser(site, "job")
    parked = run_pages(ctx_for(browser, _TYPEAHEAD_ANSWERS, tmp_path, widget_poll_secs=0))
    combo = next(e for e in site["s1"].els if e.role == "combobox")
    assert isinstance(parked, Parked) and fake.sent == []
    assert combo.value == "Hybrid (3 days onsite)"        # not "Remote"


def test_typeahead_without_a_matching_suggestion_is_a_broken_form(tmp_path):
    """A required typeahead nothing matches must not click a stray suggestion."""
    site = _typeahead_site("Remote")                       # nothing starts with "Hybrid"
    browser, fake = fake_browser(site, "job")
    with pytest.raises(NeedsAttention) as exc:
        run_pages(ctx_for(browser, _TYPEAHEAD_ANSWERS, tmp_path, widget_poll_secs=0))
    combo = next(e for e in site["s1"].els if e.role == "combobox")
    assert exc.value.cls == "broken_form" and fake.sent == [] and combo.value != "Remote"


def test_mismatches_holds_after_radio_rerender():
    """P4 T1a: the option_ref is gone, so the read-back asks the question's own group what is checked."""
    from assistant.fill import mismatches
    items = _rerender_items()
    assert mismatches(items, _rerender_page({"g0": "Yes", "g1": "Yes", "g2": "Yes"})) == []
    assert len(mismatches(items, _rerender_page({}))) == 3            # nothing checked => all three report


def test_mismatches_are_scoped_to_one_radio_group():
    """P4 T1a regression: one checked "Yes" must not vouch for the other groups answered "Yes" (DISCOVERY).
    A page-wide label scan reported 0 mismatches here, so three empty required groups looked filled."""
    from assistant.fill import mismatches
    items = _rerender_items()
    bad = mismatches(items, _rerender_page({"g2": "Yes"}))            # only the third group got its toggle
    assert [q.question for q in bad] == _RERENDER_QS[:2]


def test_mismatches_catch_the_wrong_option_in_the_right_group():
    """The group holds an answer, but not the one we asked for."""
    from assistant.fill import mismatches
    items = _rerender_items()
    bad = mismatches(items, _rerender_page({"g0": "No", "g1": "Yes", "g2": "Yes"}))
    assert [q.question for q in bad] == _RERENDER_QS[:1]


def test_mismatches_fail_closed_on_two_groups_with_the_same_text():
    """P4 T1a: _group_label falls back to "Select one" when a group key is opaque, and HANDOVER records such a
    question on Linda AI page 2. Two of them must not let the first group's answer vouch for the second."""
    from assistant.fill import mismatches
    from assistant.browser import Element, Table
    from assistant import pages
    opaque = "urn:li:fsd_formElement:" + "x" * 70            # >80 chars => _group_label uses context/"Select one"
    url = "https://www.linkedin.com/jobs/view/4470454940/"
    els = [Element(ref=f"m{i}{j}", role="radio", name=opt, tag="DIV", scope="form",
                   group=f"{opaque}-{i}", checked=(i == 0 and opt == "Yes"))
           for i in range(2) for j, opt in enumerate(("Yes", "No"))]
    p = pages.Page(url=url, title="t", text="", table=Table(url=url, elements=els))
    q = PageAnswers.model_validate({"questions": [
        Q("Select one", "Yes", kind="choice", option_ref="e200")]}).questions[0]
    assert mismatches([q], p) == [q]      # ambiguous => not held, even though one group holds "Yes"


class _PickDecider:
    """Picks the suggestion labelled `want` in `_typeahead`'s "option" question; every other decision is "none".
    It resolves the ref from the criteria it is handed, so the test never hard-codes an observation ref."""

    def __init__(self, want):
        self.want, self.asked, self.calls = want, [], 0      # `calls` is the Jev budget counter fill.py reads

    def ask(self, kind, state, qs):
        self.asked.append(sorted(qs))
        self.calls += 1

        class _A:
            def __init__(self, choice):
                self.choice = choice

        def answer(key, question):
            if key != "option":
                return "none"
            refs = [r for r, label in question["criteria"].items() if label == self.want]
            return refs[0] if refs else "none"
        return {k: _A(answer(k, v)) for k, v in qs.items()}


def test_typeahead_asks_the_model_between_two_close_suggestions(tmp_path):
    """P4 T1b: "Milan" against "Milan, Lombardy, Italy" and "Milan, MI, US" — both start with the answer, so the
    code cannot tell them apart and asks (T1: "several close candidates → Jev choice")."""
    from assistant import decide
    site = _typeahead_site("Hybrid, 3 days onsite", "Hybrid, 2 days onsite")
    browser, fake = fake_browser(site, "job")
    d = _PickDecider("Hybrid, 2 days onsite")          # the second suggestion, so "first wins" cannot pass this
    decide.use(d)
    try:
        parked = run_pages(ctx_for(browser, _TYPEAHEAD_ANSWERS, tmp_path, widget_poll_secs=0))
    finally:
        decide.use(None)
    combo = next(e for e in site["s1"].els if e.role == "combobox")
    assert isinstance(parked, Parked) and fake.sent == []
    assert ["option"] in d.asked                      # it did ask
    assert combo.value == "Hybrid, 2 days onsite"     # and clicked the model's pick, not the first suggestion


def test_typeahead_does_not_ask_about_unrelated_suggestions(tmp_path):
    """Two suggestions that do not start with the answer are not "close candidates": block, never guess."""
    from assistant import decide
    site = _typeahead_site("Remote", "On-site")
    browser, fake = fake_browser(site, "job")
    d = _PickDecider("nonsense")
    decide.use(d)
    try:
        with pytest.raises(NeedsAttention) as exc:
            run_pages(ctx_for(browser, _TYPEAHEAD_ANSWERS, tmp_path, widget_poll_secs=0))
    finally:
        decide.use(None)
    assert exc.value.cls == "broken_form" and fake.sent == []
    assert ["option"] not in d.asked                  # no model call for an unrelated list


@pytest.mark.parametrize("answer, held, holds", [
    ("Milan", "Milan, Lombardy, Italy", True),          # a typeahead expands the label it accepts
    ("Dublin", "Dublin, County Dublin, Ireland", True),
    ("Hybrid", "Hybrid (3 days onsite)", True),
    ("Milan", "Milan", True),
    ("Milan", "Milano", False),                         # only at a token boundary
    ("1", "10", False),
    ("Hybrid", "Remote", False),                        # a wrong suggestion is still a mismatch
])
def test_fuzzy_holds_accepts_an_expanded_typeahead_label(answer, held, holds):
    """P4 T1b: without this the read-back turns every correct City pick into a false broken_form."""
    from assistant.fill import _fuzzy_holds
    assert _fuzzy_holds(answer, held) is holds


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


def test_a_native_select_reads_back_from_the_control_not_the_option(tmp_path):
    """Live 2026-10-08, Toast: 'Are you currently based in Ireland? (required)' is a native <select> whose
    option values Greenhouse makes opaque hashes. The plan's option_ref is "e31:3", which is NOT a top-level
    element, so `mismatches` fell through to the radio-group fallback — which can never answer for a select —
    and reported a mismatch on a field that was correctly set to "No", twice, → broken_form."""
    from assistant.browser import Element, Option, Table
    from assistant.fill import mismatches
    from assistant.llm_inference import Question
    from assistant.pages import Page

    url = "https://careers.toasttab.com/jobs/software-engineer-ii-iq-grow"
    sel = Element(ref="e31", role="combobox", name="Are you currently based in Ireland? (required)",
                  value="3e6420fe23fa0dd5632749f038b51ae0", current="No", tag="SELECT", required=True,
                  options=[Option(ref="e31:1", label="", value="", selected=False),
                           Option(ref="e31:2", label="Yes", value="81d4831bf958d43c9c970450a1301992",
                                  selected=False),
                           Option(ref="e31:3", label="No", value="3e6420fe23fa0dd5632749f038b51ae0",
                                  selected=True)])
    p = Page(url=url, title="Software Engineer II, IQ Grow", text="Are you currently based in Ireland?",
             table=Table(url=url, elements=[sel]))
    q = Question(id="s_e31", question=sel.name, kind="choice", ref="e31", option_ref="e31:3",
                 options=["Yes", "No"], required=True, answer="No", source="profile", quote="Italy",
                 relies_on=None)
    assert mismatches([q], p) == []
    wrong = q.model_copy(update={"answer": "Yes", "option_ref": "e31:2"})
    assert mismatches([wrong], p) == [wrong]        # a genuinely unset choice is still caught


def test_a_checkbox_answered_no_is_unticked_but_a_radio_is_always_clicked(tmp_path):
    """`state` was hard-coded True, so a toggle could only ever TICK a box — LinkedIn's "Follow <company>"
    default could not be declined (live 2026-10-08). A radio is different: its option_ref already IS the
    option to pick, so "No" there means click the No radio."""
    from assistant.browser import Element, Table
    from assistant.fill import _carry_out
    from assistant.llm_inference import Question
    from assistant.pages import Page

    box = Element(ref="e9", role="checkbox", name="Follow Acme to stay up to date with their page",
                  tag="INPUT", type="checkbox", checked=True)
    no_radio = Element(ref="e151", role="radio", name="Are you legally authorized to work in Ireland?",
                       label="No", tag="DIV")
    by_ref = {e.ref: e for e in (box, no_radio)}

    follow = Question(id="c_e9", question=box.name, kind="choice", ref="e9", option_ref="e9",
                      options=[box.name, "No"], required=False, answer="No", source="computed",
                      quote=None, relies_on=None)
    assert _carry_out(follow, "check", "e9", "e9", by_ref) == {"op": "toggle", "ref": "e9", "state": False}

    keep = follow.model_copy(update={"answer": box.name})
    assert _carry_out(keep, "check", "e9", "e9", by_ref)["state"] is True

    auth = Question(id="r_e151", question=no_radio.name, kind="choice", ref=None, option_ref="e151",
                    options=["Yes", "No"], required=True, answer="No", source="profile", quote="x",
                    relies_on=None)
    assert _carry_out(auth, "check", None, "e151", by_ref) == {"op": "toggle", "ref": "e151", "state": True}


def test_an_unanswered_required_radio_group_parks_at_the_question(tmp_path):
    """US-001's behaviour end to end: with the group marked required (extract_questions does that from the
    page's asterisk — see test_answers.py) and no answer, the loop must raise ParkedAtQuestion and park with
    the question noted, NOT click Review and report "the Easy Apply step did not advance" (live 2026-10-08,
    Linda AI). `_starred_in_text` is unit-tested separately; what this pins is the loop's reaction."""
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q("Are you comfortable working in an onsite setting?", None, kind="choice",
          options=["Yes", "No"], ref=None, option_ref=None, source=None, required=True),
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    onsite = "Are you comfortable working in an onsite setting?"
    site["s1"].els[1:1] = [El("radio", onsite, label="Yes", dialog="d", group=onsite),
                           El("radio", onsite, label="No", dialog="d", group=onsite)]
    browser, fake = fake_browser(site, "job")
    result = run_pages(ctx_for(browser, answers, tmp_path))
    assert isinstance(result, Parked) and fake.sent == []
    assert [o.question for o in result.parked_at] == [onsite]
    assert all(e.checked is not True for e in site["s1"].els if e.role == "radio")   # nothing was guessed


def test_the_follow_checkbox_is_actually_unticked_end_to_end(tmp_path):
    """The reviewer's finding: FOUR places decide what a toggle answer means, and only _carry_out had been
    taught to untick — `_set_option_refs` nulled the "No", `_goal_items` skipped an already-checked box and
    `mismatches` read `checked` as success. So the box stayed ticked AND went back on the Scratch Pad. This
    drives the whole loop over a pre-ticked box and asserts the live DOM state, not an intermediate."""
    # The real element, from runs/20261008-143810/…Digital-Manufacturing-Ireland…: a DIV role=checkbox with
    # no `label`, `checked: true`, inside the Easy Apply <dialog>. The driver reads `aria-checked` for an
    # ARIA checkbox and clicks only when it differs from the requested state (driver/session.py:492-504).
    follow = "Follow Digital Manufacturing Ireland to stay up to date with their page"
    answers = {"Data Engineer | Acme | LinkedIn": [
        Q("City", "Milan", ref="auto"),
        Q(follow, "No", kind="choice", options=[follow, "No"], ref="auto", option_ref="same",
          source="computed", required=False),
        Q("Resume", None, kind="file", ref="auto", source=None)]}
    site = single_dialog()
    box = El("checkbox", follow, dialog="d", checked=True, tag="DIV")
    site["s1"].els.insert(1, box)
    browser, fake = fake_browser(site, "job")
    parked = run_pages(ctx_for(browser, answers, tmp_path))
    assert isinstance(parked, Parked) and fake.sent == []
    assert box.checked is False                                  # unticked on the page
    assert follow not in [o.question for o in parked.optional_empty]      # and off the Scratch Pad
    # The never-submit guard must allow the click: this control is on the review step, so if it were ever
    # reached with FORM.final set it would be refused and the decline would silently not happen.
    from assistant import guard as g
    from assistant.browser import Element
    el = Element(ref="e165", role="checkbox", name=follow, tag="DIV", checked=True, dialog="<dialog>")
    assert g.never_click_element(el, g._FormStage()) is None


def test_a_checkbox_whose_own_label_means_no_is_still_ticked(tmp_path):
    """A word list got this backwards: EEO forms use "Decline" and "None" as the OPTION TEXT, so answering
    such a checkbox with its own label means TICK it. The own-label test has to come before the deny list."""
    from assistant.browser import Element
    from assistant.llm_inference import Question, wants_checked
    for label in ("Decline", "None", "No"):
        el = Element(ref="e7", role="checkbox", name=label, tag="INPUT", type="checkbox")
        q = Question(id="c_e7", question=label, kind="choice", ref="e7", option_ref="e7",
                     options=[label, "No"], required=False, answer=label, source="profile", quote="x",
                     relies_on=None)
        assert wants_checked(q, el) is True, label
    # A radio's answer is always its own option label, so "No" there is a click, never a clear.
    no_radio = Element(ref="e151", role="radio", name="Are you legally authorized to work in Ireland?",
                       label="No", tag="DIV")
    auth = Question(id="r_e151", question=no_radio.name, kind="choice", ref=None, option_ref="e151",
                    options=["Yes", "No"], required=True, answer="No", source="profile", quote="x",
                    relies_on=None)
    assert wants_checked(auth, no_radio) is True
