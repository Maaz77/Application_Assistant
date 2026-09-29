"""Probes fit the eval cap, and the P2 tab model: the owned driver has no exit hook, so a tab it opened
survives the process detaching (D13), and `TabBook.release` (forget) leaves the job tab open."""
import pytest

from assistant import tabs
from tests.support import CDP_URL

pytestmark = pytest.mark.browser


def test_probes_fit_the_200_char_cap(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("probes.html"), "p")
        r = browser.probe("p", "REQUIRED_EMPTY", "MAXLENGTHS", "IFRAME_SRCS")
        captcha = browser.captcha_present("p")
    req = r["REQUIRED_EMPTY"]
    assert req["n"] == 35 and req["more"] is True and req["items"][0] == ["Given name", "text"]
    assert ["Citt? di residenza", "text"] in req["items"]
    assert r["MAXLENGTHS"]["min"] == 20 and r["MAXLENGTHS"]["items"][0] == ["Phone", 20]
    assert r["IFRAME_SRCS"] == {"n": 0, "more": False, "items": [], "long": 0}
    assert captcha is False


def test_a_tab_the_driver_opened_survives_detaching(new_browser, fixture_server, chrome):
    """D13: the driver installs no atexit hook, so detaching the connection never closes a tab it opened."""
    with new_browser() as browser:
        browser.open(fixture_server.url("discovery.html"), "keep")
        tab = browser.session_target("keep")
        assert tab
        browser.detach()                                   # closes the socket; must not close the tab
    assert tab in {t["id"] for t in chrome.tabs()}
    chrome.close_tab(tab)                                   # tidy the shared Chrome


def test_release_leaves_the_job_tab_open(new_browser, fixture_server, chrome):
    """TabBook.release drops the session's bookkeeping (Browser.forget) and never closes the job tab."""
    with new_browser() as browser:
        book = tabs.TabBook(browser)
        browser.open(fixture_server.url("discovery.html"), "job")
        tab = book.current_handle("job")
        book.release("job")                                # forget: no scratch tab, no close
        book.close()                                       # no helper tab to close (P2)
        assert tab in book.handles()                       # the job tab is still listed
    assert tab in {t["id"] for t in chrome.tabs()}
    chrome.close_tab(tab)


def test_tabs_py_never_uses_the_http_endpoint():
    """chrome://inspect remote debugging serves no /json/list (404): tab work goes through the owned driver."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "assistant" / "tabs.py").read_text()
    mods = {n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert not {"httpx", "urllib", "urllib.request", "requests"} & mods
