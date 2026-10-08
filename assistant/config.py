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


class FreeLLMAPI(_Strict):
    """The FreeLLMAPI router on this machine (github.com/tashfeenahmed/freellmapi): one OpenAI-compatible /v1 that
    fans a chat request out over the free tiers of many providers and falls over when one is rate-limited. It serves
    the chat models only; the System One decision model stays on the Kev server below.

    `llm_inference` is normally the router's own `"auto"`: picking the model and routing around an exhausted free
    tier is the router's job, not this program's (user decision 2026-10-07). `"auto:fast"`, `"auto:smart"`,
    `"fusion"` and a concrete ID from `GET /v1/models` all work too. A list is still accepted and still rotates
    (rotation.py), which is what a caller pinning several concrete IDs would want, but the shipped config names one
    routing mode and lets the router do the rest."""
    base_url: str = "http://127.0.0.1:31415/v1"
    llm_inference: tuple[str, ...] = ()
    # The chat timeout, for every request on this route: LLM inference and the System One chat fallback. It has to
    # cover the router's own failover, not one model's answer. A stalled upstream costs 60 s before the router may
    # move on — its own per-platform chat limit, `providerTimeoutMs("cloudflare", 6e4)` — after which the next
    # platform usually answers in seconds; `auto` also picks a 38-89 s model now and then. At the 45 s this used to
    # be, a run hung up before that limit: nine attempts in a row died on the same stalled upstream, never reaching
    # the failover that would have answered, and stopped the run as a "provider outage" while the router was up
    # (2026-10-08). 180 s is room for one stall and a slow answer behind it.
    timeout: float = 180.0

    @field_validator("llm_inference", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        """A single model ID is a rotation of one."""
        return (v,) if isinstance(v, str) else v


class LocalKev(_Strict):
    """A System One decision server on this machine: Kev (github.com/jaredpalmer/kev), which serves TypeSafe's
    System One API (POST /v1/systemone) with the same request and answer shapes as Jev, so only the URL changes.
    It serves the decision model only; the chat models (llm_inference) stay on the FreeLLMAPI router above."""
    base_url: str = "http://127.0.0.1:8009"
    system_one_decision_model: str = "kev-latest"       # the name the server answers to; --run picks the checkpoint
    state_chars: int = 12_000       # Kev was trained on states of <=384 tokens: a short state is faster and better
    timeout: float = 120.0          # one pass on an Apple GPU is seconds, not the milliseconds of a data-centre GPU


class Models(_Strict):
    """Two servers on this machine, and nothing off it: the FreeLLMAPI router answers the chat models, a Kev server
    answers the System One decision model. The OpenRouter and Vercel AI Gateway routes were removed on 2026-10-07
    (both were paid, and FreeLLMAPI serves the same OpenAI chat/completions shape for free)."""
    freellmapi: FreeLLMAPI = FreeLLMAPI()        # the chat models (the LLM inference and the chat fallback)
    local: LocalKev = LocalKev()                 # a Kev server on this machine (the decision model only)

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_route(cls, data):
        """Point a config written for the old paid routes at what replaced them, instead of a generic
        'extra fields not permitted' from the strict schema."""
        if isinstance(data, dict):
            for gone in ("openrouter", "vercel", "chat_route", "system_one_decision_provider"):
                if gone in data:
                    raise ValueError(
                        f"models.{gone} was removed on 2026-10-07: the chat models now live in "
                        f"[models.freellmapi] (base_url + llm_inference) and the System One decision model in "
                        f"[models.local]")
        return data

    @property
    def system_one_decision_model(self) -> str:
        """The System One decision model, which only the local Kev server serves."""
        return self.local.system_one_decision_model

    @property
    def llm_inference(self) -> tuple[str, ...]:
        return self.freellmapi.llm_inference


class Limits(_Strict):
    """One queue for every model request (gateway.py). A run sent 222 System One requests in 19 minutes with four
    batches in flight (spec §13.1); one at a time, spaced, is what keeps a provider from rate-limiting us."""
    max_in_flight: int = 1
    min_interval_s: float = 0.25
    max_attempts: int = 3


class SystemOneLimits(_Strict):
    """TypeSafe fails a whole request when any one question fails, so a request is split above this many
    questions and the parts are sent one after another (never side by side: see Limits)."""
    max_questions_per_request: int = 24
    max_requests_per_job: int = 40


class Decider(_Strict):
    fallback: Literal["chat", "none"] = "chat"   # D19: a chat model answers the same questions when System One fails


class Policy(_Strict):
    prefill: Literal["keep-if-silent", "strict"] = "keep-if-silent"
    free_text_max_chars: int = 1500


class Google(_Strict):
    account_email: str = ""


class Config(_Strict):
    paths: Paths
    browser: Browser = Browser()
    models: Models = Models()
    limits: Limits = Limits()
    jev: SystemOneLimits = SystemOneLimits()
    decider: Decider = Decider()
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
        if not self.models.freellmapi.llm_inference:
            out.append("models.freellmapi.llm_inference is empty (list one or more model IDs as the FreeLLMAPI "
                       "router's GET /v1/models names them, e.g. \"kimi-k3\")")
        if not self.models.freellmapi.base_url:
            out.append("models.freellmapi.base_url is empty (the address of the FreeLLMAPI router on this "
                       "machine, e.g. http://127.0.0.1:31415/v1)")
        if not self.models.local.base_url:
            out.append("models.local.base_url is empty (the address of the Kev server on this machine, "
                       "e.g. http://127.0.0.1:8009)")
        if not self.models.system_one_decision_model:
            out.append("models.local.system_one_decision_model is empty (the name the Kev server on this machine "
                       "answers to, e.g. \"kev-latest\")")
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


def local_key(env_file: Path = DEFAULT_ENV) -> str:
    """KEV_API_KEY: only when the local Kev server was started with one. A Kev server is open by default, and then
    it ignores the Authorization header, so this is empty for most local setups."""
    return _env("KEV_API_KEY", env_file)


def chat_key(env_file: Path = DEFAULT_ENV) -> str:
    """FREELLMAPI_KEY: the unified key of the FreeLLMAPI router, which every chat request uses (the LLM inference
    and the chat fallback). The router refuses an unauthenticated request with HTTP 401, so this is required even
    though the router runs on this machine."""
    return _env("FREELLMAPI_KEY", env_file)


def chat_url(cfg: "Config") -> str:
    """chat/completions on the FreeLLMAPI router."""
    return cfg.models.freellmapi.base_url.rstrip("/") + "/chat/completions"


CHAT_KEY_NAME = "FREELLMAPI_KEY"   # named in the preflight messages, never printed with its value
LOCAL_KEY_NAME = "KEV_API_KEY"     # only when the Kev server was started with one; it is open by default
