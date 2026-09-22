"""Turns one formatted utterance into schema-valid events.

Four rules shape this file:

- **Schema-valid or nothing.** The model is given a JSON schema through the
  provider's structured output support, so it cannot reply in prose. We never
  ask for JSON in a prompt and parse it out of text.
- **noise is the default answer,** not a failure. Most of an incident call is
  noise. If anything goes wrong - malformed output, a timeout, a rejected
  status change - the utterance becomes noise and the pipeline keeps moving.
  This worker never raises into the pipeline.
- **A status change must have been spoken.** The model has to quote the words
  verbatim, and `quote_is_grounded` checks the quote against the utterance
  (raw and cleaned text when cleanup is on). A quote it cannot produce is a
  status change that did not happen.
- **One utterance, many events** (contract v1.3.0). "Yeah, it was the deploy,
  I'll revert it properly" is a status_change *and* an action. Returning one
  event per utterance dropped the action with no trace, so `extract` now
  returns a list. Empty and single-element lists are both valid; a degraded
  call still returns exactly one noise event, because "the model saw nothing"
  and "the call failed" must not look the same.

Providers only call LLMs. Grounding and validation stay here, identical for
every backend. There is no silent fallback between providers.
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
from backend.extraction.providers import ProviderError
from backend.extraction.providers.anthropic_direct import AnthropicDirect
from backend.extraction.providers.factory import make_provider
from backend.metrics import EXTRACT, Metrics, now_ms
from backend.state.models import (
    Event,
    EventType,
    ExtractedEvents,
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
    ProviderError,
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
    threads: tuple[tuple[str, str], ...] = ()           # (id, text) — id must
    # match answers_thread_id when the model links an answer.

    def render(self) -> str:
        return render_context(
            self.hypotheses,
            self.threads,
            max_hypotheses=config.EXTRACTION_MAX_HYPOTHESES,
            max_threads=config.EXTRACTION_MAX_THREADS,
        )

    def has_hypothesis(self, hypothesis_id: str | None) -> bool:
        return any(h[0] == hypothesis_id for h in self.hypotheses)

    def has_open_thread(self, thread_id: str | None) -> bool:
        return bool(thread_id) and any(t[0] == thread_id for t in self.threads)


@dataclass(frozen=True)
class Rejection:
    """A claimed status_change that was refused, and what was claimed.

    Kept in full rather than counted, because the interesting thing is not how
    often the model tries to invent a state change but what it tries to invent.
    """

    reason: str
    claimed_hypothesis_id: str | None = None
    claimed_new_state: str | None = None
    claimed_quote: str | None = None


@dataclass
class ExtractionOutcome:
    """One extraction, and everything worth knowing about how it went."""

    events: list[Event]
    latency_ms: float
    attempts: int
    raw: ExtractedEvents | None = None

    # Every claimed status_change that was refused. This is the list that
    # matters: it is the model trying to invent a state change.
    rejections: list[Rejection] = field(default_factory=list)

    # Set when we fell back to noise because the call itself failed.
    degraded: bool = False
    error: str | None = None

    # Which backend answered (for the model picker / provenance).
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None

    @property
    def is_noise(self) -> bool:
        """True when nothing of substance came out of the utterance."""
        return all(e.type == EventType.NOISE.value for e in self.events)

    @property
    def substantive(self) -> list[Event]:
        return [e for e in self.events if e.type != EventType.NOISE.value]


def _slugify(text: str, fallback: str = "hypothesis") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = "-".join(slug.split("-")[:4])
    return slug or fallback


def _first_words(text: str, n: int) -> str:
    """Opening words of an utterance, for use as a last-resort summary."""
    words = text.split()
    head = " ".join(words[:n])
    return f"{head}..." if len(words) > n else head


def grounding_texts(utterance: Utterance) -> tuple[str, ...]:
    """Texts the evidence quote may match — raw and cleaned when both exist."""
    texts = [utterance.text]
    cleaned = getattr(utterance, "cleaned_text", None)
    if cleaned and cleaned.strip() and cleaned.strip() != utterance.text.strip():
        texts.append(cleaned)
    return tuple(texts)


def quote_grounded_against_utterance(
    quote: str | None, utterance: Utterance
) -> bool:
    """True when the quote appears in raw or cleaned utterance text."""
    return any(quote_is_grounded(quote, t) for t in grounding_texts(utterance))


class ExtractionWorker:
    """Extracts structured events from utterances, one call per utterance."""

    def __init__(
        self,
        *,
        client: anthropic.Anthropic | None = None,
        provider: object | None = None,
        model: str | None = None,
        metrics: Metrics | None = None,
        max_retries: int | None = None,
        timeout_s: float | None = None,
        on_log: Callable[[str], None] | None = None,
        on_provider_error: Callable[[str], None] | None = None,
    ) -> None:
        self.max_retries = (
            config.EXTRACTION_MAX_RETRIES if max_retries is None else max_retries
        )
        self.timeout_s = (
            config.EXTRACTION_TIMEOUT_S if timeout_s is None else timeout_s
        )
        self.metrics = metrics or Metrics()
        self._log = on_log or log.warning
        self._on_provider_error = on_provider_error
        self._cleanup = None  # set lazily; optional stage

        if provider is not None:
            self.provider = provider
        elif client is not None:
            # Back-compat for tests that inject an Anthropic client.
            self.provider = AnthropicDirect(
                model=model or config.extraction_model(),
                client=client,
                timeout_s=self.timeout_s,
            )
        else:
            self.provider = make_provider(
                model=model,
                timeout_s=self.timeout_s,
            )

        self.calls = 0
        self.retries = 0
        self.degraded = 0
        self.rejected_status_changes = 0
        self.events_emitted = 0
        self.multi_event_utterances = 0
        self.blank_summaries = 0
        self.unknown_hypothesis_refs = 0
        self.provider_errors = 0

    @property
    def model(self) -> str:
        return getattr(self.provider, "model", config.extraction_model())

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "name", config.extraction_provider())

    @property
    def _client(self):
        """Back-compat for tests that inspect the Anthropic fake client."""
        return getattr(self.provider, "client", getattr(self.provider, "_client", None))

    def rebind_provider(
        self, *, provider: str | None = None, model: str | None = None
    ) -> None:
        """Swap backend for subsequent utterances. Does not touch the board."""
        self.provider = make_provider(
            provider=provider,
            model=model,
            timeout_s=self.timeout_s,
        )

    # --- public API --------------------------------------------------------

    def extract(
        self, utterance: Utterance, context: RunningContext | None = None
    ) -> ExtractionOutcome:
        """Classify one utterance into zero or more events. Never raises."""
        context = context or RunningContext()
        self.calls += 1

        # Optional cleanup runs before the provider sees the text.
        # CLEANUP is timed inside cleanup_utterance; EXTRACT is provider-only.
        extract_utt = self._maybe_cleanup(utterance)

        started = now_ms()
        parsed, attempts, error, meta = self._call_with_retry(extract_utt, context)
        latency = now_ms() - started
        self.metrics.record(EXTRACT, latency)

        if parsed is None:
            self.degraded += 1
            self.provider_errors += 1
            msg = (
                f"extraction failed for turn {utterance.turn_key} "
                f"via {self.provider_name}/{self.model} "
                f"after {attempts} attempt(s): {error}. "
                f"Falling back to noise (no provider switch)."
            )
            self._log(msg)
            if self._on_provider_error:
                self._on_provider_error(msg)
            return ExtractionOutcome(
                events=[self._noise_event(utterance)],
                latency_ms=latency,
                attempts=attempts,
                degraded=True,
                error=error,
                provider=self.provider_name,
                model=self.model,
                request_id=meta.get("request_id"),
            )

        events, rejections = self._resolve(parsed, extract_utt, context)
        # Timeline quotes the raw ASR text, never the rewrite.
        events = [
            e.model_copy(update={"text": utterance.text})
            if e.text != utterance.text
            else e
            for e in events
        ]
        return ExtractionOutcome(
            events=events,
            latency_ms=latency,
            attempts=attempts,
            raw=parsed,
            rejections=rejections,
            provider=self.provider_name,
            model=self.model,
            request_id=meta.get("request_id"),
        )

    def extract_all(
        self, utterances: Sequence[Utterance], context: RunningContext | None = None
    ) -> list[ExtractionOutcome]:
        return [self.extract(u, context) for u in utterances]

    def _maybe_cleanup(self, utterance: Utterance) -> Utterance:
        if not config.cleanup_enabled():
            return utterance
        from backend.extraction.cleanup import cleanup_utterance

        return cleanup_utterance(utterance, metrics=self.metrics)

    # --- list assembly -----------------------------------------------------

    def _resolve(
        self,
        parsed: ExtractedEvents,
        utterance: Utterance,
        context: RunningContext,
    ) -> tuple[list[Event], list[Rejection]]:
        """Validate every candidate and assemble the utterance's event list."""
        kept: list[ExtractedFields] = []
        rejections: list[Rejection] = []

        for candidate in parsed.events:
            cleaned, reason = self._validate(candidate, utterance, context)
            if reason is None:
                kept.append(cleaned)
                continue

            self.rejected_status_changes += 1
            rejections.append(Rejection(
                reason=reason,
                claimed_hypothesis_id=candidate.hypothesis_id,
                claimed_new_state=getattr(
                    candidate.new_state, "value", candidate.new_state
                ),
                claimed_quote=candidate.evidence_quote,
            ))
            self._log(
                f"refused status_change on turn {utterance.turn_key}: "
                f"{reason} | utterance: {utterance.text[:80]!r}"
            )
            kept.append(cleaned)

        events = [
            self._build_event(f, utterance) for f in self._normalize(kept)
        ]
        if len(events) > 1:
            self.multi_event_utterances += 1
        self.events_emitted += len(events)
        return events, rejections

    @staticmethod
    def _normalize(kept: list[ExtractedFields]) -> list[ExtractedFields]:
        substantive = [f for f in kept if f.type != "noise"]
        if not substantive:
            return kept[:1]

        seen: set[tuple[str, str | None, str]] = set()
        out: list[ExtractedFields] = []
        for f in substantive:
            key = (f.type, f.hypothesis_id, f.summary.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
        return out

    # --- model call --------------------------------------------------------

    def _call_with_retry(
        self, utterance: Utterance, context: RunningContext
    ) -> tuple[ExtractedEvents | None, int, str | None, dict]:
        # Extraction sees cleaned text when cleanup ran; grounding still
        # checks both via quote_grounded_against_utterance.
        text_for_model = utterance.cleaned_text or utterance.text
        user_message = render_user_message(
            utterance.speaker_label, text_for_model, context.render()
        )
        last_error: str | None = None
        meta: dict = {}

        for attempt in range(1, self.max_retries + 2):
            try:
                result = self.provider.complete(
                    system=SYSTEM, user_message=user_message
                )
                meta = {
                    "request_id": result.request_id,
                    "region": result.region,
                    "model": result.model,
                }
                return result.parsed, attempt, None, meta
            except FATAL_ERRORS as exc:
                return None, attempt, f"{type(exc).__name__}: {exc}", meta
            except RETRYABLE_ERRORS as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, ProviderError):
                    last_error = str(exc)
            except Exception as exc:  # noqa: BLE001 - the pipeline must survive
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt <= self.max_retries:
                self.retries += 1

        return None, self.max_retries + 1, last_error, meta

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
                self.unknown_hypothesis_refs += 1
                self._log(
                    f"status_change on turn {utterance.turn_key} references "
                    f"hypothesis {fields.hypothesis_id!r}, which is not on the "
                    f"board. The state machine must create it on reference."
                )
            if reason is not None:
                data.update(
                    type="noise",
                    summary="",
                    hypothesis_id=None,
                    new_state=None,
                    evidence_quote=None,
                    answers_thread_id=None,
                    claim_subject=None,
                    claim_assertion=None,
                    claim_quote=None,
                )
                return ExtractedFields(**data), reason

        elif fields.type == "hypothesis":
            if not fields.hypothesis_id:
                data["hypothesis_id"] = _slugify(fields.summary or utterance.text)
            data["new_state"] = None

        elif fields.type == "resolution":
            data["hypothesis_id"] = None
            data["new_state"] = None
            data["owner"] = None

        else:
            data["hypothesis_id"] = None
            data["new_state"] = None

        if data["type"] == "noise":
            data["summary"] = ""
            data["owner"] = None
        elif not data["summary"].strip():
            self.blank_summaries += 1
            data["summary"] = _first_words(utterance.text, 12)
            self._log(
                f"blank summary on a {data['type']} event, turn "
                f"{utterance.turn_key}: fell back to the utterance's own words"
            )

        data["evidence_quote"] = (
            fields.evidence_quote if data["type"] == "status_change" else None
        )

        # Silence detector: answers_thread_id must name an open thread from
        # context. Topic similarity guesses are cleared rather than trusted.
        answers_id = data.get("answers_thread_id")
        if answers_id and not context.has_open_thread(answers_id):
            self._log(
                f"cleared answers_thread_id={answers_id!r} on turn "
                f"{utterance.turn_key}: not in open threads"
            )
            data["answers_thread_id"] = None

        # Contradiction claims: never on status_change or thread; otherwise
        # require a complete grounded triple or clear all three.
        if data["type"] in ("status_change", "thread"):
            data["claim_subject"] = None
            data["claim_assertion"] = None
            data["claim_quote"] = None
        else:
            subj = data.get("claim_subject")
            assertion = data.get("claim_assertion")
            quote = data.get("claim_quote")
            complete = bool(
                isinstance(subj, str)
                and subj.strip()
                and isinstance(assertion, str)
                and assertion.strip()
                and isinstance(quote, str)
                and quote.strip()
            )
            if complete and quote_grounded_against_utterance(quote, utterance):
                data["claim_subject"] = subj.strip()
                data["claim_assertion"] = assertion.strip()
                data["claim_quote"] = quote
            else:
                if subj or assertion or quote:
                    self._log(
                        f"cleared claim_* on turn {utterance.turn_key}: "
                        f"incomplete or ungrounded "
                        f"(subject={subj!r} quote={quote!r})"
                    )
                data["claim_subject"] = None
                data["claim_assertion"] = None
                data["claim_quote"] = None

        return ExtractedFields(**data), None

    @staticmethod
    def _status_change_rejection(
        fields: ExtractedFields, utterance: Utterance
    ) -> str | None:
        if not fields.new_state:
            return "no new_state given"
        if not fields.hypothesis_id:
            return "no hypothesis_id given"
        if not quote_grounded_against_utterance(fields.evidence_quote, utterance):
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
            addressee=fields.addressee,
            answers_thread_id=fields.answers_thread_id,
            claim_subject=fields.claim_subject,
            claim_assertion=fields.claim_assertion,
            claim_quote=fields.claim_quote,
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
            addressee=None,
            answers_thread_id=None,
            claim_subject=None,
            claim_assertion=None,
            claim_quote=None,
        )

    # --- reporting ---------------------------------------------------------

    def report(self) -> str:
        m = self.metrics
        return (
            f"extraction[{self.provider_name}/{self.model}]: "
            f"{self.calls} calls -> {self.events_emitted} events "
            f"({self.multi_event_utterances} utterances yielded >1), "
            f"{self.retries} retries, {self.degraded} degraded to noise, "
            f"{self.rejected_status_changes} status_changes refused, "
            f"{self.blank_summaries} blank summaries, "
            f"{self.unknown_hypothesis_refs} unknown-hypothesis refs | "
            f"p50={m.p50(EXTRACT):.0f}ms p95={m.p95(EXTRACT):.0f}ms"
        )
