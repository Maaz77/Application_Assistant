"""Classifier on every fixture (§9), read live from the throwaway Chrome."""
import pytest

from assistant import pages
from assistant.pages import classify, classify_entry, read_page

pytestmark = pytest.mark.browser

KINDS = {
    "f01.html": "form", "f02.html": "form", "f03.html": "form", "f04.html": "form", "f05.html": "form",
    "f06.html": "cookie", "f07.html": "form", "f08.html": "form", "f09.html": "form",
    "f10.html": "ats_entry", "f10_form.html": "form", "f13.html": "form", "f14.html": "alarm",
    "f15_google.html": "google_wall", "f16_signup.html": "blocker", "f17_captcha.html": "blocker",
    "f18_cookie.html": "cookie", "uas/login.html": "blocker",
    "jobs/view/4012345605-submitted.html": "alarm",
}
ENTRY = {
    "jobs/view/4012345601-easy.html": "entry", "jobs/view/4012345602-closed.html": "closed",
    "jobs/view/4012345603-applied.html": "applied", "jobs/view/4012345604-external.html": "entry",
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
    assert pages.is_final(f01) and not pages.has_advance(f01)
    f02 = _page(new_browser, fixture_server.url("f02.html"))
    assert pages.has_advance(f02) and not pages.has_transmit(f02)      # hidden steps are not listed
    f06 = _page(new_browser, fixture_server.url("f06.html"))
    assert classify(f06).detail == "Reject all"
    f18 = _page(new_browser, fixture_server.url("f18_cookie.html"))
    assert classify(f18).detail == "Only necessary"
    f16 = _page(new_browser, fixture_server.url("f16_signup.html"))
    assert classify(f16).detail.startswith("signup") and pages.guest_link(f16).name == "Apply without an account"
    f17 = _page(new_browser, fixture_server.url("f17_captcha.html"))
    assert f17.captcha and classify(f17).detail.startswith("captcha")
    f10 = _page(new_browser, fixture_server.url("f10.html"))
    assert not pages.has_transmit(f10)                                  # entry labels don't count without fields


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
        nxt = pages.advance_buttons(p)[0]
        browser.act([{"op": "click", "ref": nxt.ref}], "v", p.table)
        p = read_page(browser, "v")
        browser.close("v")
    assert pages.validation_error(p) == "This field is required"
    assert pages.gate(p, "Amin_Acme.pdf", {}).startswith("required fields still empty")


def test_guest_link_click_on_real_signup_page(new_browser, fixture_server):
    from assistant.entry import guest_click
    with new_browser() as browser:
        browser.open(fixture_server.url("f16_signup.html"), "g")
        p = read_page(browser, "g")
        out = guest_click(browser, "g", p.table)
        after = read_page(browser, "g")
        browser.close("g")
    assert "needs_confirmation" not in out and after.title == "f01 single page"
    assert fixture_server.posts() == []
