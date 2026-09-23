"""Deterministic page rules (§6). Pure functions over a Page snapshot; read_page() is the only I/O here."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from assistant import guard
from assistant.guard import is_advance, is_entry, is_transmit
from assistant.jev import Jev, Element, Table, split_json

FORM_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "switch", "file"}

SIGNED_OUT_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/")
CLOSED_RE = re.compile(r"no longer accepting applications", re.I)
APPLIED_RE = re.compile(r"\bapplied \d+ \w+ ago\b|application submitted", re.I)
ALARM_RE = re.compile(r"(your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)", re.I)
VALIDATION_RE = re.compile(r"this field is required|is required\.|please (enter|select|fill|provide)|"
                           r"invalid (value|format|email|phone)", re.I)
CAPTCHA_TEXT_RE = re.compile(r"verify you('| a)re human|are you a robot", re.I)
SIGNUP_RE = re.compile(r"create (an |your )?account|sign up|register", re.I)
GOOGLE_BUTTON_RE = re.compile(r"(sign in|continue) with google", re.I)
GOOGLE_REGISTRATION_RE = re.compile(r"create (an |your )?(account|profile)|terms of (use|service)|"
                                    r"complete (your )?registration", re.I)
GOOGLE_CONSENT_RE = re.compile(r"wants to access your google account|\ballow\b", re.I)
GUEST_RE = re.compile(guard.GUEST_RE, re.I)
TWO_STEP_RE = re.compile(r"2-step verification|two-step verification|verification code|enter the code", re.I)
LOAD_FAIL_RE = re.compile(r"^\s*(404|500|502|503)\b|page not found|this site can.t be reached|"
                          r"err_[a-z_]+|server error", re.I)
COOKIE_RE = re.compile(r"cookie", re.I)
COOKIE_CHOICES = [r"^\s*reject all\b", r"^\s*only (the )?necessary\b", r"^\s*accept all\b"]  # C24, in order
RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)


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


def read_page(browser: Jev, session: str) -> Page:
    view, table = split_json(browser.observe(session))
    probes = browser.probe(session, "REQUIRED_EMPTY", "MAXLENGTHS", "IFRAME_SRCS")
    return Page(url=table.url, title=table.title, text=view_text(view), table=table,
                required_empty=probes["REQUIRED_EMPTY"], maxlengths=probes["MAXLENGTHS"],
                iframe_srcs=probes["IFRAME_SRCS"], captcha=browser.captcha_present(session))


def page_from_capture(observe_text: str) -> Page:
    """A Page built from a saved browser_observe(mode="full", include_json=True) output (no probes)."""
    view, table = split_json(observe_text)
    return Page(url=table.url, title=table.title, text=view_text(view), table=table)


# ------------------------------------------------------------------ element helpers

def norm_label(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def buttons(p: Page) -> list[Element]:
    return [e for e in p.elements if e.role in {"button", "link", "menuitem", "tab"}]


def fields(p: Page) -> list[Element]:
    return [e for e in p.elements if e.role in FORM_ROLES]


def password_fields(p: Page) -> list[Element]:
    return [e for e in p.elements if e.role == "textbox" and re.search(r"pass(word|code)", e.name, re.I)]


def find_button(p: Page, pattern: str | re.Pattern) -> Element | None:
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    return next((e for e in buttons(p) if rx.search(e.name)), None)


# ------------------------------------------------------------------ §6.5 flags

def has_fields(p: Page) -> bool:
    return bool(fields(p))


def has_transmit(p: Page) -> bool:
    no_fields = not has_fields(p)
    return any(is_transmit(e.name) and not (no_fields and is_entry(e.name)) for e in buttons(p)
               if e.role in {"button", "link"})


def advance_buttons(p: Page) -> list[Element]:
    return [e for e in buttons(p) if e.role == "button" and is_advance(e.name)]


def has_advance(p: Page) -> bool:
    return bool(advance_buttons(p))


def is_final(p: Page) -> bool:
    return has_transmit(p) and not has_advance(p)


# ------------------------------------------------------------------ §6.1 LinkedIn job page

def classify_entry(p: Page) -> str:
    """signed_out | closed | applied | entry | none."""
    if any(m in p.url for m in SIGNED_OUT_MARKERS):
        return "signed_out"
    if CLOSED_RE.search(p.text):
        return "closed"
    if APPLIED_RE.search(p.text):
        return "applied"
    if any(is_entry(e.name) for e in buttons(p) if e.role in {"button", "link"}):
        return "entry"
    return "none"


def linkedin_feed_ok(url: str) -> bool:
    """Preflight probe: still on /feed and no signed-out marker."""
    return "/feed" in url and not any(m in url for m in SIGNED_OUT_MARKERS)


# ------------------------------------------------------------------ §6.2 Google

def is_google_page(p: Page) -> bool:
    return p.host == "accounts.google.com"


def google_button(p: Page) -> Element | None:
    return find_button(p, GOOGLE_BUTTON_RE)


def google_blocker(p: Page) -> str | None:
    """On accounts.google.com: why the one-click rule cannot continue, else None."""
    if password_fields(p) or TWO_STEP_RE.search(p.text):
        return "Google asks for a password or 2-step verification"
    if GOOGLE_CONSENT_RE.search(p.text):
        return "Google asks for consent to share the account"
    return None


def after_google_blocker(p: Page) -> str | None:
    """Back on the site after Google: registration or terms still pending?"""
    return "site asks to complete a registration after Google sign-in" if GOOGLE_REGISTRATION_RE.search(p.text) else None


# ------------------------------------------------------------------ §6.3 blockers

@dataclass
class Blocker:
    cls: str       # captcha | signup | credentials | broken_form | load_failure
    cue: str


def blocker(p: Page) -> Blocker | None:
    """First blocker cue on the page. Google paths are not blockers here (handled by §6.2)."""
    if p.captcha or CAPTCHA_TEXT_RE.search(p.text):
        return Blocker("captcha", "captcha on page")
    if password_fields(p):
        if google_button(p) or is_google_page(p):
            return None
        if SIGNUP_RE.search(p.text) or SIGNUP_RE.search(p.title):
            return Blocker("signup", "site asks to create an account")
        return Blocker("credentials", "site asks for a password")
    if not p.elements and not p.text.strip():
        return Blocker("load_failure", "blank page")
    if LOAD_FAIL_RE.search(p.title) or (not fields(p) and LOAD_FAIL_RE.search(p.text[:300])):
        return Blocker("load_failure", f"error page: {p.title or p.text[:60]!r}")
    return None


def validation_error(p: Page) -> str | None:
    m = VALIDATION_RE.search(p.text)
    return m.group(0) if m else None


def guest_link(p: Page) -> Element | None:
    return find_button(p, GUEST_RE)


# ------------------------------------------------------------------ banners, ATS pages, iframes

def cookie_choice(p: Page) -> Element | None:
    """The cookie-banner button to click (C24): Reject all, else Only necessary, else Accept all."""
    if not COOKIE_RE.search(p.text):
        return None
    for pat in COOKIE_CHOICES:
        if hit := find_button(p, pat):
            return hit
    return None


def ats_entry_button(p: Page) -> Element | None:
    """An Apply button on a page without form fields (an ATS job page)."""
    if has_fields(p):
        return None
    hits = [e for e in buttons(p) if e.role in {"button", "link"} and is_entry(e.name)]
    return hits[0] if len(hits) == 1 else None


def iframe_form_src(p: Page) -> str | None:
    """A form-looking iframe src to navigate to when the page itself has no fields."""
    if has_fields(p) or not p.iframe_srcs.get("items"):
        return None
    return p.iframe_srcs["items"][0]


def is_alarm(p: Page) -> bool:
    return bool(ALARM_RE.search(p.text))


# ------------------------------------------------------------------ classifier (the §5 loop order)

@dataclass
class Verdict:
    kind: str       # alarm | blocker | google | google_wall | cookie | ats_entry | iframe | form | final | advance | unknown
    detail: str = ""
    ref: str | None = None


def classify(p: Page) -> Verdict:
    if is_alarm(p):
        return Verdict("alarm", ALARM_RE.search(p.text).group(0))
    if is_google_page(p):
        return Verdict("google", google_blocker(p) or "")
    if b := blocker(p):
        return Verdict("blocker", f"{b.cls}: {b.cue}")
    if g := google_button(p):
        return Verdict("google_wall", g.name, g.ref)
    if c := cookie_choice(p):
        return Verdict("cookie", c.name, c.ref)
    if a := ats_entry_button(p):
        return Verdict("ats_entry", a.name, a.ref)
    if src := iframe_form_src(p):
        return Verdict("iframe", src)
    if has_fields(p):
        return Verdict("form", f"{len(fields(p))} fields")
    if is_final(p):
        return Verdict("final")
    if has_advance(p):
        return Verdict("advance", advance_buttons(p)[0].name, advance_buttons(p)[0].ref)
    return Verdict("unknown")


# ------------------------------------------------------------------ §6.4 gate

def gate(p: Page, resume_name: str, typed: dict[str, str]) -> str | None:
    """None if the final page may be parked, else the first failed check. `typed` = ref → generated text."""
    if p.required_empty.get("n"):
        items = ", ".join(i[0] for i in p.required_empty.get("items", []))
        return f"required fields still empty ({p.required_empty['n']}): {items}"
    if v := validation_error(p):
        return f"validation message on page: {v!r}"
    stem = resume_name[:30]
    if stem not in p.text and not any(stem in (e.value or "") for e in p.elements):
        return f"resume file name {stem!r} not on the page"
    if not has_transmit(p):
        return "no submit button on the final page"
    if is_alarm(p):
        return "confirmation text on page"
    by_ref = {e.ref: e for e in p.elements}
    for ref, text in typed.items():
        e = by_ref.get(ref)
        if e is None or (e.value or "")[:160] != text[:160]:
            return f"typed text not held by field {ref}"
    return None
