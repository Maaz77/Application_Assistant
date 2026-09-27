import pytest

from assistant import config
from assistant.cli import build_parser

pytestmark = pytest.mark.unit


def test_shipped_config_loads():
    cfg = config.load()
    assert cfg.browser.max_actions == 2000
    assert (cfg.base_dir() / "Tools").is_dir()


def test_unknown_key_is_an_error(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[paths]\nbase = "x"\nbogus = 1\n')
    with pytest.raises(Exception):
        config.load(f)


def test_empty_llm_inference_is_a_problem(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n')
    assert any("llm_inference" in p for p in config.load(f).problems())


def test_cli_parses_run_flags():
    a = build_parser().parse_args(["run", "--dry-run", "--limit", "2"])
    assert a.dry_run and a.limit == 2
