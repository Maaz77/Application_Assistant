"""Widget handlers for form elements that type/select/toggle cannot fill (P4 T1).

handle()    tries registered handlers in turn; True = handled, False = not handled.
_typeahead  types the answer, polls for role=option suggestions, clicks best match.
"""
from __future__ import annotations

import time
from assistant.pages import norm_label


def handle(ctx, p, q) -> bool:
    """Try each widget handler. True = handled; False = broken_form for caller."""
    by_ref = {e.ref: e for e in p.elements}
    el = by_ref.get(q.ref or "")
    if el is not None and el.role == "combobox":
        return _typeahead(ctx, p, q, el)
    return False


def _typeahead(ctx, p, q, el) -> bool:
    """Type answer into combobox, poll for role=option suggestions, click best match."""
    from assistant import pages as _pages
    poll_secs = getattr(ctx, "widget_poll_secs", 3.0)
    ctx.browser.act(
        [{"op": "type", "ref": el.ref, "text": q.answer or "", "clear": True, "submit": False}],
        ctx.session, p.table, stop_on_error=False)
    target = norm_label(q.answer or "")
    deadline = time.monotonic() + poll_secs
    while True:
        fresh = _pages.read_page(ctx.browser, ctx.session)
        opts = [e for e in fresh.elements if e.role == "option"]
        if opts:
            match = next((e for e in opts if norm_label(e.name) == target), opts[0])
            ctx.browser.act(
                [{"op": "click", "ref": match.ref}],
                ctx.session, fresh.table, stop_on_error=False)
            return True
        if time.monotonic() >= deadline:
            return False
        ctx.sleep(0.1)
