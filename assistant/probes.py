"""Read-only page probes (§4.7). Constants only: guard.check accepts an `eval` whose js is identical to one of these.

jev 0.1.5 returns an eval value as json.dumps(value)[:200] (DISCOVERY.md). Every probe therefore returns a plain
object and trims itself to FIT chars of Python json.dumps output: `n` is the true count, `more` says items were cut.
"""
from __future__ import annotations

import json
import re

FIT = 190  # < 200, the server's cap

# Shared helpers, inlined into each probe (probes must stay self-contained constants).
_LIB = r"""
const clean = s => String(s || '').replace(/\s+/g, ' ').replace(/[^\x20-\x7e]/g, '?').trim();
const jlen = v => Array.isArray(v) ? 2 + v.reduce((a, x) => a + jlen(x), 0) + Math.max(0, 2 * (v.length - 1))
  : v && typeof v === 'object' ? 2 + Object.entries(v).reduce((a, [k, x]) => a + JSON.stringify(k).length + 2 + jlen(x), 0)
      + Math.max(0, 2 * (Object.keys(v).length - 1))
  : v === null || v === undefined ? 4 : JSON.stringify(v).length;
const byIds = ids => String(ids || '').split(/\s+/).filter(Boolean)
  .map(i => { const x = document.getElementById(i); return x ? x.innerText : ''; }).join(' ');
const groupLabel = e => { const g = e.closest('[role=group],[role=radiogroup],fieldset'); if (!g) return '';
  const lg = g.querySelector('legend'); return byIds(g.getAttribute('aria-labelledby')) || (lg ? lg.innerText : ''); };
const labelOf = e => clean((e.type === 'file' && groupLabel(e)) || e.getAttribute('aria-label')
  || byIds(e.getAttribute('aria-labelledby')) || (e.labels && e.labels[0] && e.labels[0].innerText) || groupLabel(e)
  || e.getAttribute('placeholder') || e.name || e.id || e.tagName.toLowerCase()).slice(0, 28);
const ownControl = e => e.tagName === 'INPUT' && /^(radio|checkbox|file)$/i.test(e.type || '');
const modalOpen = () => { const ms = [...document.querySelectorAll('dialog:modal')];
  for (let i = ms.length - 1; i >= 0; i--) { const r = ms[i].getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (hit && ms[i].contains(hit)) return ms[i]; }
  return ms.length ? ms[ms.length - 1] : null; };
const shown = e => { const r = e.getBoundingClientRect(); const m = modalOpen();
  return !e.disabled && !e.closest('[aria-hidden="true"],[inert],[aria-disabled="true"]') && (!m || m.contains(e))
    && e.checkVisibility({checkOpacity: !ownControl(e), checkVisibilityCSS: true}) && r.width > 0 && r.height > 0; };
const fit = (items, extra) => { const kept = []; let more = false;
  for (const it of items) { kept.push(it);
    if (jlen(Object.assign({n: items.length, more: false, items: kept}, extra || {})) > FIT) { kept.pop(); more = true; break; } }
  return Object.assign({n: items.length, more, items: kept}, extra || {}); };
""".replace("FIT", str(FIT))

REQUIRED_EMPTY = "(() => {" + _LIB + r"""
const req = [...document.querySelectorAll('input,select,textarea')].filter(e =>
  (e.required || e.getAttribute('aria-required') === 'true') && e.type !== 'hidden' && !e.disabled);
const seen = new Set(); const empty = [];
for (const e of req) {
  const t = (e.type || e.tagName).toLowerCase();
  if (t === 'radio' || t === 'checkbox') {
    const key = t + ':' + (e.name || labelOf(e)); if (seen.has(key)) continue; seen.add(key);
    const group = e.name ? [...document.querySelectorAll('input[name="' + CSS.escape(e.name) + '"]')] : [e];
    if (!group.some(g => g.checked)) empty.push([labelOf(e), t]);
  } else if (t === 'file' ? e.files.length === 0 : !String(e.value || '').trim()) empty.push([labelOf(e), t]);
}
return fit(empty);
})()"""

MAXLENGTHS = "(() => {" + _LIB + r"""
const els = [...document.querySelectorAll('input,textarea')].filter(e => e.maxLength > 0);
const min = els.length ? Math.min(...els.map(e => e.maxLength)) : null;
return fit(els.sort((a, b) => a.maxLength - b.maxLength).map(e => [labelOf(e), e.maxLength]), {min});
})()"""

IFRAME_SRCS = "(() => {" + _LIB.replace("replace(/[^\\x20-\\x7e]/g, '?')", "replace(/[^\\x20-\\x7e]/g, '')") + r"""
const srcs = [...document.querySelectorAll('iframe')].map(f => f.src).filter(s => /^https?:/.test(s));
const form = s => /greenhouse|lever|workday|ashby|smartrecruiters|icims|jobvite|apply|job|career|form/i.test(s) ? 0 : 1;
srcs.sort((a, b) => form(a) - form(b));
return fit(srcs.map(s => s.length > 170 ? '' : s).filter(Boolean), {long: srcs.filter(s => s.length > 170).length});
})()"""

# Only a visible, interactive challenge counts. Invisible reCAPTCHA (size=invisible, the .grecaptcha-badge) sits on
# many ATS forms (Ashby, live 2026-09-23) and never asks a human anything before submit, which we never reach.
CAPTCHA_PRESENT = r"""[...document.querySelectorAll('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"],.g-recaptcha,.h-captcha,.cf-turnstile')].some(e => { const s = e.getAttribute('src') || ''; const r = e.getBoundingClientRect(); return !/size=invisible/.test(s) && e.getAttribute('data-size') !== 'invisible' && !e.closest('.grecaptcha-badge') && e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && r.width > 30 && r.height > 30 && r.bottom > 0; })"""

# Each visible file input's label, in DOM order, preferring its group's label: Greenhouse names both file inputs
# "Attach" and puts "Resume/CV" / "Cover Letter" on the surrounding [role=group] (live 2026-09-23).
FILE_LABELS = "(() => {" + _LIB + r"""
const files = [...document.querySelectorAll('input[type=file]')].filter(shown);
return fit(files.map(e => [labelOf(e), clean(e.id || e.name).slice(0, 16)]));
})()"""

# The value each visible combobox shows, in DOM order: a native <select>'s chosen option, else the input's own value,
# else its control's text (react-select keeps its input empty and draws the choice in a sibling). Chunks of 7 because
# of the 200-char cap; `o` is the chunk's offset.
COMBO_CHUNK = 7
_COMBO = "(() => {" + _LIB + r"""
const combos = [...document.querySelectorAll('select:not([multiple]), [role=combobox]')].filter(shown);
const val = e => e.tagName === 'SELECT' ? [...e.selectedOptions].map(o => o.label).join(', ')
  : (e.value || (e.closest('[class*="control"]') || e.parentElement || e).innerText || '');
return fit(combos.slice(OFFSET, OFFSET + CHUNK).map(e => clean(val(e)).slice(0, 20)), {o: OFFSET, total: combos.length});
})()""".replace("CHUNK", str(COMBO_CHUNK))
COMBO_VALUES = {f"COMBO_VALUES_{i}": _COMBO.replace("OFFSET", str(i * COMBO_CHUNK)) for i in range(4)}

# Each visible radio's own option text, in DOM order. LinkedIn's Yes/No radios are DIV role=radio whose aria-label is
# the *question* for every option; the option ("Yes") is only their text (live 2026-09-23).
RADIO_CHUNK = 8
_RADIO = "(() => {" + _LIB + r"""
const radios = [...document.querySelectorAll('input[type=radio], [role=radio]')].filter(shown);
const txt = e => e.tagName === 'INPUT' ? ((e.labels && e.labels[0] && e.labels[0].innerText) || e.value || '') : (e.innerText || '');
return fit(radios.slice(OFFSET, OFFSET + CHUNK).map(e => clean(txt(e)).slice(0, 18)), {o: OFFSET, total: radios.length});
})()""".replace("CHUNK", str(RADIO_CHUNK))
RADIO_OPTIONS = {f"RADIO_OPTIONS_{i}": _RADIO.replace("OFFSET", str(i * RADIO_CHUNK)) for i in range(5)}

EVAL_PROBES = {"REQUIRED_EMPTY": REQUIRED_EMPTY, "MAXLENGTHS": MAXLENGTHS, "IFRAME_SRCS": IFRAME_SRCS,
               "FILE_LABELS": FILE_LABELS, **COMBO_VALUES, **RADIO_OPTIONS}
ASSERT_PROBES = {"CAPTCHA_PRESENT": CAPTCHA_PRESENT}
ALL = {**EVAL_PROBES, **ASSERT_PROBES}

_EVAL_LINE = re.compile(r"^\s*\+ eval → (.*?)\s+\d+ms$", re.M)


class ProbeError(RuntimeError):
    pass


def parse_eval_results(act_text: str) -> list:
    """Values of every `+ eval → <json>` line in a browser_act result, in order."""
    out = []
    for raw in _EVAL_LINE.findall(act_text):
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ProbeError(f"eval value unreadable (cut at 200 chars?): {raw[:60]}…") from exc
    return out
