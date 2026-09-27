"""RuleDecider: an offline stand-in for Jev in unit and browser tests.

It answers the questions the program asks Jev (pages.page_questions, answers.judge_answers,
answers.judge_questions, fill.plan_fill, fill.mismatches) with the regex rules the program used before Jev decided (user decision 2026-09-24). The
program never uses these rules; tests do, so the loop can run without a network. Live tests use the real Jev.

A test can pin a page's kind by URL (`kinds`), where the rules cannot know (a job-alert box, say).
"""
from __future__ import annotations

import re
from typing import Any

from assistant.decide import Answer

# The label lists of the old guard (before 2026-09-24): the rules still recognise pages by their buttons.
TRANSMIT = [r"\bsubmit", r"\bsend\b", r"\bapply\b", r"\bconfirm\b", r"\bdone\b", r"\bfinish", r"\bcomplete\b"]
ENTRY_RE = r"^\s*(easy apply|apply)\b"
GUEST_RE = r"apply without an account|continue as guest"


def is_transmit(label: str) -> bool:
    return any(re.search(p, label or "", re.I) for p in TRANSMIT)


def is_entry(label: str) -> bool:
    return bool(re.search(ENTRY_RE, label or "", re.I))


def is_guest(label: str) -> bool:
    return bool(re.search(GUEST_RE, label or "", re.I))


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


def held(v: bool) -> Answer:
    return pick("holds" if v else "different")


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

    # ---------------------------------------------------------------- fill.plan_fill
    def _fill(self, qid: str, q: dict, s: dict) -> Answer:
        """The routing the program used before Jev planned the fill (direct_op, 2026-09-23/24): the LLM inference's
        ref decides the target, the kind of control decides the operation."""
        ins, crit = q["instructions"], q["criteria"]
        key = lambda x: re.sub(r"[^a-z0-9]+", "", (x or "").lower())
        by_ref = {c["ref"]: c for c in s["controls"]}
        ref, oref, answer = ins.get("llm_inference_ref"), ins.get("llm_inference_option_ref"), ins["answer"]
        el = by_ref.get(ref or "")
        sel = next((r for r in (oref, ref) if r and re.match(r"^e\d+:\d+$", r) and r.split(":")[0] in by_ref), None)
        listed = el is not None and el["role"] in {"combobox", "listbox"} and any(
            key(o) == key(answer) for o in el.get("options") or [])
        if qid.startswith("op_"):
            if sel or listed:
                return pick("select")
            if oref and (by_ref.get(oref) or {}).get("role") in {"radio", "checkbox", "switch"}:
                return pick("check")
            if el is not None and el["role"] in {"textbox", "searchbox", "spinbutton"} and not el.get("read_only"):
                return pick("type")
            return pick("widget")
        if qid.startswith("field_"):
            base = sel.split(":")[0] if sel else ref
            return pick(base if base in crit else "none")
        if qid.startswith("option_"):
            if sel in crit or (oref and oref in crit):
                return pick(sel if sel in crit else oref)
            hit = next((k for k, v in crit.items() if ref and k.startswith(f"{ref}:") and key(v).endswith(key(answer))),
                       None)
            return pick(hit or "none")
        raise KeyError(qid)

    # ---------------------------------------------------------------- fill.mismatches (the read-back)
    def _readback(self, qid: str, q: dict, s: dict) -> Answer:
        """The read-back rules the program used before Jev read fields back (2026-09-24), on the page state."""
        ins, controls = q["instructions"], s["controls"]
        key = lambda x: re.sub(r"[^a-z0-9]+", "", (x or "").lower())
        answer, question = key(ins["answer"]), key(ins["question"])
        by_ref = {c["ref"]: c for c in controls}
        for ref in (ins.get("option_ref"), ins.get("ref")):          # a <select> option named by its own ref
            m = re.match(r"^(e\d+):\d+$", ref or "")
            if m and m.group(1) in by_ref:
                return held(key(by_ref[m.group(1)].get("value")) == answer)
        toggles = [c for c in controls if c["role"] in {"radio", "checkbox", "switch"}]
        if ins.get("option_ref"):                                     # a redraw may renumber the option
            hits = [by_ref[ins["option_ref"]]] if ins["option_ref"] in by_ref else (
                [c for c in toggles if key(c["name"]) == question and key(c.get("option")) == answer]
                or [c for c in toggles if key(c["name"]) == answer])
            return held(len(hits) == 1 and bool(hits[0].get("checked")))
        el = by_ref.get(ins.get("ref") or "") or next((c for c in controls if key(c["name"]) == question), None)
        if el is None:
            radios = [c for c in toggles if c["role"] == "radio" and key(c["name"]) == answer]
            return held(len(radios) == 1 and bool(radios[0].get("checked")))
        if key(el.get("value")) == answer:
            return held(True)
        if el["role"] == "combobox" and not el.get("options") and not el.get("value"):
            return held(answer in key(s["text"]))                     # a custom combobox shows its pick as text
        return held(False)

    def _preflight(self, qid: str, q: dict, s: Any) -> Answer:
        return yes(True)
