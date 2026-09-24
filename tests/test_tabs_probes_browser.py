import pytest

from assistant import tabs
from assistant.pages import read_page

pytestmark = pytest.mark.browser


def test_probes_fit_the_200_char_cap(new_browser, fixture_server):
    with new_browser() as browser:
        browser.open(fixture_server.url("probes.html"), "p")
        r = browser.probe("p", "REQUIRED_EMPTY", "MAXLENGTHS", "IFRAME_SRCS")
        captcha = browser.captcha_present("p")
    req = r["REQUIRED_EMPTY"]
    # Given name, Città, Relocate radio group, Resume file, Country select + 30 extras; Family name is filled
    assert req["n"] == 35 and req["more"] is True and req["items"][0] == ["Given name", "text"]
    assert ["Citt? di residenza", "text"] in req["items"]
    assert r["MAXLENGTHS"]["min"] == 20 and r["MAXLENGTHS"]["items"][0] == ["Phone", 20]
    assert r["IFRAME_SRCS"] == {"n": 0, "more": False, "items": [], "long": 0}
    assert captcha is False


def _child(url: str, value: str, mode: str) -> str:
    """Run tests/release_child.py; returns the target ID of the tab it typed into."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "tests.release_child", url, value, mode], cwd=root,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def test_release_keeps_tab_and_values_after_process_exit(new_browser, fixture_server, chrome):
    """B3: the package's exit hook closes each session's current tab; release moves the session off it first."""
    tab = _child(fixture_server.url("discovery.html"), "kept after release", "release")
    assert tab in {t["id"] for t in chrome.tabs()}                     # the child exited; the tab survived
    with new_browser() as browser:
        browser.tabs("check", "switch", target_id=tab)
        _, table = browser.table("check")
        assert next(e for e in table.elements if e.name == "Cover note").value == "kept after release"
        browser.tabs("check", "close", target_id=tab)
        browser.close("check")
    assert fixture_server.posts() == []


def test_without_release_the_exit_hook_closes_the_tab(fixture_server, chrome):
    """Control: the same child without release — its tab is gone after it exits."""
    import time
    tab = _child(fixture_server.url("discovery.html"), "lost", "keep")
    for _ in range(20):
        if tab not in {t["id"] for t in chrome.tabs()}:
            break
        time.sleep(0.1)
    assert tab not in {t["id"] for t in chrome.tabs()}


def test_hand_off_to_new_tab_leaves_baseline_alone(new_browser, fixture_server, chrome):
    baseline_ids = {t["id"] for t in chrome.tabs()}
    with new_browser() as browser:
        book = tabs.TabBook(browser)
        baseline = book.handles()
        browser.open(fixture_server.url("f10.html"), "job2")
        known = book.handles()
        p = read_page(browser, "job2")
        browser.act([{"op": "click", "ref": next(e.ref for e in p.elements if e.name == "Apply")}], "job2", p.table)
        new = book.hand_off("job2", baseline, known)
        assert new is not None
        _, table = browser.table("job2")
        assert table.title == "Apply — Acme"
        app = book.current("job2")
        book.release("job2")
        book.close()
    after = {t["id"] for t in chrome.tabs()}
    assert baseline_ids <= after                   # user's tabs untouched
    assert app in after                            # application tab parked
    assert not any("f10.html" in t["url"] for t in chrome.tabs())   # job page tab closed
    assert fixture_server.posts() == []


def test_tabs_py_never_uses_the_http_endpoint():
    """chrome://inspect remote debugging serves no /json/list (404): tab work must use browser_* only."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "assistant" / "tabs.py").read_text()
    mods = {n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert not {"httpx", "urllib", "urllib.request", "requests"} & mods


def test_release_survives_the_helper_tab_being_closed_from_outside(new_browser, fixture_server, chrome):
    """Live 2026-09-23: the helper's about:blank tab vanished mid-run and release crashed the whole run."""
    with new_browser() as browser:
        book = tabs.TabBook(browser)
        browser.open(fixture_server.url("discovery.html"), "job3")
        job_tab = book.current("job3")
        helper = next(t["id"] for t in chrome.tabs() if t["id"].upper().startswith(book.helper_tab))
        chrome.close_tab(helper)                           # someone closes the stray blank tab
        import time
        time.sleep(0.5)
        book.release("job3")                               # revives the helper, switches by verified position
        book.close()
    ids = {t["id"] for t in chrome.tabs()}
    assert job_tab in ids                                  # the job's tab is still open
    assert helper not in ids
    chrome.close_tab(job_tab)
