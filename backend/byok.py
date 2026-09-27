"""Bring-your-own-key: session-scoped ASSEMBLYAI_API_KEY / ANTHROPIC_API_KEY.

Live pipeline and upload sessions on the hosted build run on a visitor's own
keys, not the host's. A key is held only as an attribute on the owning
IncidentHub for the life of that session — never written to disk, never
logged (not even in `config.redacted_key` form), never included in any HTTP
response, snapshot, or WS broadcast. No code path keeps a second copy, so it
is discarded the moment the session is dropped (seat freed, inactivity
expiry, or the visitor leaves) and the IncidentHub is garbage collected.

Recorded replay needs none of this — it is client-side and zero-API by
design (see frontend/src/replay/emitter.ts) and is untouched by BYOK.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
import httpx


@dataclass(frozen=True)
class ApiKeys:
    assemblyai: str
    anthropic: str


class KeyValidationError(RuntimeError):
    """A supplied key failed the one cheap validation call.

    `str(exc)` never includes the key itself — only a human-readable reason
    safe to return to the client and to log.
    """

    def __init__(self, which: str, message: str) -> None:
        super().__init__(message)
        self.which = which  # "assemblyai" | "anthropic"


def validate_assemblyai_key(key: str, *, timeout_s: float = 8.0) -> None:
    """One cheap authenticated call: list at most one transcript.

    Raises only on an unambiguous bad-key signal (401). Anything else
    (rate-limited, a transient 5xx, an unexpected response shape) means the
    key reached AssemblyAI's auth layer fine, so it is treated as valid
    rather than blocking a real key on a validation-endpoint hiccup.
    """
    try:
        resp = httpx.get(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": key},
            params={"limit": 1},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise KeyValidationError(
            "assemblyai", f"could not reach AssemblyAI to verify this key: {exc}"
        ) from exc
    if resp.status_code == 401:
        raise KeyValidationError(
            "assemblyai", "AssemblyAI rejected this key (401 unauthorized)"
        )


def validate_anthropic_key(key: str, *, timeout_s: float = 8.0) -> None:
    """One cheap call: Haiku, max_tokens=1.

    Raises only on AuthenticationError (bad key) or a connection failure.
    A key that authenticates but is rate-limited or over its usage cap
    (APIStatusError, e.g. 429 or a 400 "usage limits" response) is still a
    real, valid key — reject only what is unambiguously wrong.
    """
    client = anthropic.Anthropic(api_key=key, timeout=timeout_s)
    try:
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except anthropic.AuthenticationError as exc:
        raise KeyValidationError(
            "anthropic", "Anthropic rejected this key (invalid API key)"
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise KeyValidationError(
            "anthropic", f"could not reach Anthropic to verify this key: {exc}"
        ) from exc
    except anthropic.APIStatusError:
        pass
