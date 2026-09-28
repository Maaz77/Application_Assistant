"""LLM inference (§7): one chat call per form page (OpenRouter or Vercel AI Gateway), then deterministic checks
in code."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, ValidationError

from assistant import decide, gateway as gateway_mod, inference_log
from assistant.decide import THRESHOLDS as T
from assistant.pages import Page
from assistant.rotation import NoModelAvailable, Rotation

OPENROUTER_CHAT = "https://openrouter.ai/api/v1/chat/completions"   # the default route (config.chat_url)
PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "llm_inference.md"
PAGE_TEXT_MAX = 12_000

Kind = Literal["text", "longtext", "choice", "file", "other"]
Source = Literal["profile", "job", "resume", "generated", "computed", "linkedin-prefill"]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    question: str
    kind: Kind
    ref: str | None
    option_ref: str | None
    options: list[str] | None
    required: bool
    answer: str | None
    source: Source | None
    quote: str | None
    relies_on: list[str] | None
    note: str | None = None      # set by the checks (why an answer was dropped); not part of the model schema
    resume_upload: bool = False  # Jev (judge_questions): the resume upload answers this question
    cover_letter: bool = False   # Jev (judge_questions): an upload that asks for a cover letter


class PageAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Question]
    model: str | None = None     # set by call_engine: the model that answered; not part of the model schema


def _nullable(t: dict) -> dict:
    return {**t, "type": [t["type"], "null"]}


QUESTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "question", "kind", "ref", "option_ref", "options", "required", "answer", "source",
                 "quote", "relies_on"],
    "properties": {
        "id": {"type": "string"}, "question": {"type": "string"},
        "kind": {"type": "string", "enum": ["text", "longtext", "choice", "file", "other"]},
        "ref": _nullable({"type": "string"}), "option_ref": _nullable({"type": "string"}),
        "options": _nullable({"type": "array", "items": {"type": "string"}}),
        "required": {"type": "boolean"}, "answer": _nullable({"type": "string"}),
        "source": {"type": ["string", "null"],
                   "enum": ["profile", "job", "resume", "generated", "computed", "linkedin-prefill", None]},
        "quote": _nullable({"type": "string"}),
        "relies_on": _nullable({"type": "array", "items": {"type": "string"}}),
    },
}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
          "properties": {"questions": {"type": "array", "items": QUESTION_SCHEMA}}}

LONG_TEXT = 300
# Vercel AI Gateway caps a new team at 5 requests a minute per model and says when to come back (HTTP 429 with
# `Retry-After: 22`, mistral-nemo, live 2026-09-24). A wait that short beats failing the page; longer ones rotate on.
MAX_TOKENS = 8192          # one page of answers; OpenRouter otherwise reserves the model maximum (HTTP 402)
# Extraction with quotes needs no hidden reasoning, and a reasoning model spends MAX_TOKENS on it: qwen3.7-flash
# returned an empty and then a cut-off answer on The Flex's 43-field form (live 2026-09-23, finish_reason=length).
# With reasoning off the same page answered in 21 s with 2,473 tokens. `effort: low` still used all 8,192.
REASONING = {"enabled": False}


class LLMInferenceError(RuntimeError):
    """Blocker 'LLM inference output invalid' (or unreachable)."""


class ModelUnavailable(LLMInferenceError):
    """This model cannot answer right now (rate-limited, overloaded, timed out, or wrong output); the next one may."""


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


LOOSE_MIN = 12      # a formatting-free match needs this many letters and digits, so a stray "Yes" proves nothing


def _loose(s: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def quoted_in(quote: str | None, text: str) -> bool:
    """`quote` is in `text`, ignoring whitespace — or, for a quote of LOOSE_MIN letters and digits or more, ignoring
    case, spacing and punctuation too: the model writes a resume's "+393519358813" as "+39 351 935 8813"
    (Mastercard, live 2026-09-23), which is the same fact."""
    q = norm(quote)
    if not q:
        return False
    return q in norm(text) or (len(_loose(q)) >= LOOSE_MIN and _loose(q) in _loose(text))


@dataclass
class Sources:
    profile: str
    job: str
    resume: str

    def by_name(self, name: str) -> str:
        return {"profile": self.profile, "job": self.job, "resume": self.resume}.get(name, "")

    def any_contains(self, snippet: str) -> bool:
        return any(quoted_in(snippet, t) for t in (self.profile, self.job, self.resume))


def resume_text(pdf: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


# ------------------------------------------------------------------ the call

def page_payload(p: Page) -> dict:
    return {"url": p.url, "title": p.title,
            "elements": [e.model_dump(exclude_defaults=True) for e in p.elements],
            "text": p.text[:PAGE_TEXT_MAX], "maxlengths": p.maxlengths, "required_empty": p.required_empty}


def system_prompt(free_text_max_chars: int) -> str:
    return PROMPT.read_text().replace("{free_text_max_chars}", str(free_text_max_chars))


# Mistral Nemo on Vercel took 58.6 s for a realistic page (5.3K tokens in, 970 out) and timed out at 60 s on
# Linda AI's Easy Apply form (live 2026-09-24).
LLM_INFERENCE_TIMEOUT = 45.0


def call_engine(*, key: str, models: Rotation | str | list[str], system: str, user: dict,
                url: str = OPENROUTER_CHAT, post: Callable | None = None, timeout: float = LLM_INFERENCE_TIMEOUT,
                sleep: Callable[[float], None] = time.sleep) -> PageAnswers:
    """Ask the models in turn (rotation.py) until one gives a valid answer; LLMInferenceError when none does.
    `url` is the route's chat/completions (config.chat_url): OpenRouter and Vercel AI Gateway take the same request.
    `post(url, json, headers, timeout) -> (status, body)` is injectable for tests."""
    rotation = models if isinstance(models, Rotation) else Rotation(models)
    # `post` is for a caller that sends with its own client (the tests and the preflight probe). It does not bypass
    # the Gateway: it gets a Gateway of its own, so the retry rule and the log still apply (A2).
    installed = gateway_mod.current()
    if post is not None and installed is None:
        gateway_mod.use(gateway_mod.private(post, sleep=sleep))
    gateway = gateway_mod.required()           # the verdict below is reported on the Gateway actually in use
    # One attempt per model while another is untried; the last model in the order gets the Gateway's full ladder.
    last = rotation.order()[-1]
    try:
        pa = rotation.call(lambda m: _ask_model(m, key=key, system=system, user=user, url=url,
                                                post=post, timeout=timeout, sleep=sleep,
                                                attempts=None if m == last else 1),
                           ModelUnavailable)
    except NoModelAvailable as exc:
        # Every model in the rotation failed: that is one failed request for the breaker, not one per model. A model
        # handing over to the next is the rotation working, and counting each handover would trip an outage on a
        # provider that is answering — Vercel allows 5 requests a minute per model, so a busy first model plus a
        # second that answers is a steady alternation of failures and successes (D21, and the same rule as T4's
        # fallback).
        gateway.note_failure(True)
        raise LLMInferenceError(f"LLM inference: {exc}") from None
    else:
        gateway.note_failure(False)
    finally:
        if post is not None and installed is None:
            gateway_mod.use(None)
    pa.model = rotation.last
    return pa


def _ask_model(model: str, *, key: str, system: str, user: dict, url: str, post: Callable, timeout: float,
               sleep: Callable[[float], None], attempts: int | None = None) -> PageAnswers:
    """One model: POST chat/completions through the Gateway, json_schema strict with a json_object fallback on
    HTTP 400. Anything that fails is ModelUnavailable, so the rotation tries the next model; a rejected key or an
    account out of credit is not — the Gateway raises CreditOrKey and stops the run, because no other model on that
    key would do better. The Gateway holds the one retry layer, the queue and the log (P1 T2); `post`, `timeout` and
    `sleep` are honoured only when a test passes its own gateway-less sender. `attempts` is 1 while the rotation
    still has an untried model: handing over is cheaper than waiting to ask a rate-limited model again."""
    fmt: dict = {"type": "json_schema", "json_schema": {"name": "page_answers", "strict": True, "schema": SCHEMA}}
    body = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS, "reasoning": REASONING,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
    # OpenRouter only: route to a provider that supports every parameter sent, instead of one that drops
    # response_format or temperature silently. Vercel AI Gateway has no such field.
    if inference_log.gateway_of(url) == "openrouter":
        body["provider"] = {"require_parameters": True}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    gateway = gateway_mod.required()
    while True:
        sent = {**body, "response_format": fmt}
        # The verdict is `call_engine`'s: this model failing while the next answers is not a failed request.
        out = gateway.send(gateway_mod.CHAT, url, sent, headers, model=model, timeout=timeout,
                           attempts=attempts, defer_verdict=True)
        data = out.body
        if out.status == 400 and fmt["type"] == "json_schema":
            fmt = {"type": "json_object"}
            continue
        # OpenRouter passes an overloaded upstream through as HTTP 200 with an error body and an empty choice
        # (nemotron-3-super:free, live 2026-09-24), so an error body fails the model whatever the status.
        err = data.get("error") if isinstance(data, dict) else None
        if not out.ok or err:
            raise ModelUnavailable(f"HTTP {_code(out.status, err)} {_message(err)}".rstrip())
        try:
            content = data["choices"][0]["message"]["content"]
            return PageAnswers.model_validate_json(_strip_fences(content))
        except (KeyError, IndexError, TypeError, ValidationError, ValueError):
            raise ModelUnavailable("output invalid") from None


def _code(status: int, err) -> int:
    return err.get("code", status) if isinstance(err, dict) and isinstance(err.get("code"), int) else status


def _message(err) -> str:
    """Errors: {"message", "code" or "type", "metadata": {"raw"}} (metadata on OpenRouter only); the upstream's
    raw text says more."""
    if not isinstance(err, dict):
        return str(err or "")[:120]
    raw = (err.get("metadata") or {}).get("raw")
    return str(raw or err.get("message") or "")[:120]


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


# ------------------------------------------------------------------ computed years

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_POINT = rf"(?:{_MON}\s+(\d{{4}})|(\d{{1,2}})[/.-](\d{{4}})|(\d{{4}})[/.-](\d{{1,2}}))"
RANGE_RE = re.compile(rf"{_POINT}\s*(?:–|—|-|to|until)\s*(?:{_POINT}|(present|current|now|today))", re.I)


def _month_index(groups: tuple, today: date) -> int | None:
    mon, y1, mm1, y2, y3, mm3 = groups
    if mon:
        return int(y1) * 12 + MONTHS[mon[:3].lower()] - 1
    if mm1:
        return int(y2) * 12 + int(mm1) - 1
    if y3:
        return int(y3) * 12 + int(mm3) - 1
    return None


def total_months(lines: list[str], today: date) -> int:
    """Months covered by the month-year ranges in `lines`, overlaps merged, both ends inclusive."""
    spans = []
    now = today.year * 12 + today.month - 1
    for line in lines:
        for m in RANGE_RE.finditer(line):
            g = m.groups()
            start = _month_index(g[0:6], today)
            end = now if g[12] else _month_index(g[6:12], today)
            if start is not None and end is not None and end >= start:
                spans.append((start, end))
    total, cur = 0, None
    for s, e in sorted(spans):
        if cur and s <= cur[1] + 1:
            cur = (cur[0], max(cur[1], e))
        else:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = (s, e)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


# ------------------------------------------------------------------ checks

@dataclass
class Policy:
    prefill: str = "keep-if-silent"
    free_text_max_chars: int = 1500


def _drop(q: Question, why: str) -> None:
    q.answer, q.note = None, why


def _limit(q: Question, p: Page, policy: Policy) -> int:
    for label, n in p.maxlengths.get("items", []):
        if label and norm(q.question).lower().startswith(norm(label).lower()[:20]):
            return int(n)
    return policy.free_text_max_chars


# ------------------------------------------------------------------ Jev's verdicts on the questions

MUST_NOT_GENERATE = {
    "true": "It asks for a fact only the candidate can state: salary or pay, notice period, start date, availability, "
            "visa, sponsorship, work authorization, years of experience, gender, ethnicity, veteran or disability "
            "status, or another personal fact.",
    "false": "It asks for a text to write, such as a motivation, why this company, or a description of experience.",
}
YEARS_KINDS = {
    "total": "Total years of professional work experience, in any field or role.",
    "specific": "Years of experience with a particular skill, tool, programming language, technology, industry "
                "or role.",
    "other": "Something else.",
}


@dataclass
class Verdicts:
    """Jev's verdicts for check_answers, keyed by the normalised question text or shown value."""
    must_not_generate: set[str] = field(default_factory=set)
    total_years: set[str] = field(default_factory=set)
    placeholders: set[str] = field(default_factory=set)


def _shown(e) -> str:
    return "" if e is None else ((e.current or "") if e.options else (e.current or e.value or "")).strip()


def judge_answers(pa: "PageAnswers", p: Page) -> Verdicts:
    """One Jev call for the page's answers: which generated answers ask for a fact that must not be written, which
    computed answers ask for total years of experience, and which shown values are placeholder prompts."""
    by_ref = {e.ref: e for e in p.elements}
    qs: dict[str, dict] = {}
    for i, q in enumerate(pa.questions):
        if q.source == "generated":
            qs[f"gen_{i}"] = decide.noul({"form_question": q.question, "question": "Does `form_question` ask for "
                                          "a fact only the candidate can state, rather than a text to write?"},
                                         **MUST_NOT_GENERATE)
        elif q.source == "computed":
            qs[f"years_{i}"] = decide.choice({"form_question": q.question,
                                              "question": "What does `form_question` ask for?"}, YEARS_KINDS)
    values = sorted({v for q in pa.questions if (v := _shown(by_ref.get(q.ref or "")))})
    for j, v in enumerate(values):
        qs[f"shown_{j}"] = decide.noul({"value": v, "question": "Is `value` a placeholder prompt, such as 'Select "
                                        "an option', 'Choose…' or '--', rather than a real answer?"})
    a = decide.current().ask("answers", {"page": p.title, "url": p.url}, qs)
    v = Verdicts()
    for i, q in enumerate(pa.questions):
        if f"gen_{i}" in a and a[f"gen_{i}"].yes(T["must_not_generate"]):
            v.must_not_generate.add(norm(q.question))
        if f"years_{i}" in a and a[f"years_{i}"].choice == "total":
            v.total_years.add(norm(q.question))
    v.placeholders = {norm(val) for j, val in enumerate(values) if a[f"shown_{j}"].yes(T["placeholder"])}
    return v


def judge_questions(pa: "PageAnswers", p: Page) -> None:
    """One Jev call: flag the questions the resume upload answers, and the uploads that ask for a cover letter."""
    qs: dict[str, dict] = {}
    for i, q in enumerate(pa.questions):
        qs[f"resume_{i}"] = decide.noul(
            {"form_question": q.question, "options": q.options or [],
             "question": "Is `form_question` answered by uploading the candidate's resume (CV), or by choosing "
                         "among uploaded resume files?"})
        if q.kind == "file":
            qs[f"cover_{i}"] = decide.noul({"upload": q.question, "question": "Does `upload` ask for a cover letter?"})
    a = decide.current().ask("questions", {"page": p.title, "url": p.url}, qs)
    for i, q in enumerate(pa.questions):
        q.resume_upload = a[f"resume_{i}"].yes(T["resume_upload"])
        q.cover_letter = f"cover_{i}" in a and a[f"cover_{i}"].yes(T["cover_letter"])


def _held(e, v: Verdicts) -> str:
    """The value a field already holds: a native select's chosen option (not a blank or placeholder option),
    a custom combobox's shown value (enrich), or a text field's value. Placeholders are Jev's verdict."""
    if e is None:
        return ""
    if e.options and not (e.value or "").strip():             # native <select>: its value must be non-empty
        return ""
    held = _shown(e)
    return "" if norm(held) in v.placeholders else held


def check_answers(pa: PageAnswers, p: Page, src: Sources, policy: Policy, today: date,
                  verdicts: Verdicts | None = None) -> list[Question]:
    """Apply §7 checks in place (a failed check sets answer=None with a note).
    Returns the generated questions whose relies_on/length failed — the caller regenerates them once.

    Live lessons (qwen3.7-flash on LinkedIn, 2026-09-23): a model mislabels which file a quote came from, echoes
    option lists with typos, and may pick a wrong option ("Austria (+43)" over a prefilled "Italy (+39)"). So:
    a quote counts if it is in any of the three files (the source is corrected); a choice is judged by what it
    would set — the field's own options or current value, the targeted radio's own label — not by the echoed list;
    and a required field that already holds a value keeps it when no valid answer is left."""
    v = verdicts if verdicts is not None else judge_answers(pa, p)
    labels = norm(p.text).lower() + " " + " ".join(
        [norm(e.name).lower() for e in p.elements] + [norm(e.label).lower() for e in p.elements if e.label]
        + [norm(o.label).lower() for e in p.elements for o in e.options if o.label])
    by_ref = {e.ref: e for e in p.elements}
    regenerate = []
    for q in pa.questions:
        if q.kind == "file":
            q.answer = None
            continue
        if q.answer is None:
            continue
        if q.source in ("profile", "job", "resume"):
            if not q.quote:
                _drop(q, "no quote")
                continue
            if not quoted_in(q.quote, src.by_name(q.source)):
                found = next((n for n in ("profile", "job", "resume") if quoted_in(q.quote, src.by_name(n))), None)
                if found is None:
                    _drop(q, "quote not found in the sources")
                    continue
                q.note, q.source = f"quote is from {found}, not {q.source}", found
        el = by_ref.get(q.ref or "")
        opt = by_ref.get(q.option_ref or "")
        if opt is not None and opt.role in {"radio", "checkbox", "switch"}:
            # the targeted option must be the answer (its label, e.g. "Yes", or its own name)
            if norm(q.answer).lower() not in {norm(opt.name).lower(), norm(opt.label).lower()}:
                _drop(q, f"answer {q.answer!r} does not match the option it targets")
                continue
        elif el is not None and el.options:
            # a native select: its real options (the table lists the first 40) or its current value decide
            real = [o.label for o in el.options if o.label] + ([el.current] if el.current else [])
            match = [o for o in real if norm(o).lower() == norm(q.answer).lower()]
            if not match:
                _drop(q, "answer is not one of the field's options")
                continue
            q.answer = match[0]
        elif q.kind == "choice" and q.options:
            # a custom widget: the answer itself must be on the page
            if norm(q.answer).lower() not in labels:
                _drop(q, "answer not on the page")
                continue
            match = [o for o in q.options if norm(o).lower() == norm(q.answer).lower()]
            q.answer = match[0] if match else q.answer
        if q.source == "generated":
            if q.kind not in ("text", "longtext") or norm(q.question) in v.must_not_generate:
                _drop(q, "generated text not allowed for this question")
                continue
            if not q.relies_on or not all(src.any_contains(s) for s in q.relies_on) \
                    or len(q.answer) > _limit(q, p, policy):
                regenerate.append(q)
                continue
        elif q.source == "computed":
            if norm(q.question) not in v.total_years:
                # §7: computed only for *total* years of experience; years with a tool are never guessed
                # (live 2026-09-23: "years of work experience … with C++?" was offered total years)
                _drop(q, "computed is only for total years of experience")
                continue
            if not q.relies_on or not all(src.any_contains(s) for s in q.relies_on):
                _drop(q, "computed from lines not in the sources")
                continue
            years = total_months(q.relies_on, today) // 12
            if not re.fullmatch(r"\s*\d+\s*", q.answer) or int(q.answer) != years:
                _drop(q, f"computed years mismatch (recomputed {years})")
                continue
        elif q.source == "linkedin-prefill":
            held = _held(el, v)
            if policy.prefill == "strict" or not held or norm(held) != norm(q.answer):
                _drop(q, "pre-fill not kept")
                continue
        elif q.source is None:
            _drop(q, "no source")
    if policy.prefill != "strict":
        for q in pa.questions:
            # §7: "a pre-filled value the sources do not address → keep it" — also when the model's answer failed
            held = _held(by_ref.get(q.ref or ""), v) if q.kind != "file" and q.answer is None else ""
            if held:
                q.answer, q.source = held, "linkedin-prefill"
                q.note = (q.note + "; " if q.note else "") + "kept the value the page already holds"
    return regenerate


def answer_page(p: Page, src: Sources, *, key: str, models: Rotation | str | list[str], policy: Policy,
                today: date | None = None, url: str = OPENROUTER_CHAT, post: Callable | None = None) -> PageAnswers:
    """Call the engine, run the checks, regenerate bad generated texts once, check again. `models` rotate
    (rotation.py): pass one Rotation for a whole run so each page starts at the model that answered last."""
    today = today or date.today()
    system = system_prompt(policy.free_text_max_chars)
    user = {"page": page_payload(p), "sources": {"profile": src.profile, "job": src.job, "resume": src.resume}}
    models = models if isinstance(models, Rotation) else Rotation(models)
    pa = call_engine(key=key, models=models, system=system, user=user, url=url, post=post)
    verdicts = judge_answers(pa, p)
    bad = check_answers(pa, p, src, policy, today, verdicts)
    if bad:
        user["regenerate"] = {"questions": [q.question for q in bad],
                              "reason": "relies_on must be exact sentences from the sources and the text must fit "
                                        "its length limit; answer these again"}
        again = {norm(q.question): q for q in call_engine(key=key, models=models, system=system, user=user,
                                                          url=url, post=post).questions}
        for q in bad:
            new = again.get(norm(q.question))
            if new and new.source == "generated":
                q.answer, q.relies_on = new.answer, new.relies_on
            else:
                q.answer = None
        still = check_answers(PageAnswers(questions=bad), p, src, policy, today, verdicts)
        for q in still:
            _drop(q, "generated text failed its checks twice")
    return pa


def uncovered_required(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if q.required and q.answer is None and q.kind != "file"]


def uncovered_optional(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if not q.required and q.answer is None and q.kind != "file"]
