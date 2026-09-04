"""Utterance grouping and speaker revision.

stdlib unittest, not pytest — pytest would be a dependency beyond the four
that were approved, and this needs none.
"""

from __future__ import annotations

import unittest

from backend.transcription.buffer import FinalTurn, UtteranceBuffer


def turn(order, speaker, text, start, end, formatted=True):
    return FinalTurn(
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
        self.assertEqual(done[0].turn_orders, [0, 1])
        self.assertEqual(done[0].turn_order, 0)
        self.assertEqual(done[0].start_ms, 0)
        self.assertEqual(done[0].end_ms, 2000)

    def test_speaker_change_flushes(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "The API is down.", 0, 1000))
        flushed = b.add(turn(1, "B", "Checking the load balancer.", 1100, 2000))
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].speaker_label, "A")
        self.assertEqual(flushed[0].text, "The API is down.")
        rest = b.flush()
        self.assertEqual(rest[0].speaker_label, "B")

    def test_pause_flushes_even_for_same_speaker(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "Rolling back.", 0, 1000))
        # 1.6s of silence, above the 1.5s threshold.
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
        flushed = b.tick(2600)
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].text, "Anyone looking at this?")
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


class TestRevision(unittest.TestCase):
    def test_revision_amends_an_emitted_utterance(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        (utt,) = b.flush()
        self.assertEqual(utt.speaker_label, "A")

        amendment = b.apply_revision(0, "B")
        self.assertIsNotNone(amendment)
        self.assertTrue(amendment.changed)
        self.assertFalse(amendment.partial)
        self.assertEqual(amendment.previous_label, "A")
        self.assertEqual(amendment.new_label, "B")
        # The same object the caller already holds is corrected in place.
        self.assertEqual(utt.speaker_label, "B")
        self.assertEqual(utt.previous_speaker_label, "A")
        self.assertTrue(utt.amended)

    def test_revision_finds_a_still_open_utterance(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        amendment = b.apply_revision(0, "B")
        self.assertIsNotNone(amendment)
        (utt,) = b.flush()
        self.assertEqual(utt.speaker_label, "B")

    def test_revision_of_a_merged_utterance_is_flagged_partial(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        b.add(turn(1, "A", "Or maybe the CDN.", 1100, 2000))
        b.flush()
        amendment = b.apply_revision(1, "B")
        self.assertTrue(amendment.partial)
        self.assertTrue(amendment.changed)

    def test_unknown_turn_returns_none(self):
        b = UtteranceBuffer()
        self.assertIsNone(b.apply_revision(99, "B"))

    def test_restated_label_is_reported_but_not_a_change(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "It is the cache.", 0, 1000))
        b.flush()
        amendment = b.apply_revision(0, "A")
        self.assertIsNotNone(amendment)
        self.assertFalse(amendment.changed)

    def test_apply_revisions_batch(self):
        b = UtteranceBuffer()
        b.add(turn(0, "A", "One.", 0, 900))
        b.add(turn(1, "B", "Two.", 2000, 2900))
        b.flush()
        out = b.apply_revisions([(0, "C"), (1, "C"), (42, "Z")])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(a.changed for a in out))


if __name__ == "__main__":
    unittest.main()
