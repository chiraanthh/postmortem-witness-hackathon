"""Factory for extraction providers. No silent cross-provider fallback."""

from __future__ import annotations

from typing import Any

from backend import config
from backend.extraction.providers.anthropic_direct import AnthropicDirect
from backend.extraction.providers.assemblyai_gateway import AssemblyAIGateway


def make_provider(
    *,
    provider: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> AnthropicDirect | AssemblyAIGateway:
    name = (provider or config.extraction_provider()).strip().lower()
    model_name = model or config.extraction_model()

    if name in ("anthropic", "anthropic_direct", "direct"):
        return AnthropicDirect(model=model_name, **kwargs)
    if name in ("assemblyai_gateway", "gateway", "assemblyai"):
        return AssemblyAIGateway(model=model_name, **kwargs)

    raise ValueError(
        f"unknown EXTRACTION_PROVIDER={name!r}; "
        "expected 'anthropic' or 'assemblyai_gateway'"
    )
