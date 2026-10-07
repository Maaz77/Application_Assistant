"""rotation.py: the order of a call, handing over on an unavailable model, and the error when none answers."""
import pytest

from assistant.rotation import NoModelAvailable, Rotation

pytestmark = pytest.mark.unit


class Busy(Exception):
    pass


def test_a_call_starts_at_the_model_that_answered_last_then_the_rest_as_configured():
    r = Rotation(["a", "b", "c"])
    assert r.order() == ["a", "b", "c"]
    tried = []

    def attempt(m):
        tried.append(m)
        if m != "c":
            raise Busy(f"{m} busy")
        return m.upper()
    assert r.call(attempt, Busy) == "C" and tried == ["a", "b", "c"] and r.last == "c"
    assert r.order() == ["c", "a", "b"]


def test_none_answered_names_every_failure_and_keeps_the_last_good_model():
    r = Rotation(["a", "b"])
    r.last = "b"

    def attempt(m):
        raise Busy(f"HTTP 429 {m}")
    with pytest.raises(NoModelAvailable, match=r"none of 2 models answered \(b: HTTP 429 b; a: HTTP 429 a\)"):
        r.call(attempt, Busy)
    assert r.last == "b"


def test_other_errors_are_not_skipped():
    def attempt(m):
        raise KeyError("bad key")
    with pytest.raises(KeyError):
        Rotation(["a", "b"]).call(attempt, Busy)


def test_one_model_is_a_rotation_of_one_and_none_is_an_error():
    assert Rotation("a").models == ["a"]
    with pytest.raises(ValueError):
        Rotation([])


def test_config_takes_one_model_or_a_list(tmp_path):
    from assistant import config
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\nllm_inference = "x/one"\n')
    assert config.load(f).models.llm_inference == ("x/one",)
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\n'
                 f'llm_inference = ["y/a", "y/b"]\n')
    assert config.load(f).models.llm_inference == ("y/a", "y/b")


def test_an_empty_model_list_is_a_problem_not_a_silent_run(tmp_path):
    """With no models there is nothing to rotate, so the run must be stopped by preflight rather than failing
    every page with "none of 0 models answered"."""
    from assistant import config
    f = tmp_path / "c.toml"
    f.write_text(f'[paths]\nbase = "{tmp_path}"\n[models.freellmapi]\nbase_url = "http://x/v1"\n')
    assert any("models.freellmapi.llm_inference is empty" in p for p in config.load(f).problems())


# The package text helper and jev.rotate_text_helper are gone in P2 (the package is removed); its rotation
# test is dropped. The Gateway owns the one retry/rotation layer now — see tests/test_gateway.py.
