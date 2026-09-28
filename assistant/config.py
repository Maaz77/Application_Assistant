"""Loads config.toml (strict: unknown keys are an error) and the keys from .env."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

TOOL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOL_DIR / "config.toml"
DEFAULT_ENV = TOOL_DIR / ".env"
# Who serves the chat models (the LLM inference and the text helper): both speak OpenAI's chat/completions, and
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
    """The models one provider serves for us, as that provider names them. The chat lists are tried in turn
    (rotation.py): the model that answered last first, the next when one is out. `system_one_decision_model` is that
    provider's System One decision model — a single ID, e.g. the Jev instance "typesafe-ai/jev" (chosen by
    models.system_one_decision_provider; the chat lists by models.chat_route)."""
    llm_inference: tuple[str, ...] = ()
    text_helper: tuple[str, ...] = ()
    system_one_decision_model: str = ""

    @field_validator("llm_inference", "text_helper", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        """A single model ID is a rotation of one."""
        return (v,) if isinstance(v, str) else v


class LocalKev(_Strict):
    """A System One decision server on this machine: Kev (github.com/jaredpalmer/kev), which serves TypeSafe's
    System One API (POST /v1/systemone) with the same request and answer shapes as Jev, so only the URL changes.
    It serves the decision model only; the chat models (llm_inference, text_helper) stay on models.chat_route."""
    base_url: str = "http://127.0.0.1:8009"
    system_one_decision_model: str = "kev-latest"       # the name the server answers to; --run picks the checkpoint
    state_chars: int = 12_000       # Kev was trained on states of <=384 tokens: a short state is faster and better
    timeout: float = 120.0          # one pass on an Apple GPU is seconds, not the milliseconds of a data-centre GPU


class Models(_Strict):
    chat_route: Literal["openrouter", "vercel"] = "openrouter"   # who serves the chat models: the table below
    openrouter: ChatModels = ChatModels()
    vercel: ChatModels = ChatModels()
    local: LocalKev = LocalKev()                                 # a Kev server on this machine (decision model only)
    # Serves the System One decision model. "local" needs no key and no quota; the cloud routes need theirs.
    system_one_decision_provider: Literal["vercel", "openrouter", "local"] = "vercel"

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_key(cls, data):
        """The P0 rename (2026-09-27): point an old config at its new key instead of a generic 'extra' error."""
        old = "answer_engine"  # rename-guard: the pre-P0 key name, kept only to detect and redirect it
        if isinstance(data, dict):
            for route in ("openrouter", "vercel"):
                if isinstance(data.get(route), dict) and old in data[route]:
                    raise ValueError(f"models.{route}.{old} was renamed to models.{route}.llm_inference")
        return data

    @property
    def chat(self) -> ChatModels:
        """The chat models of the route in use."""
        return self.vercel if self.chat_route == "vercel" else self.openrouter

    @property
    def system_one_decision_model(self) -> str:
        """The System One decision model on models.system_one_decision_provider
        (models.<system_one_decision_provider>.system_one_decision_model)."""
        return getattr(self, self.system_one_decision_provider).system_one_decision_model

    @property
    def llm_inference(self) -> tuple[str, ...]:
        return self.chat.llm_inference

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
        for name in ("llm_inference", "text_helper"):
            if not getattr(self.models.chat, name):
                out.append(f"models.{route}.{name} is empty (models.chat_route is {route!r}: list one or more "
                           f"model IDs as {route} names them)")
        if self.models.system_one_decision_provider == "local" and not self.models.local.base_url:
            out.append("models.local.base_url is empty (the address of the Kev server on this machine, "
                       "e.g. http://127.0.0.1:8009)")
        if not self.models.system_one_decision_model:
            out.append(f"models.{self.models.system_one_decision_provider}.system_one_decision_model is empty "
                       f"(models.system_one_decision_provider is {self.models.system_one_decision_provider!r}: set "
                       f"the System One decision model for that provider)")
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
    """The OpenRouter key (the LLM inference and the text helper): .env first, then the process environment."""
    return _env("OPENROUTER_API_KEY", env_file)


def gateway_key(env_file: Path = DEFAULT_ENV) -> str:
    """The Vercel AI Gateway key (the System One decision model, when models.system_one_decision_provider is "vercel")."""
    return _env("AI_GATEWAY_API_KEY", env_file)


def local_key(env_file: Path = DEFAULT_ENV) -> str:
    """KEV_API_KEY: only when the local Kev server was started with one. A Kev server is open by default, and then
    it ignores the Authorization header, so this is empty for most local setups."""
    return _env("KEV_API_KEY", env_file)


def system_one_decision_key(cfg: "Config", env_file: Path = DEFAULT_ENV) -> str:
    """The key for the System One decision provider's route ("" for a local server that asks for none)."""
    return {"vercel": gateway_key, "openrouter": api_key, "local": local_key}[
        cfg.models.system_one_decision_provider](env_file)


def chat_key(cfg: "Config", env_file: Path = DEFAULT_ENV) -> str:
    """The key for the chat models' route (the LLM inference and the text helper)."""
    return gateway_key(env_file) if cfg.models.chat_route == "vercel" else api_key(env_file)


def chat_url(cfg: "Config") -> str:
    """chat/completions on the chat models' route."""
    return CHAT_BASES[cfg.models.chat_route] + "/chat/completions"


KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "vercel": "AI_GATEWAY_API_KEY", "local": "KEV_API_KEY"}
KEYLESS_PROVIDERS = ("local",)   # a server on this machine: a key only when it was started with KEV_API_KEY set
