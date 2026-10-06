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
