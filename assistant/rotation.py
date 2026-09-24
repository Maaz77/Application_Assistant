"""Model rotation: a list of free OpenRouter models, tried in turn until one answers.

A free model shares its provider's capacity with everyone, so any one of them can be out for minutes: HTTP 429
"temporarily rate-limited upstream", an overloaded provider passed through as HTTP 200 with an error body, a read
timeout (live 2026-09-24). A call starts at the model that answered last, then tries the rest in their configured
order; a model that fails is skipped for this call only. When none answers, the call fails with every reason."""
from __future__ import annotations

from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


class NoModelAvailable(RuntimeError):
    """Every model in the rotation failed on this call."""


class Rotation:
    def __init__(self, models: str | Sequence[str]):
        self.models = [models] if isinstance(models, str) else list(models)
        if not self.models:
            raise ValueError("a model rotation needs at least one model")
        self.last: str | None = None          # the model that answered the latest call

    def order(self) -> list[str]:
        """This call's order: the model that answered last, then the others as configured."""
        return [self.last] + [m for m in self.models if m != self.last] if self.last else list(self.models)

    def call(self, attempt: Callable[[str], T], unavailable: type[Exception] | tuple[type[Exception], ...]) -> T:
        """attempt(model) for each model in order; an `unavailable` error moves on to the next, any other error
        propagates (a rejected key fails every model alike)."""
        failures = []
        for model in self.order():
            try:
                out = attempt(model)
            except unavailable as exc:
                failures.append(f"{model}: {exc}")
                continue
            self.last = model
            return out
        raise NoModelAvailable(f"none of {len(self.models)} models answered ({'; '.join(failures)})")
