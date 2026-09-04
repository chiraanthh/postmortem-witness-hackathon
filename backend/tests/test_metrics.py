"""Latency accounting and revision-delay tracking."""

from __future__ import annotations

import unittest

from backend.metrics import ASR, AudioClock, Metrics, RevisionLog, percentile


class TestPercentile(unittest.TestCase):
    def test_nearest_rank(self):
        v = list(range(1, 11))
        self.assertEqual(percentile(v, 50), 5)
        self.assertEqual(percentile(v, 95), 10)
        self.assertEqual(percentile(v, 100), 10)

    def test_degenerate_inputs(self):
        self.assertEqual(percentile([], 50), 0.0)
        self.assertEqual(percentile([42], 95), 42)
        self.assertEqual(percentile([7, 7, 7], 50), 7)

    def test_unsorted_input(self):
        self.assertEqual(percentile([9, 1, 5, 3, 7], 50), 5)


class TestAudioClock(unittest.TestCase):
    def setUp(self):
        self.c = AudioClock()
        for i in range(1, 6):
            self.c.record_chunk(i * 50, 1000.0 + i * 50)

    def test_maps_audio_position_to_send_time(self):
        self.assertEqual(self.c.sent_at(50), 1050.0)
        self.assertEqual(self.c.sent_at(60), 1100.0)   # inside chunk 2
        self.assertEqual(self.c.sent_at(100), 1100.0)

    def test_latency_is_arrival_minus_send(self):
        self.assertEqual(self.c.latency_ms(100, 1400.0), 300.0)

    def test_never_negative(self):
        self.assertEqual(self.c.latency_ms(100, 1000.0), 0.0)

    def test_position_past_what_was_sent_attributes_to_last_chunk(self):
        self.assertEqual(self.c.sent_at(99_999), 1250.0)

    def test_empty_clock(self):
        self.assertIsNone(AudioClock().sent_at(10))
        self.assertIsNone(AudioClock().latency_ms(10))

    def test_tracks_total_audio_sent(self):
        self.assertEqual(self.c.audio_sent_ms, 250)


class TestMetrics(unittest.TestCase):
    def test_summary_and_snapshot(self):
        m = Metrics()
        for x in (100, 150, 200, 900):
            m.record(ASR, x)
        self.assertEqual(m.count(ASR), 4)
        self.assertEqual(m.p50(ASR), 150)
        self.assertEqual(m.p95(ASR), 900)
        self.assertIn("n=4", m.summary(ASR))
        self.assertEqual(m.snapshot()[ASR]["n"], 4.0)

    def test_unknown_stage_is_empty_not_an_error(self):
        m = Metrics()
        self.assertEqual(m.p50("nope"), 0.0)
        self.assertIn("no samples", m.summary("nope"))

    def test_contract_latency_shape(self):
        self.assertEqual(
            set(Metrics().contract_latency()), {"asr_ms", "extract_ms", "e2e_ms"}
        )


class TestRevisionLog(unittest.TestCase):
    def test_delay_is_measured_from_first_sighting(self):
        log = RevisionLog()
        log.note_turn(1, "A")
        rev = log.note_revision(1, "B")
        self.assertEqual(rev.previous_label, "A")
        self.assertTrue(rev.changed)
        self.assertGreaterEqual(rev.delay_ms, 0.0)
        self.assertEqual(log.total, 1)
        self.assertEqual(len(log.changed), 1)

    def test_restatement_is_counted_but_not_a_change(self):
        log = RevisionLog()
        log.note_turn(1, "A")
        log.note_revision(1, "A")
        self.assertEqual(log.total, 1)
        self.assertEqual(log.changed, [])
        self.assertIn("0 changed", log.summary())

    def test_first_sighting_is_not_overwritten(self):
        log = RevisionLog()
        log.note_turn(1, "A")
        log.note_turn(1, "A")   # the formatted pass for the same turn
        self.assertEqual(log.turns_seen, 1)

    def test_revision_for_unseen_turn_does_not_crash(self):
        log = RevisionLog()
        rev = log.note_revision(7, "B")
        self.assertEqual(rev.delay_ms, 0.0)
        self.assertIsNone(rev.previous_label)

    def test_summary_with_no_revisions(self):
        log = RevisionLog()
        log.note_turn(1, "A")
        self.assertIn("0 received", log.summary())


if __name__ == "__main__":
    unittest.main()
