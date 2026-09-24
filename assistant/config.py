"""Loads config.toml (strict: unknown keys are an error) and the keys from .env."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator

TOOL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOL_DIR / "config.toml"
DEFAULT_ENV = TOOL_DIR / ".env"
# Who serves the chat models (the answer engine and the text helper): both speak OpenAI's chat/completions, and
# both take the same `response_format` and `reasoning` fields (Vercel docs, checked 2026-09-24).
CHAT_BASES = {"openrouter": "https://openrouter.ai/api/v1", "vercel": "https://ai-gateway.vercel.sh/v1"}


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


class ChatModels(_Strict):
    """One provider's chat models, as that provider names them. Each list is tried in turn (rotation.py): the model
    that answered last first, the next when one is out."""
    answer_engine: tuple[str, ...] = ()
    text_helper: tuple[str, ...] = ()

    @field_validator("answer_engine", "text_helper", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        """A single model ID is a rotation of one."""
        return (v,) if isinstance(v, str) else v


class Models(_Strict):
    chat_route: Literal["openrouter", "vercel"] = "openrouter"   # who serves the chat models: the table below
    openrouter: ChatModels = ChatModels()
    vercel: ChatModels = ChatModels()
    jev: str = "typesafe-ai/jev"
    jev_route: Literal["vercel", "openrouter"] = "vercel"   # who serves Jev: Vercel AI Gateway or OpenRouter

    @property
    def chat(self) -> ChatModels:
        """The chat models of the route in use."""
        return self.vercel if self.chat_route == "vercel" else self.openrouter

    @property
    def answer_engine(self) -> tuple[str, ...]:
        return self.chat.answer_engine

    @property
    def text_helper(self) -> tuple[str, ...]:
        return self.chat.text_helper


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
        route = self.models.chat_route
        for name in ("answer_engine", "text_helper"):
            if not getattr(self.models.chat, name):
                out.append(f"models.{route}.{name} is empty (models.chat_route is {route!r}: list one or more "
                           f"model IDs as {route} names them)")
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


def _env(name: str, env_file: Path) -> str:
    return ((dotenv_values(env_file).get(name) if env_file.exists() else None) or os.environ.get(name, "")).strip()


def api_key(env_file: Path = DEFAULT_ENV) -> str:
    """The OpenRouter key (the answer engine and the text helper): .env first, then the process environment."""
    return _env("OPENROUTER_API_KEY", env_file)


def gateway_key(env_file: Path = DEFAULT_ENV) -> str:
    """The Vercel AI Gateway key (Jev, when models.jev_route is "vercel")."""
    return _env("AI_GATEWAY_API_KEY", env_file)


def jev_key(cfg: "Config", env_file: Path = DEFAULT_ENV) -> str:
    """The key for Jev's route."""
    return gateway_key(env_file) if cfg.models.jev_route == "vercel" else api_key(env_file)


def chat_key(cfg: "Config", env_file: Path = DEFAULT_ENV) -> str:
    """The key for the chat models' route (the answer engine and the text helper)."""
    return gateway_key(env_file) if cfg.models.chat_route == "vercel" else api_key(env_file)


def chat_url(cfg: "Config") -> str:
    """chat/completions on the chat models' route."""
    return CHAT_BASES[cfg.models.chat_route] + "/chat/completions"


KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "vercel": "AI_GATEWAY_API_KEY"}
