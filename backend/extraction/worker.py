"""Turns one formatted utterance into one schema-valid event.

Three rules shape this file:

- **Schema-valid or nothing.** The model is given a JSON schema through the
  SDK's structured output support, so it cannot reply in prose. We never ask
  for JSON in a prompt and parse it out of text.
- **noise is the default answer,** not a failure. Most of an incident call is
  noise. If anything goes wrong - malformed output, a timeout, a rejected
  status change - the utterance becomes noise and the pipeline keeps moving.
  This worker never raises into the pipeline.
- **A status change must have been spoken.** The model has to quote the words
  verbatim, and `quote_is_grounded` checks the quote against the utterance.
  A quote it cannot produce is a status change that did not happen.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import anthropic

from backend import config
from backend.extraction.prompts import SYSTEM, render_context, render_user_message
from backend.metrics import EXTRACT, Metrics, now_ms
from backend.state.models import (
    Event,
    EventType,
    ExtractedFields,
    quote_is_grounded,
)
from backend.transcription.buffer import Utterance

log = logging.getLogger("postmortem.extraction")

# Worth trying again: transient, and a second attempt costs one round trip.
RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)

# Not worth trying again: a bad key or a bad request will fail identically,
# and a live call does not have a spare round trip to waste proving it.
FATAL_ERRORS = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
)


@dataclass(frozen=True)
class RunningContext:
    """A bounded snapshot of what is already known.

    The transcript is never resent. Only what the model needs to reuse an
    existing hypothesis id and to avoid re-opening an open thread.
    """

    hypotheses: tuple[tuple[str, str, str], ...] = ()   # (id, text, state)
    threads: tuple[tuple[str, str], ...] = ()           # (id, text)

    def render(self) -> str:
        return render_context(
            self.hypotheses,
            self.threads,
            max_hypotheses=config.EXTRACTION_MAX_HYPOTHESES,
            max_threads=config.EXTRACTION_MAX_THREADS,
        )

    def has_hypothesis(self, hypothesis_id: str | None) -> bool:
        return any(h[0] == hypothesis_id for h in self.hypotheses)


@dataclass
class ExtractionOutcome:
    """One extraction, and everything worth knowing about how it went."""

    event: Event
    latency_ms: float
    attempts: int
    raw: ExtractedFields | None = None

    # Set when a claimed status_change was refused, with the reason. This is
    # the number that matters: it is the model trying to invent a state change.
    rejected_status_change: str | None = None

    # Set when we fell back to noise because the call itself failed.
    degraded: bool = False
    error: str | None = None

    @property
    def is_noise(self) -> bool:
        return self.event.type == EventType.NOISE.value


def _slugify(text: str, fallback: str = "hypothesis") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = "-".join(slug.split("-")[:4])
    return slug or fallback


class ExtractionWorker:
    """Extracts structured events from utterances, one call per utterance."""

    def __init__(
        self,
        *,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        metrics: Metrics | None = None,
        max_retries: int | None = None,
        timeout_s: float | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        # The model is resolved from config, never named at the call site, so
        # one env var swaps it.
        self.model = model or config.extraction_model()
        self.max_retries = (
            config.EXTRACTION_MAX_RETRIES if max_retries is None else max_retries
        )
        self.timeout_s = (
            config.EXTRACTION_TIMEOUT_S if timeout_s is None else timeout_s
        )
        self.metrics = metrics or Metrics()
        self._log = on_log or log.warning
        self._client = client  # constructed lazily so import never needs a key

        self.calls = 0
        self.retries = 0
        self.degraded = 0
        self.rejected_status_changes = 0
        # A status change naming a hypothesis nobody put on the board. It is
        # accepted - the speaker did say it - but it is a dangling reference
        # until the state machine creates hypotheses on first reference.
        self.unknown_hypothesis_refs = 0

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=config.anthropic_api_key(),
                timeout=self.timeout_s,
            )
        return self._client

    # --- public API --------------------------------------------------------

    def extract(
        self, utterance: Utterance, context: RunningContext | None = None
    ) -> ExtractionOutcome:
        """Classify one utterance. Never raises."""
        context = context or RunningContext()
        started = now_ms()
        self.calls += 1

        fields, attempts, error = self._call_with_retry(utterance, context)
        latency = now_ms() - started
        self.metrics.record(EXTRACT, latency)

        if fields is None:
            self.degraded += 1
            self._log(
                f"extraction failed for turn {utterance.turn_key} "
                f"after {attempts} attempt(s): {error}. Falling back to noise."
            )
            return ExtractionOutcome(
                event=self._noise_event(utterance),
                latency_ms=latency,
                attempts=attempts,
                degraded=True,
                error=error,
            )

        cleaned, rejection = self._validate(fields, utterance, context)
        if rejection is not None:
            self.rejected_status_changes += 1
            self._log(
                f"refused status_change on turn {utterance.turn_key}: "
                f"{rejection} | utterance: {utterance.text[:80]!r}"
            )

        return ExtractionOutcome(
            event=self._build_event(cleaned, utterance),
            latency_ms=latency,
            attempts=attempts,
            raw=fields,
            rejected_status_change=rejection,
        )

    def extract_all(
        self, utterances: Sequence[Utterance], context: RunningContext | None = None
    ) -> list[ExtractionOutcome]:
        return [self.extract(u, context) for u in utterances]

    # --- model call --------------------------------------------------------

    def _call_with_retry(
        self, utterance: Utterance, context: RunningContext
    ) -> tuple[ExtractedFields | None, int, str | None]:
        user_message = render_user_message(
            utterance.speaker_label, utterance.text, context.render()
        )
        last_error: str | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                fields = self._call_once(user_message)
                if fields is not None:
                    return fields, attempt, None
                last_error = "model returned no parsable structured output"
            except FATAL_ERRORS as exc:
                # Retrying will fail the same way. Stop and degrade.
                return None, attempt, f"{type(exc).__name__}: {exc}"
            except RETRYABLE_ERRORS as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 - the pipeline must survive
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt <= self.max_retries:
                self.retries += 1

        return None, self.max_retries + 1, last_error

    def _call_once(self, user_message: str) -> ExtractedFields | None:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=config.EXTRACTION_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM,
                    # Stable across every call in the incident, so it is worth
                    # caching. Silently a no-op if it is under the model's
                    # minimum cacheable length.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_format=ExtractedFields,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        return response.parsed_output

    # --- validation --------------------------------------------------------

    def _validate(
        self,
        fields: ExtractedFields,
        utterance: Utterance,
        context: RunningContext,
    ) -> tuple[ExtractedFields, str | None]:
        """Enforce what the prompt asks for. Returns (cleaned, rejection)."""
        data = fields.model_dump()

        if fields.type == "status_change":
            reason = self._status_change_rejection(fields, utterance)
            if reason is None and not context.has_hypothesis(fields.hypothesis_id):
                # Accepted, not refused: someone can rule out a cause that was
                # never formally proposed, and refusing would lose a real
                # statement. Counted so the dangling reference is visible.
                self.unknown_hypothesis_refs += 1
                self._log(
                    f"status_change on turn {utterance.turn_key} references "
                    f"hypothesis {fields.hypothesis_id!r}, which is not on the "
                    f"board. The state machine must create it on reference."
                )
            if reason is not None:
                # Downgrade rather than drop: the utterance still happened, it
                # just did not change any hypothesis state.
                data.update(
                    type="noise",
                    summary="",
                    hypothesis_id=None,
                    new_state=None,
                    evidence_quote=None,
                )
                return ExtractedFields(**data), reason

        elif fields.type == "hypothesis":
            if not fields.hypothesis_id:
                data["hypothesis_id"] = _slugify(fields.summary or utterance.text)
            data["new_state"] = None

        else:
            # Only hypothesis and status_change may carry these.
            data["hypothesis_id"] = None
            data["new_state"] = None

        if data["type"] == "noise":
            data["summary"] = ""
            data["owner"] = None

        data["evidence_quote"] = (
            fields.evidence_quote if data["type"] == "status_change" else None
        )
        return ExtractedFields(**data), None

    @staticmethod
    def _status_change_rejection(
        fields: ExtractedFields, utterance: Utterance
    ) -> str | None:
        if not fields.new_state:
            return "no new_state given"
        if not fields.hypothesis_id:
            return "no hypothesis_id given"
        if not quote_is_grounded(fields.evidence_quote, utterance.text):
            return (
                "evidence quote is not in the utterance "
                f"(claimed {fields.evidence_quote!r})"
            )
        return None

    # --- event construction ------------------------------------------------

    def _build_event(self, fields: ExtractedFields, utterance: Utterance) -> Event:
        return Event(
            event_id=str(uuid.uuid4()),
            type=EventType(fields.type),
            connection_epoch=utterance.connection_epoch,
            turn_order=utterance.turn_order,
            speaker_label=utterance.speaker_label,
            speaker_name=utterance.speaker_name,
            timestamp_ms=utterance.start_ms,
            text=utterance.text,
            summary=fields.summary or "",
            hypothesis_id=fields.hypothesis_id,
            new_state=fields.new_state,
            owner=fields.owner,
            confidence=fields.confidence,
            previous_speaker_label=None,
        )

    def _noise_event(self, utterance: Utterance) -> Event:
        """The fallback. Always valid, always cheap, never wrong about state."""
        return Event(
            event_id=str(uuid.uuid4()),
            type=EventType.NOISE,
            connection_epoch=utterance.connection_epoch,
            turn_order=utterance.turn_order,
            speaker_label=utterance.speaker_label,
            speaker_name=utterance.speaker_name,
            timestamp_ms=utterance.start_ms,
            text=utterance.text,
            summary="",
            hypothesis_id=None,
            new_state=None,
            owner=None,
            confidence=0.0,
            previous_speaker_label=None,
        )

    # --- reporting ---------------------------------------------------------

    def report(self) -> str:
        m = self.metrics
        return (
            f"extraction: {self.calls} calls, {self.retries} retries, "
            f"{self.degraded} degraded to noise, "
            f"{self.rejected_status_changes} status_changes refused, "
            f"{self.unknown_hypothesis_refs} unknown-hypothesis refs | "
            f"p50={m.p50(EXTRACT):.0f}ms p95={m.p95(EXTRACT):.0f}ms"
        )
