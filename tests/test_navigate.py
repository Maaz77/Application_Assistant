"""P2 T5: deterministic LinkedIn Easy Apply navigation. Real driver on throwaway Chrome + fixtures."""
from types import SimpleNamespace

import pytest

from assistant import guard, navigate, pages
from assistant.blockers import GoExternal, NeedsAttention
from assistant.browser import Browser
from tests.support import CDP_URL, FixtureServer

pytestmark = pytest.mark.browser


@pytest.fixture
def nav_browser(cfg, chrome):
    c = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    b = Browser(c)
    guard.FORM.started = False
    guard.FORM.final = False
    try:
        yield b
    finally:
        b.detach()


def ctx_for(browser, session="t"):
    return SimpleNamespace(browser=browser, session=session, stage="", sleep=lambda s: None)


def read(browser, srv, name, session="t"):
    browser.open(srv.url(f"jobs/view/{name}.html"), session)
    return pages.read_page(browser, session)


def test_entry_helpers_classify_the_posting(nav_browser, fixture_server):
    b, srv = nav_browser, fixture_server
    assert navigate.easy_apply_button(read(b, srv, "4012345601-easy")) is not None
    assert navigate.easy_apply_button(read(b, srv, "4012345609-easy-li")) is not None
    assert navigate.external_apply(read(b, srv, "4012345604-external")) is not None
    p602 = read(b, srv, "4012345602-closed")
    assert navigate.easy_apply_button(p602) is None and navigate.CLOSED_RE.search(p602.text)
    p603 = read(b, srv, "4012345603-applied")
    assert navigate.easy_apply_button(p603) is None and navigate.APPLIED_RE.search(p603.text)


def test_external_apply_raises_go_external(nav_browser, fixture_server):
    """P5 (was test_external_apply_is_needs_attention): an external, non-Easy-Apply Apply is no longer a
    dead-end — enter() signals GoExternal, and the caller hands the job off to the external tab
    (external.run_external). The posting itself is never submitted."""
    ctx = ctx_for(nav_browser)
    p = read(nav_browser, fixture_server, "4012345604-external")
    with pytest.raises(GoExternal):
        navigate.enter(ctx, p)
    assert fixture_server.posts() == []


@pytest.mark.parametrize("name,cls", [("4012345602-closed", "closed"), ("4012345603-applied", "applied"),
                                      ("4012345605-submitted", "applied")])
def test_closed_and_applied_are_needs_attention(nav_browser, fixture_server, name, cls):
    ctx = ctx_for(nav_browser)
    p = read(nav_browser, fixture_server, name)
    with pytest.raises(NeedsAttention) as exc:
        navigate.enter(ctx, p)
    assert exc.value.cls == cls


def test_easy_apply_opens_the_dialog_then_advances_to_final_without_submitting(nav_browser, fixture_server):
    b, srv = nav_browser, fixture_server
    ctx = ctx_for(b)
    p = read(b, srv, "4012345610-easy-dialog")
    assert navigate.enter(ctx, p) == "form"          # clicks Easy Apply, waits for the dialog
    p = pages.read_page(b, "t")
    assert navigate.dialog_is_open(p)                 # the Easy Apply form is open
    # advance through every step; the loop must reach "final" and never submit.
    steps, result = 0, "moved"
    while result == "moved" and steps < 8:
        result = navigate.advance(ctx, p)
        p = pages.read_page(b, "t")
        steps += 1
    assert result == "final"
    assert 2 <= steps <= 6                            # Contact -> Resume -> Questions -> Review -> final
    # the final step offers only "Submit application", which the guard refuses.
    submit = next((e for e in navigate.in_dialog(p) if guard.REFUSE_LABEL_RE.search(e.name or "")), None)
    assert submit is not None
    assert guard.never_click_element(submit, guard.FORM)      # refused
    assert srv.posts() == []                          # nothing was ever submitted
