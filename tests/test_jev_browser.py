import pytest

pytestmark = pytest.mark.browser


def test_submit_click_needs_confirmation(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("discovery.html"), "t")
        _, table = browser.table("t")
        ref = next(e.ref for e in table.elements if e.name == "Submit application")
        # bypass the wrapper guard on purpose: this proves the *server* rule (B1) is the second lock
        out = browser.checked("browser_act", ops=[{"op": "click", "ref": ref}], session="t", observe_after=False)
    assert "needs_confirmation" in out
    assert fixture_server.posts() == []


def test_calls_log_written(new_browser, tmp_path):
    with new_browser() as browser:
        browser.doctor()
    assert "browser_doctor" in (tmp_path / "calls.jsonl").read_text()
