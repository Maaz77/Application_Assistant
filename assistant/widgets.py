"""Widget handlers for form elements that type/select/toggle cannot fill (P4 T1).

handle()    tries registered handlers in turn. True = filled. False = not filled, and fill.fill_page decides
            what that costs: an optional field is left empty and listed in the note, a REQUIRED one is a
            `broken_form` blocker. Never a silent wrong value.
_typeahead  types the answer, polls for this field's `role=option` suggestions, then picks the option equal to
            the answer, else the single one that starts with it, else Jev among the ones that start with it.
            The selection is read back by fill.mismatches on the next read, which is why nothing here
            re-verifies it: a wrong suggestion leaves a value that does not prefix-match the answer.

Unverified live (the fixtures cannot settle it): suggestions are matched by `e.dialog == el.dialog`, which
assumes LinkedIn renders the option list inside the modal's subtree. If it renders in a portal outside the
dialog, `opts` is always empty and every typeahead becomes a `broken_form` — check this on the first live run.
"""
from __future__ import annotations

import time

from assistant import decide, pages
from assistant.pages import norm_label

MAX_SUGGESTIONS = 50          # a typeahead list this long is a wrong field, not a choice


def handle(ctx, p, q) -> bool:
    """Try each widget handler. True = handled; False = the caller leaves it empty or blocks."""
    by_ref = {e.ref: e for e in p.elements}
    el = by_ref.get(q.ref or "")
    if el is not None and el.role in ("combobox", "listbox"):
        return _typeahead(ctx, p, q, el)
    return False


def _suggestions(ctx, el, deadline: float):
    """Poll until this field's dialog shows `role=option` items, or the deadline passes. (page, options)."""
    while True:
        fresh = pages.read_page(ctx.browser, ctx.session)
        opts = [e for e in fresh.elements if e.role == "option" and e.dialog == el.dialog]
        if opts or time.monotonic() >= deadline:
            return fresh, opts[:MAX_SUGGESTIONS]
        ctx.sleep(0.1)


def _pick(ctx, fresh, q, answer: str, close):
    """The suggestion that is the answer: equal to it, else the only one starting with it, else Jev among the
    ones that start with it. A suggestion that does not start with the answer is never a candidate — Jev is for
    "Milan, Lombardy, Italy" vs "Milan, MI, US", not for guessing between unrelated entries."""
    target = norm_label(answer)
    exact = [e for e in close if norm_label(e.name) == target]
    if exact:
        return exact[0]
    starts = [e for e in close if target and norm_label(e.name).startswith(target)]
    if len(starts) < 2:
        return starts[0] if starts else None
    a = decide.current().ask("fill", pages.page_state(fresh), {"option": decide.choice(
        {"question": q.question, "answer": answer, "ask": "Which suggestion is `answer` for `question`?"},
        {**{e.ref: e.name[:90] for e in starts}, "none": "None of these suggestions."})})
    chosen = a["option"].choice
    return next((e for e in starts if e.ref == chosen), None) if chosen != "none" else None


def _typeahead(ctx, p, q, el) -> bool:
    answer = q.answer or ""
    typed = ctx.browser.act([{"op": "type", "ref": el.ref, "text": answer, "clear": True, "submit": False}],
                            ctx.session, p.table, stop_on_error=False)
    if "1/1 ops ok" not in typed:              # the field moved under us; a stale ref must not be clicked on
        return False
    fresh, opts = _suggestions(ctx, el, time.monotonic() + getattr(ctx, "widget_poll_secs", 3.0))
    if not opts:
        return False                           # T1: no suggestions => leave it empty and note it
    pick = _pick(ctx, fresh, q, answer, opts)
    if pick is None:
        return False
    out = ctx.browser.act([{"op": "click", "ref": pick.ref}], ctx.session, fresh.table, stop_on_error=False)
    return "1/1 ops ok" in out
