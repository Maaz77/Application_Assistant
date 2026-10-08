"""P5: the external-ATS loop's safety predicate looks_like_application (fill vs unsupported_ats)."""
import pytest

from assistant import external
from assistant.browser import Element, Table
from assistant.pages import Page

pytestmark = pytest.mark.unit


def page(elements, url="https://boards.greenhouse.io/acme/jobs/1", text=""):
    return Page(url=url, title="Apply", text=text, table=Table(url=url, elements=elements))


def test_resume_upload_is_an_application():
    p = page([Element(ref="e1", role="file", name="Resume/CV"),
              Element(ref="e2", role="button", name="Submit application")])
    assert external.looks_like_application(p) is True


def test_name_and_email_is_an_application():
    p = page([Element(ref="e1", role="textbox", name="Full name"),
              Element(ref="e2", role="textbox", name="Email"),
              Element(ref="e3", role="button", name="Apply now!")])
    assert external.looks_like_application(p) is True


def test_lone_email_box_is_not_an_application():
    """The Mastercard job-alert bug (DISCOVERY ~198): a single email box is not an application form."""
    p = page([Element(ref="e1", role="textbox", name="Email address"),
              Element(ref="e2", role="button", name="Set alert for similar jobs")])
    assert external.looks_like_application(p) is False


def test_company_name_plus_email_is_not_an_application():
    """A 'Company name' + email (e.g. a sales/contact form) is not a candidate's identity."""
    p = page([Element(ref="e1", role="textbox", name="Company name"),
              Element(ref="e2", role="textbox", name="Work email")])
    assert external.looks_like_application(p) is False


# --- the cookie-consent modal that blocked Toast (live 2026-10-08) ----------------------------------------

def test_a_consent_modal_is_declined_before_the_external_form_is_filled(tmp_path):
    """careers.toasttab.com opens a native modal <dialog> named "Cookie consent" over its embedded Greenhouse
    form. A modal dialog makes the rest of the page inert, so all eight fields failed to take a value, twice,
    and the job became broken_form. It also makes pages.covered true, which hides the form from the hand-off.
    The reject control must be clicked before anything else is attempted."""
    from assistant.blockers import Parked
    from assistant.pages import read_page
    from tests.fake_browser import El, FakePage, fake_browser
    from tests.test_fill_loop import Q, ctx_for

    ats = "https://careers.toasttab.com/jobs/software-engineer-ii-iq-grow"

    def form(refuse):
        return [El("textbox", "Legal First Name", required=True, refuse_typing=refuse),
                El("file", "Resume", required=True),
                El("button", "Submit application", submits=True)]

    site = {
        "job": FakePage("https://www.linkedin.com/jobs/view/4470928514/",
                        "Software Engineer II | Toast | LinkedIn", "Software Engineer II, IQ Grow. About the job.",
                        [El("link", "Apply on company website", goto="ats")]),
        # The form is listed but inert behind the modal: a typed value does not stick.
        "ats": FakePage(ats, "Software Engineer II, IQ Grow", "We use cookies", [
            El("button", "I do not accept", goto="ats_clean", dialog="<dialog>"),
            El("button", "I accept", dialog="<dialog>"),
            *form(refuse=True)], modal="Cookie consent"),
        "ats_clean": FakePage(ats, "Software Engineer II, IQ Grow", "Apply for this job", form(refuse=False)),
    }
    browser, fake = fake_browser(site, "job")
    assert read_page(browser, "job").dialogs == []                       # the posting has no modal
    answers = {"Software Engineer II, IQ Grow": [Q("Legal First Name", "Amin", ref="auto"),
                                                 Q("Resume", None, kind="file", ref="auto", source=None)]}
    parked = external.run_external(ctx_for(browser, answers, tmp_path))
    assert isinstance(parked, Parked) and fake.sent == []
    assert fake.cur == "ats_clean"                       # the reject was clicked and the form became fillable
    assert next(e.value for e in site["ats_clean"].els if e.name == "Legal First Name") == "Amin"
