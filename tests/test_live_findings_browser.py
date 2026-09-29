"""Regression tests for what live LinkedIn / ATS pages showed on 2026-09-23 (DISCOVERY.md), on local look-alikes."""
from contextlib import nullcontext
from datetime import date

import pytest
from pypdf import PdfWriter

from assistant import cli, config as config_mod, pages, tabs
from assistant.llm_inference import PageAnswers
from assistant.fill import resume_input
from assistant.browser import Browser
from assistant.pages import classify, read_page
from assistant.records import Job
from tests.support import CDP_URL

pytestmark = pytest.mark.browser
CHROME_NAMES = ("Search", "Select language", "Set alert for similar jobs", "Easy Apply to this job", "Save the job")


def click_label(b, session: str, label: str) -> str:
    """Click a control by its label, as the agent does before the form (Apply is allowed there)."""
    p = read_page(b, session)
    ref = next(e.ref for e in p.elements if e.name == label)
    return b.act([{"op": "click", "ref": ref}], session, p.table)


def _read(new_browser, url, *, settle=False):
    with new_browser() as b:
        b.open(url, "live")
        p = pages.settle(lambda: read_page(b, "live")) if settle else read_page(b, "live")
        b.close("live")
    return p


def test_greenhouse_like_labels_values_and_no_false_captcha(new_browser, fixture_server):
    p = _read(new_browser, fixture_server.url("ats/greenhouse_like.html"))
    files = [e for e in p.elements if e.role == "file"]
    assert [e.name for e in files] == ["Attach", "Attach"]                     # what the table alone says
    assert [e.label for e in files] == ["Resume/CV* (resume)", "Cover Letter (cover_letter)"]
    assert resume_input(p) == files[0].ref                                     # C25 by the group label
    combos = {e.name: e.current for e in p.elements if e.role == "combobox"}
    assert combos["Will you now or in the future require sponsorship for a visa?*"] == "No"
    assert combos["Gender"] == "Select..."
    assert p.captcha is False                                                  # invisible reCAPTCHA badge
    assert ["Resume/CV*", "file"] in p.required_empty["items"]                 # not "Attach"
    assert classify(p).kind == "final" and pages.is_final(p)


def test_a_visible_captcha_still_counts(new_browser, fixture_server):
    p = _read(new_browser, fixture_server.url("ats/captcha_visible.html"))
    assert p.captcha is True and classify(p).detail.startswith("captcha")


def test_greeting_like_link_wrapped_apply_is_the_entry_not_the_map(new_browser, fixture_server):
    p = _read(new_browser, fixture_server.url("ats/greeting_like.html"))
    v = classify(p)
    assert v.kind == "navigate" and v.detail == "job_posting"
    assert pages.iframe_form_src(p) is None                                    # the map is not a form


def test_ashby_like_entry_and_late_form(new_browser, fixture_server):
    job = _read(new_browser, fixture_server.url("ats/ashby_like.html"))
    assert classify(job).kind == "navigate"
    app = _read(new_browser, fixture_server.url("ats/ashby_like_app.html"), settle=True)
    assert {"Name*", "Email*", "Resume*"} <= {e.name for e in app.elements}    # waited for "Fetching application form"
    radios = [e.name for e in app.elements if e.role == "radio"]
    assert radios == ["Job board", "Referral"]                                 # opacity-0 custom radios (aa3)
    assert app.captcha is False and classify(app).kind in ("form", "final")


def test_modal_dialog_hides_the_page_behind_it(new_browser, fixture_server):
    with new_browser() as b:
        b.open(fixture_server.url("jobs/view/4012345610-easy-dialog.html"), "dlg")
        before = read_page(b, "dlg")
        assert pages.classify_entry(before) == "open" and not pages.real_fields(before)    # chrome only
        assert click_label(b, "dlg", "Easy Apply to this job").startswith("1/1 ops ok")
        for _ in range(8):                                                     # the dialog opens 1.5 s later
            p = read_page(b, "dlg")
            if pages.real_fields(p):
                break
            import time
            time.sleep(0.5)
        b.close("dlg")
    names = {e.name for e in p.elements}
    assert "Mobile phone number*" in names and "Continue to next step" in names
    assert not any(n.startswith(CHROME_NAMES) for n in names), names          # inert behind the modal (aa2)
    assert "real-time systems" not in p.text                                   # nor its text


@pytest.mark.live_model          # the browser agent finds the way from the posting (OpenRouter)
def test_linkedin_like_easy_apply_dialog_is_parked(fixture_server, chrome, tmp_path, monkeypatch):
    seen = []

    def canned(p, src, **kw):
        seen.append([e.name for e in p.elements])
        qs = []
        cards = [e for e in p.elements if e.role == "radio" and e.name.endswith(".pdf")]
        if cards:                                     # a careless engine picking an older resume card: ignored
            old = next(e for e in cards if "Old-Company" in e.name)
            qs.append(dict(id="q1", question="Resume", kind="choice", ref=None, option_ref=old.ref,
                           options=[c.name for c in cards], required=True, answer=old.name, source="profile",
                           quote="x", relies_on=None))
        yes = next((e for e in p.elements if e.role == "radio" and e.label == "Yes"), None)
        if yes:                                       # the Yes/No question, told apart by the probe's option text
            qs.append(dict(id="q2", question=yes.name, kind="choice", ref=None, option_ref=yes.ref,
                           options=["Yes", "No"], required=True, answer="Yes", source="profile", quote="x",
                           relies_on=None))
        return PageAnswers.model_validate({"questions": qs})

    cfg = config_mod.load()
    cfg = cfg.model_copy(update={"browser": cfg.browser.model_copy(update={"cdp_url": CDP_URL})})
    monkeypatch.setattr(cli, "answer_page", canned)
    d = tmp_path / "4012345610_Acme_Junior-C++"
    d.mkdir()
    (d / "job.md").write_text(f"- LinkedIn URL: {fixture_server.url('jobs/view/4012345610-easy-dialog.html')}\n"
                              "- Company: Acme\n- Job Title: Junior C++ Software Engineer\n")
    w = PdfWriter(); w.add_blank_page(100, 100)
    with open(d / "Amin_Acme_Junior-C++.pdf", "wb") as fh:
        w.write(fh)
    with nullcontext(Jev(cfg, "")) as browser:
        book = tabs.TabBook(browser)
        parked = cli.process(Job.from_dir(d), browser=browser, book=book, cfg=cfg, key="", profile="",
                             run_dir=tmp_path / "run", today=date(2026, 9, 23))
        book.close()
    assert parked.pages == 4 and fixture_server.posts() == []
    assert seen and all(not any(n.startswith(CHROME_NAMES) for n in names) for names in seen), seen
    job_tab = next(t for t in chrome.tabs() if "4012345610-easy-dialog" in t["url"])
    chrome.close_tab(job_tab["id"])


def test_only_the_topmost_modal_counts(new_browser, fixture_server):
    """LinkedIn: Dismiss opens "Save this application?" as a second, sibling modal <dialog> on top (aa4)."""
    import time
    with new_browser() as b:
        b.open(fixture_server.url("jobs/view/4012345610-easy-dialog.html"), "top")
        p = read_page(b, "top")
        click_label(b, "top", "Easy Apply to this job")
        for _ in range(8):
            p = read_page(b, "top")
            if pages.real_fields(p):
                break
            time.sleep(0.5)
        dismiss = next(e for e in pages.buttons(p) if e.name.lower().startswith("dismiss"))
        b.act([{"op": "click", "ref": dismiss.ref}], "top", p.table)
        prompt = read_page(b, "top")
        b.close("top")
    names = {e.name for e in prompt.elements}
    assert {"Discard", "Save"} <= names
    assert "Mobile phone number*" not in names and "Continue to next step" not in names   # blocked underneath
