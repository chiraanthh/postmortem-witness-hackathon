"""Export and server wire-shape smoke tests. No network, no API keys."""

from __future__ import annotations

import unittest

from backend.export import render_postmortem
from backend.main import (
    CONTRACT_VERSION,
    diff_message,
    export_render,
    handshake_message,
    snapshot_message,
)
from backend.state.machine import (
    IncidentMachine,
    StateDiff,
    apply_diff,
    empty_incident_state,
)
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


class TestExportRender(unittest.TestCase):
    """Recorded replay has no server-side session, so there is nothing to
    hit /s/<id>/export on. The portal's replay Export button instead POSTs
    a client-reconstructed IncidentState to the stateless /export/render
    endpoint. These tests are what makes that trustworthy: export_render
    must be a pure passthrough to render_postmortem, and folding the same
    diff stream through apply_diff (the backend's own mirror of the
    frontend's shared/events.ts reducer) must produce a state whose
    rendered markdown is byte-identical to the live IncidentMachine path.
    """

    def test_export_render_delegates_to_render_postmortem(self):
        m = IncidentMachine(incident_id="incident_01")
        m.apply(event(
            "hypothesis",
            hypothesis_id="dns",
            summary="DNS",
            speaker="A", order=0, ts=0,
            text="Could be DNS.",
        ))
        state = m.snapshot()
        self.assertEqual(export_render(state), render_postmortem(state))

    def test_replay_reconstruction_matches_live_export(self):
        m = IncidentMachine(incident_id="incident_01")
        events = [
            event(
                "hypothesis", hypothesis_id="dns", summary="DNS",
                speaker="A", order=0, ts=0, text="Could be DNS.",
            ),
            event(
                "status_change", hypothesis_id="dns", new_state="ruled_out",
                summary="DNS ruled out",
                speaker="C", order=1, ts=1000, text="DNS is fine.",
            ),
            event(
                "thread", summary="Check the cert",
                speaker="D", order=2, ts=2000, text="Did we check the cert?",
            ),
            event(
                "action", summary="Check rate limit",
                speaker="A", order=3, ts=3000,
                text="Someone needs to check the rate limit.",
            ),
            event(
                "resolution", summary="Resolved",
                speaker="A", order=4, ts=4000, text="Declaring this resolved.",
            ),
        ]
        replay_state = empty_incident_state("incident_01")
        for e in events:
            diff = m.apply(e)
            wire = diff_message(diff)
            if wire is not None:
                replay_state = apply_diff(replay_state, wire)

        live_markdown = render_postmortem(m.snapshot())
        replay_markdown = export_render(replay_state)
        self.assertEqual(replay_markdown, live_markdown)
        # Sanity: the fold actually carried real content, not two empty boards.
        self.assertIn("dns", replay_markdown)
        self.assertIn("Check the cert", replay_markdown)


class TestWireEnvelopes(unittest.TestCase):
    def test_handshake_carries_schema_version(self):
        msg = handshake_message()
        self.assertEqual(msg["type"], "handshake")
        self.assertEqual(msg["contract_version"], CONTRACT_VERSION)
        self.assertEqual(CONTRACT_VERSION, "1.6.0")

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
