"""Live: Jev (typesafe/jev-1.13 on OpenRouter) makes the program's decisions on real pages — the six pages of the run
that sent every job to Needs-Attention (runs/20260923-224945), pages captured from real sites, every fixture page,
and the question checks of llm_inference.py. Runs with --live; about $0.0003 per page."""
from pathlib import Path

import pytest

from assistant import pages, probes
from assistant.llm_inference import PageAnswers, judge_answers, judge_questions
from assistant.browser import Table
from assistant.pages import FORM_KINDS, Page, classify, classify_entry, judge, page_from_capture, read_page
from tests.test_pages_browser import ENTRY, KINDS

pytestmark = pytest.mark.live_model
CAPTURED = Path(__file__).parent / "captured"
AT_FORM = {"form", "final"}          # a one-page form is its own last step: either answer puts the loop at the form


def captured(name: str) -> Page:
    """A captured page as the run read it: the element table plus the probes taken with it."""
    d = CAPTURED / name
    p = page_from_capture((d / "observe.txt").read_text())
    if (d / "probes.txt").exists():
        pr = dict(zip(pages.PAGE_PROBES, probes.parse_eval_results((d / "probes.txt").read_text())))
        pages.enrich(p.table, pr)
        p.required_empty, p.maxlengths, p.iframe_srcs = pr["REQUIRED_EMPTY"], pr["MAXLENGTHS"], pr["IFRAME_SRCS"]
    return p


def names(els) -> set[str]:
    return {e.name for e in els}


# ------------------------------------------------------------------ the run that failed, page by page

def test_linda_ai_safety_reminder_is_between_the_posting_and_the_form():
    p = captured("run-20260923-linda")
    assert judge(p).kind == "interstitial" and not pages.form_is_here(p), judge(p)


def test_mastercard_job_alert_box_is_not_the_application():
    p = captured("run-20260923-mastercard")
    assert judge(p).kind == "job_posting", judge(p)
    assert "Please enter your email address" not in names(pages.real_fields(p))
    assert not pages.form_is_here(p)


def test_toast_form_under_a_cookie_pop_up_is_covered():
    p = captured("run-20260923-toast")
    j = judge(p)
    assert j.covered and not pages.form_is_here(p), j
    assert {"Legal First Name (required) e6bb177b", "Email (required) 54e4089a"} <= names(pages.real_fields(p))


def test_digital_manufacturing_radio_step_is_a_form_step():
    p = captured("run-20260923-dmi")
    assert judge(p).kind in FORM_KINDS and pages.form_is_here(p), judge(p)
    assert "Are you currently a resident of Ireland?" in names(pages.real_fields(p))


def test_genesys_posting_is_open_and_its_language_picker_is_not_a_question():
    p = captured("run-20260923-genesys")
    assert classify_entry(p) == "open", judge(p)
    assert "Select language" not in names(pages.real_fields(p))


def test_the_flex_ashby_form_is_the_form_and_has_a_resume_upload():
    p = captured("run-20260923-theflex")
    j = judge(p)
    assert j.kind in FORM_KINDS and "Full Name" in names(pages.real_fields(p)), j
    assert j.resume_ref is not None


# ------------------------------------------------------------------ pages captured from real sites

REAL = {
    "career-nota-ai-20260923-150502": {"navigate"},
    "job-boards-greenhouse-io-20260923-150510": AT_FORM,
    "jobs-ashbyhq-com-20260923-150512": {"navigate"},
    "jobs-ashbyhq-com-20260923-150816": AT_FORM,
    "nvidia-wd5-myworkdayjobs-com-20260923-144704": {"navigate"},
}


@pytest.mark.parametrize("name", sorted(REAL))
def test_captured_real_pages(name):
    p = captured(name)
    assert classify(p).kind in REAL[name], (name, judge(p))


@pytest.mark.parametrize("name", ["www-linkedin-com-20260923-123939", "jobs-ashbyhq-com-20260923-144702"])
def test_pages_captured_before_they_were_drawn_are_waited_for(name):
    assert pages.unsettled(captured(name)), judge(captured(name))


# ------------------------------------------------------------------ fixture pages, in the throwaway Chrome

def _page(new_browser, url):
    with new_browser() as browser:
        browser.open(url, "jev")
        p = read_page(browser, "jev")
        browser.close("jev")
    return p


@pytest.mark.browser
@pytest.mark.parametrize("name", sorted(KINDS))
def test_fixture_kinds(new_browser, fixture_server, name):
    p = _page(new_browser, fixture_server.url(name))
    want = AT_FORM if KINDS[name] in AT_FORM else {KINDS[name]}
    # at the form — or a cookie banner judged to be in the way, which the agent clears first (f06)
    assert classify(p).kind in want or (KINDS[name] in AT_FORM and judge(p).covered), (name, judge(p))


@pytest.mark.browser
@pytest.mark.parametrize("name", sorted(ENTRY))
def test_fixture_posting_states(new_browser, fixture_server, name):
    p = _page(new_browser, fixture_server.url(name))
    assert classify_entry(p) == ENTRY[name], (name, judge(p))


# ------------------------------------------------------------------ llm_inference.py's questions

def _q(question, *, source=None, kind="text", options=None):
    return {"id": "q", "question": question, "kind": kind, "ref": None, "option_ref": None, "options": options,
            "required": True, "answer": "x", "source": source, "quote": None, "relies_on": None}


def test_answer_checks():
    pa = PageAnswers.model_validate({"questions": [
        _q("How many years of work experience do you have with C++?", source="computed"),
        _q("How many years of professional experience do you have?", source="computed"),
        _q("What are your salary expectations?", source="generated"),
        _q("Why do you want to work at Acme?", source="generated", kind="longtext")]})
    v = judge_answers(pa, Page(url="https://acme.io/apply", title="Apply", text="", table=Table(url="u")))
    assert v.total_years == {"How many years of professional experience do you have?"}
    assert v.must_not_generate == {"What are your salary expectations?"}


def test_resume_and_cover_letter_questions():
    pa = PageAnswers.model_validate({"questions": [
        _q("Resume*", kind="choice", options=["Amin_Old.pdf", "Amin_Acme.pdf"]), _q("Cover Letter", kind="file"),
        _q("Mobile phone number*")]})
    judge_questions(pa, Page(url="https://acme.io/apply", title="Apply", text="", table=Table(url="u")))
    assert [q.resume_upload for q in pa.questions] == [True, False, False]
    assert [q.cover_letter for q in pa.questions] == [False, True, False]


def test_read_back_on_toasts_filled_greenhouse_form():
    """Toast, live 2026-09-24: "No" was selected in e31 (named by its option ref e31:3) and the phone widget showed
    the typed "351 935 8813" as "+393519358813". The old rules failed the first; exact matching would fail the second."""
    from assistant.llm_inference import Question
    from assistant.fill import mismatches
    p = captured("run-20260924-toast-filled")
    base = dict(id="x", kind="choice", options=None, required=True, source="profile", quote="x", relies_on=None)
    q = lambda question, answer, ref, option_ref=None: Question(**base, question=question, answer=answer, ref=ref,
                                                                 option_ref=option_ref)
    based_no = q("Are you currently based in Ireland? (required)", "No", "e31", "e31:3")
    based_yes = q("Are you currently based in Ireland? (required)", "Yes", "e31", "e31:2")
    phone = q("Phone (required)", "351 935 8813", "e25")
    email = q("Email (required)", "abbaszadehmohammadamin@yahoo.com", "e23")
    location = q("Location (required)", "Dublin, Ireland", "e26")
    assert mismatches([based_no, based_yes, phone, email, location], p) == [based_yes, location]


def _q(question, answer, ref=None, option_ref=None):
    from assistant.llm_inference import Question
    return Question(id="x", kind="text", options=None, required=True, source="profile", quote="x", relies_on=None,
                    question=question, answer=answer, ref=ref, option_ref=option_ref)


def test_fill_plan_on_toasts_greenhouse_form():
    """Jev picks the operation and the field, with or without the LLM inference's refs as hints (2026-09-24)."""
    from assistant.fill import plan_fill
    p = captured("run-20260924-toast-filled")
    plan = plan_fill([_q("Legal First Name (required)", "Amin", "e21"),
                      _q("Are you currently based in Ireland? (required)", "No", "e31", "e31:3"),
                      _q("Are you currently based in Ireland? (required)", "No"),
                      _q("Website", "https://maaz77.github.io")], p)
    assert [(op or {}).get("op") for op in plan] == ["type", "select", "select", "type"]
    assert [op["ref"] for op in plan] == ["e21", "e31", "e31", "e30"] and plan[1]["value"] == plan[2]["value"] == "No"


def test_fill_plan_on_a_linkedin_radio_step():
    from assistant.fill import plan_fill
    p = captured("run-20260923-dmi")
    plan = plan_fill([_q("Are you currently a resident of Ireland?", "No"),
                      _q("Do you require a visa or work permit to work in Ireland?", "Yes")], p)
    assert plan == [{"op": "toggle", "ref": "e221", "state": True}, {"op": "toggle", "ref": "e222", "state": True}]
