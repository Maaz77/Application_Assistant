"""Tripwire (§9, P2): drive every op the program can emit at the local forms that end in "Submit", "Send" or
"Apply", with the form being filled, and prove nothing was ever sent.

The never-submit rule (guard.never_click_element) refuses submit-labelled and structural-submit controls
everywhere and "Apply" once the form is being filled; the owned driver applies it in its press path to every
click, a toggle, an upload and a type's focus click. Forms that send on "Confirm"/"Done" (f06, f07) or Enter
(f04) are outside the rule by the 2026-09-24 decision, so they are not walked here: a walk would send them —
and the driver has no op that sends Enter anyway (test_guard.py documents both)."""
import re
import time

import pytest

from assistant import guard
from assistant.browser import Browser
from tests.rule_decider import is_advance

TRIPWIRE = ["f01.html", "f02.html", "f03.html", "f05.html"]   # Submit application / Submit / Apply / Send application
FIELD_ROLES = {"textbox", "searchbox", "spinbutton"}


@pytest.fixture(autouse=True)
def form_started():
    guard.FORM.started = True                            # the walk fills the form, as fill_page does
    guard.FORM.final = False
    yield
    guard.FORM.started = False


def walk(browser: Browser, session: str, rounds: int = 6) -> dict:
    """Type into every field and click every button; returns the labels the driver refused and clicked."""
    refused, allowed, clicked = [], [], set()
    for _ in range(rounds):
        _, table = browser.table(session)
        progressed = False
        order = sorted(table.elements, key=lambda e: not is_advance(e.name))     # advance first; never go back
        for e in order:
            if re.match(r"\s*(back|previous)\b", e.name, re.I):
                continue
            if e.role in FIELD_ROLES:
                browser.act([{"op": "type", "ref": e.ref, "text": "tripwire", "clear": True, "submit": False}],
                            session, table, stop_on_error=False)
            elif e.role in {"button", "link"}:
                out = browser.act([{"op": "click", "ref": e.ref}], session, table, stop_on_error=False)
                if "needs_confirmation" in out:
                    assert guard.never_click_element(e, guard.FORM), e.name
                    refused.append(e.name)
                    continue
                assert not guard.never_click_element(e, guard.FORM), e.name
                allowed.append(e.name)
                if (e.ref, e.name) not in clicked:
                    clicked.add((e.ref, e.name))
                    progressed = True
                    break                                  # the page may have changed: re-observe
        if not progressed:
            break
    return {"refused": refused, "allowed": allowed}


@pytest.mark.browser
@pytest.mark.parametrize("page", TRIPWIRE)
def test_wrapper_paths_send_nothing(new_browser, fixture_server, page):
    with new_browser() as browser:
        browser.open(fixture_server.url(page), "trip")
        result = walk(browser, "trip")
        browser.close("trip")
    assert fixture_server.posts() == [], fixture_server.posts()
    assert result["refused"], "tripwire proved nothing: no click was refused"


@pytest.mark.browser
def test_f02_walks_to_the_final_step(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("f02.html"), "trip")
        result = walk(browser, "trip", rounds=8)
        browser.close("trip")
    assert {"Next", "Review"} <= set(result["allowed"])
    assert "Submit application" in result["refused"]
    assert fixture_server.posts() == []


ATS_FINALS = ["ats/greenhouse_like.html",   # "Submit application" (structural submit)
              "ats/lever_like.html",        # "Submit application" (structural submit)
              "ats/toast_like.html",         # "Apply now!" (JS button, refused by the apply rule)
              "ats/ashby_like_app.html"]     # "Submit Application" — rendered after a ~2.5 s delay


@pytest.mark.browser
@pytest.mark.parametrize("page", ATS_FINALS)
def test_each_ats_final_button_is_refused_by_the_driver(new_browser, fixture_server, page):
    """P5 A1: the final submit/apply control of each external ATS is refused by the driver's press path, and
    nothing is sent. guard.FORM.started is set (the autouse fixture), as it is once the form is being filled, so
    an apply-labelled control (Toast's 'Apply now!') is refused too."""
    with new_browser() as browser:
        browser.open(fixture_server.url(page), "trip")
        target = None
        for _ in range(40):                              # Ashby renders its form via setTimeout(~2.5 s)
            _, table = browser.table("trip")
            els = [e for e in table.elements if e.role in {"button", "link"}]
            target = (next((e for e in els if guard._submit_like(e)), None)
                      or next((e for e in els if guard.APPLY_RE.search(e.name or "")), None))
            if target is not None:
                break
            time.sleep(0.2)
        assert target is not None, f"{page}: no final submit/apply control found"
        assert guard.never_click_element(target, guard.FORM), f"{page}: {target.name!r} is not refused"
        s = browser._session("trip")                     # bypass guard.check: prove the driver's own lock
        payload = s.act([{"op": "click", "ref": target.ref}], observe_after=False)
        browser.close("trip")
    assert payload["ops"][0]["error"] == "needs_confirmation", payload
    assert fixture_server.posts() == []


@pytest.mark.browser
@pytest.mark.parametrize("page", ["f01.html", "f03.html", "f05.html"])   # submit on step 1 (f02's is multi-step)
def test_a_direct_submit_click_is_refused_by_the_driver(new_browser, fixture_server, page):
    """Bypass the wrapper guard and order the driver to click the page's submit control directly: its press
    path must still refuse it (defence in depth, B1), and nothing is sent. Replaces the old browser_goal
    tripwire — there is no goal now, and this exercises the same second lock more directly."""
    with new_browser() as browser:
        browser.open(fixture_server.url(page), "trip")
        _, table = browser.table("trip")
        target = next((e for e in table.elements if e.role in {"button", "link"}
                       and guard.never_click_element(e, guard.FORM)), None)
        assert target is not None, f"{page}: no refusable submit control found"
        s = browser._session("trip")                    # bypass guard.check on purpose (prove the driver lock)
        payload = s.act([{"op": "click", "ref": target.ref}], observe_after=False)
        browser.close("trip")
    assert payload["ops"][0]["error"] == "needs_confirmation", payload
    assert fixture_server.posts() == []
