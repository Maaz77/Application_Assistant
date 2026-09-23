"""T1 discovery: drive the throwaway Chrome against local fixtures and save raw outputs to tests/golden/.

    .venv/bin/python -m tests.discover
"""
import json
from pathlib import Path

from assistant import config, jev
from tests.support import CDP_URL, FixtureServer, ThrowawayChrome

GOLDEN = Path(__file__).parent / "golden"


def save(name: str, text: str) -> None:
    (GOLDEN / name).write_text(text)
    print(f"--- {name} ---\n{text[:3000]}\n")


def main() -> None:
    cfg = config.load()
    GOLDEN.mkdir(exist_ok=True)
    with FixtureServer() as srv, ThrowawayChrome(cfg.browser.chrome) as chrome:
        m = jev.Jev(cfg, "", calls_log=GOLDEN / "calls.jsonl")
        S = "disc"
        save("doctor_before.json", m.call("browser_doctor"))
        save("open.txt", m.open(srv.url("discovery.html"), S))
        save("observe_full_json.txt", m.observe(S))
        view, table = m.table(S)
        els = table.elements
        save("elements.json", table.model_dump_json(indent=2))
        save("doctor_after.json", m.call("browser_doctor"))
        short = "JSON.stringify({a: 1, b: [2, 3]})"
        long_ = "JSON.stringify(Array.from({length: 60}, (_, i) => 'label-' + i))"
        obj = "({a: 1, b: 'x'})"
        save("act_eval.txt", m.call("browser_act", **{"ops": [
            {"op": "eval", "js": short}, {"op": "eval", "js": long_}, {"op": "eval", "js": obj}],
            "session": S, "observe_after": False}))
        save("assert.txt", m.call("browser_assert", **{"session": S, "checks": [
            {"type": "text_contains", "text": "Discovery"}, {"type": "text_absent", "text": "Discovery"},
            {"type": "js", "expr": "document.querySelectorAll('input').length > 2"}]}))
        file_refs = [e.ref for e in els if e.role == "file"]
        pdf = Path(__import__("tempfile").mkdtemp()) / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        if file_refs:
            save("act_upload.txt", m.call("browser_act", **{"session": S, "ops": [
                {"op": "upload", "ref": file_refs[0], "path": str(pdf)},
                {"op": "eval", "js": "document.getElementById('cv').files.length"}]}))
        save("tabs_list.txt", m.tabs(S))
        before = {t["id"] for t in chrome.tabs()}
        save("tabs_new.txt", m.tabs(S, "new"))
        save("tabs_after_new_observe.txt", (m.observe(S, include_json=False))[:400])
        new = [t for t in chrome.tabs() if t["id"] not in before]
        save("json_list_after_new.json", json.dumps(chrome.tabs(), indent=2))
        if new:
            save("tabs_switch.txt", (m.tabs(S, "switch", target_id=new[0]["id"]))[:400])
        save("goal_no_key.txt", m.goal("Fill the first name with Amin", S))
        save("act_blocked_click.txt", m.call("browser_act", **{"session": "disc2", "ops": [
            {"op": "click", "ref": "e1"}]}))
        save("json_list_after_exit.json", json.dumps(chrome.tabs(), indent=2))


if __name__ == "__main__":
    jev.apply_env(config.load(), "", cdp_url=CDP_URL)
    main()
