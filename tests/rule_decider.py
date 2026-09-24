"""RuleDecider: an offline stand-in for Jev in unit and browser tests.

It answers the questions the program asks Jev (pages.page_questions, entry.starts_application, answers.judge_answers,
answers.judge_questions) with the regex rules the program used before Jev decided (user decision 2026-09-24). The
program never uses these rules; tests do, so the loop can run without a network. Live tests use the real Jev.

A test can pin a page's kind by URL (`kinds`), where the rules cannot know (a job-alert box, say).
"""
from __future__ import annotations

import re
from typing import Any

from assistant.decide import Answer
from assistant.guard import is_entry, is_guest, is_transmit

ADVANCE_RE = r"^\s*(next|continue|review|save and continue|save & continue)\b"


def is_advance(label: str) -> bool:
    return bool(re.search(ADVANCE_RE, label or "", re.I)) and not is_transmit(label)


ALARM_RE = re.compile(r"(your )?application (was )?(submitted|sent)|thank(s| you) for (applying|your application)", re.I)
CLOSED_RE = re.compile(r"no longer accepting applications", re.I)
APPLIED_RE = re.compile(r"\bapplied \d+ \w+ ago\b|application submitted", re.I)
VALIDATION_RE = re.compile(r"this field is required|is required\.|please (enter|select|fill|provide)|"
                           r"invalid (value|format|email|phone)", re.I)
CAPTCHA_TEXT_RE = re.compile(r"verify you('| a)re human|are you a robot", re.I)
SIGNUP_RE = re.compile(r"create (an |your )?account|sign up|register", re.I)
GOOGLE_BUTTON_RE = re.compile(r"(sign in|continue) with google", re.I)
REGISTRATION_RE = re.compile(r"create (an |your )?(account|profile)|terms of (use|service)|"
                             r"complete (your )?registration", re.I)
GOOGLE_CONSENT_RE = re.compile(r"wants to access your google account|\ballow\b", re.I)
TWO_STEP_RE = re.compile(r"2-step verification|two-step verification|verification code|enter the code", re.I)
LOAD_FAIL_RE = re.compile(r"^\s*(404|500|502|503)\b|page not found|this site can.t be reached|"
                          r"err_[a-z_]+|server error", re.I)
RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
UPLOAD_TRIGGER_RE = re.compile(r"^\s*(upload|attach|add)\b.{0,20}\b(resume|résumé|cv|file|document)\b", re.I)
RESUME_FILE_RE = re.compile(r"\.(pdf|docx?|rtf|txt)\b", re.I)
CONSENT_RE = re.compile(r"cookie|consent", re.I)
SITE_CHROME_RE = re.compile(r"^\s*(search\b|select language\s*$|set alert for similar jobs\b)", re.I)
LOADING_RE = re.compile(r"\b(fetching|loading)\b[^.\n]{0,40}\b(form|application|questions|job|content)\b"
                        r"|\bplease wait\b|\bloading\s*(\.\.\.|…)", re.I)
FORM_IFRAME_RE = re.compile(r"greenhouse|lever\.co|workday|myworkdayjobs|ashbyhq|smartrecruiters|icims|jobvite|"
                            r"apply|application|candidate|career|recruit|/jobs?/|(?<![a-z])forms?(?![a-z])", re.I)
NON_FORM_IFRAME_RE = re.compile(r"google\.[a-z.]+/maps|maps\.google|youtube|vimeo|recaptcha|hcaptcha|doubleclick|"
                                r"googletagmanager|analytics|onetrust|cookiebot|consent", re.I)
FORBIDDEN_GENERATED = re.compile(
    r"salary|compensation|\bpay\b|notice period|start date|availability|visa|sponsor|right to work|authori[sz]ed|"
    r"years of experience|gender|ethnic|race|veteran|disabilit", re.I)
TOTAL_YEARS_RE = re.compile(r"\byears?\b.{0,40}\bexperience\b|\bexperience\b.{0,20}\byears?\b", re.I)
TOOL_YEARS_RE = re.compile(r"\bexperience\b.{0,30}\b(with|in|using|on|of)\s+(?!(the\s+)?(industry|field|workforce)\b)"
                           r"\S", re.I)
PLACEHOLDER_RE = re.compile(r"^\s*(select|choose|pick|please select|--|—)\b.*$|^\s*(select|choose)\s*\.{0,3}\s*$", re.I)

FIELD_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "switch", "file"}
CONTROL_ROLES = {"button", "link", "menuitem", "tab"}


def yes(v: bool) -> Answer:
    return Answer("noul", noul=0.95 if v else 0.05)


def pick(c: str, confidence: float = 0.9) -> Answer:
    return Answer("choice", choice=c, confidence=confidence, probabilities={c: confidence})


class RuleDecider:
    def __init__(self, kinds: dict[str, str] | None = None):
        self.kinds = dict(kinds or {})      # url → page kind, for what the rules cannot see
        self.asked: list[tuple[str, list[str]]] = []
        self.calls = 0
        self.cost = 0.0
        self.log = None

    def ask(self, topic: str, state: Any, questions: dict[str, dict]) -> dict[str, Answer]:
        self.asked.append((topic, list(questions)))
        self.calls += 1
        fn = getattr(self, f"_{topic}")
        return {qid: fn(qid, q, state) for qid, q in questions.items()}

    # ---------------------------------------------------------------- pages.page_questions
    def _page(self, qid: str, q: dict, s: dict) -> Answer:
        controls, text, title = s["controls"], s["text"], s["title"]
        names = [c["name"] for c in controls]
        fields = [c for c in controls if c["role"] in FIELD_ROLES]
        real = [c for c in fields if not (c["role"] == "searchbox" or SITE_CHROME_RE.match(c["name"]))]
        buttons = [c for c in controls if c["role"] in CONTROL_ROLES]
        password = [c for c in controls if c["role"] == "textbox" and re.search(r"pass(word|code)", c["name"], re.I)]
        google = next((c for c in buttons if GOOGLE_BUTTON_RE.search(c["name"])), None)
        transmit = any(is_transmit(c["name"]) and not (not real and is_entry(c["name"])) for c in buttons
                       if c["role"] == "button" or (c["role"] == "link" and len(c["name"]) <= 40))
        advance = any(c["role"] == "button" and is_advance(c["name"]) for c in buttons)
        host = re.sub(r"^https?://([^/]+).*$", r"\1", s["url"])
        if qid == "kind":
            if s["url"] in self.kinds:
                return pick(self.kinds[s["url"]])
            if ALARM_RE.search(text):
                return pick("confirmation")
            if host == "accounts.google.com":
                return pick("google_sign_in")
            if CAPTCHA_TEXT_RE.search(text):
                return pick("captcha")
            if password or google:
                return pick("account_wall")
            if LOAD_FAIL_RE.search(title) or (not fields and LOAD_FAIL_RE.search(text[:300])):
                return pick("error")
            if CLOSED_RE.search(text):
                return pick("closed")
            if transmit and not advance:
                return pick("final_step")
            if real:
                return pick("application_form")
            if any(is_entry(c["name"]) for c in buttons if c["role"] in {"button", "link"}):
                return pick("job_posting")
            if any(re.match(r"^\s*(continue|next|review)\b", c["name"], re.I) for c in buttons):
                return pick("interstitial")               # a pop-up or step with a way on, and no questions
            return pick("other")
        if qid == "submitted":
            return yes(bool(ALARM_RE.search(text)))
        if qid == "applied":
            return yes(bool(APPLIED_RE.search(text)))
        if qid == "covered":
            return yes(any(CONSENT_RE.search(d) for d in s["open_dialogs"]) or any(c.get("covered") for c in real))
        if qid == "actionable":
            if real or ALARM_RE.search(text):
                return yes(True)
            if LOADING_RE.search(text):
                return yes(False)
            if len(text.strip()) >= 200:
                return yes(True)
            return yes(any(is_entry(n) or is_advance(n) or is_transmit(n) or GOOGLE_BUTTON_RE.search(n)
                           or is_guest(n) for n in [c["name"] for c in buttons]))
        if qid == "validation":
            return yes(bool(VALIDATION_RE.search(text)))
        if qid == "registration":
            return yes(bool(REGISTRATION_RE.search(text)))
        if qid == "account":
            if SIGNUP_RE.search(text) or SIGNUP_RE.search(title):
                return pick("create_account")
            return pick("sign_in" if password else "none")
        if qid == "submit_button":
            return yes(transmit)
        if qid.startswith("field_"):
            c = q["instructions"]["field"]
            return yes(not (c["role"] == "searchbox" or SITE_CHROME_RE.match(c["name"])))
        if qid == "resume_input":
            return self._resume_input(controls, text)
        if qid.startswith("cover_"):
            label = q["instructions"]["upload"]
            return yes(bool(re.search(r"cover", label, re.I)) and not RESUME_RE.search(label))
        if qid == "google_step":
            if password or TWO_STEP_RE.search(text):
                return pick("two_step" if TWO_STEP_RE.search(text) else "password")
            return pick("consent" if GOOGLE_CONSENT_RE.search(text) else "account_chooser")
        if qid == "google_button":
            return pick(google["ref"] if google else "none")
        if qid == "form_iframe":
            for key, src in q["criteria"].items():
                if key != "none" and FORM_IFRAME_RE.search(src) and not NON_FORM_IFRAME_RE.search(src):
                    return pick(key)
            return pick("none")
        raise KeyError(qid)

    @staticmethod
    def _resume_input(controls: list[dict], text: str) -> Answer:
        files = [c for c in controls if c["role"] == "file"]
        resume_step = bool(RESUME_RE.search(text))
        if not files:
            triggers = [c for c in controls if c["role"] == "button" and UPLOAD_TRIGGER_RE.search(c["name"])
                        and not is_transmit(c["name"])]
            return pick(triggers[0]["ref"] if resume_step and len(triggers) == 1 else "none")
        named = [c for c in files if RESUME_RE.search(c["name"] + " " + c.get("option", ""))]
        if named:
            return pick(named[0]["ref"])
        if len(files) == 1 and resume_step:
            return pick(files[0]["ref"])
        if len(files) > 1 and resume_step:
            return pick(files[0]["ref"], 0.3)             # several uploads, none named: the program must not guess
        return pick("none")

    # ---------------------------------------------------------------- entry.starts_application
    def _entry(self, qid: str, q: dict, s: Any) -> Answer:
        label = q["instructions"]["control"]
        return yes(is_entry(label) or is_guest(label))

    # ---------------------------------------------------------------- answers.judge_answers / judge_questions
    def _answers(self, qid: str, q: dict, s: Any) -> Answer:
        ins = q["instructions"]
        if qid.startswith("gen_"):
            return yes(bool(FORBIDDEN_GENERATED.search(ins["form_question"])))
        if qid.startswith("years_"):
            fq = ins["form_question"]
            if TOTAL_YEARS_RE.search(fq):
                return pick("specific" if TOOL_YEARS_RE.search(fq) else "total")
            return pick("other")
        if qid.startswith("shown_"):
            return yes(bool(PLACEHOLDER_RE.match(ins["value"])))
        raise KeyError(qid)

    def _questions(self, qid: str, q: dict, s: Any) -> Answer:
        ins = q["instructions"]
        if qid.startswith("resume_"):
            fq = ins["form_question"]
            return yes(bool(RESUME_RE.search(fq)) and not re.search(r"cover", fq, re.I)
                       or any(RESUME_FILE_RE.search(o or "") for o in ins.get("options") or []))
        if qid.startswith("cover_"):
            return yes(bool(re.search(r"cover", ins["upload"], re.I)) and not RESUME_RE.search(ins["upload"]))
        raise KeyError(qid)

    def _preflight(self, qid: str, q: dict, s: Any) -> Answer:
        return yes(True)
