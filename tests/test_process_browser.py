"""T9 end-to-end in the throwaway Chrome: process() from a LinkedIn-like job page to a parked, released tab.

The browser agent (browser_goal's decision model on OpenRouter) finds the way from the posting to the form and from
step to step, so these run with --live: a few decisions per job page, about $0.0001 in all."""
from contextlib import nullcontext
from datetime import date

import pytest

from assistant import cli, config as config_mod, tabs
from assistant.llm_inference import PageAnswers
from assistant.browser import Browser
from assistant.records import Job
from tests.support import CDP_URL

pytestmark = [pytest.mark.browser, pytest.mark.live_model]


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
    "4012345609-easy-li",      # LinkedIn-like: display:contents card, Search + "Select language", Easy Apply dialog
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
    with nullcontext(Jev(cfg, "", calls_log=tmp_path / "calls.jsonl")) as browser:
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
    assert not any(job_page in t["url"] for t in after)               # the LinkedIn tab was handed off and closed
    assert fixture_server.posts() == []
    chrome.close_tab(app[0]["id"])                                    # tidy the shared test Chrome
