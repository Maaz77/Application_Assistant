"""P0 T5 — the "answer engine" → "LLM inference" rename is complete (00_common §7).

No component-name token survives in the code, tests, prompts, config or the tracked docs (DISCOVERY.md keeps its
old entries by decision, and this test names the token to search for, so both are out of scope). The one tolerated
occurrence is the config guard that still recognises the deprecated key, marked with `rename-guard`.
"""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant.config import Config

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
TOKEN = re.compile(r"answer.?engine|AnswerEngine", re.IGNORECASE)
SELF = Path(__file__).name


def _scanned_files():
    for pat in ("assistant/**/*.py", "tests/**/*.py", "prompts/**/*.md"):
        yield from ROOT.glob(pat)
    for name in ("config.toml", "README.md", "CLAUDE.md", "LIVE_TEST.md", "application_assistant_build_spec.md"):
        if (ROOT / name).exists():
            yield ROOT / name


def test_no_component_name_survives():
    offenders = []
    for path in _scanned_files():
        if path.name == SELF or "golden" in path.parts or "__pycache__" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "rename-guard" in line:
                continue
            if TOKEN.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "old component name still present:\n" + "\n".join(offenders)


def test_old_config_key_is_rejected_with_the_new_name():
    with pytest.raises(ValidationError, match="answer_engine was renamed to models.openrouter.llm_inference"):
        Config(paths={"base": "x"}, models={"openrouter": {"answer_engine": ["m"]}})
