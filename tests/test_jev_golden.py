import json
from pathlib import Path

import pytest

from assistant import config
from assistant.jev import env_values, split_json

pytestmark = pytest.mark.unit
GOLDEN = Path(__file__).parent / "golden"


def test_golden_observe_parses():
    view, table = split_json((GOLDEN / "observe_full_json.txt").read_text())
    roles = {e.role for e in table.elements}
    assert {"textbox", "combobox", "radio", "checkbox", "file", "button"} <= roles
    country = next(e for e in table.elements if e.name == "Country")
    assert [o.label for o in country.options] == ["Choose", "Italy", "Iran"]
    assert table.title == "Discovery form" and view.startswith("[obs#")


def test_env_values_match_spec_table():
    cfg = config.load()
    cfg = cfg.model_copy(update={"models": cfg.models.model_copy(update={"chat_route": "openrouter"})})
    env = env_values(cfg, "k-123")
    assert env["OPENROUTER_API_KEY"] == env["TEXT_MODEL_API_KEY"] == "k-123"
    assert env["JEVMCP_MODE"] == "attach" and env["JEVMCP_ALLOW_JS"] == "1"
    assert "JEVMCP_CONFIRM_PATTERNS" not in env               # clicks are refused by guard.never_click instead
