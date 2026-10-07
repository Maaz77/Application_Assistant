"""T9 end-to-end in the throwaway Chrome: process() from a LinkedIn-like job page to a parked, released tab.

P2+ navigation is deterministic (navigate.py / external.py) and P3 fills from code-extracted questions with the
answers canned here, so these need no model and run in the offline browser suite: the external cases exercise
external.run_external (the P5 hand-off + general ATS loop); the easy-li case exercises the Easy Apply loop."""
from contextlib import nullcontext
from datetime import date

import pytest

from assistant import cli, config as config_mod, tabs
from assistant.blockers import NeedsAttention
from assistant.llm_inference import PageAnswers
from assistant.browser import Browser
from assistant.records import Job
from tests.support import CDP_URL


def _job_dir(tmp_path, folder, url):
    d = tmp_path / folder
    d.mkdir()
    (d / "job.md").write_text(f"- LinkedIn URL: {url}\n- Company: Acme\n- Job Title: Data Engineer\n")
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100)
    with open(d / "Amin_Acme_Data-Engineer.pdf", "wb") as fh:
        w.write(fh)
    return d

pytestmark = pytest.mark.browser


def canned(p, src, **kw):
    n = {e.name: e.ref for e in p.elements}
    if "Full name" not in n:                     # the LinkedIn-like Easy Apply dialog (…09-easy-li)
        qs = []
        if "Yes" in n:
            qs.append(dict(id="q", question="Willing to relocate?", kind="choice", ref=None, option_ref=n["Yes"],
                           options=["Yes", "No"], required=True, answer="Yes", source="profile", quote="x",
                           relies_on=None))
        if "Resume" in n:
            qs.append(dict(id="q2", question="Resume", kind="file", ref=n["Resume"], option_ref=None, options=None,
                           required=True, answer=None, source=None, quote=None, relies_on=None))
        return PageAnswers.model_validate({"questions": qs})
    Q = lambda **k: {"id": "q", "ref": None, "option_ref": None, "options": None, "quote": None, "relies_on": None,
                     **k}
    return PageAnswers.model_validate({"questions": [
        Q(question="Full name", kind="text", ref=n["Full name"], required=False, answer=None, source=None),
        Q(question="Willing to relocate?", kind="choice", option_ref=n["Yes"], options=["Yes", "No"],
          required=True, answer="Yes", source="profile", quote="x"),
        Q(question="Resume", kind="file", ref=n["Resume"], required=True, answer=None, source=None)]})


@pytest.mark.parametrize("job_page", [
    "4012345604-external",     # Apply opens the company form in a new tab at once
    "4012345607-late-tab",     # … 2.5 s after the click
    "4012345608-dialog",       # … only after "Continue" in a covering "You are leaving LinkedIn" dialog
    # LinkedIn-like Easy Apply still asks the System One model one entry/advance question, so it stays live_model:
    pytest.param("4012345609-easy-li", marks=pytest.mark.live_model),
])
def test_process_parks_and_releases(fixture_server, chrome, tmp_path, monkeypatch, job_page):
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = tmp_path / "4012345604_Acme_Data-Engineer"
    d.mkdir()
    (d / "job.md").write_text(f"- LinkedIn URL: {fixture_server.url(f'jobs/view/{job_page}.html')}\n"
                              "- Company: Acme\n- Job Title: Data Engineer\n")
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100)
    with open(d / "Amin_Acme_Data-Engineer.pdf", "wb") as fh:
        w.write(fh)
    baseline = {t["id"] for t in chrome.tabs()}
    forms_before = {t["id"] for t in chrome.tabs() if "f10_form.html" in t["url"]}
    with nullcontext(Browser(cfg, "", actions_log=tmp_path / "browser_actions.jsonl")) as browser:
        browser.connect(5.0)
        book = tabs.TabBook(browser)
        parked = cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="", run_dir=tmp_path / "run",
                                   today=date(2026, 9, 23))
        book.close()
    assert (tmp_path / "run" / d.name / "screenshot.jpg").exists()
    after = chrome.tabs()
    assert fixture_server.posts() == []
    if job_page.endswith("easy-li"):                  # Easy Apply: same tab, parked on the dialog's last step
        assert parked.pages == 3 and any(job_page in t["url"] for t in after)
        chrome.close_tab(next(t["id"] for t in after if job_page in t["url"]))
        return
    assert parked.title == "Apply — Acme"
    assert baseline <= {t["id"] for t in after}                       # the user's tabs are untouched
    app = [t for t in after if "f10_form.html" in t["url"] and t["id"] not in forms_before]
    assert len(app) == 1                                              # the application tab is parked, still open
    # The LinkedIn tab THIS test opened was handed off and closed. Exclude baseline tabs: an earlier browser
    # test (test_navigate) drives the same fixture URL and, by D13, its detach leaves that tab open in the
    # shared Chrome — a pre-existing tab is not this run's.
    assert not any(job_page in t["url"] and t["id"] not in baseline for t in after)
    assert fixture_server.posts() == []
    chrome.close_tab(app[0]["id"])                                    # tidy the shared test Chrome


def test_an_alert_box_page_is_unsupported_ats_and_nothing_is_clicked(fixture_server, chrome, tmp_path, monkeypatch):
    """P5: an external page that only has a job-alert email box (the Mastercard case, DISCOVERY ~198) is not an
    application. run_external reports unsupported_ats and clicks nothing on it — its 'Apply now' never fires (the
    page's title would change to 'CLICKED-alert' if it did), and nothing is submitted."""
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = _job_dir(tmp_path, "4012345610_Acme_Data-Engineer",
                 fixture_server.url("jobs/view/4012345610-alertbox.html"))
    with nullcontext(Browser(cfg, "", actions_log=tmp_path / "browser_actions.jsonl")) as browser:
        browser.connect(5.0)
        book = tabs.TabBook(browser)
        with pytest.raises(NeedsAttention) as exc:
            cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="",
                        run_dir=tmp_path / "run", today=date(2026, 9, 23))
        book.close()
    assert exc.value.cls == "unsupported_ats"
    after = chrome.tabs()
    alert = [t for t in after if "alert_box.html" in t["url"]]
    assert len(alert) == 1 and alert[0]["title"] == "Acme Careers"    # 'Apply now' was never clicked
    assert fixture_server.posts() == []
    chrome.close_tab(alert[0]["id"])


def test_an_ashby_form_rendered_late_is_reached_not_given_up(fixture_server, chrome, tmp_path, monkeypatch):
    """P5 regression for the live run: Ashby renders its form after 'Fetching application form'. The loop must
    wait for it (not give up as unsupported_ats) and must not crash observing the freshly-adopted tab (the Toast
    createTreeWalker-on-null-body crash). Asserts the job reached the Ashby application form and classified it as
    a fillable form (park / gate / broken_form), never unsupported_ats / navigation / a traceback."""
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = _job_dir(tmp_path, "4012345611_Acme_Data-Scientist",
                 fixture_server.url("jobs/view/4012345611-ashby.html"))
    with nullcontext(Browser(cfg, "", actions_log=tmp_path / "browser_actions.jsonl")) as browser:
        browser.connect(5.0)
        book = tabs.TabBook(browser)
        try:
            outcome = cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="",
                                  run_dir=tmp_path / "run", today=date(2026, 9, 23))
            url, cls = outcome.url, "parked"
        except NeedsAttention as na:
            url, cls = na.url, na.cls
        book.close()
    assert "ashby_like_app.html" in (url or ""), f"did not reach the Ashby form: {url!r} ({cls})"
    assert cls in ("parked", "gate", "broken_form"), f"gave up on the Ashby form as {cls}"
    assert fixture_server.posts() == []
    for t in chrome.tabs():
        if "ashby_like" in t["url"]:
            chrome.close_tab(t["id"])


def test_a_greenhouse_careers_page_reaches_the_form_via_apply_now(fixture_server, chrome, tmp_path, monkeypatch):
    """P5 regression for the live run: Toast's 'Apply on company website' lands on its own careers page whose
    only field is a site-search box in a <form> (so it carries a scope). That must not be read as an application
    (the scope short-circuit bug); the loop must click 'Apply now' (new tab) to reach the Greenhouse form. Asserts
    the Greenhouse form is reached and classified fillable, never unsupported_ats."""
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = _job_dir(tmp_path, "4012345612_Toast_Software-Engineer",
                 fixture_server.url("jobs/view/4012345612-toast.html"))
    with nullcontext(Browser(cfg, "", actions_log=tmp_path / "browser_actions.jsonl")) as browser:
        browser.connect(5.0)
        book = tabs.TabBook(browser)
        try:
            outcome = cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="",
                                  run_dir=tmp_path / "run", today=date(2026, 9, 23))
            url, cls = outcome.url, "parked"
        except NeedsAttention as na:
            url, cls = na.url, na.cls
        book.close()
    assert "greenhouse_like.html" in (url or ""), f"did not reach the Greenhouse form: {url!r} ({cls})"
    assert cls in ("parked", "gate", "broken_form"), f"gave up on the Greenhouse form as {cls}"
    assert fixture_server.posts() == []
    for t in chrome.tabs():
        if "toast_careers.html" in t["url"] or "greenhouse_like.html" in t["url"]:
            chrome.close_tab(t["id"])


def test_an_embedded_greenhouse_form_rendered_late_is_reached(fixture_server, chrome, tmp_path, monkeypatch):
    """P5 regression for live Toast: the Greenhouse form is embedded on the company careers page and renders
    below the fold a beat after load, so the first observe sees only the nav + search box + an 'Apply now'
    anchor. The loop must wait for the embedded form (re-read only, never a submit) and reach it, not give up
    unsupported_ats."""
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = _job_dir(tmp_path, "4012345613_Toast_Software-Engineer",
                 fixture_server.url("jobs/view/4012345613-toast-embedded.html"))
    with nullcontext(Browser(cfg, "", actions_log=tmp_path / "browser_actions.jsonl")) as browser:
        browser.connect(5.0)
        book = tabs.TabBook(browser)
        try:
            outcome = cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="",
                                  run_dir=tmp_path / "run", today=date(2026, 9, 23))
            url, cls = outcome.url, "parked"
        except NeedsAttention as na:
            url, cls = na.url, na.cls
        book.close()
    assert "toast_embedded.html" in (url or ""), f"did not reach the embedded form: {url!r} ({cls})"
    assert cls in ("parked", "gate", "broken_form"), f"gave up on the embedded form as {cls}"
    assert fixture_server.posts() == []
    for t in chrome.tabs():
        if "toast_embedded.html" in t["url"]:
            chrome.close_tab(t["id"])
