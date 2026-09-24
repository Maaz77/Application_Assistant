"""Classifier on every fixture (§9), read live from the throwaway Chrome."""
import pytest

from assistant import pages
from assistant.pages import classify, classify_entry, read_page

pytestmark = pytest.mark.browser

# What each fixture page is (pages.classify). The same table runs offline, answered by tests/rule_decider.py, and
# with --live (tests/test_decisions_live.py), answered by Jev.
KINDS = {
    "f01.html": "final", "f02.html": "form", "f03.html": "final", "f04.html": "form", "f05.html": "final",
    "f06.html": "final", "f07.html": "final", "f08.html": "form", "f09.html": "form",
    "f10.html": "navigate", "f10_form.html": "final", "f13.html": "form", "f14.html": "alarm",
    "f15_google.html": "google_wall", "f16_signup.html": "blocker", "f17_captcha.html": "blocker",
    "f18_cookie.html": "form", "uas/login.html": "blocker",
    "jobs/view/4012345605-submitted.html": "alarm",
}
ENTRY = {
    "jobs/view/4012345601-easy.html": "open", "jobs/view/4012345602-closed.html": "closed",
    "jobs/view/4012345603-applied.html": "applied", "jobs/view/4012345604-external.html": "open",
    "jobs/view/4012345605-submitted.html": "applied", "uas/login.html": "signed_out",
}


def _page(new_browser, url):
    with new_browser() as browser:
        browser.open(url, "cls")
        p = read_page(browser, "cls")
        browser.close("cls")
    return p


@pytest.mark.parametrize("name", sorted(KINDS))
def test_classify_fixture(new_browser, fixture_server, name):
    p = _page(new_browser, fixture_server.url(name))
    assert classify(p).kind == KINDS[name], (name, classify(p), p.title)
    assert fixture_server.posts() == []


@pytest.mark.parametrize("name", sorted(ENTRY))
def test_classify_linkedin_entry(new_browser, fixture_server, name):
    p = _page(new_browser, fixture_server.url(name))
    assert classify_entry(p) == ENTRY[name]


def test_flags_and_details(new_browser, fixture_server):
    f01 = _page(new_browser, fixture_server.url("f01.html"))
    assert pages.is_final(f01) and pages.judge(f01).submit_button
    f02 = _page(new_browser, fixture_server.url("f02.html"))
    assert not pages.is_final(f02) and not pages.judge(f02).submit_button      # hidden steps are not listed
    f16 = _page(new_browser, fixture_server.url("f16_signup.html"))
    assert classify(f16).detail.startswith("signup") and pages.guest_link(f16).name == "Apply without an account"
    f17 = _page(new_browser, fixture_server.url("f17_captcha.html"))
    assert f17.captcha and classify(f17).detail.startswith("captcha")
    f10 = _page(new_browser, fixture_server.url("f10.html"))
    assert not pages.judge(f10).submit_button and pages.judge(f10).kind == "job_posting"


def test_f12_iframe_from_second_origin(new_browser, fixture_server):
    other = fixture_server.url("f12_form.html").replace("127.0.0.1", "localhost")
    p = _page(new_browser, fixture_server.url("f12.html") + "?form=" + other)
    v = classify(p)
    # cross-origin frames are not merged into the table (DISCOVERY.md) → navigate the tab to the src
    assert v.kind == "iframe" and v.detail == other, v


def test_f13_validation_after_advance(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("f13.html"), "v")
        p = read_page(browser, "v")
        assert pages.validation_error(p) is None
        nxt = next(e for e in pages.buttons(p) if e.name == "Next")
        browser.act([{"op": "click", "ref": nxt.ref}], "v", p.table)
        p = read_page(browser, "v")
        browser.close("v")
    assert pages.validation_error(p)
    assert pages.gate(p, "Amin_Acme.pdf", {}).startswith("required fields still empty")


def test_guest_link_click_on_real_signup_page(new_browser, fixture_server):
    from assistant.entry import guest_click
    with new_browser() as browser:
        browser.open(fixture_server.url("f16_signup.html"), "g")
        p = read_page(browser, "g")
        out = guest_click(browser, "g", p)
        after = read_page(browser, "g")
        browser.close("g")
    assert "needs_confirmation" not in out and after.title == "f01 single page"
    assert fixture_server.posts() == []


def test_late_rendered_linkedin_page_is_waited_for(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("jobs/view/4012345606-late.html"), "late")
        early = read_page(browser, "late")                              # control: read at once, too early
        settled = pages.settle(lambda: read_page(browser, "late"))
        browser.close("late")
    assert classify_entry(early) == "none"
    assert classify_entry(settled) == "open"


def test_display_contents_wrapper_is_seen(new_browser, fixture_server):
    """Stock 0.1.5 dropped everything under a display:contents wrapper (LinkedIn's job card); 0.1.5+aa1 fixes it."""
    with new_browser() as browser:
        browser.open(fixture_server.url("probe_lab/display_contents.html"), "dc")
        p = read_page(browser, "dc")
        browser.close("dc")
    assert "Easy Apply" in [e.name for e in p.elements]
    assert "real-time systems in modern C++" in p.text
