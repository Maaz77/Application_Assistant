from pathlib import Path

import pytest

from assistant import guard, pages
from assistant.browser import Element, Option, Table
from assistant.pages import Page, classify, gate

pytestmark = pytest.mark.unit
CAPTURED = Path(__file__).parent / "captured"


def page(elements, text="", url="https://jobs.example.com/apply", **kw):
    return Page(url=url, title=kw.pop("title", "Apply"), text=text, table=Table(url=url, elements=elements), **kw)


FINAL = [Element(ref="e1", role="textbox", name="Why us?", value="Because " * 30),
         Element(ref="e2", role="file", name="Resume", value="Amin_Acme_Data_Engineer_2026.pdf"),
         Element(ref="e3", role="button", name="Submit application")]


def test_gate_passes_on_a_clean_final_page():
    assert gate(page(FINAL), "Amin_Acme_Data_Engineer_2026.pdf", {"e1": "Because " * 30}) is None


@pytest.mark.parametrize("change, reason", [
    (dict(required_empty={"n": 2, "more": False, "items": [["Phone", "tel"]]}), "required fields still empty"),
    (dict(text="Please enter a valid phone"), "validation message"),
    (dict(text="Thank you for applying!"), "confirmation text"),
])
def test_gate_fails(change, reason):
    assert gate(page(FINAL, **change), "Amin_Acme_Data_Engineer_2026.pdf", {}).startswith(reason)


def test_gate_checks_resume_submit_and_typed_text():
    assert "resume file name" in gate(page(FINAL), "Amin_Other.pdf", {})
    assert "no submit button" in gate(page(FINAL[:2]), "Amin_Acme_Data_Engineer_2026.pdf", {})
    assert "typed text" in gate(page(FINAL), "Amin_Acme_Data_Engineer_2026.pdf", {"e1": "Something else"})


def test_resume_input_skips_an_autofill_parser_input():
    """P5: Ashby lists an 'Autofill from resume' file input before the real 'Resume' one; _code_resume must
    pick the real control, not the parser (DISCOVERY 2026-09-23)."""
    p = page([Element(ref="e9", role="file", name="", label="Autofill from resume"),
              Element(ref="e15", role="file", name="Resume", label="")], text="Upload your resume here")
    assert pages.judge(p).resume_ref == "e15"


def test_settle_is_bounded_by_wall_clock_not_read_count(monkeypatch):
    """P6 fix (DISCOVERY 2026-10-07): a persistently-unsettled page whose reads are slow must not make settle run
    `seconds` reads. settle caps by wall-clock, so slow multi-second reads still return after ~`seconds` with only
    a few reads, not one read per second."""
    clock = [0.0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: clock[0])
    reads = [0]

    def slow_read():
        reads[0] += 1
        return page([])                 # no elements, no text -> unsettled() stays True

    def fake_sleep(_):
        clock[0] += 4.0                 # each read "costs" ~4 s of wall-clock (the slow gadget page)

    out = pages.settle(slow_read, sleep=fake_sleep, seconds=10.0)
    assert pages.unsettled(out)         # gave up on the deadline, not on a count
    assert reads[0] <= 4, reads[0]      # ~10 s / 4 s per loop -> ~3 reads, not the old 10


def test_forward_submit_counts_an_apply_labelled_control():
    """P5: Greenhouse's 'Apply now!' is not _submit_like, but pages.forward_submit counts it, so a filled
    'Apply now!' final page passes the gate (parks) instead of failing 'no submit button'. The guard still
    never clicks it."""
    apply_final = [Element(ref="e1", role="textbox", name="Why us?", value="Because " * 20),
                   Element(ref="e2", role="file", name="Resume", value="Amin_Acme_Data_Engineer_2026.pdf"),
                   Element(ref="e3", role="button", name="Apply now!")]
    p = page(apply_final)
    assert pages.forward_submit(p) and not pages.judge(p).submit_button
    assert gate(p, "Amin_Acme_Data_Engineer_2026.pdf", {"e1": "Because " * 20}) is None


def test_final_step_and_submit_button_come_from_the_judgment():
    p = page([Element(ref="e1", role="textbox", name="City"), Element(ref="e2", role="button", name="Continue"),
              Element(ref="e3", role="button", name="Save and continue")])
    assert pages.classify(p).kind == "form" and not pages.is_final(p) and not pages.judge(p).submit_button
    q = page([Element(ref="e1", role="button", name="Continue to submit")])
    assert pages.is_final(q) and pages.judge(q).submit_button


def test_no_decision_call_for_page_judgment(decider):
    """P3: judge() is all code rules — no Jev call at all."""
    p = page([Element(ref="e1", role="textbox", name="City"), Element(ref="e2", role="button", name="Next")])
    pages.classify(p), pages.is_final(p), pages.real_fields(p), pages.covered(p), pages.unsettled(p)
    assert decider.asked == []


def test_google_rules():
    g = page([Element(ref="e1", role="textbox", name="Enter your password")], url="https://accounts.google.com/x")
    assert classify(g).kind == "google" and "password" in pages.google_blocker(g)
    c = page([Element(ref="e1", role="button", name="Allow")], text="Acme wants to access your Google Account",
             url="https://accounts.google.com/consent")
    assert "consent" in pages.google_blocker(c)
    back = page([], text="Complete your registration to continue")
    assert pages.after_google_blocker(back)


def test_linkedin_entry_and_feed():
    assert pages.classify_entry(page([], url="https://www.linkedin.com/authwall?x")) == "signed_out"
    assert pages.linkedin_feed_ok("https://www.linkedin.com/feed/")
    assert not pages.linkedin_feed_ok("https://www.linkedin.com/checkpoint/lg/login?feed")


@pytest.mark.parametrize("capture", sorted(CAPTURED.glob("*/observe.txt")) or [None])
def test_classifier_on_captured_pages(capture):
    if capture is None:
        pytest.skip("no tests/captured/ pages yet (the user adds them with `capture URL`)")
    p = pages.page_from_capture(capture.read_text())
    v = classify(p)
    expected = capture.parent / "expected_kind.txt"
    if expected.exists():
        assert v.kind == expected.read_text().strip(), (capture.parent.name, v)
    else:
        assert v.kind != "alarm", (capture.parent.name, v)


def test_unsettled_pages_are_recognised():
    nav = [Element(ref="e1", role="link", name="Home"), Element(ref="e2", role="combobox", name="Search")]
    li = "https://www.linkedin.com/jobs/view/4460337345/"
    assert pages.unsettled(page(nav, text="0 notifications", url=li))              # nav bar + site Search box
    drawn = page(nav + [Element(ref="e3", role="textbox", name="Phone number")], text="0 notifications", url=li)
    assert not pages.unsettled(drawn)                                               # a real form field: drawn
    job = page([Element(ref="e1", role="button", name="Easy Apply")], text="Data Engineer")
    assert not pages.unsettled(job)
    assert not pages.unsettled(page([], text="x" * 250))                         # a real amount of text
    assert not pages.unsettled(page([], text="Thank you for applying!"))         # alarm text is never waited on
    assert pages.unsettled(page([], text="", captcha=True))                        # nothing drawn at all


def test_settle_rereads_until_rendered_and_gives_up_after_the_budget(monkeypatch):
    bare = page([Element(ref="e1", role="link", name="Home")], text="0 notifications")
    job = page([Element(ref="e1", role="button", name="Easy Apply")], text="Data Engineer")
    seq, slept = iter([bare, bare, job]), []
    assert pages.settle(lambda: next(seq), slept.append) is job and slept == [1.0, 1.0]
    # Gives up after the WALL-CLOCK budget (seconds), not after `seconds` reads: drive a fake clock that each
    # sleep advances by 1 s, so a never-settling page stops once the deadline passes (P6 fix, 2026-10-07).
    clock = [0.0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: clock[0])
    slept.clear()

    def tick(s):
        clock[0] += 1.0
        slept.append(s)

    assert pages.settle(lambda: bare, tick, seconds=3) is bare and len(slept) == 3


def test_the_real_linkedin_capture_was_taken_too_early():
    """tests/captured/www-linkedin-com-20260923-123939: nav bar only, the job card came later (screenshot)."""
    early = sorted(CAPTURED.glob("www-linkedin-com-*/observe.txt"))
    if not early:
        pytest.skip("no LinkedIn capture")
    p = pages.page_from_capture(early[0].read_text())
    assert pages.classify_entry(p) == "none" and pages.unsettled(p)       # would have been waited for


# ------------------------------------------------------------------ live findings, 2026-09-23 (DISCOVERY.md)

def test_only_form_looking_iframes_are_followed():
    def p(*srcs):
        return page([], iframe_srcs={"n": len(srcs), "more": False, "items": list(srcs), "long": 0})
    assert pages.iframe_form_src(p("https://www.google.com/maps/embed/v1/place?key=x")) is None
    assert pages.iframe_form_src(p("https://www.youtube.com/embed/abc")) is None
    gh = "https://boards.greenhouse.io/embed/job_app?for=acme&token=1"
    assert pages.iframe_form_src(p("https://www.google.com/maps/embed/v1/place?key=x", gh)) == gh
    assert pages.iframe_form_src(p("http://localhost:1234/f12_form.html")) == "http://localhost:1234/f12_form.html"
    assert pages.iframe_form_src(p("https://acme.com/platform/overview")) is None


def test_a_loading_message_means_not_drawn_yet():
    ashby = page([Element(ref="e1", role="tab", name="Application")],
                 text="Data Scientist Location London Employment Type Full time " * 5 + "Fetching application form")
    assert pages.unsettled(ashby)
    assert not pages.unsettled(page([Element(ref="e1", role="textbox", name="Name")], text="Loading... Name"))


def test_linkedin_alert_switch_is_site_chrome():
    li = page([Element(ref="e1", role="switch", name="Set alert for similar jobs as Junior C++ Software Engineer"),
               Element(ref="e2", role="button", name="Easy Apply to this job")],
              url="https://www.linkedin.com/jobs/view/4460337345/")
    assert not pages.has_fields(li) and pages.classify(li).kind == "navigate"


def test_enrich_adds_file_group_labels_and_shown_combo_values():
    t = Table(url="u", elements=[
        Element(ref="e1", role="file", name="Attach"), Element(ref="e2", role="file", name="Attach"),
        Element(ref="e3", role="combobox", name="Visa?*"),
        Element(ref="e4", role="combobox", name="Country", value="it", current="Italy",
                options=[Option(label="Italy"), Option(label="Ireland")])])
    pages.enrich(t, {"FILE_LABELS": {"n": 2, "more": False, "items": [["Resume/CV*", "resume"], ["Cover Letter", ""]]},
                     "COMBO_VALUES_0": {"n": 2, "more": False, "items": ["No", "Italy"], "o": 0, "total": 2}})
    assert [e.label for e in t.elements[:2]] == ["Resume/CV* (resume)", "Cover Letter"]
    assert t.elements[2].current == "No" and t.elements[3].current == "Italy"      # native select untouched
    t2 = Table(url="u", elements=[Element(ref="e1", role="file", name="Attach")])
    pages.enrich(t2, {"FILE_LABELS": {"n": 2, "more": False, "items": [["a", ""], ["b", ""]]}})
    assert t2.elements[0].label == ""                                            # counts disagree: untouched


# --- a cookie-consent modal <dialog> on an external ATS (live 2026-10-08, careers.toasttab.com) ------------

TOAST_CONSENT = [Element(ref="e49", role="button", name="Close", tag="BUTTON", type="button", dialog="<dialog>"),
                 Element(ref="e50", role="link", name="Cookie Policy", tag="A", dialog="<dialog>"),
                 Element(ref="e52", role="button", name="Manage Cookies", tag="BUTTON", type="submit",
                         dialog="<dialog>"),
                 Element(ref="e53", role="button", name="I do not accept", tag="BUTTON", type="button",
                         dialog="<dialog>"),
                 Element(ref="e54", role="button", name="I accept", tag="BUTTON", type="button",
                         dialog="<dialog>")]


def test_a_cookie_consent_modal_is_declined_without_a_cmp_marker():
    """Toast's consent dialog is a native modal <dialog>, so the page behind it is inert and every field read
    back `occluded` (eight of them, twice, → broken_form). It is in no CMP container, so the observer sets no
    `consent` marker: the dialog's own name is the signal, and "I do not accept" is the reject control."""
    from assistant import navigate
    p = page(TOAST_CONSENT, text="We use cookies", dialogs=["Cookie consent"])
    assert navigate.consent_modal(p) is True
    c = navigate.cookie_reject(p)
    assert c is not None and c.name == "I do not accept"
    assert guard.never_click_element(c) is None          # the never-submit guard allows it


def test_the_application_dialog_is_never_mistaken_for_a_consent_dialog():
    """`all` over the open modals keeps the Easy Apply dialog out: its name is "Apply to <company>", so no
    control inside it is ever treated as a cookie control."""
    from assistant import navigate
    els = [Element(ref="e153", role="button", name="Back", dialog="<dialog>"),
           Element(ref="e154", role="button", name="Review", dialog="<dialog>")]
    p = page(els, text="Apply to Linda AI 3/4 pages", dialogs=["Apply to Linda AI"])
    assert navigate.consent_modal(p) is False
    assert navigate.cookie_reject(p) is None
    # A consent dialog open at the same time as the application dialog is also not treated as consent-only.
    assert navigate.consent_modal(page(TOAST_CONSENT, dialogs=["Cookie consent", "Apply to Linda AI"])) is False


def test_cookie_reject_still_prefers_a_cmp_container_control():
    from assistant import navigate
    p = page([Element(ref="e1", role="button", name="Reject all", consent="#onetrust-banner-sdk")],
             text="We use cookies")
    assert navigate.cookie_reject(p).ref == "e1"


# --- two more live findings from run 20261008-143810 ------------------------------------------------------

def test_linkedin_words_a_closed_posting_two_ways():
    """Mastercard's posting reads "Not currently accepting applications" and has no apply control at all, but
    _CLOSED_RE only knew "No longer accepting applications" — so the job came back as a `navigation` failure,
    "no way to start the application", which reads as requeueable when the posting is simply shut."""
    from assistant import navigate
    for text in ("Dublin, County Dublin, Ireland - 2 weeks ago - Not currently accepting applications",
                 "This job is no longer accepting applications"):
        assert pages._CLOSED_RE.search(text) and navigate.CLOSED_RE.search(text)
    assert not pages._CLOSED_RE.search("Over 100 people clicked apply - On-site - Full-time")


def test_the_apply_with_linkedin_widget_host_is_not_a_hosted_form():
    """Genesys: the run reached the right Workday posting and then hopped to the embedded "Apply with LinkedIn"
    widget's own host, which matched _FORM_IFRAME_RE on "apply". That host is configured entirely by its query
    string, so navigating to it bare renders nothing — `load_failure: blank page`, twice."""
    awli = "https://applywithlinkedin.myworkdaygadgets.com/awli/"
    assert pages._FORM_IFRAME_RE.search(awli) and pages._NON_FORM_IFRAME_RE.search(awli)
    real = "https://genesys.wd1.myworkdayjobs.com/Genesys/job/Galway-Ireland/Software-Engineer_JR112303-1"
    assert pages._FORM_IFRAME_RE.search(real) and not pages._NON_FORM_IFRAME_RE.search(real)
    greenhouse = "https://boards.greenhouse.io/embed/job_app?token=123"
    assert pages._FORM_IFRAME_RE.search(greenhouse) and not pages._NON_FORM_IFRAME_RE.search(greenhouse)
