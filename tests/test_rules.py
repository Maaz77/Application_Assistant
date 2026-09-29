"""Rule tests (P2): the never-submit rule on the deterministic Easy Apply loop, the Attempts blocker logic,
and the job-card judgment. The Google one-click, sign-up-wall and external-ATS-entry rule tests are dropped:
P2 is LinkedIn Easy Apply only (an external Apply is Needs Attention external_ats, tested in test_fill_loop /
test_navigate), and those paths are P5's."""
import pytest

from assistant.blockers import Attempts, NeedsAttention
from assistant.fill import run_pages
from assistant.browser import Element, Table
from tests.fake_browser import El, FakePage, fake_browser
from tests.test_fill_loop import SINGLE_ANSWERS, ctx_for, single_dialog, posting

pytestmark = pytest.mark.unit
LI = "https://www.linkedin.com/jobs/view/4012345678/"


def T(url, *els):
    return Table(url=url, elements=list(els))


def _confirms(fake):
    return [op for k, op in fake.log if op.get("confirm")]


def test_the_entry_easy_apply_click_needs_no_confirm(tmp_path):
    """Before the form is being filled, "Easy Apply" is clicked by the program like any button: no confirm."""
    browser, fake = fake_browser(single_dialog(), "job")
    run_pages(ctx_for(browser, SINGLE_ANSWERS, tmp_path))
    assert fake.sent == [] and _confirms(fake) == []                 # the Easy Apply click went through, no confirm
    assert any(k == "click" for k, _ in fake.log)


def test_an_apply_now_submit_in_the_dialog_is_the_final_step(tmp_path):
    """A dialog whose last control is a structural submit reading "Apply now!" is refused once filling started,
    and the job parks on it (never submitted)."""
    site = single_dialog()
    site["s1"].els[-1] = El("button", "Apply now!", submits=True, dialog="d")
    browser, fake = fake_browser(site, "job")
    run_pages(ctx_for(browser, SINGLE_ANSWERS, tmp_path))
    assert fake.sent == [] and _confirms(fake) == []


def test_attempt_rules():
    a = Attempts()
    a.fail("captcha", "x")                                 # attempt 2 allowed
    with pytest.raises(NeedsAttention, match="after 2 attempts"):
        a.fail("captcha", "x")
    with pytest.raises(NeedsAttention):
        Attempts().fail("credentials", "password")         # no attempt 2 for credentials


def test_long_job_card_links_do_not_make_a_final_step():
    from assistant.pages import Page, is_final, judge
    card = Element(ref="e1", role="link", name="More options Acme Kildare (Hybrid) 50K EUR/yr Promoted · Easy Apply")
    p = Page(url=LI, title="Job", text="", table=T(LI, card))
    assert not judge(p).submit_button and not is_final(p)
    p2 = Page(url=LI, title="Apply", text="", table=T(LI, Element(ref="e2", role="link", name="Submit application")))
    assert judge(p2).submit_button
