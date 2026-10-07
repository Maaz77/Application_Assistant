"""LLM inference (§7): one chat call per form page (the FreeLLMAPI router on this machine), then deterministic
checks in code."""
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

from assistant import decide, gateway as gateway_mod, inference_log, pages
from assistant.decide import THRESHOLDS as T
from assistant.pages import Page
from assistant.rotation import NoModelAvailable, Rotation

# The FreeLLMAPI router's chat/completions, matching config.FreeLLMAPI.base_url. A run always passes the
# configured URL (config.chat_url); this default is only what a caller that names none gets.
FREELLM_CHAT = "http://127.0.0.1:31415/v1/chat/completions"
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
    resume_upload: bool = False  # code rule (judge_questions): the resume upload answers this question
    cover_letter: bool = False   # code rule (judge_questions): an upload that asks for a cover letter


class PageAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Question]
    model: str | None = None     # set by call_engine: the model that answered; not part of the model schema


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    answer: str | None
    source: Source | None
    quote: str | None
    relies_on: list[str] | None


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[ModelAnswer]
    model: str | None = None


@dataclass
class Extraction:
    questions: list[Question]
    option_maps: dict[str, dict[str, str]]   # qid → {option_label: element_ref}
    current_values: dict[str, str]           # qid → current held value


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

ANSWER_ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "answer", "source", "quote", "relies_on"],
    "properties": {
        "id": {"type": "string"},
        "answer": _nullable({"type": "string"}),
        "source": {"type": ["string", "null"],
                   "enum": ["profile", "job", "resume", "generated", "computed", "linkedin-prefill", None]},
        "quote": _nullable({"type": "string"}),
        "relies_on": _nullable({"type": "array", "items": {"type": "string"}}),
    },
}
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"],
                 "properties": {"answers": {"type": "array", "items": ANSWER_ITEM_SCHEMA}}}

LONG_TEXT = 300
# A free tier behind the FreeLLMAPI router runs out often: it answers HTTP 429 with a `rate_limit_error` body
# ("All models exhausted … Soonest reset ~16s") and a `retryAtMs`. The Gateway waits a short Retry-After; the
# rotation hands a longer one over to the next model (live 2026-10-07).
MAX_TOKENS = 8192          # one page of answers
# Extraction with quotes needs no hidden reasoning, and a reasoning model spends MAX_TOKENS on it: qwen3.7-flash
# returned an empty and then a cut-off answer on The Flex's 43-field form (live 2026-09-23, finish_reason=length).
# With reasoning off the same page answered in 21 s with 2,473 tokens. `effort: low` still used all 8,192.
# OpenRouter's `reasoning: {"enabled": false}` is not the FreeLLMAPI router's spelling — it was passed through and
# ignored there (645 of 696 completion tokens were reasoning tokens, live 2026-10-07). `reasoning_effort` is what
# its catalogue lists, and "none" does switch reasoning off (kimi-k3: 0 reasoning tokens, same day).
REASONING_EFFORT = "none"


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


# ------------------------------------------------------------------ T4: code extracts questions

_UUID_RE = re.compile(r"^[0-9a-f-]{20,}$", re.I)
# A UUID anywhere in the key (any join char): Ashby's radio group id is two UUIDs joined by '_', which slips
# past _UUID_RE's all-hex-and-hyphen anchor, so the cosmetic '-'/'_'→space transform turned it into a question
# label of space-separated hex (live 2026-10-07). Such a key carries no question text.
_OPAQUE_ID_RE = re.compile(r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}", re.I)


def _is_required(name: str, attr_required: bool, p: Page) -> bool:
    if attr_required:
        return True
    if (name or "").rstrip().endswith("*"):
        return True
    for label, _kind, *_ in p.required_empty.get("items", []):
        if label and pages.norm_label(name)[:20] == pages.norm_label(label)[:20]:
            return True
    return False


def _group_radios(radios: list) -> dict[str, list]:
    """Group radio elements: by group field, then by shared name, then by scope."""
    grouped: dict[str, list] = {}
    ungrouped: list = []
    for r in radios:
        if r.group:
            grouped.setdefault(r.group, []).append(r)
        else:
            ungrouped.append(r)
    if not ungrouped:
        return grouped
    by_name: dict[str, list] = {}
    for r in ungrouped:
        by_name.setdefault(r.name, []).append(r)
    for name, members in by_name.items():
        if len(members) > 1:
            grouped[f"_name_{name}"] = members
        else:
            r = members[0]
            grouped.setdefault(f"_scope_{r.scope or '_page'}", []).append(r)
    return grouped


def checked_option(question: str, p: Page) -> str | None:
    """The checked option's label in the radio group whose question is `question`, or None.

    Re-derives the grouping exactly as extract_questions did, so the read-back after a re-render looks only
    inside the one group: a LinkedIn page carries several Yes/No groups whose option labels are identical, and a
    page-wide scan would let one checked "Yes" vouch for all of them. The question text is a sound join key
    because _merge_answers merges the model's reply by `id` and never rewrites `question`.

    None means no answer can be claimed for this question — the group has nothing checked, no group matched, or
    **two groups carry the same text** (_group_label falls back to `context` or "Select one", so that is not
    hypothetical). The caller treats every one of those as not held, which is the safe direction.
    """
    want = pages.norm_label(question)
    app_refs = pages.code_app_fields(p)
    radios = [e for e in p.elements if e.role == "radio" and e.ref in app_refs]
    hits = []
    for key, members in _group_radios(radios).items():
        q_text, _opts, _omap, cur = _radio_q(key, members, p)
        if pages.norm_label(q_text) == want:
            hits.append(cur)
    return hits[0] or None if len(hits) == 1 else None


def _group_label(group_key: str, members: list) -> str:
    """Human-readable question text from a radio group key."""
    if not group_key or group_key.startswith("_"):
        return members[0].context or "Select one"
    if _UUID_RE.match(group_key) or _OPAQUE_ID_RE.search(group_key) or len(group_key) > 80:
        return members[0].context or "Select one"
    all_native = all(m.tag == "INPUT" for m in members)
    if not all_native:
        return group_key
    return re.sub(r"[_-]+", " ", group_key).strip().capitalize()


def _radio_q(group_key: str, members: list, p: Page) -> tuple[str, list[str], dict[str, str], str]:
    """(question_text, option_labels, {label: ref}, current_checked_label) for one radio group."""
    names = [m.name for m in members]
    labels = [m.label for m in members if m.label]
    same_name = len(set(names)) == 1

    if same_name:
        q_text = members[0].name.rstrip(" *")
        opts = labels if len(labels) == len(members) else names
    else:
        q_text = _group_label(group_key, members)
        opts = names

    seen, unique = set(), []
    for o in opts:
        if o not in seen:
            unique.append(o)
            seen.add(o)

    omap: dict[str, str] = {}
    for m in members:
        label = (m.label or m.name) if same_name else m.name
        if label not in omap:
            omap[label] = m.ref

    checked = next((m for m in members if m.checked), None)
    cur = ""
    if checked:
        cur = (checked.label or checked.name) if same_name else checked.name

    return q_text, unique, omap, cur


def _single_question(e, p: Page) -> tuple[Question | None, str]:
    """One question from a non-radio app field."""
    name = (e.name or e.label or "").rstrip(" *")
    req = _is_required(e.name or e.label or "", e.required, p)

    if e.role in ("textbox", "searchbox", "spinbutton"):
        kind: Kind = "longtext" if e.tag == "TEXTAREA" or (e.maxlength and e.maxlength > 200) else "text"
        return (Question(id=f"t_{e.ref}", question=name, kind=kind, ref=e.ref, option_ref=None,
                         options=None, required=req, answer=None, source=None, quote=None, relies_on=None),
                (e.value or "").strip())
    if e.role in ("combobox", "listbox"):
        opts = [o.label for o in e.options if o.label] or None
        cur = (e.current or e.value or "").strip()
        return (Question(id=f"s_{e.ref}", question=name, kind="choice", ref=e.ref, option_ref=None,
                         options=opts, required=req, answer=None, source=None, quote=None, relies_on=None),
                "" if not cur or _is_placeholder(cur) else cur)
    if e.role == "file":
        return (Question(id=f"f_{e.ref}", question=name, kind="file", ref=e.ref, option_ref=None,
                         options=None, required=req, answer=None, source=None, quote=None, relies_on=None), "")
    if e.role in ("checkbox", "switch"):
        label = name or "Yes"
        return (Question(id=f"c_{e.ref}", question=label, kind="choice", ref=e.ref, option_ref=None,
                         options=[label, "No"], required=req, answer=None, source=None, quote=None, relies_on=None),
                label if e.checked else "")
    return None, ""


def extract_questions(p: Page) -> Extraction:
    """Code extracts questions from page elements (P3 T4). Model only answers them."""
    app_refs = pages.code_app_fields(p)
    questions: list[Question] = []
    option_maps: dict[str, dict[str, str]] = {}
    current_values: dict[str, str] = {}
    consumed: set[str] = set()

    radios = [e for e in p.elements if e.role == "radio" and e.ref in app_refs]
    for _key, members in _group_radios(radios).items():
        qid = f"r_{members[0].ref}"
        q_text, opts, omap, cur = _radio_q(_key, members, p)
        req = _is_required(q_text, any(m.required for m in members), p)
        questions.append(Question(
            id=qid, question=q_text, kind="choice", ref=None, option_ref=None,
            options=opts, required=req, answer=None, source=None, quote=None, relies_on=None))
        option_maps[qid] = omap
        if cur:
            current_values[qid] = cur
        consumed.update(m.ref for m in members)

    for e in p.elements:
        if e.ref not in app_refs or e.ref in consumed:
            continue
        q, cur = _single_question(e, p)
        if q:
            questions.append(q)
            if cur:
                current_values[q.id] = cur

    return Extraction(questions, option_maps, current_values)


def _questions_for_model(ext: Extraction, p: Page) -> list[dict]:
    by_ref = {e.ref: e for e in p.elements}
    out: list[dict] = []
    for q in ext.questions:
        d: dict = {"id": q.id, "question": q.question, "kind": q.kind, "required": q.required}
        if q.options:
            d["options"] = q.options
        cur = ext.current_values.get(q.id, "")
        if cur:
            d["current_value"] = cur
        el = by_ref.get(q.ref or "")
        if el and el.maxlength:
            d["maxlength"] = el.maxlength
        out.append(d)
    return out


def _merge_answers(questions: list[Question], response: ModelResponse) -> PageAnswers:
    by_id = {a.id: a for a in response.answers}
    for q in questions:
        a = by_id.get(q.id)
        if a:
            q.answer, q.source, q.quote, q.relies_on = a.answer, a.source, a.quote, a.relies_on
    return PageAnswers(questions=questions, model=response.model)


def _set_option_refs(pa: PageAnswers, option_maps: dict[str, dict[str, str]], p: Page) -> None:
    """Set option_ref after check_answers: radios from option_maps, toggles from ref, selects from options."""
    by_ref = {e.ref: e for e in p.elements}
    for q in pa.questions:
        if q.answer is None:
            continue
        omap = option_maps.get(q.id)
        if omap:
            q.option_ref = next((r for label, r in omap.items()
                                 if norm(label).lower() == norm(q.answer).lower()), None)
            continue
        el = by_ref.get(q.ref or "")
        if el is None:
            continue
        if el.role in ("checkbox", "switch"):
            if norm(q.answer).lower() == "no":
                q.answer = None
            else:
                q.option_ref = q.ref
        elif el.options:
            for opt in el.options:
                if opt.label and norm(opt.label).lower() == norm(q.answer).lower() and opt.ref:
                    q.option_ref = opt.ref
                    break


# ------------------------------------------------------------------ the call

def page_payload(p: Page) -> dict:
    return {"url": p.url, "title": p.title,
            "elements": [e.model_dump(exclude_defaults=True) for e in p.elements],
            "text": p.text[:PAGE_TEXT_MAX], "maxlengths": p.maxlengths, "required_empty": p.required_empty}


def system_prompt(free_text_max_chars: int) -> str:
    return PROMPT.read_text().replace("{free_text_max_chars}", str(free_text_max_chars))


# A realistic 6-question page through the FreeLLMAPI router, live 2026-10-07: kimi-k3 9.1 s, deepseek-v4-flash
# 11.1 s, deepseek-v4-pro-0813 21.0 s, glm-5.2 32.2 s, qwen3.8-2.4t-a95b 34.0 s. The slower ones leave little room,
# which is why the configured rotation leads with the two fast models.
LLM_INFERENCE_TIMEOUT = 45.0


def call_engine(*, key: str, models: Rotation | str | list[str], system: str, user: dict,
                url: str = FREELLM_CHAT, post: Callable | None = None, timeout: float = LLM_INFERENCE_TIMEOUT,
                sleep: Callable[[float], None] = time.sleep,
                schema: dict | None = None, response_cls: type | None = None) -> PageAnswers | ModelResponse:
    """Ask the models in turn (rotation.py) until one gives a valid answer; LLMInferenceError when none does.
    `url` is the FreeLLMAPI router's chat/completions (config.chat_url), an OpenAI-compatible endpoint.
    `post(url, json, headers, timeout) -> (status, body)` is injectable for tests."""
    schema = schema or SCHEMA
    response_cls = response_cls or PageAnswers
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
                                                attempts=None if m == last else 1,
                                                schema=schema, response_cls=response_cls),
                           ModelUnavailable)
    except NoModelAvailable as exc:
        # Every model in the rotation failed: that is one failed request for the breaker, not one per model. A model
        # handing over to the next is the rotation working, and counting each handover would trip an outage on a
        # provider that is answering — a free tier behind the FreeLLMAPI router is rate-limited in bursts, so a
        # busy first model plus a second that answers is a steady alternation of failures and successes (D21, and
        # the same rule as T4's fallback).
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
               sleep: Callable[[float], None], attempts: int | None = None,
               schema: dict | None = None, response_cls: type | None = None) -> PageAnswers | ModelResponse:
    """One model: POST chat/completions through the Gateway, json_schema strict with a json_object fallback on
    HTTP 400. Anything that fails is ModelUnavailable, so the rotation tries the next model; a rejected key or an
    account out of credit is not — the Gateway raises CreditOrKey and stops the run, because no other model on that
    key would do better. The Gateway holds the one retry layer, the queue and the log (P1 T2); `post`, `timeout` and
    `sleep` are honoured only when a test passes its own gateway-less sender. `attempts` is 1 while the rotation
    still has an untried model: handing over is cheaper than waiting to ask a rate-limited model again."""
    schema = schema or SCHEMA
    response_cls = response_cls or PageAnswers
    fmt: dict = {"type": "json_schema", "json_schema": {"name": "page_answers", "strict": True, "schema": schema}}
    body = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS, "reasoning_effort": REASONING_EFFORT,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
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
        # A router can pass an overloaded upstream through as HTTP 200 with an error body and an empty choice
        # (OpenRouter's nemotron-3-super:free did, 2026-09-24), so an error body fails the model whatever the
        # status. The same guard covers a routed model that returns something unusable: the validation below is
        # what finally decides, so a 200 is never trusted on its status alone.
        err = data.get("error") if isinstance(data, dict) else None
        if not out.ok or err:
            raise ModelUnavailable(f"HTTP {_code(out.status, err)} {_message(err)}".rstrip())
        try:
            content = data["choices"][0]["message"]["content"]
            return response_cls.model_validate_json(_strip_fences(content))
        except (KeyError, IndexError, TypeError, ValidationError, ValueError):
            # Tests and the preflight probe may hand back either the old {"questions": [...]} shape or the
            # T4 {"answers": [...]} shape; accept whichever the model returned by trying the other schema.
            try:
                other = PageAnswers if response_cls is ModelResponse else ModelResponse
                return other.model_validate_json(_strip_fences(content))
            except Exception:
                raise ModelUnavailable("output invalid") from None


def _code(status: int, err) -> int:
    return err.get("code", status) if isinstance(err, dict) and isinstance(err.get("code"), int) else status


def _message(err) -> str:
    """Errors: {"message", "code" or "type", "metadata": {"raw"}}; the upstream's raw text says more. The
    FreeLLMAPI router's rate-limit body is {"error": {"message", "type": "rate_limit_error", "retryAtMs"}}."""
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


def _note(note: str | None, add: str) -> str:
    return (note + "; " if note else "") + add


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
    """Verdicts for check_answers: Jev for gen/years, code regex for placeholders."""
    must_not_generate: set[str] = field(default_factory=set)
    total_years: set[str] = field(default_factory=set)
    placeholders: set[str] = field(default_factory=set)


def _shown(e) -> str:
    return "" if e is None else ((e.current or "") if e.options else (e.current or e.value or "")).strip()


_PLACEHOLDER_RE = re.compile(
    r"^\s*(select( an? option)?|choose(\.{3}|…)?|please select|--|—|select\.{3})\s*$", re.I)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value))


def judge_answers(pa: "PageAnswers", p: Page, current_values: dict[str, str] | None = None) -> Verdicts:
    """Jev call for generated/computed questions only; placeholder detection is a code regex."""
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
    v = Verdicts()
    if qs:
        a = decide.current().ask("answers", {"page": p.title, "url": p.url}, qs)
        for i, q in enumerate(pa.questions):
            if f"gen_{i}" in a and a[f"gen_{i}"].yes(T["must_not_generate"]):
                v.must_not_generate.add(norm(q.question))
            if f"years_{i}" in a and a[f"years_{i}"].choice == "total":
                v.total_years.add(norm(q.question))
    values = sorted({val for q in pa.questions if (val := _shown(by_ref.get(q.ref or "")))})
    v.placeholders = {norm(val) for val in values if _is_placeholder(val)}
    return v


_COVER_LETTER_RE = re.compile(r"cover\s*letter", re.I)


def judge_questions(pa: "PageAnswers", p: Page) -> None:
    """Code rule: flag resume-upload questions and cover-letter uploads. No model call."""
    for q in pa.questions:
        text = q.question or ""
        q.resume_upload = bool(pages._RESUME_RE.search(text)) or any(
            pages.RESUME_FILE_RE.search(o) for o in (q.options or []))
        q.cover_letter = q.kind == "file" and bool(_COVER_LETTER_RE.search(text))


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
                  verdicts: Verdicts | None = None, current_values: dict[str, str] | None = None) -> list[Question]:
    """Apply §7 checks in place (a failed check sets answer=None with a note).
    Returns the generated questions whose relies_on/length failed — the caller regenerates them once.

    Live lessons (qwen3.7-flash on LinkedIn, 2026-09-23): a model mislabels which file a quote came from, echoes
    option lists with typos, and may pick a wrong option ("Austria (+43)" over a prefilled "Italy (+39)"). So:
    a quote counts if it is in any of the three files (the source is corrected); a choice is judged by what it
    would set — the field's own options or current value, the targeted radio's own label — not by the echoed list;
    and a required field that already holds a value keeps it when no valid answer is left."""
    v = verdicts if verdicts is not None else judge_answers(pa, p)
    current_values = current_values or {}
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
            # A verbatim quote is the evidence for free text, and the only evidence it has. A `choice` answer has
            # stronger evidence available: the branches below require it to be one of the page's OWN options, so
            # it is not dropped for a citation that does not match character for character — the citation is kept
            # in the record, with a note, so an unverified one is still visible in answers.json and the report.
            #
            # Live, Linda AI 2026-10-06: "Have you completed the following level of education: Bachelor's Degree?"
            # was answered "Yes" twice and dropped twice. Attempt 1 cited the resume and spliced "Jun" into its
            # date range; attempt 2 cited Profile.md and elided "(Final Grade: 100/110)" from the middle of the
            # line. `quoted_in` already ignores case, spacing and punctuation, so neither failure was about
            # formatting — an insertion or an omission inside a quote is not a contiguous substring at all. The
            # degree was in both files; only the citation was not verbatim, and the required radio stayed empty.
            found = next((n for n in ("profile", "job", "resume")
                          if q.quote and quoted_in(q.quote, src.by_name(n))), None)
            if found is None:
                if q.kind != "choice":
                    _drop(q, "quote not found in the sources" if q.quote else "no quote")
                    continue
                q.note = _note(q.note, f"quote not verbatim in {q.source}" if q.quote else "no quote")
            elif found != q.source:
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
        elif q.kind == "choice" and not q.options and not el and not opt:
            # a radio group whose ref is null: the answer must be one of the group's options (live 2026-09-23)
            cur = (current_values or {}).get(q.id, "")
            if cur and norm(q.answer).lower() == norm(cur).lower():
                q.source, q.quote = "linkedin-prefill", None
                q.note = "kept the value the page already holds"
            else:
                _drop(q, "answer is not one of the page's options")
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
                today: date | None = None, url: str = FREELLM_CHAT, post: Callable | None = None) -> PageAnswers:
    """Code extracts questions, the model answers them, checks run in code (P3 T4). `models` rotate
    (rotation.py): pass one Rotation for a whole run so each page starts at the model that answered last."""
    today = today or date.today()
    ext = extract_questions(p)
    qs = _questions_for_model(ext, p)
    if not qs:
        return PageAnswers(questions=[])
    system = system_prompt(policy.free_text_max_chars)
    user = {"page": {"url": p.url, "title": p.title}, "questions": qs,
            "sources": {"profile": src.profile, "job": src.job, "resume": src.resume}}
    models = models if isinstance(models, Rotation) else Rotation(models)
    pa = _merge_answers(ext.questions, call_engine(key=key, models=models, system=system, user=user, url=url,
                                                    post=post, schema=ANSWER_SCHEMA, response_cls=ModelResponse))
    verdicts = judge_answers(pa, p, ext.current_values)
    bad = check_answers(pa, p, src, policy, today, verdicts)
    if bad:
        user["regenerate"] = {"questions": [q.question for q in bad],
                              "reason": "relies_on must be exact sentences from the sources and the text must fit "
                                        "its length limit; answer these again"}
        again = {a.id: a for a in call_engine(key=key, models=models, system=system, user=user, url=url,
                                              post=post, schema=ANSWER_SCHEMA, response_cls=ModelResponse).answers}
        for q in bad:
            new = again.get(q.id)
            if new and new.source == "generated":
                q.answer, q.relies_on = new.answer, new.relies_on
            else:
                q.answer = None
        still = check_answers(PageAnswers(questions=bad), p, src, policy, today, verdicts)
        for q in still:
            _drop(q, "generated text failed its checks twice")
    fill_contact_from_profile(pa, src)
    _set_option_refs(pa, ext.option_maps, p)
    return pa


_EMAIL_Q = re.compile(r"e-?mail", re.I)
_PHONE_Q = re.compile(r"\b(phone|mobile|cell|telephone)\b", re.I)
_EMAIL_V = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_PHONE_V = re.compile(r"\+?\d[\d().\-\s]{6,}\d")
_PHONE_HINT = re.compile(r"phone|mobile|cell|tel|contact|whatsapp", re.I)


def fill_contact_from_profile(pa: PageAnswers, src: Sources) -> None:
    """Deterministic fallback for the standard contact fields the model left blank (user decision 2026-10-08):
    a required phone or email field with no answer is filled from Profile.md by regex, because the profile
    plainly contains it — the free router occasionally misses a field it should answer (the DMI phone, live
    2026-10-07, though email/phone were answered for other jobs). Runs after all checks, so it only touches a
    field that is still unanswered; `source`/`quote` point at the profile. Name is left to the model: which line
    is the candidate's own name is too ambiguous to extract safely."""
    prof = src.profile or ""
    if not prof:
        return
    email = _EMAIL_V.search(prof)
    phone = None                                        # prefer a phone on a line that names one, else the first
    for line in prof.splitlines():
        if _PHONE_HINT.search(line) and (m := _PHONE_V.search(line)):
            phone = (m.group(0).strip(), line.strip()); break
    if phone is None and (m := _PHONE_V.search(prof)):
        phone = (m.group(0).strip(), next((l.strip() for l in prof.splitlines() if m.group(0) in l), m.group(0)))
    for q in pa.questions:
        if q.answer is not None or not q.required or q.kind == "file":
            continue
        ql = q.question or ""
        if _EMAIL_Q.search(ql) and email:
            q.answer, q.source, q.quote = email.group(0), "profile", email.group(0)
        elif _PHONE_Q.search(ql) and "country code" not in ql.lower() and phone:
            q.answer, q.source, q.quote = phone[0], "profile", phone[1][:200]


def uncovered_required(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if q.required and q.answer is None and q.kind != "file"]


def uncovered_optional(pa: PageAnswers) -> list[Question]:
    return [q for q in pa.questions if not q.required and q.answer is None and q.kind != "file"]
