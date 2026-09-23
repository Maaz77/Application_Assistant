"""Safety layer (§4.1): every browser_act op passes check() before it reaches the server."""
from __future__ import annotations

import re
from typing import Iterable

from assistant import probes

TRANSMIT_STRONG = [r"\bsubmit", r"\bsend\b", r"\bapply\b"]
TRANSMIT_WEAK = [r"\bconfirm\b", r"\bdone\b", r"\bfinish", r"\bcomplete\b"]
TRANSMIT = TRANSMIT_STRONG + TRANSMIT_WEAK  # re.I, re.search on the element label
ADVANCE_RE = r"^\s*(next|continue|review|save and continue|save & continue)\b"  # and not TRANSMIT
ENTRY_RE = r"^\s*(easy apply|apply)\b"
GUEST_RE = r"apply without an account|continue as guest"   # §6.3 signup attempt 2 (user decision 2026-09-23)


class GuardError(RuntimeError):
    pass


class _EntryToken:
    """Only entry.py imports ENTRY (checked by an AST test). It unlocks `confirm` on an ENTRY_RE or GUEST_RE click."""
    __slots__ = ()


ENTRY = _EntryToken()


def is_transmit(label: str) -> bool:
    return any(re.search(p, label or "", re.I) for p in TRANSMIT)


def is_strong_transmit(label: str) -> bool:
    return any(re.search(p, label or "", re.I) for p in TRANSMIT_STRONG)


def is_advance(label: str) -> bool:
    return bool(re.search(ADVANCE_RE, label or "", re.I)) and not is_transmit(label)


def is_entry(label: str) -> bool:
    return bool(re.search(ENTRY_RE, label or "", re.I))


def is_guest(label: str) -> bool:
    return bool(re.search(GUEST_RE, label or "", re.I))


def _entry_label(label: str) -> bool:
    """Labels the ENTRY token may unlock: the entry button, or a sign-up wall's guest link."""
    return is_entry(label) or is_guest(label)


def label_of(ref: str, table) -> str | None:
    """The label the server will test for a ref (option refs 'e2:1' resolve to the option label)."""
    base, _, opt = str(ref).partition(":")
    for e in table.elements:
        if e.ref == base:
            if not opt:
                return e.name
            idx = int(opt) - 1
            return e.options[idx].label if 0 <= idx < len(e.options) else None
    return None


def _key_names(op: dict) -> Iterable[str]:
    keys = op.get("keys")
    if isinstance(keys, str):
        keys = [keys]
    return [op.get("key") or "", *(keys or [])]


def keys_would_submit(op: dict) -> bool:
    """True if a `keys` op would press Enter or Return (§4.1), alone or in a combo."""
    # Loose on purpose: any mention of enter/return (combos, keypad, casing) or a raw CR/LF blocks the op.
    return any(re.search(r"enter|return|[\r\n]", str(k), re.I) for k in _key_names(op))


def check(op: dict, table, token: object = None) -> None:
    """Raise GuardError if `op` could transmit an application. `table` is the latest jev.Table."""
    kind = str(op.get("op") or "").strip().lower()
    if "confirm" in op:
        label = label_of(op.get("ref", ""), table) or ""
        if token is not ENTRY or kind != "click" or not _entry_label(label):
            raise GuardError(f"'confirm' is reserved for the entry click (op={kind}, label={label!r})")
    if kind == "keys" and keys_would_submit(op):
        raise GuardError(f"keys would press Enter/Return: {op}")
    if kind == "type" and op.get("submit"):
        raise GuardError("type with submit is forbidden")
    if kind == "eval" and op.get("js") not in probes.EVAL_PROBES.values():
        raise GuardError("eval js must be a probes.py constant")
    if kind == "tab" and str(op.get("action") or "list") != "list":
        raise GuardError("tab ops go through browser_tabs (tabs.py), not browser_act")
    if kind in {"nav", "back", "forward"}:
        raise GuardError(f"'{kind}' is not a wrapper op; navigation goes through browser_open")
    if kind == "click":
        label = label_of(op.get("ref", ""), table)
        if label is None:
            raise GuardError(f"click target {op.get('ref')!r} is not in the latest element table")
        if is_transmit(label) and not (token is ENTRY and _entry_label(label)):
            raise GuardError(f"click on a transmit label: {label!r}")
