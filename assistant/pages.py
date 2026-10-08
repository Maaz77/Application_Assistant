"""Pages: what the program reads (read_page) and what it decides about a page.

P3: every judgment is a deterministic code rule (no model call). The rules are ported from the RuleDecider
test double, which has been passing 375+ tests since P2. Jev is not called for page classification.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from assistant import guard
from assistant import probes as probes_mod
from assistant.browser import Browser, Element, Table, split_json

FORM_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "switch", "file"}
CONTROL_ROLES = {"button", "link", "menuitem", "tab"}

SIGNED_OUT_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/")
ALARM_RE = re.compile(r"(your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)", re.I)
RESUME_FILE_RE = re.compile(r"\.(pdf|docx?|rtf|txt)\b", re.I)

# Code-rule patterns (ported from RuleDecider, P3 T2).
# A posting can also simply be taken down: careers.toasttab.com served "The page you are trying to view is
# no longer available." for a job that had parked four hours earlier (live 2026-10-08), and with no form
# on it the run reported `unsupported_ats: no application form`, which reads like a site we cannot drive
# rather than a job that is gone.
_CLOSED_RE = re.compile(r"(no longer|not currently) accepting applications|this job is (closed|no longer)|no longer available", re.I)
_APPLIED_RE = re.compile(r"\bapplied \d+ \w+ ago\b|application submitted|see application", re.I)
_VALIDATION_RE = re.compile(r"this field is required|is required\.|please enter a valid |"
                            r"invalid (value|format|email|phone)", re.I)
_REGISTRATION_RE = re.compile(r"create (an |your )?(account|profile)|terms of (use|service)|"
                              r"complete (your )?registration", re.I)
_SIGNUP_RE = re.compile(r"create (an |your )?account|sign up|register", re.I)
_CONSENT_RE = re.compile(r"cookie|consent", re.I)
_GOOGLE_BUTTON_RE = re.compile(r"(sign in|continue) with google", re.I)
_GOOGLE_CONSENT_RE = re.compile(r"wants to access your google account|\ballow\b", re.I)
_TWO_STEP_RE = re.compile(r"2-step verification|two-step verification|verification code|enter the code", re.I)
_CAPTCHA_TEXT_RE = re.compile(r"verify you('| a)re human|are you a robot", re.I)
_LOAD_FAIL_RE = re.compile(r"^\s*(404|500|502|503)\b|page not found|this site can.t be reached|"
                           r"err_[a-z_]+|server error", re.I)
_RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
_AUTOFILL_RE = re.compile(r"autofill|auto-fill|\bparse\b|populate", re.I)
_UPLOAD_TRIGGER_RE = re.compile(r"^\s*(upload|attach|add)\b.{0,20}\b(resume|résumé|cv|file|document)\b", re.I)
_SITE_CHROME_RE = re.compile(r"^\s*(search\b|select language\s*$|set alert for similar jobs\b)", re.I)
_FORM_IFRAME_RE = re.compile(r"greenhouse|lever\.co|workday|myworkdayjobs|ashbyhq|smartrecruiters|icims|jobvite|"
                             r"apply|application|candidate|career|recruit|/jobs?/|(?<![a-z])forms?(?![a-z])", re.I)
# The "Apply with LinkedIn" widget host matches _FORM_IFRAME_RE on "apply" but is never the form: it is an
# iframe on the ATS posting whose whole configuration arrives in its query string, so the hosted-form hop
# navigates to it bare and it paints nothing (live 2026-10-08, Genesys: the run reached the right Workday
# posting, then hopped to applywithlinkedin.myworkdaygadgets.com/awli/ and reported `blank page` twice —
# the widget script is requested with apiKey=undefined and applyUrl pointing back at the gadget itself).
_NON_FORM_IFRAME_RE = re.compile(r"google\.[a-z.]+/maps|maps\.google|youtube|vimeo|recaptcha|hcaptcha|doubleclick|"
                                 r"googletagmanager|analytics|onetrust|cookiebot|consent|"
                                 r"applywithlinkedin|myworkdaygadgets|talentwidgets", re.I)


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


def read_page(browser: Browser, session: str) -> Page:
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


def code_app_fields(p: Page) -> set[str]:
    """Application fields: scoped inside the LinkedIn Easy Apply dialog, or all form-role elements on external
    ATS minus the site chrome. `scope` marks a field inside that dialog and is a LinkedIn concept — off LinkedIn
    a scoped field is just a form field (e.g. a site-search box in a `<form role=search>`, which carries a
    scope), so there it must still be filtered as site chrome rather than short-circuiting to the app fields
    (Toast's careers page, live 2026-10-07)."""
    if "linkedin.com" in p.host:
        return {e.ref for e in fields(p) if e.scope}
    return {e.ref for e in fields(p)
            if e.role != "searchbox" and not _SITE_CHROME_RE.match(e.name or "")}


def _code_kind(p: Page) -> str:
    """Page classification from code rules. No model call."""
    btns = buttons(p)
    flds = fields(p)
    real = [e for e in flds if not (e.role == "searchbox" or _SITE_CHROME_RE.match(e.name or ""))]
    password = [e for e in flds if e.role == "textbox" and re.search(r"pass(word|code)", e.name or "", re.I)]
    google = next((e for e in btns if _GOOGLE_BUTTON_RE.search(e.name or "")), None)
    transmit = any(guard._submit_like(e) for e in p.elements if e.role in CONTROL_ROLES)
    advance = any(e.role == "button" and guard.ADVANCE_RE.search(e.name or "")
                  and not guard.REFUSE_LABEL_RE.search(e.name or "") for e in btns)
    if ALARM_RE.search(p.text):
        return "confirmation"
    if p.host == "accounts.google.com":
        return "google_sign_in"
    if _CAPTCHA_TEXT_RE.search(p.text) or p.captcha:
        return "captcha"
    if password or (google and not real):
        return "account_wall"
    if _LOAD_FAIL_RE.search(p.title) or (not flds and _LOAD_FAIL_RE.search(p.text[:300])):
        return "error"
    if _CLOSED_RE.search(p.text):
        return "closed"
    if transmit and not advance:
        return "final_step"
    if (any(e.scope for e in flds) and "linkedin.com" in p.host) or real:   # scope counts only on LinkedIn
        return "application_form"
    if any(re.search(r"^\s*(easy apply|apply)\b", e.name or "", re.I) for e in btns):
        return "job_posting"
    if any(re.match(r"^\s*(continue|next|review)\b", e.name or "", re.I) for e in btns):
        return "interstitial"
    return "other"


def _code_resume(p: Page) -> tuple[str | None, float, dict]:
    """Resume input ref, confidence, probabilities — from code."""
    controls = [e for e in p.elements if e.role in CONTROL_ROLES | {"file"}]
    file_inputs = [e for e in controls if e.role == "file"]
    resume_step = bool(_RESUME_RE.search(p.text))
    if not file_inputs:
        triggers = [e for e in controls if e.role == "button"
                    and _UPLOAD_TRIGGER_RE.search(e.name or "") and not guard._submit_like(e)]
        if resume_step and len(triggers) == 1:
            return triggers[0].ref, 0.9, {triggers[0].ref: 0.9}
        return None, 0.0, {}
    # Ashby lists an "Autofill from resume" file input before the real "Resume" input (DISCOVERY 2026-09-23):
    # drop the autofill/parser input when a non-autofill file input is also present, so the résumé goes to the
    # real control, not the parser.
    non_autofill = [e for e in file_inputs if not _AUTOFILL_RE.search(f"{e.name} {e.label}")]
    if non_autofill and len(non_autofill) < len(file_inputs):
        file_inputs = non_autofill
    named = [e for e in file_inputs if _RESUME_RE.search(f"{e.name} {e.label}")]
    if named:
        return named[0].ref, 0.9, {named[0].ref: 0.9}
    if len(file_inputs) == 1 and resume_step:
        return file_inputs[0].ref, 0.8, {file_inputs[0].ref: 0.8}
    if len(file_inputs) > 1 and resume_step:
        return file_inputs[0].ref, 0.3, {e.ref: 0.3 / len(file_inputs) for e in file_inputs}
    return None, 0.0, {}


def _code_cover_letters(p: Page) -> list[str]:
    return [label for label, kind_, *_ in p.required_empty.get("items", [])
            if kind_ == "file" and re.search(r"cover", label, re.I) and not _RESUME_RE.search(label)]


def _code_google(p: Page) -> tuple[str | None, str | None]:
    """(google_button_ref, google_step)."""
    if p.host == "accounts.google.com":
        pw = any(e.role == "textbox" and re.search(r"pass(word|code)", e.name or "", re.I) for e in p.elements)
        if _TWO_STEP_RE.search(p.text):
            return None, "two_step"
        if pw:
            return None, "password"
        if _GOOGLE_CONSENT_RE.search(p.text):
            return None, "consent"
        return None, "account_chooser"
    ref = next((e.ref for e in buttons(p) if _GOOGLE_BUTTON_RE.search(e.name or "")), None)
    return ref, None


def _code_form_iframe(p: Page) -> str | None:
    for src in p.iframe_srcs.get("items", []):
        if _FORM_IFRAME_RE.search(src) and not _NON_FORM_IFRAME_RE.search(src):
            return src
    return None


def judge(p: Page) -> Judgment:
    """Deterministic page judgment from code rules. No model call. Cached on Page."""
    if p.judgment is not None:
        return p.judgment
    kind = _code_kind(p)
    resume_ref, resume_conf, resume_probs = _code_resume(p)
    google_ref, google_step = _code_google(p)
    real = code_app_fields(p)
    pw = any(e.role == "textbox" and re.search(r"pass(word|code)", e.name or "", re.I) for e in fields(p))
    p.judgment = Judgment(
        kind=kind, kind_confidence=0.9,
        submitted=bool(ALARM_RE.search(p.text)),
        applied=bool(_APPLIED_RE.search(p.text)),
        covered=any(_CONSENT_RE.search(d) for d in p.dialogs)
                or any(e.occluded for e in fields(p) if e.ref in real),
        loading=unsettled(p),
        validation=bool(_VALIDATION_RE.search(p.text)),
        registration=bool(_REGISTRATION_RE.search(p.text)),
        account="create_account" if _SIGNUP_RE.search(p.text + " " + p.title) else ("sign_in" if pw else "none"),
        submit_button=any(guard._submit_like(e) for e in p.elements if e.role in CONTROL_ROLES),
        app_fields=real,
        resume_ref=resume_ref, resume_confidence=resume_conf, resume_probabilities=resume_probs,
        cover_letters=_code_cover_letters(p),
        google_ref=google_ref, google_step=google_step,
        form_iframe=_code_form_iframe(p))
    return p.judgment


# ------------------------------------------------------------------ settling

SETTLE_SECONDS = 10.0


_ACTION_RE = re.compile(r"easy apply|apply|next|continue|submit", re.I)
_REAL_FORM = {"textbox", "radio", "checkbox", "file", "spinbutton"}


def unsettled(p: Page) -> bool:
    """Not drawn yet: blank, nav-only, or loading placeholder. Code rule, no model call."""
    if not p.elements and not p.text.strip():
        return True
    if ALARM_RE.search(p.text):
        return False
    if any(e.scope for e in p.elements if e.role in FORM_ROLES):
        return False
    if any(e.role in _REAL_FORM for e in p.elements):
        return False
    if any(e.role == "button" and _ACTION_RE.search(e.name or "") for e in p.elements):
        return False
    if len(p.text) > 200 and not p.elements:
        return False
    return True


def settle(read, sleep=time.sleep, seconds: float = SETTLE_SECONDS) -> Page:
    """Re-read until the page is no longer `unsettled`, bounded by `seconds` of WALL-CLOCK time.

    Wall-clock, not a read counter: a `read()` is cheap on a fast page but can cost several seconds on a slow one
    (a client-rendered page behind a slow local model, or Workday's blank "Apply with LinkedIn" gadget whose
    observe burns the driver's 4 s client-render wait twice per read). Counting iterations (`waited += 1` per
    loop) let a persistently-unsettled slow page run ~`seconds` reads of several seconds each — ~90 s per call,
    which stacked up into the P6 live-run "stuck" (DISCOVERY 2026-10-07). A monotonic deadline caps the real time
    spent here regardless of read cost."""
    deadline = time.monotonic() + seconds
    p = read()
    while unsettled(p) and time.monotonic() < deadline:
        sleep(1.0)
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
    """The application's own fields (not site search, language picker or job alert). Code-determined."""
    refs = judge(p).app_fields
    return [e for e in fields(p) if e.ref in refs]


def has_fields(p: Page) -> bool:
    return bool(real_fields(p))


def form_is_here(p: Page) -> bool:
    """The application form is on screen and nothing covers it. Code-determined (P3 T2)."""
    j = judge(p)
    return j.kind in FORM_KINDS and not j.covered


def covered(p: Page) -> bool:
    return judge(p).covered


def is_final(p: Page) -> bool:
    return judge(p).kind == "final_step"


def is_alarm(p: Page) -> bool:
    """Confirmation text detected by ALARM_RE (code rule, no model call)."""
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

def forward_submit(p: Page) -> bool:
    """A control that would submit the application: a submit-like control (guard._submit_like), or an
    apply-labelled forward control such as Greenhouse's 'Apply now!'. The guard still never clicks it
    (never_click_element refuses it); this only lets an external final page pass the gate so it parks,
    instead of failing 'no submit button'. Kept out of _submit_like so a LinkedIn posting's top-card
    'Apply' does not turn every posting into final_step."""
    if judge(p).submit_button:
        return True
    return any(e.role in CONTROL_ROLES and guard.APPLY_RE.search(f"{e.name or ''} {e.value or ''}")
               for e in p.elements)


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
    if not forward_submit(p):
        return "no submit button on the final page"
    if is_alarm(p):
        return "confirmation text on page"
    by_ref = {e.ref: e for e in p.elements}
    for ref, text in typed.items():
        e = by_ref.get(ref)
        if e is None or (e.value or "")[:160] != text[:160]:
            return f"typed text not held by field {ref}"
    return None
