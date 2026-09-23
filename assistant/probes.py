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
const labelOf = e => clean(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText)
  || (e.closest('fieldset') && e.closest('fieldset').querySelector('legend') && e.closest('fieldset').querySelector('legend').innerText)
  || e.getAttribute('placeholder') || e.name || e.id || e.tagName.toLowerCase()).slice(0, 28);
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

CAPTCHA_PRESENT = (
    "!!document.querySelector('iframe[src*=\"recaptcha\"],iframe[src*=\"hcaptcha\"],"
    "iframe[src*=\"challenges.cloudflare.com\"],.g-recaptcha,.h-captcha,.cf-turnstile,[data-sitekey]')"
)

EVAL_PROBES = {"REQUIRED_EMPTY": REQUIRED_EMPTY, "MAXLENGTHS": MAXLENGTHS, "IFRAME_SRCS": IFRAME_SRCS}
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
