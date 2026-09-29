"""T8 rule tests: the never-submit rule on the page loop, Google one-click (§6.2), blockers and attempt 2 (§6.3)."""
import pytest

from assistant.blockers import Attempts, NeedsAttention
from assistant.fill import run_pages
from assistant.google_signin import sign_in
from assistant.browser import Element, Table
from tests.fake_mcp import El, FakeBook, FakeMCP, FakePage
from tests.test_fill_loop import SINGLE_ANSWERS, ctx_for, single_page

pytestmark = pytest.mark.unit
LI = "https://www.linkedin.com/jobs/view/4012345678/"


def T(url, *els):
    return Table(url=url, elements=list(els))


def _confirms(fake):
    return [op for n, a in fake.log if n == "browser_act" for op in a["ops"] if op.get("confirm")]


def test_the_agents_apply_on_a_job_page_is_its_own_click(tmp_path):
    """Before the form is being filled, "Apply" / "Easy Apply" is clicked by the agent like any button (user decision
    2026-09-24): no confirm, no extra checks."""
    fake = FakeMCP({"j": FakePage(LI, "Job", "Data Engineer", [El("button", "Easy Apply to Data Engineer at Acme",
                                                                     goto="p1"), El("button", "Save")]),
                    **single_page()}, "j")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and fake.sent == [] and _confirms(fake) == []


def test_a_forms_own_apply_is_the_last_step_once_filling_started(tmp_path):
    """Toast's Greenhouse form ends in "Apply now!": once filling started it is refused like Submit, and the job
    parks on it."""
    site = single_page()
    site["p1"].els[-1] = El("button", "Apply now!", submits=True)
    fake = FakeMCP(site, "p1")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and fake.sent == [] and _confirms(fake) == []


def google_site(chooser_email="maaz1377.aa@gmail.com", google_text="Choose an account", after_text="Apply. Resume"):
    site = single_page()
    site["wall"] = FakePage("https://acme.io/login", "Sign in", "Sign in to apply", [
        El("button", "Sign in with Google", goto="google")])
    site["google"] = FakePage("https://accounts.google.com/o/oauth2", "Sign in – Google", google_text, [
        El("link", f"Amin Abbaszadeh {chooser_email}", goto="p1"), El("link", "Use another account")])
    site["p1"].text = after_text + " " + site["p1"].text
    return site


def test_google_one_click_returns_to_the_site():
    fake = FakeMCP(google_site(), "wall")
    sign_in(fake, "s", FakeBook(), "maaz1377.aa@gmail.com", set())
    assert fake.cur == "p1"
    clicks = [op for n, a in fake.log if n == "browser_act" for op in a["ops"] if op["op"] == "click"]
    assert len(clicks) == 2                                            # the Google button, then the account, once


@pytest.mark.parametrize("site_kw, email, match", [
    ({}, "", "no google.account_email"),
    ({"chooser_email": "someone@else.com"}, "maaz1377.aa@gmail.com", "does not offer"),
    ({"google_text": "Enter the code from 2-Step Verification"}, "maaz1377.aa@gmail.com", "2-step"),
    ({"google_text": "Acme wants to access your Google Account. Allow"}, "maaz1377.aa@gmail.com", "consent"),
    ({"after_text": "Complete your registration to continue."}, "maaz1377.aa@gmail.com", "registration"),
])
def test_google_blockers(site_kw, email, match):
    fake = FakeMCP(google_site(**site_kw), "wall")
    with pytest.raises(NeedsAttention, match=match):
        sign_in(fake, "s", FakeBook(), email, set())


def test_google_wall_inside_the_loop_then_park(tmp_path):
    fake = FakeMCP(google_site(), "wall")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path, google_email="maaz1377.aa@gmail.com"))
    assert parked.pages == 1 and fake.sent == []


def test_attempt_rules():
    a = Attempts()
    a.fail("captcha", "x")                                 # attempt 2 allowed
    with pytest.raises(NeedsAttention, match="after 2 attempts"):
        a.fail("captcha", "x")
    with pytest.raises(NeedsAttention):
        Attempts().fail("credentials", "password")         # no attempt 2 for credentials


def signup_site(guest_label):
    site = single_page()
    site["signup"] = FakePage("https://acme.io/register", "Create account", "Create account to apply", [
        El("textbox", "Email"), El("textbox", "Password"), El("button", "Register"),
        El("link", guest_label, goto="p1")])
    return site


def test_signup_wall_uses_the_guest_link(tmp_path):
    fake = FakeMCP(signup_site("Continue as guest"), "signup")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1


def test_apply_labelled_guest_link_is_clicked_without_confirm(tmp_path):
    fake = FakeMCP(signup_site("Apply without an account"), "signup")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and fake.sent == [] and _confirms(fake) == []


def test_password_wall_without_google_is_credentials(tmp_path):
    site = {"login": FakePage("https://acme.io/login", "Log in", "Log in", [
        El("textbox", "Email"), El("textbox", "Password"), El("button", "Log in")])}
    with pytest.raises(NeedsAttention) as ei:
        run_pages(ctx_for(FakeMCP(site, "login"), {}, tmp_path))
    assert ei.value.cls == "credentials"


def test_ats_job_page_entry_then_form(tmp_path):
    site = single_page()
    site["ats"] = FakePage("https://acme.io/jobs/42", "Data Engineer", "Data Engineer at Acme", [
        El("link", "Apply", goto="p1")])
    fake = FakeMCP(site, "ats")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and _confirms(fake) == []


def test_long_job_card_links_do_not_make_a_final_step():
    from assistant.pages import Page, is_final, judge
    card = Element(ref="e1", role="link", name="More options Acme Kildare (Hybrid) 50K EUR/yr Promoted · Easy Apply")
    p = Page(url=LI, title="Job", text="", table=T(LI, card))
    assert not judge(p).submit_button and not is_final(p)
    p2 = Page(url=LI, title="Apply", text="", table=T(LI, Element(ref="e2", role="link", name="Submit application")))
    assert judge(p2).submit_button
