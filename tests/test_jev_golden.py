import json
from pathlib import Path

import pytest

from assistant import config
from assistant.guard import TRANSMIT
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
    env = env_values(config.load(), "k-123")
    assert env["OPENROUTER_API_KEY"] == env["TEXT_MODEL_API_KEY"] == "k-123"
    assert env["JEVMCP_MODE"] == "attach" and env["JEVMCP_ALLOW_JS"] == "1"
    assert all(p in env["JEVMCP_CONFIRM_PATTERNS"].split(",") for p in TRANSMIT)
