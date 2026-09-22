"""Assert the recorded WS fixture yields the live-capture board (zero API)."""

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
        self.assertEqual(meta["contract_version"], "1.5.0")
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
        hyp_ids = {h["hypothesis_id"] for h in state["hypotheses"]}
        self.assertEqual(
            hyp_ids,
            {
                "upstream-provider",
                "retry-logic-backoff",
                "bad-deploy",
                "dns-or-pool",
            },
        )
        self.assertTrue(state["resolved"])
        silence = state["silence"]
        self.assertIsNotNone(silence)
        self.assertEqual(silence["questions_unanswered"], 2)
        tls = [
            t
            for t in silence["open_threads"]
            if "TLS" in (t.get("text") or "")
        ]
        self.assertEqual(len(tls), 1)
        self.assertEqual(tls[0]["unanswered_age_ms"], 98885)

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
        self.assertIsNotNone(recon)
        self.assertEqual(recon["events_touched"], 5)
        self.assertEqual(len(recon["speakers"]), 5)


if __name__ == "__main__":
    unittest.main()
