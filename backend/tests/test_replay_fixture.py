"""Assert the recorded WS fixture yields a coherent live-capture board."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.state.machine import apply_diff

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "replay"
    / "incident_01.fixture.json"
)


class TestReplayFixture(unittest.TestCase):
    def test_fixture_matches_live_capture_board(self) -> None:
        meta = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(meta["contract_version"], "1.6.0")
        self.assertEqual(meta["extraction_temperature"], 0.0)
        self.assertGreater(len(meta["messages"]), 10)

        state = None
        for fr in meta["messages"]:
            msg = fr["message"]
            if msg["type"] == "snapshot":
                state = msg["state"]
            elif msg["type"] == "diff" and state is not None:
                state = apply_diff(state, {"ops": msg.get("ops") or []})

        assert state is not None
        hyp_by_id = {h["hypothesis_id"]: h for h in state["hypotheses"]}
        # Compound "DNS or pool" must be split; no packed dns-or-pool id.
        self.assertNotIn("dns-or-pool", hyp_by_id)
        self.assertIn("dns", hyp_by_id)
        self.assertEqual(hyp_by_id["dns"]["state"], "ruled_out")
        self.assertFalse(hyp_by_id["dns"].get("implicit"))
        # At least one open cause and a confirmed deploy.
        confirmed = [h for h in state["hypotheses"] if h["state"] == "confirmed"]
        self.assertGreaterEqual(len(confirmed), 1)
        self.assertTrue(state["resolved"])
        silence = state["silence"]
        self.assertIsNotNone(silence)
        self.assertGreaterEqual(silence["questions_asked"], 1)

        # Reconciliation is best-effort: AssemblyAI may send zero SpeakerRevision
        # items for a given run. When present, it must be well-formed.
        recon = None
        for fr in reversed(meta["messages"]):
            msg = fr["message"]
            if msg.get("type") != "diff":
                continue
            for op in msg.get("ops") or []:
                if op.get("op") == "reconciliation":
                    recon = op["value"]
                    break
            if recon:
                break
        if recon is not None:
            self.assertIsInstance(recon.get("events_touched"), int)
            self.assertIsInstance(recon.get("speakers"), list)


if __name__ == "__main__":
    unittest.main()
