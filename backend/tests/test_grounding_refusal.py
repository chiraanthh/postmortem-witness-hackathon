"""Offline: grounding refusal reaches the wire; board stays unchanged.

Extraction is mocked. Worker validation, machine apply, and wire envelopes
are real — no API keys, no network.
"""

from __future__ import annotations

import unittest

from backend.extraction.worker import ExtractionWorker, RunningContext
from backend.main import diff_message, refusal_to_wire
from backend.state.machine import IncidentMachine
from backend.tests.test_extraction import utt, worker_with, one
from backend.transcription.buffer import Utterance
from backend.state.models import TurnKey


class TestGroundingRefusalWire(unittest.TestCase):
    def test_refusal_sidecar_on_empty_board_diff(self):
        """Unevidenced status_change → noise on board, refusal on the wire."""
        utterance = Utterance(
            turn_key=TurnKey(0, 19),
            turn_keys=[TurnKey(0, 19)],
            speaker_label="C",
            start_ms=98223,
            end_ms=101793,
            text="So it's probably not the cache, I'd say we drop that one.",
        )
        # Model invents a quote that is not in the utterance — guard must fire.
        w = worker_with(one(
            type="status_change",
            summary="Cache ruled out",
            hypothesis_id="cache",
            new_state="ruled_out",
            evidence_quote="we ruled out the cache",
            confidence=0.9,
        ))
        ctx = RunningContext(
            hypotheses=(
                ("cache", "Cache eviction storm", "open"),
                ("dns", "DNS resolution failing", "ruled_out"),
            ),
        )
        outcome = w.extract(utterance, ctx)

        self.assertEqual(len(outcome.rejections), 1)
        self.assertIn("not in the utterance", outcome.rejections[0].reason)
        self.assertEqual(only_type(outcome), "noise")

        machine = IncidentMachine(incident_id="incident_01")
        # Seed the board with an open cache hyp so a false accept would move it.
        from backend.tests.test_machine import event

        machine.apply(event(
            "hypothesis",
            hypothesis_id="cache",
            summary="Cache eviction storm",
            speaker="A", order=0, ts=1000,
            text="Could be the cache.",
        ))
        before = machine.hypotheses["cache"].state

        diff = machine.apply_many(outcome.events)
        self.assertFalse(diff)  # noise is a no-op
        self.assertEqual(machine.hypotheses["cache"].state, before)
        self.assertNotIn("ruled_out", [h.state for h in machine.hypotheses.values()])

        refusals = [
            refusal_to_wire(
                r,
                utterance,
                provider=outcome.provider or w.provider_name,
                model=outcome.model or w.model,
            )
            for r in outcome.rejections
        ]
        msg = diff_message(diff, refusals=refusals)
        assert msg is not None
        self.assertEqual(msg["type"], "diff")
        self.assertEqual(msg["ops"], [])
        self.assertEqual(len(msg["refusals"]), 1)
        ref = msg["refusals"][0]
        self.assertEqual(ref["kind"], "grounding_refusal")
        self.assertIs(ref["recorded"], False)
        self.assertEqual(ref["claimed_hypothesis_id"], "cache")
        self.assertEqual(ref["claimed_new_state"], "ruled_out")
        self.assertEqual(ref["claimed_quote"], "we ruled out the cache")
        self.assertIn("not in the utterance", ref["reason"])
        self.assertEqual(ref["utterance_text"], utterance.text)
        self.assertEqual(ref["speaker_label"], "C")
        self.assertEqual(ref["timestamp_ms"], 98223)

    def test_grounded_status_change_still_lands_without_refusal(self):
        w = worker_with(one(
            type="status_change",
            summary="DNS ruled out",
            hypothesis_id="dns",
            new_state="ruled_out",
            evidence_quote="DNS is fine",
        ))
        u = utt(
            "DNS is fine. I checked the resolver.",
            speaker="C",
            order=14,
            start=72274,
        )
        out = w.extract(u, RunningContext(
            hypotheses=(("dns", "DNS resolution failing", "open"),),
        ))
        self.assertEqual(out.rejections, [])
        self.assertEqual(only_type(out), "status_change")
        msg = diff_message(
            IncidentMachine(incident_id="x").apply_many(out.events),
            refusals=[],
        )
        assert msg is not None
        self.assertNotIn("refusals", msg)
        ops = [o["op"] for o in msg["ops"]]
        self.assertIn("upsert_hypothesis", ops)


def only_type(outcome) -> str:
    assert len(outcome.events) == 1
    return outcome.events[0].type


if __name__ == "__main__":
    unittest.main()
