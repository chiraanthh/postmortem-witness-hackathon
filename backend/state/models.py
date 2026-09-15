"""Pydantic mirrors of shared/schema.json v1.3.0.

shared/schema.json is the contract and this file follows it. If the two ever
disagree, the schema wins and this file is the bug.

`TurnKey` lives here because it is the one identifier every layer needs, and
it is a pair for a reason - see "The turn key is a pair" in CLAUDE.md.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "shared" / "schema.json"
SCHEMA_VERSION = "1.3.0"


class TurnKey(NamedTuple):
    """Identifies one ASR turn, unambiguously.

    `turn_order` is assigned by the server and restarts at 0 on every new
    connection, so it means nothing on its own. Everything downstream keys on
    this pair - dict keys, revision lookups, event joins. Never on the bare
    turn number.
    """

    connection_epoch: int
    turn_order: int

    def __str__(self) -> str:
        return f"e{self.connection_epoch}/t{self.turn_order}"


class EventType(str, Enum):
    ACTION = "action"
    HYPOTHESIS = "hypothesis"
    STATUS_CHANGE = "status_change"
    THREAD = "thread"
    # An explicit spoken declaration that the incident is over. About the
    # incident, not about a hypothesis, so it carries no hypothesis_id.
    RESOLUTION = "resolution"
    NOISE = "noise"
    # Emitted by us when the ASR reassigns a speaker. The extractor must never
    # produce this one, which is why ExtractedFields below excludes it.
    SPEAKER_AMENDED = "speaker_amended"


class HypothesisState(str, Enum):
    OPEN = "open"
    RULED_OUT = "ruled_out"
    CONFIRMED = "confirmed"


class Event(BaseModel):
    """One event on the incident timeline. Mirrors $defs.Event exactly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    event_id: str
    type: EventType
    connection_epoch: int = Field(ge=0)
    turn_order: int = Field(ge=0)
    speaker_label: str
    speaker_name: str | None = None
    timestamp_ms: int = Field(ge=0)
    text: str
    summary: str
    hypothesis_id: str | None = None
    new_state: HypothesisState | None = None
    owner: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    previous_speaker_label: str | None = None

    @property
    def turn_key(self) -> TurnKey:
        return TurnKey(self.connection_epoch, self.turn_order)

    @model_validator(mode="after")
    def _check_conditionals(self) -> Event:
        # Mirrors the allOf/if-then blocks in the schema.
        if self.type == EventType.STATUS_CHANGE.value:
            if not self.hypothesis_id:
                raise ValueError("status_change requires hypothesis_id")
            if not self.new_state:
                raise ValueError("status_change requires new_state")
        if self.type == EventType.SPEAKER_AMENDED.value:
            if not self.previous_speaker_label:
                raise ValueError("speaker_amended requires previous_speaker_label")
        return self


# --- what the model is actually asked for ----------------------------------

# The extractor never chooses these: we already know them. Asking the model to
# echo an event_id or a timestamp is a way to get a hallucinated one back.
ExtractableType = Literal[
    "action", "hypothesis", "status_change", "thread", "resolution", "noise"
]


class ExtractedFields(BaseModel):
    """The judgement calls, and only those. Everything else we fill in.

    This is the schema handed to the model, so every description in it is
    prompt text. Keep them short and behavioural.
    """

    model_config = ConfigDict(extra="forbid")

    type: ExtractableType = Field(
        description=(
            "What kind of event this is. Use 'noise' unless it is clearly one "
            "of the others. Most utterances on an incident call are noise."
        )
    )
    # Deliberately has no default, which makes it `required` in the JSON
    # schema handed to the model. With a default it was optional, and once
    # ExtractedFields moved inside a list the model started omitting it -
    # every status_change came back with a blank summary, which is a blank row
    # in the timeline. Required costs a few tokens on noise events and is
    # worth it.
    summary: str = Field(
        description=(
            "Short normalized phrasing for a timeline, under 12 words, e.g. "
            "'Ruled out DNS' or 'Rolled back deploy 4c21f'. REQUIRED and must "
            "be non-empty for every type except noise - it is the only thing "
            "the timeline displays. Empty string for noise, and only noise."
        ),
    )
    hypothesis_id: str | None = Field(
        default=None,
        description=(
            "Short kebab-case slug naming the proposed cause, e.g. "
            "'cache-eviction'. Set on hypothesis and status_change only. "
            "On status_change it must match a hypothesis already on the "
            "board, given in the context."
        ),
    )
    new_state: HypothesisState | None = Field(
        default=None,
        description=(
            "Only on status_change. The state the speaker explicitly said the "
            "hypothesis has moved to."
        ),
    )
    owner: str | None = Field(
        default=None,
        description=(
            "Person who owns the action or thread, if a name was actually "
            "spoken. Null otherwise. Never guess."
        ),
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="How confident you are in this classification.",
    )
    evidence_quote: str | None = Field(
        default=None,
        description=(
            "REQUIRED for status_change: the exact words from the utterance, "
            "copied verbatim, in which the speaker states the change. If you "
            "cannot quote it word for word, it was not said, and the type is "
            "not status_change. Null for every other type."
        ),
    )


class ExtractedEvents(BaseModel):
    """What one utterance yielded. The model's whole reply.

    A list, because a single spoken line can be two things at once - "yeah, it
    was the deploy, I'll revert it properly" is a status_change and an action.
    Returning one event per utterance silently dropped the second.
    """

    model_config = ConfigDict(extra="forbid")

    events: list[ExtractedFields] = Field(
        default_factory=list,
        description=(
            "One entry per distinct thing this utterance does. Usually "
            "exactly one, and usually noise. Use two only when the utterance "
            "genuinely does two separable things - do not split one statement "
            "into pieces to look thorough. Never mix 'noise' with another "
            "type in the same list: if anything real happened, it is not "
            "noise."
        ),
    )


def _normalize(text: str) -> str:
    """Loose comparison form for checking a quote really came from the text."""
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def quote_is_grounded(quote: str | None, utterance: str) -> bool:
    """True when `quote` genuinely appears in `utterance`.

    The guard behind the "never invent a state change" rule. A model that has
    to quote the words before it may claim a hypothesis was ruled out cannot
    rule one out by vibes - and this checks the quote instead of trusting it.
    """
    if not quote or not quote.strip():
        return False
    needle = _normalize(quote)
    haystack = _normalize(utterance)
    if not needle:
        return False
    if needle in haystack:
        return True
    # Tolerate light paraphrase at the edges (a dropped filler word) by
    # requiring most of the quoted words to appear, in order.
    needle_words = needle.split()
    if len(needle_words) < 3:
        return False
    hay_words = haystack.split()
    i = 0
    matched = 0
    for w in needle_words:
        while i < len(hay_words) and hay_words[i] != w:
            i += 1
        if i < len(hay_words):
            matched += 1
            i += 1
    return matched / len(needle_words) >= 0.8


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())
