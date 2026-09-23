"""Answer engine (§7): one OpenRouter call per form page, then deterministic checks in code."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from assistant.pages import Page

OPENROUTER_CHAT = "https://openrouter.ai/api/v1/chat/completions"
PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "answer_engine.md"
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


class PageAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Question]


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

FORBIDDEN_GENERATED = re.compile(
    r"salary|compensation|\bpay\b|notice period|start date|availability|visa|sponsor|right to work|authori[sz]ed|"
    r"years of experience|gender|ethnic|race|veteran|disabilit", re.I)
LONG_TEXT = 300
TOTAL_YEARS_RE = re.compile(r"\byears?\b.{0,40}\bexperience\b|\bexperience\b.{0,20}\byears?\b", re.I)
TOOL_YEARS_RE = re.compile(r"\bexperience\b.{0,30}\b(with|in|using|on|of)\s+(?!(the\s+)?(industry|field|workforce)\b)\S", re.I)
RATE_LIMIT_WAIT = 15.0
MAX_TOKENS = 8192          # one page of answers; OpenRouter otherwise reserves the model maximum (HTTP 402)


class AnswerEngineError(RuntimeError):
    """Blocker 'answer engine output invalid' (or unreachable)."""


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class Sources:
    profile: str
    job: str
    resume: str

    def by_name(self, name: str) -> str:
        return {"profile": self.profile, "job": self.job, "resume": self.resume}.get(name, "")

    def any_contains(self, snippet: str) -> bool:
        s = norm(snippet)
        return bool(s) and any(s in norm(t) for t in (self.profile, self.job, self.resume))


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


def call_engine(*, key: str, model: str, system: str, user: dict, post: Callable | None = None,
                timeout: float = 60.0, sleep: Callable[[float], None] | None = None) -> PageAnswers:
    """POST chat/completions; json_schema strict, json_object fallback on HTTP 400; one retry on timeout/5xx
    and one on invalid output. `post(url, json, headers, timeout) -> (status, body)` is injectable for tests."""
    post = post or _httpx_post
    fmt: dict = {"type": "json_schema", "json_schema": {"name": "page_answers", "strict": True, "schema": SCHEMA}}
    body = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    transient = invalid = 0
    fell_back = False
    while True:
        try:
            status, data = post(OPENROUTER_CHAT, {**body, "response_format": fmt}, headers, timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            status, data = 599, {"error": str(exc)}
        if status == 400 and fmt["type"] == "json_schema" and not fell_back:
            fmt, fell_back = {"type": "json_object"}, True
            continue
        if status >= 500 or status == 429:        # 429: free models are rate-limited upstream; wait, then retry
            transient += 1
            if transient > 1:
                raise AnswerEngineError(f"answer engine unreachable (HTTP {status})")
            if status == 429:
                (sleep or time.sleep)(RATE_LIMIT_WAIT)
            continue
        if status != 200:
            raise AnswerEngineError(f"answer engine HTTP {status}: {str(data)[:200]}")
        try:
            content = data["choices"][0]["message"]["content"]
            return PageAnswers.model_validate_json(_strip_fences(content))
        except (KeyError, IndexError, TypeError, ValidationError, ValueError):
            invalid += 1
            if invalid > 1:
                raise AnswerEngineError("answer engine output invalid")


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


def _httpx_post(url, payload, headers, timeout):
    r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:500]}


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


PLACEHOLDER_RE = re.compile(r"^\s*(select|choose|pick|please select|--|—)\b.*$|^\s*(select|choose)\s*\.{0,3}\s*$", re.I)


def _held(e) -> str:
    """The value a field already holds: a native select's chosen option (not a blank or placeholder option),
    a custom combobox's shown value (enrich), or a text field's value."""
    if e is None:
        return ""
    if e.options:                                             # native <select>: its value must be non-empty
        return (e.current or "") if (e.value or "").strip() and not PLACEHOLDER_RE.match(e.current or "") else ""
    held = (e.current or e.value or "").strip()
    return "" if PLACEHOLDER_RE.match(held) else held


def check_answers(pa: PageAnswers, p: Page, src: Sources, policy: Policy, today: date) -> list[Question]:
    """Apply §7 checks in place (a failed check sets answer=None with a note).
    Returns the generated questions whose relies_on/length failed — the caller regenerates them once.

    Live lessons (qwen3.7-flash on LinkedIn, 2026-09-23): a model mislabels which file a quote came from, echoes
    option lists with typos, and may pick a wrong option ("Austria (+43)" over a prefilled "Italy (+39)"). So:
    a quote counts if it is in any of the three files (the source is corrected); a choice is judged by what it
    would set — the field's own options or current value, the targeted radio's own label — not by the echoed list;
    and a required field that already holds a value keeps it when no valid answer is left."""
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
            if norm(q.quote) not in norm(src.by_name(q.source)):
                found = next((n for n in ("profile", "job", "resume") if norm(q.quote) in norm(src.by_name(n))), None)
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
            if q.kind not in ("text", "longtext") or FORBIDDEN_GENERATED.search(q.question):
                _drop(q, "generated text not allowed for this question")
                continue
            if not q.relies_on or not all(src.any_contains(s) for s in q.relies_on) \
                    or len(q.answer) > _limit(q, p, policy):
                regenerate.append(q)
                continue
        elif q.source == "computed":
            if TOOL_YEARS_RE.search(q.question) or not TOTAL_YEARS_RE.search(q.question):
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
            held = _held(el)
            if policy.prefill == "strict" or not held or norm(held) != norm(q.answer):
                _drop(q, "pre-fill not kept")
                continue
        elif q.source is None:
            _drop(q, "no source")
    if policy.prefill != "strict":
        for q in pa.questions:
            # §7: "a pre-filled value the sources do not address → keep it" — also when the model's answer failed
            held = _held(by_ref.get(q.ref or "")) if q.kind != "file" and q.answer is None else ""
            if held:
                q.answer, q.source = held, "linkedin-prefill"
                q.note = (q.note + "; " if q.note else "") + "kept the value the page already holds"
    return regenerate


def answer_page(p: Page, src: Sources, *, key: str, model: str, policy: Policy, today: date | None = None,
                post: Callable | None = None) -> PageAnswers:
    """Call the engine, run the checks, regenerate bad generated texts once, check again."""
    today = today or date.today()
    system = system_prompt(policy.free_text_max_chars)
    user = {"page": page_payload(p), "sources": {"profile": src.profile, "job": src.job, "resume": src.resume}}
    pa = call_engine(key=key, model=model, system=system, user=user, post=post)
    bad = check_answers(pa, p, src, policy, today)
    if bad:
        user["regenerate"] = {"questions": [q.question for q in bad],
                              "reason": "relies_on must be exact sentences from the sources and the text must fit "
                                        "its length limit; answer these again"}
        again = {norm(q.question): q for q in call_engine(key=key, model=model, system=system, user=user,
                                                          post=post).questions}
        for q in bad:
            new = again.get(norm(q.question))
            if new and new.source == "generated":
                q.answer, q.relies_on = new.answer, new.relies_on
            else:
                q.answer = None
        still = check_answers(PageAnswers(questions=bad), p, src, policy, today)
        for q in still:
            _drop(q, "generated text failed its checks twice")
    return pa


def uncovered_required(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if q.required and q.answer is None and q.kind != "file"]


def uncovered_optional(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if not q.required and q.answer is None and q.kind != "file"]
