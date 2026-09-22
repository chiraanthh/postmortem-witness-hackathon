"""Export and server wire-shape smoke tests. No network, no API keys."""

from __future__ import annotations

import unittest

from backend.export import render_postmortem
from backend.main import (
    CONTRACT_VERSION,
    diff_message,
    handshake_message,
    snapshot_message,
)
from backend.state.machine import IncidentMachine, StateDiff
from backend.tests.test_machine import event


class TestExport(unittest.TestCase):
    def test_renders_implicit_unowned_and_open_thread(self):
        m = IncidentMachine(incident_id="incident_01")
        m.apply(event(
            "status_change",
            hypothesis_id="dns",
            new_state="ruled_out",
            summary="DNS ruled out",
            speaker="C", order=1, ts=1000,
            text="DNS is fine.",
        ))
        m.apply(event(
            "thread",
            summary="TLS cert expires in two days",
            speaker="D", order=2, ts=2000,
            text="TLS certificate expires in two days.",
        ))
        m.apply(event(
            "action",
            summary="Check rate limit config",
            speaker="A", order=3, ts=3000,
            text="Someone needs to check the rate limit configuration.",
        ))
        m.apply(event(
            "resolution",
            summary="Incident declared resolved",
            speaker="A", order=4, ts=4000,
            text="Declaring this resolved.",
        ))
        md = render_postmortem(m.snapshot())
        self.assertIn("# Postmortem: incident_01", md)
        self.assertIn("dns", md)
        self.assertIn("implicit", md)
        self.assertIn("unowned", md)
        self.assertIn("TLS cert", md)
        self.assertIn("Resolved", md)


class TestWireEnvelopes(unittest.TestCase):
    def test_handshake_carries_schema_version(self):
        msg = handshake_message()
        self.assertEqual(msg["type"], "handshake")
        self.assertEqual(msg["contract_version"], CONTRACT_VERSION)
        self.assertEqual(CONTRACT_VERSION, "1.5.0")

    def test_snapshot_then_diff_shape(self):
        m = IncidentMachine(incident_id="inc")
        snap = snapshot_message(m.snapshot())
        self.assertEqual(snap["type"], "snapshot")
        self.assertIn("state", snap)
        self.assertEqual(snap["state"]["incident_id"], "inc")

        diff = m.apply(event(
            "hypothesis",
            hypothesis_id="dns",
            summary="DNS",
            speaker="A", order=0, ts=0,
            text="Could be DNS.",
        ))
        msg = diff_message(diff)
        assert msg is not None
        self.assertEqual(msg["type"], "diff")
        self.assertIn("ops", msg)
        self.assertTrue(msg["ops"])
        self.assertIsNone(diff_message(StateDiff()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
