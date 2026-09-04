"""Event handling, driven with real SDK event objects and no network.

These are the parts that cannot be checked by staring at the docs: that a turn
finalizing twice is timed once and read once, that a reconnect does not alias
turn numbering, and that a speaker revision reaches the utterance it belongs to
with a delay attached.
"""

from __future__ import annotations

import time
import unittest

from assemblyai.streaming.v3 import (
    SpeakerRevisionEvent,
    SpeakerRevisionItem,
    TurnEvent,
    Word,
)

from backend.metrics import ASR, FORMAT
from backend.transcription.stream import TranscriptionStream, UNKNOWN_SPEAKER


def word(text, start, end, speaker=None):
    return Word(
        text=text, start=start, end=end, confidence=0.99,
        word_is_final=True, speaker=speaker,
    )


def turn_event(order, transcript, *, formatted, speaker=None, words=None,
               end_of_turn=True):
    return TurnEvent(
        type="Turn",
        turn_order=order,
        turn_is_formatted=formatted,
        end_of_turn=end_of_turn,
        transcript=transcript,
        end_of_turn_confidence=0.9,
        words=words if words is not None else [word(transcript, 0, 1000)],
        speaker_label=speaker,
    )


class Harness(unittest.TestCase):
    """A stream with no socket behind it."""

    def setUp(self):
        self.utterances = []
        self.amendments = []
        self.turns = []
        self.stream = TranscriptionStream(
            chunks=iter([]),
            api_key="test-key-not-used",
            on_utterance=self.utterances.append,
            on_amendment=lambda a, d: self.amendments.append((a, d)),
            on_turn=lambda t, l: self.turns.append((t, l)),
        )
        # Pretend we streamed 10s of audio, one 50 ms chunk at a time.
        for i in range(1, 201):
            self.stream.clock.record_chunk(i * 50)

    def fire(self, event):
        self.stream._handle_turn(None, event)

    def revise(self, *items):
        self.stream._handle_revision(
            None,
            SpeakerRevisionEvent(
                revisions=[
                    SpeakerRevisionItem(turn_order=t, speaker_label=s, words=[])
                    for t, s in items
                ]
            ),
        )


class TestTwoPhaseFinal(Harness):
    def test_raw_final_is_timed_formatted_final_is_read(self):
        self.fire(turn_event(0, "the api is down", formatted=False, speaker="A"))
        self.assertEqual(self.stream.metrics.count(ASR), 1)
        self.assertEqual(self.stream.buffer.dropped_unformatted, 1)
        self.assertEqual(self.utterances, [])

        self.fire(turn_event(0, "The API is down.", formatted=True, speaker="A"))
        # Still open until something ends it, but the formatted text is in.
        (utt,) = self.stream.buffer.flush()
        self.assertEqual(utt.text, "The API is down.")
        # Timed once, not twice.
        self.assertEqual(self.stream.metrics.count(ASR), 1)
        self.assertEqual(self.stream.turns_seen, 1)

    def test_format_phase_is_timed_separately(self):
        self.fire(turn_event(1, "raw", formatted=False, speaker="A"))
        time.sleep(0.02)
        self.fire(turn_event(1, "Raw.", formatted=True, speaker="A"))
        self.assertEqual(self.stream.metrics.count(FORMAT), 1)
        self.assertGreater(self.stream.metrics.p50(FORMAT), 0)

    def test_partial_turns_are_ignored(self):
        self.fire(turn_event(0, "the api", formatted=False, end_of_turn=False))
        self.assertEqual(self.turns, [])
        self.assertEqual(self.stream.metrics.count(ASR), 0)

    def test_asr_latency_is_positive_and_bounded(self):
        self.fire(turn_event(0, "hello", formatted=False, speaker="A",
                             words=[word("hello", 0, 5000)]))
        (_, latency), = self.turns
        self.assertIsNotNone(latency)
        self.assertGreaterEqual(latency, 0.0)


class TestSpeakerLabels(Harness):
    def test_falls_back_to_word_level_speaker(self):
        self.fire(turn_event(0, "It is the cache.", formatted=True, speaker=None,
                             words=[word("It", 0, 100, speaker="C"),
                                    word("cache", 100, 400, speaker="C")]))
        (utt,) = self.stream.buffer.flush()
        self.assertEqual(utt.speaker_label, "C")

    def test_unknown_speaker_when_diarization_returns_nothing(self):
        self.fire(turn_event(0, "Anyone there?", formatted=True, speaker=None,
                             words=[word("Anyone", 0, 100)]))
        (utt,) = self.stream.buffer.flush()
        self.assertEqual(utt.speaker_label, UNKNOWN_SPEAKER)


class TestRevision(Harness):
    def test_revision_amends_and_reports_delay(self):
        self.fire(turn_event(0, "raw", formatted=False, speaker="A"))
        self.fire(turn_event(0, "It is the cache.", formatted=True, speaker="A"))
        self.fire(turn_event(1, "raw two", formatted=False, speaker="B"))
        self.fire(turn_event(1, "Checking now.", formatted=True, speaker="B"))
        self.assertEqual(len(self.utterances), 1)   # speaker change flushed A

        time.sleep(0.03)
        self.revise((0, "B"))

        self.assertEqual(len(self.amendments), 1)
        amendment, delay = self.amendments[0]
        self.assertEqual(amendment.previous_label, "A")
        self.assertEqual(amendment.new_label, "B")
        self.assertTrue(amendment.changed)
        self.assertGreater(delay, 0.0)
        self.assertEqual(self.utterances[0].speaker_label, "B")
        self.assertTrue(self.utterances[0].amended)
        self.assertEqual(self.stream.revisions.total, 1)

    def test_revision_for_a_turn_we_never_saw_is_survivable(self):
        self.revise((404, "B"))
        self.assertEqual(self.amendments, [])
        self.assertEqual(self.stream.revisions.total, 1)

    def test_revision_carries_the_quoted_text(self):
        self.fire(turn_event(0, "raw", formatted=False, speaker="A"))
        self.fire(turn_event(0, "It is the cache.", formatted=True, speaker="A"))
        self.revise((0, "B"))
        (rev,) = self.stream.revisions.changed
        self.assertEqual(rev.text, "It is the cache.")


class TestReconnectNumbering(Harness):
    """The failure this guards against is silent and destroys attribution."""

    def test_turn_order_does_not_alias_across_a_reconnect(self):
        self.fire(turn_event(0, "raw a", formatted=False, speaker="A"))
        self.fire(turn_event(0, "First thing.", formatted=True, speaker="A"))
        self.fire(turn_event(1, "raw b", formatted=False, speaker="B"))
        self.fire(turn_event(1, "Second thing.", formatted=True, speaker="B"))

        # A reconnect: the server starts counting turns from zero again.
        self.stream._session.turn_offset = self.stream._highest_turn + 1
        self.stream._session.audio_base_ms = 4000

        self.fire(turn_event(0, "raw c", formatted=False, speaker="A"))
        self.fire(turn_event(0, "Third thing.", formatted=True, speaker="A"))

        orders = [t.turn_order for t, _ in self.turns]
        self.assertEqual(orders, [0, 0, 1, 1, 2, 2])

        third = self.stream.buffer.utterance_for_turn(2)
        self.assertIsNotNone(third)
        self.assertEqual(third.text, "Third thing.")
        # And it is a different utterance than turn 0's.
        self.assertIsNot(third, self.stream.buffer.utterance_for_turn(0))

    def test_timestamps_are_rebased_after_a_reconnect(self):
        self.stream._session.turn_offset = 5
        self.stream._session.audio_base_ms = 30_000
        self.fire(turn_event(0, "Later on.", formatted=True, speaker="A",
                             words=[word("Later", 100, 900)]))
        utt = self.stream.buffer.utterance_for_turn(5)
        self.assertEqual(utt.start_ms, 30_100)
        self.assertEqual(utt.end_ms, 30_900)

    def test_revision_after_reconnect_targets_the_right_turn(self):
        self.fire(turn_event(0, "raw a", formatted=False, speaker="A"))
        self.fire(turn_event(0, "First thing.", formatted=True, speaker="A"))
        self.stream._session.turn_offset = self.stream._highest_turn + 1

        self.fire(turn_event(0, "raw b", formatted=False, speaker="B"))
        self.fire(turn_event(0, "Second thing.", formatted=True, speaker="B"))
        # Session-local turn 0 in session 2 is global turn 1.
        self.revise((0, "C"))

        (amendment, _), = self.amendments
        self.assertEqual(amendment.turn_order, 1)
        self.assertEqual(amendment.utterance.text, "Second thing.")
        self.assertEqual(
            self.stream.buffer.utterance_for_turn(0).speaker_label, "A"
        )


if __name__ == "__main__":
    unittest.main()
