"""Pages: what the program reads (read_page: the element table and the read-only probes) and what it decides
about a page. Facts stay facts — refs, values, URLs, the probes' counts, the resume's file name. Every judgment
about what a page *is* comes from TypeSafe's Jev (decide.py): one call per page snapshot, made the first time a
question about it is asked, then cached on the Page (judge). The confirmation-text tripwire below stays a rule
beside Jev's "submitted" answer. The never-submit rule itself is jev.never_click.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from assistant import decide
from assistant.decide import THRESHOLDS as T
from assistant import probes as probes_mod
from assistant.jev import Jev, Element, Table, split_json

FORM_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "switch", "file"}
CONTROL_ROLES = {"button", "link", "menuitem", "tab"}

SIGNED_OUT_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/")
# Safety floor, kept beside Jev's "submitted" answer: the run stops if either sees a confirmation (exit 3).
ALARM_RE = re.compile(r"(your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)", re.I)
RESUME_FILE_RE = re.compile(r"\.(pdf|docx?|rtf|txt)\b", re.I)      # a file name, not a judgment: LinkedIn's resume cards


@dataclass
class Page:
    url: str
    title: str
    text: str
    table: Table
    required_empty: dict = field(default_factory=lambda: {"n": 0, "more": False, "items": []})
    maxlengths: dict = field(default_factory=lambda: {"n": 0, "more": False, "items": [], "min": None})
    iframe_srcs: dict = field(default_factory=lambda: {"n": 0, "more": False, "items": [], "long": 0})
    captcha: bool = False
    dialogs: list[str] = field(default_factory=list)   # open modal dialogs' names (`! dialog open: … [modal]`)
    judgment: "Judgment | None" = field(default=None, repr=False, compare=False)

    @property
    def elements(self) -> list[Element]:
        return self.table.elements

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").lower()


def view_text(view: str) -> str:
    """The page text section of an observe view ('text:' line onwards)."""
    _, sep, tail = view.partition("\ntext:\n")
    return tail.strip() if sep else ""


PAGE_PROBES = ("REQUIRED_EMPTY", "MAXLENGTHS", "IFRAME_SRCS", "FILE_LABELS", *sorted(probes_mod.COMBO_VALUES),
               *sorted(probes_mod.RADIO_OPTIONS))


MODAL_RE = re.compile(r"^\s*! dialog open: (.*?) \[modal\]\s*$", re.M)


def read_page(browser: Jev, session: str) -> Page:
    view, table = split_json(browser.observe(session))
    pr = browser.probe(session, *PAGE_PROBES)
    enrich(table, pr)
    return Page(url=table.url, title=table.title, text=view_text(view), table=table,
                required_empty=pr["REQUIRED_EMPTY"], maxlengths=pr["MAXLENGTHS"],
                iframe_srcs=pr["IFRAME_SRCS"], captcha=browser.captcha_present(session),
                dialogs=MODAL_RE.findall(view.partition("\ntext:\n")[0]))


def enrich(table: Table, pr: dict) -> None:
    """Add what the element table cannot show, from the read-only probes (live ATS findings, 2026-09-23):

    - file inputs get their group label in `label` (Greenhouse names both inputs "Attach"; the group says
      "Resume/CV" / "Cover Letter");
    - custom comboboxes (no <option> list, empty input value, e.g. react-select) get the value they show in
      `current`, so read-back and prefill checks can see a choice.

    Both are matched to the table by DOM order and applied only when the counts agree (else left as is)."""
    files = [e for e in table.elements if e.role == "file"]
    fl = pr.get("FILE_LABELS") or {}
    if files and fl.get("n") == len(files) and not fl.get("more"):
        for e, (label, ident) in zip(files, fl["items"]):
            e.label = label + (f" ({ident})" if ident else "")
    radios = [e for e in table.elements if e.role == "radio"]
    rchunks = sorted((pr[k] for k in probes_mod.RADIO_OPTIONS if pr.get(k)), key=lambda c: c.get("o", 0))
    if radios and rchunks and rchunks[0].get("total") == len(radios):
        opt: dict[int, str] = {}
        for c in rchunks:
            if not c.get("more"):
                opt.update({c["o"] + i: v for i, v in enumerate(c["items"])})
        for i, e in enumerate(radios):
            if opt.get(i) and norm_label(opt[i]) != norm_label(e.name) and not e.label:
                e.label = opt[i]                          # the option ("Yes") when the name is the question
    combos = [e for e in table.elements if e.role == "combobox"]
    chunks = sorted((pr[k] for k in probes_mod.COMBO_VALUES if pr.get(k)), key=lambda c: c.get("o", 0))
    if not combos or not chunks or chunks[0].get("total") != len(combos):
        return
    shown: dict[int, str] = {}
    for c in chunks:
        if c.get("more"):
            continue                                       # a trimmed chunk: its indexes stay unknown
        shown.update({c["o"] + i: v for i, v in enumerate(c["items"])})
    for i, e in enumerate(combos):
        if i in shown and not e.options and not (e.value or "").strip() and shown[i]:
            e.current = shown[i]


# ------------------------------------------------------------------ Jev's judgment of a page

PAGE_KINDS = {
    "job_posting": "A job description page. It offers a button or link to start applying (Apply, Easy Apply, "
                   "Apply now).",
    "interstitial": "A step between the job posting and the application form: a job-search safety reminder, a "
                    "'you are leaving this site' notice, or a choice of how to start the application (apply "
                    "manually, autofill with a resume).",
    "application_form": "A step of the job application itself: its own fields such as name, email, phone, "
                        "resume upload or screening questions, with a way to go on to the next step.",
    "final_step": "The application's last step: a review or summary, or a form whose only way forward is a "
                  "button that submits the application.",
    "account_wall": "Asks to sign in, to create an account or to enter a password before the application can go on.",
    "google_sign_in": "Google's own sign-in page: an account chooser, a permission request, a password or code.",
    "captcha": "A human-verification challenge.",
    "confirmation": "Says that the application was submitted, sent or received.",
    "closed": "Says that the job is closed or no longer accepts applications.",
    "error": "An error page, a page that did not load, or an empty page.",
    "other": "None of the above.",
}
FORM_KINDS = {"application_form", "final_step"}
SETTLED_KINDS = {"confirmation", "closed", "error", "captcha", "account_wall", "google_sign_in"}   # never waited on
GOOGLE_STEPS = {
    "account_chooser": "Choose one of the listed Google accounts.",
    "consent": "Asks to allow an app to access the Google account or share its details.",
    "password": "Asks for the Google account's password.",
    "two_step": "Asks for a verification code or another 2-step verification.",
    "other": "None of the above.",
}
MAX_CONTROLS = 120          # controls listed in the state and offered as options (Jev's context is 32K tokens)
STATE_TEXT = 6000           # chars of page text in the state


@dataclass
class Judgment:
    kind: str
    kind_confidence: float
    submitted: bool             # the page says an application was submitted
    applied: bool               # the page says the candidate already applied to this job
    covered: bool               # a pop-up that is not part of the application sits over the page
    loading: bool               # nothing to act on yet, and not a page that is final in itself
    validation: bool            # a field shows an error message
    registration: bool          # asks to complete a registration or accept terms
    account: str                # create_account | sign_in | none
    submit_button: bool         # a button that submits the application is on screen
    app_fields: set[str]        # refs of fields that belong to the application
    resume_ref: str | None      # the control that uploads the resume
    resume_confidence: float
    resume_probabilities: dict[str, float]
    cover_letters: list[str]    # required-empty upload labels that ask for a cover letter
    google_ref: str | None      # the control that signs in with Google
    google_step: str | None     # on accounts.google.com
    form_iframe: str | None     # the iframe src that holds the application form


def _describe(e: Element) -> dict:
    d = {"ref": e.ref, "role": e.role, "name": (e.name or e.label)[:90]}
    if e.label and e.label != e.name:
        d["option"] = e.label[:40]
    shown = e.current or e.value
    if shown:
        d["value"] = str(shown)[:60]
    if e.checked is not None:
        d["checked"] = e.checked
    if e.role in {"textbox", "searchbox", "spinbutton"} and not e.editable:
        d["read_only"] = True
    if e.occluded:
        d["covered"] = True
    if e.options:
        d["options"] = [o.label for o in e.options[:8] if o.label]
    return d


def page_state(p: Page) -> dict:
    """What Jev sees of a page: its address, open dialogs, controls and text."""
    return {"url": p.url, "title": p.title, "open_dialogs": p.dialogs,
            "controls": [_describe(e) for e in p.elements[:MAX_CONTROLS]],
            "required_fields_still_empty": [i[0] for i in p.required_empty.get("items", [])],
            "text": p.text[:STATE_TEXT]}


def _field_question(e: Element) -> dict:
    return decide.noul({"field": _describe(e), "question": "Is `field` one of the job application's own questions?"},
                       true="A question of the application form: contact details, resume, cover letter, experience, "
                            "eligibility, or any other question the employer asks the candidate.",
                       false="Part of the site around the application: a site search box, a language picker, a "
                             "job-alert or newsletter sign-up, a chat box, a filter or a sort control.")


def page_questions(p: Page) -> dict[str, dict]:
    controls = [e for e in p.elements if e.role in CONTROL_ROLES | {"file"}][:MAX_CONTROLS]
    qs = {
        "kind": decide.choice("What is this page, as a step of applying for a job?", PAGE_KINDS),
        "submitted": decide.noul("Does the page say that a job application was just submitted, sent or received?",
                                 true="A confirmation such as 'Your application was submitted' or 'Thank you for "
                                      "applying'.",
                                 false="No such confirmation. Counts of applicants, or a badge on another job, "
                                       "do not count."),
        "applied": decide.noul("Does the page say that the candidate has already applied to this job?"),
        "covered": decide.noul("Is a pop-up that is not part of the job application in front of the page, covering "
                               "what is behind it?",
                               true="A cookie or consent banner, a newsletter pop-up or another overlay sits over "
                                    "the application or the job page.",
                               false="Nothing covers the page, or what is in front is the application form itself."),
        # "Still loading?" alone was answered from the title (a LinkedIn posting with only its nav bar drawn, 0.35);
        # "anything to act on?" separates drawn pages (0.94 and up) from early ones (0.22–0.30), live 2026-09-24.
        "actionable": decide.noul("Besides the site's own navigation, does the page already offer something to act "
                                  "on for this job?",
                                  true="An apply button or link, a field of a form, or the buttons of a dialog "
                                       "about the application.",
                                  false="Only the site's navigation links, a search box, a spinner or a loading "
                                        "message."),
        "validation": decide.noul("Does the page show an error message about a form field, such as 'This field is "
                                  "required' or 'Invalid email'?"),
        "registration": decide.noul("Does the page ask to complete a registration, create a profile or accept terms "
                                    "of use before going on?"),
        "account": decide.choice("What does the page ask for about an account?", {
            "create_account": "To create a new account: sign up, register, choose a password.",
            "sign_in": "To sign in to an existing account or enter a password.",
            "none": "Neither."}),
        "submit_button": decide.noul("Is there a button that would submit the job application, such as 'Submit "
                                     "application', 'Send application' or 'Apply'?"),
    }
    for e in fields(p):
        qs[f"field_{e.ref}"] = _field_question(e)
    # File inputs when the page has any: an "Upload File" button beside the "Resume" input is the same field, and
    # offering both split Jev's answer (The Flex on Ashby: the right input at confidence 0.4, live 2026-09-24).
    # Buttons only when there is no file input (LinkedIn's "Upload resume" opens the file chooser, aa6).
    uploads = [e for e in controls if e.role == "file"] or [e for e in controls if e.role == "button"]
    if uploads:
        qs["resume_input"] = decide.choice(
            "Which control uploads the candidate's resume (CV)?",
            {**{e.ref: _describe(e)["name"] or e.role for e in uploads},
             "none": "No control on this page uploads a resume."})
    for i, (label, kind, *_) in enumerate(p.required_empty.get("items", [])):
        if kind == "file":
            qs[f"cover_{i}"] = decide.noul({"upload": label, "question": "Does `upload` ask for a cover letter?"})
    if p.host == "accounts.google.com":
        qs["google_step"] = decide.choice("What does this Google page ask for?", GOOGLE_STEPS)
    elif controls:
        qs["google_button"] = decide.choice(
            "Which control signs in or continues with Google?",
            {**{e.ref: _describe(e)["name"] or e.role for e in controls if e.role in {"button", "link"}},
             "none": "No control signs in with Google."})
    if p.iframe_srcs.get("items"):
        qs["form_iframe"] = decide.choice(
            "Which embedded frame holds the job application form?",
            {**{f"frame{i}": src for i, src in enumerate(p.iframe_srcs["items"])},
             "none": "None of these frames holds an application form."})
    return qs


def judge(p: Page) -> Judgment:
    """Jev's judgment of the page: one call per snapshot, cached on it. Raises decide.DecisionError."""
    if p.judgment is not None:
        return p.judgment
    a = decide.current().ask("page", page_state(p), page_questions(p))
    kind = a["kind"]

    def pick(key: str) -> str | None:
        return a[key].choice if key in a and a[key].choice != "none" else None
    frame = pick("form_iframe")
    p.judgment = Judgment(
        kind=kind.choice if kind.choice in PAGE_KINDS else "other", kind_confidence=kind.confidence or 0.0,
        submitted=a["submitted"].yes(T["submitted"]), applied=a["applied"].yes(T["applied"]),
        covered=a["covered"].yes(T["covered"]),
        loading=not a["actionable"].yes(T["actionable"]) and kind.choice not in SETTLED_KINDS,
        validation=a["validation"].yes(T["validation"]), registration=a["registration"].yes(T["registration"]),
        account=a["account"].choice or "none", submit_button=a["submit_button"].yes(T["submit_button"]),
        app_fields={e.ref for e in fields(p) if a[f"field_{e.ref}"].yes(T["app_field"])},
        resume_ref=pick("resume_input"), resume_confidence=(a["resume_input"].confidence or 0.0)
        if "resume_input" in a else 0.0,
        resume_probabilities=dict(a["resume_input"].probabilities) if "resume_input" in a else {},
        cover_letters=[label for i, (label, kind_, *_) in enumerate(p.required_empty.get("items", []))
                       if kind_ == "file" and a[f"cover_{i}"].yes(T["cover_letter"])],
        google_ref=pick("google_button"), google_step=pick("google_step"),
        form_iframe=p.iframe_srcs["items"][int(frame[5:])] if frame else None)
    return p.judgment


# ------------------------------------------------------------------ settling

SETTLE_SECONDS = 10.0


def unsettled(p: Page) -> bool:
    """Not drawn yet: nothing on the page at all, or nothing to act on so far (LinkedIn job pages show only their
    nav bar for a second; Ashby says "Fetching application form", live 2026-09-23)."""
    if not p.elements and not p.text.strip():
        return True
    return judge(p).loading


def settle(read, sleep=time.sleep, seconds: float = SETTLE_SECONDS) -> Page:
    """Re-read until the page is no longer `unsettled`, at most `seconds` (one read per second)."""
    p = read()
    waited = 0.0
    while unsettled(p) and waited < seconds:
        sleep(1.0)
        waited += 1.0
        p = read()
    return p


def page_from_capture(observe_text: str) -> Page:
    """A Page built from a saved browser_observe(mode="full", include_json=True) output (no probes)."""
    view, table = split_json(observe_text)
    return Page(url=table.url, title=table.title, text=view_text(view), table=table,
                dialogs=MODAL_RE.findall(view.partition("\ntext:\n")[0]))


# ------------------------------------------------------------------ element helpers

def norm_label(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def buttons(p: Page) -> list[Element]:
    return [e for e in p.elements if e.role in CONTROL_ROLES]


def fields(p: Page) -> list[Element]:
    return [e for e in p.elements if e.role in FORM_ROLES]


def real_fields(p: Page) -> list[Element]:
    """The fields Jev counts as the application's own (not a site search, language picker or job alert)."""
    refs = judge(p).app_fields
    return [e for e in fields(p) if e.ref in refs]


def has_fields(p: Page) -> bool:
    return bool(real_fields(p))


def form_is_here(p: Page) -> bool:
    """The application form is on screen and nothing covers it."""
    j = judge(p)
    # Jev's top choice decides (user decision 2026-09-24), not the summed probability of the form kinds or a
    # confidence floor. A near-tie goes either way; a wrong "form" fails the final check, a wrong "not yet" costs
    # one agent action.
    return j.kind in FORM_KINDS and not j.covered


def covered(p: Page) -> bool:
    return judge(p).covered


def is_final(p: Page) -> bool:
    return judge(p).kind == "final_step"


def is_alarm(p: Page) -> bool:
    """Confirmation text: Jev's answer, or the tripwire rule (the safety floor)."""
    return bool(ALARM_RE.search(p.text)) or judge(p).submitted


def validation_error(p: Page) -> str | None:
    return "an error message on a field" if judge(p).validation else None


def page_shape(p: Page) -> tuple:
    """What a click is expected to change: the address, the fields and the controls (a change detector)."""
    return (urlparse(p.url)._replace(query="", fragment="").geturl(),
            tuple(sorted((e.role, e.name) for e in fields(p))),
            tuple(sorted(e.name for e in buttons(p) if not e.occluded)))


# ------------------------------------------------------------------ §6.1 LinkedIn job page

def classify_entry(p: Page) -> str:
    """signed_out | closed | applied | open | none."""
    if any(m in p.url for m in SIGNED_OUT_MARKERS):
        return "signed_out"
    j = judge(p)
    if j.kind == "closed":
        return "closed"
    if j.applied:
        return "applied"
    if j.kind in {"job_posting", "interstitial"} | FORM_KINDS:
        return "open"
    return "none"


def linkedin_feed_ok(url: str) -> bool:
    """Preflight probe: still on /feed and no signed-out marker."""
    return "/feed" in url and not any(m in url for m in SIGNED_OUT_MARKERS)


# ------------------------------------------------------------------ §6.2 Google

def is_google_page(p: Page) -> bool:
    return p.host == "accounts.google.com"


def google_button(p: Page) -> Element | None:
    ref = judge(p).google_ref
    return next((e for e in p.elements if e.ref == ref), None) if ref else None


def google_blocker(p: Page) -> str | None:
    """On accounts.google.com: why the one-click rule cannot continue, else None."""
    step = judge(p).google_step
    if step in ("password", "two_step"):
        return "Google asks for a password or 2-step verification"
    if step == "consent":
        return "Google asks for consent to share the account"
    return None


def after_google_blocker(p: Page) -> str | None:
    """Back on the site after Google: registration or terms still pending?"""
    return "site asks to complete a registration after Google sign-in" if judge(p).registration else None


# ------------------------------------------------------------------ §6.3 blockers

@dataclass
class Blocker:
    cls: str       # captcha | signup | credentials | broken_form | load_failure
    cue: str


def blocker(p: Page) -> Blocker | None:
    """A blocker on the page. Google paths are not blockers here (handled by §6.2)."""
    if not p.elements and not p.text.strip():
        return Blocker("load_failure", "blank page")
    j = judge(p)
    if p.captcha or j.kind == "captcha":
        return Blocker("captcha", "captcha on page")
    if j.kind == "account_wall" and not is_google_page(p) and not j.google_ref:
        if j.account == "create_account":
            return Blocker("signup", "site asks to create an account")
        return Blocker("credentials", "site asks for a password")
    if j.kind == "error":
        return Blocker("load_failure", f"error page: {p.title or p.text[:60]!r}")
    return None


GUEST_RE = re.compile(r"apply without an account|continue as guest", re.I)   # §6.3 signup, attempt 2


def guest_link(p: Page) -> Element | None:
    """The guest link a sign-up wall offers."""
    return next((e for e in buttons(p) if GUEST_RE.search(e.name or "")), None)


def iframe_form_src(p: Page) -> str | None:
    """The iframe that holds the application form, when the page itself shows none of its fields."""
    return None if has_fields(p) else judge(p).form_iframe


# ------------------------------------------------------------------ classifier (the §5 loop order)

@dataclass
class Verdict:
    kind: str       # alarm | google | blocker | google_wall | iframe | form | final | navigate
    detail: str = ""
    ref: str | None = None


def classify(p: Page) -> Verdict:
    if is_alarm(p):
        return Verdict("alarm", "confirmation text on the page")
    if is_google_page(p):
        return Verdict("google", google_blocker(p) or "")
    if b := blocker(p):
        return Verdict("blocker", f"{b.cls}: {b.cue}")
    j = judge(p)
    if j.kind == "account_wall" and (g := google_button(p)):
        return Verdict("google_wall", g.name, g.ref)
    if src := iframe_form_src(p):
        return Verdict("iframe", src)
    if j.kind == "final_step":
        return Verdict("final")
    if j.kind == "application_form":
        return Verdict("form", f"{len(real_fields(p))} fields")
    return Verdict("navigate", j.kind)


# ------------------------------------------------------------------ §6.4 gate

def gate(p: Page, resume_name: str, typed: dict[str, str]) -> str | None:
    """None if the final page may be parked, else the first failed check. `typed` = ref → generated text."""
    if p.required_empty.get("n"):
        items = ", ".join(i[0] for i in p.required_empty.get("items", []))
        return f"required fields still empty ({p.required_empty['n']}): {items}"
    if v := validation_error(p):
        return f"validation message on page: {v}"
    stem = resume_name[:30]
    if stem not in p.text and not any(stem in (e.value or "") for e in p.elements):
        return f"resume file name {stem!r} not on the page"
    if not judge(p).submit_button:
        return "no submit button on the final page"
    if is_alarm(p):
        return "confirmation text on page"
    by_ref = {e.ref: e for e in p.elements}
    for ref, text in typed.items():
        e = by_ref.get(ref)
        if e is None or (e.value or "")[:160] != text[:160]:
            return f"typed text not held by field {ref}"
    return None
