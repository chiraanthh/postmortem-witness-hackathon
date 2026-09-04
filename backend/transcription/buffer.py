"""Utterance assembly.

The ASR emits turns. A turn is not an utterance: one person saying one thing
often arrives as two or three consecutive turns split by breathing. This groups
consecutive turns from the same speaker into a single utterance, flushing when
the speaker changes or when the audio goes quiet for longer than PAUSE_MS.

Only formatted finals are grouped — the unformatted pass exists to be timed,
not to be read (see "Known API behaviours" in CLAUDE.md).

Every utterance carries the turn_orders it was built from, because diarization
is revisable: the API can come back later and say turn 14 was actually speaker
B. `apply_revision` finds the affected utterance by turn_order and amends it.
Amendments are returned, never swallowed: the caller is expected to surface
them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

# Flush an utterance when the speaker goes quiet for this long. Measured in
# audio time, not wall clock, so a replay behaves exactly like a live call.
PAUSE_MS = 1500


@dataclass(frozen=True)
class FinalTurn:
    """A finalized turn from the ASR, decoupled from the vendor SDK."""

    turn_order: int
    speaker_label: str
    text: str
    start_ms: int
    end_ms: int
    is_formatted: bool


@dataclass
class Utterance:
    """One person saying one thing.

    `turn_order` is the first turn that went into it and acts as the
    utterance's primary key. `turn_orders` is the full membership, which is
    what a revision has to be matched against — a revision names a single turn,
    and that turn may sit in the middle of a merged utterance.
    """

    turn_order: int
    turn_orders: list[int]
    speaker_label: str
    start_ms: int
    end_ms: int
    text: str

    # Hook for the roll-call pass that will map labels to real names. Nothing
    # populates this yet, deliberately.
    speaker_name: str | None = None

    # Set once a speaker revision has touched this utterance.
    amended: bool = False
    previous_speaker_label: str | None = None

    @property
    def display_speaker(self) -> str:
        return self.speaker_name or self.speaker_label

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class Amendment:
    """A retroactive speaker correction applied to an already-emitted utterance."""

    utterance: Utterance
    turn_order: int
    previous_label: str | None
    new_label: str | None

    # True when the revision changed only some of the turns in a merged
    # utterance. That means the grouping itself was wrong, not just the label,
    # and the utterance really ought to be split. We do not split it — we say
    # so, loudly, and let a human judge whether it matters.
    partial: bool = False

    @property
    def changed(self) -> bool:
        return self.previous_label != self.new_label


@dataclass
class UtteranceBuffer:
    """Groups turns into utterances and keeps them addressable by turn_order.

    The index of already-emitted utterances is a stand-in for the state machine
    that will own this later. It lives here so the spike can demonstrate
    amendment end to end without one.
    """

    pause_ms: int = PAUSE_MS

    _open: Utterance | None = field(default=None, init=False)
    _by_turn: dict[int, Utterance] = field(default_factory=dict, init=False)
    _emitted: list[Utterance] = field(default_factory=list, init=False)
    dropped_unformatted: int = field(default=0, init=False)

    # --- ingest ------------------------------------------------------------

    def add(self, turn: FinalTurn) -> list[Utterance]:
        """Feed one finalized turn. Returns any utterances it completed."""
        if not turn.is_formatted:
            # Timed elsewhere, never read. Extraction runs on formatted text.
            self.dropped_unformatted += 1
            return []

        if not turn.text.strip():
            return []

        flushed: list[Utterance] = []
        current = self._open

        if current is not None and self._breaks(current, turn):
            flushed.append(self._close())
            current = None

        if current is None:
            self._open = Utterance(
                turn_order=turn.turn_order,
                turn_orders=[turn.turn_order],
                speaker_label=turn.speaker_label,
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                text=turn.text.strip(),
            )
        else:
            current.turn_orders.append(turn.turn_order)
            current.end_ms = turn.end_ms
            current.text = f"{current.text} {turn.text.strip()}".strip()

        # Index every constituent turn immediately, so a revision that arrives
        # before the utterance is flushed still finds it.
        assert self._open is not None
        self._by_turn[turn.turn_order] = self._open
        return flushed

    def _breaks(self, current: Utterance, turn: FinalTurn) -> bool:
        if turn.speaker_label != current.speaker_label:
            return True
        return (turn.start_ms - current.end_ms) >= self.pause_ms

    def tick(self, audio_now_ms: int) -> list[Utterance]:
        """Flush on silence when no further turn has arrived to reveal the gap."""
        if self._open is None:
            return []
        if audio_now_ms - self._open.end_ms >= self.pause_ms:
            return [self._close()]
        return []

    def flush(self) -> list[Utterance]:
        """Close whatever is open. Call at end of stream."""
        return [] if self._open is None else [self._close()]

    def _close(self) -> Utterance:
        assert self._open is not None
        done, self._open = self._open, None
        self._emitted.append(done)
        return done

    # --- revision ----------------------------------------------------------

    def apply_revision(self, turn_order: int, new_label: str | None) -> Amendment | None:
        """Apply a retroactive speaker reassignment for one turn.

        Returns the amendment, or None if we have never seen that turn — which
        happens legitimately when a revision names a turn that carried no
        formatted text.
        """
        utterance = self._by_turn.get(turn_order)
        if utterance is None:
            return None

        previous = utterance.speaker_label
        partial = len(utterance.turn_orders) > 1

        if new_label is None or new_label == previous:
            return Amendment(
                utterance=utterance,
                turn_order=turn_order,
                previous_label=previous,
                new_label=new_label,
                partial=partial,
            )

        utterance.speaker_label = new_label
        utterance.previous_speaker_label = previous
        utterance.amended = True

        return Amendment(
            utterance=utterance,
            turn_order=turn_order,
            previous_label=previous,
            new_label=new_label,
            partial=partial,
        )

    def apply_revisions(
        self, items: Iterable[tuple[int, str | None]]
    ) -> list[Amendment]:
        out = []
        for turn_order, label in items:
            amendment = self.apply_revision(turn_order, label)
            if amendment is not None:
                out.append(amendment)
        return out

    # --- introspection -----------------------------------------------------

    @property
    def emitted(self) -> list[Utterance]:
        return list(self._emitted)

    def utterance_for_turn(self, turn_order: int) -> Utterance | None:
        return self._by_turn.get(turn_order)
