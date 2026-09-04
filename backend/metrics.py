"""Latency instrumentation.

Latency is a demo feature, not a footnote, so it is measured honestly:

- `AudioClock` records the wall-clock moment each audio chunk was actually
  handed to the socket, indexed by its position in the audio. When a transcript
  comes back describing audio that ended at 12_400 ms, we can ask when that
  exact moment was sent, rather than assuming real-time replay held perfectly.
- `Metrics` keeps raw samples per named stage and reports p50/p95.
- `RevisionLog` tracks speaker revisions and, for each, how long after the
  original turn the correction arrived. That number is the go/no-go on whether
  streaming diarization is usable here.
"""

from __future__ import annotations

import bisect
import math
import time
from dataclasses import dataclass, field

# Stage names. Kept as constants so a typo cannot silently create a new,
# permanently empty stage.
ASR = "asr"           # audio sent -> unformatted final received
FORMAT = "format"     # unformatted final -> formatted final, same turn
EXTRACT = "extract"   # formatted final -> structured event (not yet wired)
E2E = "e2e"           # audio sent -> structured event (not yet wired)


def now_ms() -> float:
    """Monotonic milliseconds. Immune to the wall clock being adjusted."""
    return time.monotonic() * 1000.0


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. No interpolation, no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    rank = max(1, min(len(ordered), rank))
    return ordered[rank - 1]


class AudioClock:
    """Maps a position in the audio to the moment we sent it.

    Chunks are recorded in send order, so the audio-position list is sorted and
    a bisect is enough. Positions are cumulative across reconnects: the clock
    belongs to the audio, not to the socket.
    """

    def __init__(self) -> None:
        self._ends_ms: list[int] = []
        self._sent_at_ms: list[float] = []

    def record_chunk(self, audio_end_ms: int, sent_at_ms: float | None = None) -> None:
        self._ends_ms.append(audio_end_ms)
        self._sent_at_ms.append(now_ms() if sent_at_ms is None else sent_at_ms)

    def sent_at(self, audio_ms: int) -> float | None:
        """When the chunk covering `audio_ms` was sent, or None if not yet."""
        if not self._ends_ms:
            return None
        i = bisect.bisect_left(self._ends_ms, audio_ms)
        if i >= len(self._ends_ms):
            # Transcript describes audio past anything we have sent. Only
            # happens on a clock disagreement; attribute to the last chunk
            # rather than inventing a number.
            i = len(self._ends_ms) - 1
        return self._sent_at_ms[i]

    def latency_ms(self, audio_ms: int, received_at_ms: float | None = None) -> float | None:
        sent = self.sent_at(audio_ms)
        if sent is None:
            return None
        received = now_ms() if received_at_ms is None else received_at_ms
        return max(0.0, received - sent)

    @property
    def audio_sent_ms(self) -> int:
        return self._ends_ms[-1] if self._ends_ms else 0


@dataclass
class Metrics:
    """Raw latency samples per stage, summarised as p50/p95."""

    samples: dict[str, list[float]] = field(default_factory=dict)

    def record(self, stage: str, ms: float) -> None:
        self.samples.setdefault(stage, []).append(ms)

    def count(self, stage: str) -> int:
        return len(self.samples.get(stage, []))

    def p50(self, stage: str) -> float:
        return percentile(self.samples.get(stage, []), 50)

    def p95(self, stage: str) -> float:
        return percentile(self.samples.get(stage, []), 95)

    def summary(self, stage: str) -> str:
        n = self.count(stage)
        if n == 0:
            return f"{stage}: no samples"
        return (
            f"{stage}: n={n} p50={self.p50(stage):.0f}ms p95={self.p95(stage):.0f}ms"
        )

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            stage: {
                "n": float(len(vals)),
                "p50": percentile(vals, 50),
                "p95": percentile(vals, 95),
            }
            for stage, vals in self.samples.items()
        }

    def contract_latency(self) -> dict[str, float]:
        """The three p50 numbers the frontend contract asks for."""
        return {
            "asr_ms": self.p50(ASR),
            "extract_ms": self.p50(EXTRACT),
            "e2e_ms": self.p50(E2E),
        }


@dataclass
class Revision:
    """One retroactive speaker reassignment."""

    turn_order: int
    previous_label: str | None
    new_label: str | None
    delay_ms: float
    # If True, the revision hit only part of a multi-turn utterance, so the
    # grouping the buffer produced was itself wrong — not just the label.
    partial: bool = False
    text: str = ""

    @property
    def changed(self) -> bool:
        return self.previous_label != self.new_label


class RevisionLog:
    """Speaker revisions, and how late each one arrived.

    The API sends no timestamp on a revision, so delay is measured locally:
    when we first saw a final for that turn, against when the correction
    landed. `note_turn` must be called for every final turn, whether or not it
    is ever revised.
    """

    def __init__(self) -> None:
        self._first_seen_ms: dict[int, float] = {}
        self._labels: dict[int, str | None] = {}
        self.revisions: list[Revision] = []

    def note_turn(self, turn_order: int, speaker_label: str | None) -> None:
        self._first_seen_ms.setdefault(turn_order, now_ms())
        self._labels.setdefault(turn_order, speaker_label)

    def current_label(self, turn_order: int) -> str | None:
        return self._labels.get(turn_order)

    def first_seen(self, turn_order: int) -> float | None:
        """When this turn first arrived, for timing the second final."""
        return self._first_seen_ms.get(turn_order)

    def note_revision(
        self,
        turn_order: int,
        new_label: str | None,
        *,
        partial: bool = False,
        text: str = "",
    ) -> Revision:
        previous = self._labels.get(turn_order)
        first_seen = self._first_seen_ms.get(turn_order)
        delay = 0.0 if first_seen is None else max(0.0, now_ms() - first_seen)
        self._labels[turn_order] = new_label
        rev = Revision(
            turn_order=turn_order,
            previous_label=previous,
            new_label=new_label,
            delay_ms=delay,
            partial=partial,
            text=text,
        )
        self.revisions.append(rev)
        return rev

    @property
    def total(self) -> int:
        return len(self.revisions)

    @property
    def changed(self) -> list[Revision]:
        """Revisions that actually changed the label, not restatements."""
        return [r for r in self.revisions if r.changed]

    @property
    def turns_seen(self) -> int:
        return len(self._first_seen_ms)

    def delays(self) -> list[float]:
        return [r.delay_ms for r in self.changed]

    def summary(self) -> str:
        changed = self.changed
        if not self.revisions:
            return f"revisions: 0 received over {self.turns_seen} turns"
        delays = self.delays()
        if not delays:
            return (
                f"revisions: {self.total} received, 0 changed a label "
                f"({self.turns_seen} turns)"
            )
        rate = 100.0 * len(changed) / max(1, self.turns_seen)
        return (
            f"revisions: {self.total} received, {len(changed)} changed a label "
            f"({rate:.0f}% of {self.turns_seen} turns) | "
            f"delay p50={percentile(delays, 50):.0f}ms "
            f"p95={percentile(delays, 95):.0f}ms "
            f"max={max(delays):.0f}ms"
        )
