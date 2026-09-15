"""Incident state machine.

stdlib unittest. Covers the five behaviours the board must get right before
anything is wired to a socket: hypothesis dedupe, implicit creation on a
status_change that names an unknown id, unowned-action flagging, teardown
reconciliation changing a first-person owner, and a thread that stays open.
"""

from __future__ import annotations

import unittest
import uuid

from backend.state.machine import (
    IncidentMachine,
    apply_diff,
    boards_equivalent,
    empty_incident_state,
    is_first_person,
    resolve_action_owner,
)
from backend.state.models import Event, TurnKey


def eid() -> str:
    return str(uuid.uuid4())


def event(
    type: str,
    *,
    text: str = "",
    summary: str = "",
    speaker: str = "A",
    epoch: int = 0,
    order: int = 0,
    ts: int = 1000,
    hypothesis_id: str | None = None,
    new_state: str | None = None,
    owner: str | None = None,
    confidence: float = 0.9,
    previous_speaker_label: str | None = None,
    event_id: str | None = None,
) -> Event:
    return Event(
        event_id=event_id or eid(),
        type=type,
        connection_epoch=epoch,
        turn_order=order,
        speaker_label=speaker,
        speaker_name=None,
        timestamp_ms=ts,
        text=text,
        summary=summary,
        hypothesis_id=hypothesis_id,
        new_state=new_state,
        owner=owner,
        confidence=confidence,
        previous_speaker_label=previous_speaker_label,
    )


class TestHypothesisDedupe(unittest.TestCase):
    def test_same_hypothesis_id_updates_never_duplicates(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="payment-worker-backoff",
            summary="Retry backoff too aggressive",
            speaker="B", order=1, ts=1000,
            text="I think the backoff is too aggressive.",
        ))
        m.apply(event(
            "hypothesis",
            hypothesis_id="payment-worker-backoff",
            summary="Backoff hammering the provider",
            speaker="B", order=2, ts=5000,
            text="Yeah, the backoff is hammering the provider.",
        ))
        m.apply(event(
            "hypothesis",
            hypothesis_id="payment-worker-backoff",
            summary="Payment worker backoff",
            speaker="C", order=3, ts=9000,
            text="It's the payment-worker backoff.",
        ))

        self.assertEqual(list(m.hypotheses), ["payment-worker-backoff"])
        hyp = m.hypotheses["payment-worker-backoff"]
        # Original raiser and timestamp survive; text refreshes.
        self.assertEqual(hyp.raised_by_label, "B")
        self.assertEqual(hyp.raised_at_ms, 1000)
        self.assertEqual(hyp.text, "Payment worker backoff")
        self.assertFalse(hyp.implicit)


class TestImplicitCreation(unittest.TestCase):
    def test_status_change_for_unknown_id_creates_implicit_hypothesis(self):
        m = IncidentMachine(incident_id="inc")
        diff = m.apply(event(
            "status_change",
            hypothesis_id="dns",
            new_state="ruled_out",
            summary="DNS ruled out",
            speaker="C", order=5, ts=7000,
            text="DNS is fine. It's not that.",
        ))

        self.assertIn("dns", m.hypotheses)
        hyp = m.hypotheses["dns"]
        self.assertTrue(hyp.implicit)
        self.assertEqual(hyp.state, "ruled_out")
        self.assertEqual(hyp.raised_by_label, "C")
        self.assertEqual(hyp.resolved_at_ms, 7000)
        # Event was not dropped.
        self.assertEqual(len(m.timeline), 1)
        ops = [o.op for o in diff.ops]
        self.assertIn("upsert_hypothesis", ops)
        self.assertIn("upsert_event", ops)

    def test_status_change_on_known_id_is_not_marked_implicit(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="dns",
            summary="DNS resolver issue",
            speaker="C", order=1, ts=1000,
            text="First thing I'd check is DNS.",
        ))
        m.apply(event(
            "status_change",
            hypothesis_id="dns",
            new_state="ruled_out",
            summary="DNS ruled out",
            speaker="C", order=5, ts=7000,
            text="DNS is fine.",
        ))
        self.assertFalse(m.hypotheses["dns"].implicit)
        self.assertEqual(m.hypotheses["dns"].state, "ruled_out")


class TestUnownedAction(unittest.TestCase):
    def test_unowned_action_is_flagged(self):
        m = IncidentMachine(incident_id="inc")
        e = event(
            "action",
            summary="Check rate limit config",
            speaker="A", order=10, ts=20000,
            text="Someone needs to check the rate limit configuration.",
            owner=None,
        )
        m.apply(e)
        action = m.actions[e.event_id]
        self.assertIsNone(action.owner)
        self.assertTrue(action.unowned)
        self.assertFalse(action.first_person)

    def test_spoken_owner_wins(self):
        m = IncidentMachine(incident_id="inc")
        e = event(
            "action",
            summary="Draft status update",
            speaker="A", order=11, ts=21000,
            text="Rohan, can you draft a status update?",
            owner="Rohan",
        )
        m.apply(e)
        action = m.actions[e.event_id]
        self.assertEqual(action.owner, "Rohan")
        self.assertFalse(action.unowned)
        self.assertFalse(action.first_person)

    def test_first_person_commitment_owned_by_speaker_label(self):
        m = IncidentMachine(incident_id="inc")
        e = event(
            "action",
            summary="Roll back the deploy",
            speaker="C", order=12, ts=22000,
            text="I'll roll back the deploy now.",
            owner=None,
        )
        m.apply(e)
        action = m.actions[e.event_id]
        self.assertEqual(action.owner, "C")
        self.assertFalse(action.unowned)
        self.assertTrue(action.first_person)
        # Timeline event carries the resolved owner too.
        self.assertEqual(m.timeline[e.event_id].owner, "C")


class TestReconciliation(unittest.TestCase):
    def test_teardown_revision_changes_first_person_owner(self):
        m = IncidentMachine(incident_id="inc")
        e = event(
            "action",
            summary="Roll back the deploy",
            speaker="C", order=28, epoch=0, ts=184000,
            text="I'll roll back the deploy now.",
            owner=None,
        )
        m.apply(e)
        self.assertEqual(m.actions[e.event_id].owner, "C")

        # Resolution freezes the board - revisions still land after.
        m.apply(event(
            "resolution",
            summary="Incident declared resolved",
            speaker="A", order=40, ts=262000,
            text="Declaring this resolved at three fifteen.",
        ))
        self.assertTrue(m.frozen)

        diff = m.reconcile([(TurnKey(0, 28), "D")])
        self.assertTrue(diff)

        action = m.actions[e.event_id]
        self.assertEqual(action.owner, "D")
        self.assertEqual(m.timeline[e.event_id].speaker_label, "D")
        self.assertEqual(m.timeline[e.event_id].previous_speaker_label, "C")
        self.assertEqual(m.timeline[e.event_id].owner, "D")

        recon = next(o for o in diff.ops if o.op == "reconciliation")
        summary = recon.value
        self.assertEqual(summary.events_touched, 1)
        self.assertEqual(len(summary.owners), 1)
        self.assertEqual(summary.owners[0].previous_owner, "C")
        self.assertEqual(summary.owners[0].owner, "D")
        self.assertEqual(summary.speakers[0].previous_speaker_label, "C")
        self.assertEqual(summary.speakers[0].speaker_label, "D")

    def test_spoken_owner_is_not_rewritten_by_revision(self):
        m = IncidentMachine(incident_id="inc")
        e = event(
            "action",
            summary="Draft status update",
            speaker="A", order=7, ts=10000,
            text="Rohan, can you draft a status update?",
            owner="Rohan",
        )
        m.apply(e)
        m.reconcile([(TurnKey(0, 7), "B")])
        self.assertEqual(m.actions[e.event_id].owner, "Rohan")
        self.assertEqual(m.timeline[e.event_id].speaker_label, "B")

    def test_multi_event_turn_amends_every_sibling(self):
        m = IncidentMachine(incident_id="inc")
        sc = event(
            "status_change",
            hypothesis_id="bad-deploy",
            new_state="confirmed",
            summary="Deploy confirmed",
            speaker="C", order=37, epoch=0, ts=243000,
            text="Yeah, it was the deploy. I'll revert the retry change.",
        )
        act = event(
            "action",
            summary="Revert retry change",
            speaker="C", order=37, epoch=0, ts=243000,
            text="Yeah, it was the deploy. I'll revert the retry change.",
            owner=None,
        )
        m.apply(sc)
        m.apply(act)

        diff = m.reconcile([(TurnKey(0, 37), "D")])
        self.assertEqual(m.timeline[sc.event_id].speaker_label, "D")
        self.assertEqual(m.timeline[act.event_id].speaker_label, "D")
        self.assertEqual(m.actions[act.event_id].owner, "D")
        recon = next(o for o in diff.ops if o.op == "reconciliation")
        self.assertEqual(recon.value.events_touched, 2)


class TestThreadStaysOpen(unittest.TestCase):
    def test_thread_stays_open_through_resolution(self):
        m = IncidentMachine(incident_id="inc")
        tls = event(
            "thread",
            summary="TLS certificate on gateway expires in 2 days",
            speaker="D", order=25, ts=164000,
            text="Also, unrelated, but the TLS certificate on the gateway expires in two days.",
            owner=None,
        )
        m.apply(tls)
        m.apply(event(
            "action",
            summary="Roll back the deploy",
            speaker="B", order=28, ts=184000,
            text="I'll roll back the deploy now.",
        ))
        m.apply(event(
            "resolution",
            summary="Incident declared resolved",
            speaker="A", order=40, ts=262000,
            text="Declaring this resolved.",
        ))

        thread = m.threads[tls.event_id]
        self.assertFalse(thread.closed)
        self.assertTrue(m.resolved)
        # Still the only thread, still open.
        self.assertEqual(len(m.threads), 1)

    def test_nothing_auto_closes_a_thread(self):
        m = IncidentMachine(incident_id="inc")
        t = event(
            "thread",
            summary="Check replica lag",
            speaker="A", order=1, ts=1000,
            text="Has anyone checked the replica lag?",
        )
        m.apply(t)
        m.apply(event(
            "status_change",
            hypothesis_id="replica-lag",
            new_state="ruled_out",
            summary="Replica lag ruled out",
            speaker="B", order=2, ts=2000,
            text="Replica lag is fine.",
        ))
        self.assertFalse(m.threads[t.event_id].closed)

    def test_explicit_close_is_the_only_close(self):
        m = IncidentMachine(incident_id="inc")
        t = event(
            "thread",
            summary="TLS cert",
            speaker="D", order=1, ts=1000,
            text="TLS cert expires in two days.",
        )
        m.apply(t)
        diff = m.close_thread(t.event_id)
        self.assertTrue(m.threads[t.event_id].closed)
        self.assertTrue(any(o.op == "upsert_thread" for o in diff.ops))


class TestFreezeAndDiffs(unittest.TestCase):
    def test_resolution_freezes_further_ingest(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "resolution",
            summary="Resolved",
            speaker="A", order=1, ts=1000,
            text="Declaring this resolved.",
        ))
        diff = m.apply(event(
            "hypothesis",
            hypothesis_id="after",
            summary="Too late",
            speaker="B", order=2, ts=2000,
            text="Maybe it was the cache.",
        ))
        self.assertFalse(diff)
        self.assertNotIn("after", m.hypotheses)

    def test_diffs_not_snapshots(self):
        m = IncidentMachine(incident_id="inc")
        diff = m.apply(event(
            "hypothesis",
            hypothesis_id="dns",
            summary="DNS",
            speaker="C", order=1, ts=1000,
            text="Could be DNS.",
        ))
        wire = diff.to_wire()
        self.assertIn("ops", wire)
        self.assertTrue(all("op" in entry for entry in wire["ops"]))
        self.assertNotIn("hypotheses", wire)
        self.assertNotIn("timeline", wire)

    def test_snapshot_carries_implicit_and_unowned(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "status_change",
            hypothesis_id="dns",
            new_state="ruled_out",
            summary="DNS ruled out",
            speaker="C", order=1, ts=1000,
            text="DNS is fine.",
        ))
        e = event(
            "action",
            summary="Check rate limits",
            speaker="A", order=2, ts=2000,
            text="Someone needs to check the rate limit config.",
        )
        m.apply(e)
        snap = m.snapshot()
        hyp = next(h for h in snap["hypotheses"] if h["hypothesis_id"] == "dns")
        self.assertTrue(hyp["implicit"])
        action = next(a for a in snap["actions"] if a["action_id"] == e.event_id)
        self.assertTrue(action["unowned"])
        self.assertNotIn("first_person", action)


class TestSnapshotMatchesAccumulatedDiffs(unittest.TestCase):
    """A fresh load and a streamed board must agree (contract v1.4.0)."""

    def test_snapshot_equals_folded_diffs(self):
        m = IncidentMachine(incident_id="inc", started_at_ms=0)
        accumulated = empty_incident_state("inc", 0)

        def push(ev: Event) -> None:
            nonlocal accumulated
            diff = m.apply(ev)
            if diff:
                accumulated = apply_diff(accumulated, diff.to_wire())

        push(event(
            "hypothesis",
            hypothesis_id="payment-worker-backoff",
            summary="Retry backoff too aggressive",
            speaker="B", order=1, ts=1000,
            text="I think the backoff is too aggressive.",
        ))
        # Dedupe: second mention must not fork the board.
        push(event(
            "hypothesis",
            hypothesis_id="payment-worker-backoff",
            summary="Backoff hammering the provider",
            speaker="B", order=2, ts=5000,
            text="Yeah, the backoff is hammering the provider.",
        ))
        # Implicit creation via status_change.
        push(event(
            "status_change",
            hypothesis_id="dns",
            new_state="ruled_out",
            summary="DNS ruled out",
            speaker="C", order=5, ts=7000,
            text="DNS is fine.",
        ))
        tls = event(
            "thread",
            summary="TLS certificate on gateway expires in 2 days",
            speaker="D", order=25, ts=164000,
            text="TLS cert expires in two days.",
        )
        push(tls)
        rollback = event(
            "action",
            summary="Roll back the deploy",
            speaker="C", order=28, ts=184000,
            text="I'll roll back the deploy now.",
        )
        push(rollback)
        push(event(
            "action",
            summary="Check rate limit config",
            speaker="A", order=30, ts=193000,
            text="Someone needs to check the rate limit configuration.",
        ))
        push(event(
            "resolution",
            summary="Incident declared resolved",
            speaker="A", order=40, ts=262000,
            text="Declaring this resolved.",
        ))

        # Teardown revision: first-person owner moves C -> D.
        recon = m.reconcile([(TurnKey(0, 28), "D")])
        self.assertTrue(recon)
        accumulated = apply_diff(accumulated, recon.to_wire())

        snap = m.snapshot()
        self.assertTrue(
            boards_equivalent(snap, accumulated),
            msg=(
                f"snapshot and folded diffs diverged\n"
                f"snap actions={[a for a in snap['actions']]}\n"
                f"fold actions={[a for a in accumulated['actions']]}\n"
                f"snap hyps={[h for h in snap['hypotheses']]}\n"
                f"fold hyps={[h for h in accumulated['hypotheses']]}"
            ),
        )
        # The fields that motivated v1.4.0 are present and agree.
        self.assertTrue(
            next(h for h in snap["hypotheses"] if h["hypothesis_id"] == "dns")["implicit"]
        )
        unowned = next(a for a in snap["actions"] if a["unowned"])
        self.assertIsNone(unowned["owner"])
        owned = next(a for a in snap["actions"] if a["action_id"] == rollback.event_id)
        self.assertEqual(owned["owner"], "D")
        self.assertFalse(owned["unowned"])
        self.assertFalse(next(t for t in snap["threads"])["closed"])


class TestFirstPersonHelper(unittest.TestCase):
    def test_commitment_shapes(self):
        self.assertTrue(is_first_person("I'll roll back the deploy now."))
        self.assertTrue(is_first_person("I will watch the error rate."))
        self.assertTrue(is_first_person("I'm going to revert the change."))
        self.assertTrue(is_first_person("I've restarted the workers."))
        self.assertFalse(is_first_person("I think it's the cache."))
        self.assertFalse(is_first_person("I don't think it's the pool."))
        self.assertFalse(is_first_person("Someone needs to check the config."))

    def test_resolve_owner_priority(self):
        self.assertEqual(
            resolve_action_owner("Rohan", "I'll do it.", "A"),
            ("Rohan", False, False),
        )
        self.assertEqual(
            resolve_action_owner(None, "I'll do it.", "B"),
            ("B", False, True),
        )
        self.assertEqual(
            resolve_action_owner(None, "Someone needs to.", "A"),
            (None, True, False),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
