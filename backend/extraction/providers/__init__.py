"""LLM backends for structured event extraction.

Providers only talk to models. Grounding, referential integrity, and all
validation live in ExtractionWorker and apply identically to every answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.state.models import ExtractedEvents


class ProviderError(RuntimeError):
    """Loud failure from an extraction provider. Never swallow silently."""

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


@dataclass(frozen=True)
class ProviderResult:
    """Parsed extraction plus Gateway telemetry (None on Anthropic-direct)."""

    parsed: ExtractedEvents
    request_id: str | None = None
    region: str | None = None
    model: str | None = None


class ExtractionProvider(Protocol):
    name: str
    model: str

    def complete(self, *, system: str, user_message: str) -> ProviderResult:
        """Return schema-valid ExtractedEvents or raise ProviderError."""
        ...
