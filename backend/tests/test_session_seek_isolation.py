"""Seek on one live session must not mutate another session's board."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from backend.config import SAMPLE_RATE
from backend.main import IncidentHub
from backend.tests.test_machine import event


def _silence_wav(path: Path, duration_s: float = 1.0) -> None:
    n = int(SAMPLE_RATE * duration_s)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * n)


class TestSessionSeekIsolation(unittest.TestCase):
    def test_seek_one_session_leaves_other_untouched(self) -> None:
        a = IncidentHub()
        b = IncidentHub()
        a._reset_board(incident_id="session-a")
        b._reset_board(incident_id="session-b")

        early = [
            event(
                "hypothesis",
                hypothesis_id="dns",
                summary="DNS",
                speaker="A",
                order=0,
                ts=10_000,
                text="Could be DNS.",
                event_id="a-dns",
            ),
            event(
                "status_change",
                hypothesis_id="dns",
                new_state="ruled_out",
                summary="DNS out",
                speaker="A",
                order=1,
                ts=20_000,
                text="DNS is fine.",
                event_id="a-out",
            ),
            event(
                "hypothesis",
                hypothesis_id="deploy",
                summary="Deploy",
                speaker="B",
                order=2,
                ts=40_000,
                text="It was the deploy.",
                event_id="a-deploy",
            ),
        ]
        late_only = [
            event(
                "hypothesis",
                hypothesis_id="pool",
                summary="Pool",
                speaker="C",
                order=0,
                ts=5_000,
                text="Could be the pool.",
                event_id="b-pool",
            ),
            event(
                "action",
                summary="Check pool",
                speaker="C",
                order=1,
                ts=15_000,
                text="I'll check the pool.",
                event_id="b-act",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            wav_a = Path(tmp) / "a.wav"
            wav_b = Path(tmp) / "b.wav"
            _silence_wav(wav_a)
            _silence_wav(wav_b)

            a._event_journal = list(early)
            a.machine.apply_many(early)
            a._duration_ms = 90_000
            a._revision_journal = []
            a._pending_revisions = []
            a._file = wav_a

            b._event_journal = list(late_only)
            b.machine.apply_many(late_only)
            b._duration_ms = 90_000
            b._revision_journal = []
            b._pending_revisions = []
            b._file = wav_b

            snap_b_before = b.machine.snapshot()

            # Avoid starting the audio pipeline — seek only needs the journal rebuild.
            a._stop_pipeline = lambda join=True, suppress_teardown=False: None  # type: ignore[method-assign]
            a._start_pipeline_thread = lambda: None  # type: ignore[method-assign]
            a._ensure_latency_task = lambda: None  # type: ignore[method-assign]
            a._broadcast_snapshot = lambda: None  # type: ignore[method-assign]
            a._push_latency = lambda: None  # type: ignore[method-assign]
            a._teardown_reconcile_from_journal = lambda: None  # type: ignore[method-assign]

            a.seek(25_000)

            snap_a = a.machine.snapshot()
            snap_b_after = b.machine.snapshot()

            a_ids = {e["event_id"] for e in snap_a["timeline"]}
            self.assertIn("a-dns", a_ids)
            self.assertIn("a-out", a_ids)
            self.assertNotIn("a-deploy", a_ids)

            self.assertEqual(
                {e["event_id"] for e in snap_b_before["timeline"]},
                {e["event_id"] for e in snap_b_after["timeline"]},
            )
            self.assertEqual(snap_b_before["incident_id"], "session-b")
            self.assertEqual(snap_b_after["incident_id"], "session-b")
            b_ids = {e["event_id"] for e in snap_b_after["timeline"]}
            self.assertEqual(b_ids, {"b-pool", "b-act"})


if __name__ == "__main__":
    unittest.main()
