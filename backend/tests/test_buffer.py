"""Utterance grouping and speaker revision.

stdlib unittest, not pytest - pytest would be a dependency beyond the four
that were approved, and this needs none.
"""

from __future__ import annotations

import unittest

from backend.state.models import TurnKey
from backend.transcription.buffer import FinalTurn, UtteranceBuffer


def k(order, epoch=0):
    return TurnKey(epoch, order)


def turn(order, speaker, text, start, end, formatted=True, epoch=0):
    return FinalTurn(
        connection_epoch=epoch,
        turn_order=order,
        speaker_label=speaker,
        text=text,
        start_ms=start,
        end_ms=end,
        is_formatted=formatted,
    )


class TestGrouping(unittest.TestCase):
    def test_consecutive_same_speaker_turns_merge(self):
        b = UtteranceBuffer()
        self.assertEqual(b.add(turn(0, "A", "The API is down.", 0, 1000)), [])
        self.assertEqual(b.add(turn(1, "A", "Started around ten.", 1200, 2000)), [])
        done = b.flush()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].text, "The API is down. Started around ten.")
        self.assertEqual(done[0].turn_keys, [k(0), k(1)])
        self.assertEqual(done[0].turn_key, k(0))
        self.assertEqual(done[0].start_ms, 0)
        self.assertEqual(done[0].end_ms, 2000)

    def test_speaker_change_flushes(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "The API is down.", 0, 1000))
        flushed = b.add(turn(1, "B", "Checking the load balancer.", 1100, 2000))
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].speaker_label, "A")
        rest = b.flush()
        self.assertEqual(rest[0].speaker_label, "B")

    def test_pause_flushes_even_for_same_speaker(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "Rolling back.", 0, 1000))
        flushed = b.add(turn(1, "A", "Rollback is done.", 2600, 3400))
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].text, "Rolling back.")

    def test_pause_boundary_is_inclusive(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "One.", 0, 1000))
        self.assertEqual(len(b.add(turn(1, "A", "Two.", 2500, 3000))), 1)

        b2 = UtteranceBuffer()
        b2.add(turn(0, "A", "One.", 0, 1000))
        self.assertEqual(b2.add(turn(1, "A", "Two.", 2499, 3000)), [])

    def test_tick_flushes_on_trailing_silence(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "Anyone looking at this?", 0, 1000))
        self.assertEqual(b.tick(2000), [])
        self.assertEqual(len(b.tick(2600)), 1)
        self.assertEqual(b.tick(9000), [])

    def test_unformatted_turns_are_not_grouped(self):
        b = UtteranceBuffer()
        self.assertEqual(b.add(turn(0, "A", "the api is down", 0, 1000, formatted=False)), [])
        self.assertEqual(b.dropped_unformatted, 1)
        self.assertEqual(b.flush(), [])

    def test_empty_turns_are_ignored(self):
        b = UtteranceBuffer()
        self.assertEqual(b.add(turn(0, "A", "   ", 0, 500)), [])
        self.assertEqual(b.flush(), [])


class TestConnectionEpoch(unittest.TestCase):
    """The bug this guards against is silent and reattributes speech."""

    def test_same_turn_number_in_two_epochs_is_two_turns(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "First connection.", 0, 900, epoch=0))
        b.flush()
        b.add(turn(0, "B", "Second connection.", 0, 900, epoch=1))
        b.flush()

        first = b.utterance_for_turn(k(0, epoch=0))
        second = b.utterance_for_turn(k(0, epoch=1))
        self.assertIsNot(first, second)
        self.assertEqual(first.text, "First connection.")
        self.assertEqual(second.text, "Second connection.")

    def test_revision_in_one_epoch_leaves_the_other_alone(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "First connection.", 0, 900, epoch=0))
        b.flush()
        b.add(turn(0, "A", "Second connection.", 0, 900, epoch=1))
        b.flush()

        b.apply_revision(k(0, epoch=1), "C")
        self.assertEqual(b.utterance_for_turn(k(0, epoch=0)).speaker_label, "A")
        self.assertEqual(b.utterance_for_turn(k(0, epoch=1)).speaker_label, "C")

    def test_a_reconnect_always_ends_the_open_utterance(self):
        # Same speaker either side, and timestamps that restart at zero would
        # otherwise compute a negative pause and merge the two.
        b = UtteranceBuffer()
        b.add(turn(7, "A", "Before the drop.", 30_000, 31_000, epoch=0))
        flushed = b.add(turn(0, "A", "After the drop.", 0, 800, epoch=1))
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].text, "Before the drop.")
        self.assertEqual(b.flush()[0].text, "After the drop.")

    def test_utterance_exposes_its_epoch(self):
        b = UtteranceBuffer()
        b.add(turn(4, "A", "Hello.", 0, 900, epoch=3))
        (utt,) = b.flush()
        self.assertEqual(utt.connection_epoch, 3)
        self.assertEqual(utt.turn_order, 4)
        self.assertEqual(str(utt.turn_key), "e3/t4")


class TestRevision(unittest.TestCase):
    def test_revision_amends_an_emitted_utterance(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        (utt,) = b.flush()

        amendment = b.apply_revision(k(0), "B")
        self.assertIsNotNone(amendment)
        self.assertTrue(amendment.changed)
        self.assertFalse(amendment.partial)
        self.assertEqual(amendment.previous_label, "A")
        self.assertEqual(amendment.turn_key, k(0))
        self.assertEqual(utt.speaker_label, "B")
        self.assertEqual(utt.previous_speaker_label, "A")
        self.assertTrue(utt.amended)

    def test_revision_finds_a_still_open_utterance(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        self.assertIsNotNone(b.apply_revision(k(0), "B"))
        (utt,) = b.flush()
        self.assertEqual(utt.speaker_label, "B")

    def test_revision_of_a_merged_utterance_is_flagged_partial(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        b.add(turn(1, "A", "Or maybe the CDN.", 1100, 2000))
        b.flush()
        amendment = b.apply_revision(k(1), "B")
        self.assertTrue(amendment.partial)
        self.assertTrue(amendment.changed)

    def test_unknown_turn_returns_none(self):
        self.assertIsNone(UtteranceBuffer().apply_revision(k(99), "B"))

    def test_restated_label_is_reported_but_not_a_change(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        b.flush()
        amendment = b.apply_revision(k(0), "A")
        self.assertIsNotNone(amendment)
        self.assertFalse(amendment.changed)

    def test_apply_revisions_batch(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "One.", 0, 900))
        b.add(turn(1, "B", "Two.", 2000, 2900))
        b.flush()
        out = b.apply_revisions([(k(0), "C"), (k(1), "C"), (k(42), "Z")])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(a.changed for a in out))


if __name__ == "__main__":
    unittest.main()
