"""Anthropic Messages API — messages.parse with ExtractedEvents."""

from __future__ import annotations

import anthropic

from backend import config
from backend.extraction.providers import ProviderError, ProviderResult
from backend.extraction.schema_export import ANTHROPIC_OUTPUT_FORMAT


class AnthropicDirect:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.model = model or config.extraction_model()
        self._timeout_s = (
            config.EXTRACTION_TIMEOUT_S if timeout_s is None else timeout_s
        )
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=config.anthropic_api_key(),
                timeout=self._timeout_s,
            )
        return self._client

    def complete(self, *, system: str, user_message: str) -> ProviderResult:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=config.EXTRACTION_MAX_TOKENS,
                # Through extra_body because messages.parse() does not surface
                # temperature in anthropic 1.4.0. Asserted in TestSdkIntegration.
                extra_body={"temperature": config.EXTRACTION_TEMPERATURE},
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
                output_format=ANTHROPIC_OUTPUT_FORMAT,
            )
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.BadRequestError,
            anthropic.NotFoundError,
        ):
            # Fatal: worker must not retry. Leave the original type intact.
            raise
        except Exception as exc:  # noqa: BLE001 — rewrap for the worker
            raise ProviderError(
                f"{type(exc).__name__}: {exc}",
                provider=self.name,
                model=self.model,
            ) from exc

        if response.stop_reason == "refusal":
            raise ProviderError(
                "model refused the request",
                provider=self.name,
                model=self.model,
            )
        if response.parsed_output is None:
            raise ProviderError(
                "model returned no parsable structured output",
                provider=self.name,
                model=self.model,
            )
        return ProviderResult(
            parsed=response.parsed_output,
            request_id=getattr(response, "id", None),
            region=None,
            model=self.model,
        )
