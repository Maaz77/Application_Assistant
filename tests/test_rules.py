"""T8 rule tests: entry click (§4.2), Google one-click (§6.2), blockers and attempt 2 (§6.3)."""
import pytest

from assistant.blockers import Attempts, NeedsAttention
from assistant.entry import EntryRefused, confirm_click
from assistant.pages import read_page
from assistant.fill import run_pages
from assistant.google_signin import sign_in
from assistant.jev import Element, Table
from tests.fake_mcp import El, FakeBook, FakeMCP, FakePage
from tests.test_fill_loop import SINGLE_ANSWERS, ctx_for, single_page

pytestmark = pytest.mark.unit
LI = "https://www.linkedin.com/jobs/view/4012345678/"


def T(url, *els):
    return Table(url=url, elements=list(els))


def _confirm(fake, label):
    return confirm_click(fake, "s", label, lambda: read_page(fake, "s"), lambda _: None)


def _confirms(fake):
    return [op for n, a in fake.log if n == "browser_act" for op in a["ops"] if op.get("confirm")]


def test_confirm_click_rules():
    fake = FakeMCP({"j": FakePage(LI, "Job", "Data Engineer", [El("button", "Easy Apply to Data Engineer at Acme"),
                                                               El("button", "Save")])}, "j")
    _confirm(fake, "Easy Apply to Data Engineer at Acme")
    assert _confirms(fake) == [{"op": "click", "ref": "e1", "confirm": True}]
    with pytest.raises(EntryRefused, match="does not start"):
        _confirm(fake, "Save")
    fake.site["j"].els.insert(0, El("textbox", "q", value="x"))
    with pytest.raises(EntryRefused, match="already holds a value"):
        _confirm(fake, "Easy Apply to Data Engineer at Acme")


def test_confirm_click_is_the_only_confirm_and_never_a_submit():
    fake = FakeMCP({"j": FakePage(LI, "Job", "Data Engineer", [El("button", "Easy Apply"),
                                                               El("button", "Submit application")])}, "j")
    _confirm(fake, "Easy Apply")
    with pytest.raises(EntryRefused):
        _confirm(fake, "Submit application")
    assert len(_confirms(fake)) == 1 and fake.sent == []


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


def test_apply_labelled_guest_link_goes_through_entry_py(tmp_path):
    fake = FakeMCP(signup_site("Apply without an account"), "signup")
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1 and fake.sent == []
    confirms = [op for n, a in fake.log if n == "browser_act" for op in a["ops"] if op.get("confirm")]
    assert [fake.site["signup"].els[3].name] == ["Apply without an account"] and len(confirms) == 1


def test_guest_link_refused_when_a_field_holds_a_value(tmp_path):
    site = signup_site("Apply without an account")
    site["signup"].els[0].value = "amin@example.com"             # something typed must never go out with it
    fake = FakeMCP(site, "signup")
    with pytest.raises(NeedsAttention, match="already holds a value"):
        run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert not any(op.get("confirm") for n, a in fake.log if n == "browser_act" for op in a["ops"])


def test_guard_guest_label_needs_the_entry_token():
    from assistant.guard import ENTRY, GuardError, check
    t = T("https://acme.io/register", Element(ref="e1", role="link", name="Apply without an account"),
          Element(ref="e2", role="button", name="Submit application"))
    check({"op": "click", "ref": "e1", "confirm": True}, t, ENTRY)
    with pytest.raises(GuardError):
        check({"op": "click", "ref": "e1", "confirm": True}, t)
    with pytest.raises(GuardError):
        check({"op": "click", "ref": "e2", "confirm": True}, t, ENTRY)   # the token never unlocks Submit


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
    orig = fake._click
    # the server lets "Apply" through only with confirm (B1); the page opens the form on that click
    fake._click = lambda e, confirm=False: (setattr(fake, "cur", e.goto) if e.name == "Apply" and confirm
                                            else orig(e, confirm))
    parked = run_pages(ctx_for(fake, SINGLE_ANSWERS, tmp_path))
    assert parked.pages == 1
    confirms = [op for n, a in fake.log if n == "browser_act" for op in a["ops"] if op.get("confirm")]
    assert len(confirms) == 1 and confirms[0]["op"] == "click"


def test_entry_rules_ignore_site_chrome():
    """LinkedIn (2026-09-23): nav Search box + footer 'Select language' (always holds en_US) are not form fields."""
    els = [El("combobox", "Search"), El("button", "Easy Apply to this job"),
           El("combobox", "Select language", value="en_US", options=["en_US"])]
    fake = FakeMCP({"j": FakePage(LI, "Job", "Data Engineer", els)}, "j")
    _confirm(fake, "Easy Apply to this job")
    fake.site["j"].els.append(El("textbox", "Phone", value="+39 333"))
    with pytest.raises(EntryRefused, match="already holds a value"):     # a real typed value still blocks it
        _confirm(fake, "Easy Apply to this job")
    other = FakeMCP({"j": FakePage(LI, "Job", "x", [El("textbox", "Preferred language", value="Italian"),
                                                   El("button", "Easy Apply")])}, "j")
    with pytest.raises(EntryRefused, match="already holds a value"):     # only the site's own picker is ignored
        _confirm(other, "Easy Apply")


def test_confirm_click_retries_while_the_page_re_renders_the_button():
    """LinkedIn, live 2026-09-23: `x click e16  detached` — React replaced the button between snapshot and click."""
    fake = FakeMCP({"j": FakePage(LI, "Job", "Data Engineer", [El("button", "Easy Apply to this job")])}, "j")
    clicks = {"n": 0, "error": "detached: detached"}
    orig = fake._browser_act

    def act(ops, **kw):
        if ops and ops[0].get("op") == "click":
            clicks["n"] += 1
            if clicks["n"] == 1:
                return "0/1 ops ok  (stopped early)\n  x click e1  " + clicks["error"]
        return orig(ops, **kw)
    fake._browser_act = act
    assert _confirm(fake, "Easy Apply to this job").startswith("1/1 ops ok") and clicks["n"] == 2
    clicks.update(n=0, error="occluded: occluded")
    with pytest.raises(EntryRefused, match="did not go through"):
        _confirm(fake, "Easy Apply to this job")                           # not a re-render: reported, never retried


def test_long_job_card_links_do_not_make_a_final_step():
    from assistant.pages import Page, is_final, judge
    card = Element(ref="e1", role="link", name="More options Acme Kildare (Hybrid) 50K EUR/yr Promoted · Easy Apply")
    p = Page(url=LI, title="Job", text="", table=T(LI, card))
    assert not judge(p).submit_button and not is_final(p)
    p2 = Page(url=LI, title="Apply", text="", table=T(LI, Element(ref="e2", role="link", name="Submit application")))
    assert judge(p2).submit_button
