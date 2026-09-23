"""T9 end-to-end in the throwaway Chrome: process() from a LinkedIn-like job page to a parked, released tab."""
from contextlib import nullcontext
from datetime import date

import pytest

from assistant import cli, config as config_mod
from assistant.answers import PageAnswers
from assistant.jev import Jev
from assistant.records import Job
from tests.support import CDP_URL

pytestmark = pytest.mark.browser


def canned(p, src, **kw):
    n = {e.name: e.ref for e in p.elements}
    Q = lambda **k: {"id": "q", "ref": None, "option_ref": None, "options": None, "quote": None, "relies_on": None,
                     **k}
    return PageAnswers.model_validate({"questions": [
        Q(question="Full name", kind="text", ref=n["Full name"], required=False, answer=None, source=None),
        Q(question="Willing to relocate?", kind="choice", option_ref=n["Yes"], options=["Yes", "No"],
          required=True, answer="Yes", source="profile", quote="x"),
        Q(question="Resume", kind="file", ref=n["Resume"], required=True, answer=None, source=None)]})


def test_process_parks_and_releases(fixture_server, chrome, tmp_path, monkeypatch):
    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = tmp_path / "4012345604_Acme_Data-Engineer"
    d.mkdir()
    (d / "job.md").write_text(f"- LinkedIn URL: {fixture_server.url('jobs/view/4012345604-external.html')}\n"
                              "- Company: Acme\n- Job Title: Data Engineer\n")
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100)
    with open(d / "Amin_Acme_Data-Engineer.pdf", "wb") as fh:
        w.write(fh)
    baseline = {t["id"] for t in chrome.tabs()}
    with nullcontext(Jev(cfg, "")) as browser:
        parked = cli.process(Job.from_dir(d), browser=browser, cfg=cfg, key="", profile="", run_dir=tmp_path / "run",
                                   today=date(2026, 9, 23))
    assert parked.title == "Apply — Acme" and (tmp_path / "run/shots" / f"{d.name}.jpg").exists()
    after = chrome.tabs()
    assert baseline <= {t["id"] for t in after}                       # the user's tabs are untouched
    app = [t for t in after if "f10_form.html" in t["url"]]
    assert len(app) == 1                                              # the application tab is parked, still open
    assert not any("4012345604-external" in t["url"] for t in after)  # the LinkedIn tab was handed off and closed
    assert fixture_server.posts() == []
