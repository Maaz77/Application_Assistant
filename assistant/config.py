"""Loads config.toml (strict: unknown keys are an error) and the OpenRouter key from .env."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict

TOOL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOL_DIR / "config.toml"
DEFAULT_ENV = TOOL_DIR / ".env"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Paths(_Strict):
    base: str
    applications: str = "Applications"
    pending_review: str = "Pending-Review"
    needs_attention: str = "Needs-Attention"
    profile: str = "Profile.md"
    tracker: str = "Job_Tracker.numbers"
    tracker_sheet: str = "Jobs"


class Browser(_Strict):
    cdp_url: str = "http://127.0.0.1:9222"
    chrome: str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    max_actions: int = 2000
    max_pages_per_job: int = 15


class Models(_Strict):
    answer_engine: str = ""
    text_helper: str = "deepseek/deepseek-chat"
    jev: str = "jev-latest"


class Policy(_Strict):
    prefill: Literal["keep-if-silent", "strict"] = "keep-if-silent"
    free_text_max_chars: int = 1500


class Google(_Strict):
    account_email: str = ""


class Config(_Strict):
    paths: Paths
    browser: Browser = Browser()
    models: Models = Models()
    policy: Policy = Policy()
    google: Google = Google()
    source: Path = DEFAULT_CONFIG  # set by load(); not a TOML key

    def base_dir(self) -> Path:
        base = Path(self.paths.base).expanduser()
        return base if base.is_absolute() else (self.source.parent / base).resolve()

    def path(self, name: str) -> Path:
        """Absolute path of one of the [paths] entries, e.g. cfg.path("profile")."""
        return self.base_dir() / getattr(self.paths, name)

    def problems(self) -> list[str]:
        """Preflight-level validation beyond the schema."""
        out = []
        if not self.paths.base:
            out.append("paths.base is empty")
        if not self.models.answer_engine:
            out.append("models.answer_engine is empty (set an OpenRouter slug)")
        for name in ("applications", "profile", "tracker"):
            if not self.path(name).exists():
                out.append(f"paths.{name} not found: {self.path(name)}")
        return out


def load(path: Path = DEFAULT_CONFIG) -> Config:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    if "source" in data:
        raise ValueError("'source' is not a config key")
    return Config(**data, source=Path(path).resolve())


def api_key(env_file: Path = DEFAULT_ENV) -> str:
    """The OpenRouter key: .env first, then the process environment."""
    key = (dotenv_values(env_file).get("OPENROUTER_API_KEY") if env_file.exists() else None) or os.environ.get(
        "OPENROUTER_API_KEY", ""
    )
    return key.strip()
