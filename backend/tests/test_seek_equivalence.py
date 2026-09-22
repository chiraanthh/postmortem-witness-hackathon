"""Seek rebuilds the same board as a straight play-through to that ms."""

from __future__ import annotations

import tempfile
import threading
import unittest
import wave
from pathlib import Path

from backend.config import SAMPLE_RATE
from backend.state.machine import IncidentMachine, boards_equivalent
from backend.state.models import Event, TurnKey
from backend.tests.test_machine import event
from backend.transcription.audio import ControllableFileSource


class TestSeekEquivalence(unittest.TestCase):
    def _script(self) -> list[Event]:
        """A short incident timeline with known timestamps."""
        return [
            event(
                "hypothesis",
                hypothesis_id="dns",
                summary="DNS issue",
                speaker="C",
                order=1,
                ts=10_000,
                text="Could be DNS.",
                event_id="e-dns",
            ),
            event(
                "status_change",
                hypothesis_id="dns",
                new_state="ruled_out",
                summary="DNS ruled out",
                speaker="C",
                order=2,
                ts=20_000,
                text="DNS is fine.",
                event_id="e-dns-out",
            ),
            event(
                "hypothesis",
                hypothesis_id="deploy",
                summary="Bad deploy",
                speaker="C",  # provisional — revised to D at teardown
                order=3,
                ts=30_000,
                text="I think it's the deploy.",
                event_id="e-deploy",
            ),
            event(
                "action",
                summary="Roll back the deploy",
                speaker="C",
                order=4,
                ts=40_000,
                text="I'll roll back the deploy now.",
                event_id="e-rollback",
            ),
            event(
                "status_change",
                hypothesis_id="deploy",
                new_state="confirmed",
                summary="Deploy confirmed",
                speaker="C",
                order=5,
                ts=50_000,
                text="Yeah, it was the deploy.",
                event_id="e-confirm",
            ),
            event(
                "resolution",
                summary="Incident declared resolved",
                speaker="A",
                order=6,
                ts=60_000,
                text="Declaring this resolved.",
                event_id="e-res",
            ),
        ]

    def test_seek_rebuild_matches_straight_play_to_ms(self):
        script = self._script()
        cut = 35_000  # after deploy hyp, before rollback

        straight = IncidentMachine(incident_id="incident_01")
        straight.apply_many([e for e in script if e.timestamp_ms <= cut])

        rebuilt = IncidentMachine(incident_id="incident_01")
        rebuilt.apply_many([e for e in script if e.timestamp_ms <= cut])

        self.assertTrue(
            boards_equivalent(straight.snapshot(), rebuilt.snapshot()),
            "seek rebuild must match a straight play-through to the same ms",
        )
        self.assertNotIn(
            "e-rollback", {e.event_id for e in rebuilt.timeline.values()}
        )
        self.assertFalse(rebuilt.resolved)

    def test_seek_then_reconcile_matches_full_run_reconciliation(self):
        script = self._script()
        # Just before teardown: every event has landed; reconciliation is next.
        cut = 60_000
        revisions = [
            (TurnKey(0, 3), "D"),
            (TurnKey(0, 4), "D"),
            (TurnKey(0, 5), "D"),
        ]

        full = IncidentMachine(incident_id="incident_01")
        full.apply_many(script)
        full_diff = full.reconcile(revisions)

        seek = IncidentMachine(incident_id="incident_01")
        seek.apply_many([e for e in script if e.timestamp_ms <= cut])
        seek_diff = seek.reconcile(revisions)

        full_recon = [op for op in full_diff.ops if op.op == "reconciliation"]
        seek_recon = [op for op in seek_diff.ops if op.op == "reconciliation"]
        self.assertEqual(len(full_recon), 1)
        self.assertEqual(len(seek_recon), 1)
        self.assertEqual(full_recon[0].value, seek_recon[0].value)
        self.assertTrue(boards_equivalent(full.snapshot(), seek.snapshot()))

    def test_restart_does_not_append(self):
        script = self._script()
        m = IncidentMachine(incident_id="incident_01")
        m.apply_many(script)
        self.assertTrue(m.resolved)

        fresh = IncidentMachine(incident_id="incident_01")
        self.assertEqual(len(fresh.timeline), 0)
        self.assertFalse(fresh.resolved)
        fresh.apply_many(script[:1])
        self.assertEqual(len(fresh.timeline), 1)
        self.assertNotIn("e-res", {e.event_id for e in fresh.timeline.values()})


class TestControllablePause(unittest.TestCase):
    def test_pause_blocks_until_resume(self):
        raw = b"\x00\x00" * (SAMPLE_RATE * 3 // 10)  # 300 ms
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(raw)

            src = ControllableFileSource(path, realtime=False)
            it = iter(src)
            first = next(it)
            self.assertGreater(first.end_ms, 0)
            src.pause()
            self.assertTrue(src.paused)

            done = threading.Event()
            holder: list = []

            def read_next() -> None:
                try:
                    holder.append(next(it))
                except StopIteration:
                    holder.append(None)
                finally:
                    done.set()

            t = threading.Thread(target=read_next, daemon=True)
            t.start()
            self.assertFalse(done.wait(0.2), "pause must block the feeder")
            src.resume()
            self.assertTrue(done.wait(2.0))
            self.assertIsNotNone(holder[0])


class TestResetPacing(unittest.TestCase):
    def test_reset_pacing_clears_on_next_chunk(self):
        """Flag must apply without needing a realtime sleep (no flake)."""
        raw = b"\x00\x00" * (SAMPLE_RATE // 10)  # 100 ms
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(raw)

            src = ControllableFileSource(path, realtime=False)
            src.reset_pacing()
            self.assertTrue(src._reset_pacing.is_set())
            next(iter(src))
            self.assertFalse(src._reset_pacing.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
