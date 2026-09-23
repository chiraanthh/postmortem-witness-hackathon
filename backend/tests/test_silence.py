"""Silence detector (contract v1.5.0).

Offline only — no live Haiku/ASR. Covers thread open with addressee, explicit
answers_thread_id linking, proximity non-answers, resolution silence_summary,
and the wire shape via to_wire / diff_message.
"""

from __future__ import annotations

import unittest
import uuid

from backend.main import diff_message
from backend.state.machine import IncidentMachine
from backend.state.models import Event


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
    event_id: str | None = None,
    addressee: str | None = None,
    answers_thread_id: str | None = None,
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
        previous_speaker_label=None,
        addressee=addressee,
        answers_thread_id=answers_thread_id,
        claim_subject=None,
        claim_assertion=None,
        claim_quote=None,
    )


class TestThreadOpensWithAddressee(unittest.TestCase):
    def test_thread_carries_addressee_unanswered(self):
        m = IncidentMachine(incident_id="inc")
        # Mocked extraction → Event with addressee (as worker would pass through).
        asked = event(
            "thread",
            summary="Has anyone checked replica lag?",
            text="Maya, has anyone checked the replica lag?",
            speaker="A",
            order=1,
            ts=10_000,
            addressee="Maya",
        )
        m.apply(asked)
        thread = m.threads[asked.event_id]
        self.assertEqual(thread.addressee, "Maya")
        self.assertEqual(thread.asked_at_ms, 10_000)
        self.assertFalse(thread.answered)
        self.assertIsNone(thread.answered_at_ms)
        self.assertIsNone(thread.unanswered_age_ms)
        self.assertFalse(thread.closed)


class TestAnswersThreadId(unittest.TestCase):
    def test_linked_answer_marks_answered_and_closes(self):
        m = IncidentMachine(incident_id="inc")
        asked = event(
            "thread",
            summary="Check replica lag",
            text="Maya, has anyone checked the replica lag?",
            speaker="A",
            order=1,
            ts=10_000,
            addressee="Maya",
        )
        m.apply(asked)

        reply = event(
            "action",
            summary="Checked replica lag — fine",
            text="Replica lag is under a second, we're good.",
            speaker="B",
            order=2,
            ts=25_000,
            answers_thread_id=asked.event_id,
        )
        diff = m.apply(reply)
        thread = m.threads[asked.event_id]
        self.assertTrue(thread.answered)
        self.assertEqual(thread.answered_at_ms, 25_000)
        self.assertTrue(thread.closed)
        self.assertTrue(any(
            o.op == "upsert_thread" and o.key == asked.event_id for o in diff.ops
        ))

    def test_noise_with_answers_thread_id_still_closes_thread(self):
        """Noise is not board content but may answer an open question."""
        m = IncidentMachine(incident_id="inc")
        asked = event(
            "thread",
            summary="Check payment provider status page",
            text="Should we check the payment provider status page?",
            speaker="A",
            order=1,
            ts=10_000,
        )
        m.apply(asked)
        reply = event(
            "noise",
            summary="",
            text="And the provider status page is green. It's not upstream.",
            speaker="C",
            order=2,
            ts=25_000,
            answers_thread_id=asked.event_id,
        )
        diff = m.apply(reply)
        thread = m.threads[asked.event_id]
        self.assertTrue(thread.answered)
        self.assertTrue(thread.closed)
        self.assertTrue(any(
            o.op == "upsert_thread" and o.key == asked.event_id for o in diff.ops
        ))

    def test_proximity_without_link_does_not_answer(self):
        m = IncidentMachine(incident_id="inc")
        asked = event(
            "thread",
            summary="Check replica lag",
            text="Has anyone checked the replica lag?",
            speaker="A",
            order=1,
            ts=10_000,
        )
        m.apply(asked)
        # Same topic, no answers_thread_id — must not close/answer.
        m.apply(event(
            "action",
            summary="Looked at replicas",
            text="I just looked at the replica lag graphs.",
            speaker="B",
            order=2,
            ts=20_000,
            answers_thread_id=None,
        ))
        thread = m.threads[asked.event_id]
        self.assertFalse(thread.answered)
        self.assertFalse(thread.closed)


class TestResolutionSilenceSummary(unittest.TestCase):
    def test_tls_unanswered_age_and_silence_summary(self):
        m = IncidentMachine(incident_id="inc")
        asked_at = 164_000
        resolved_at = 262_000
        tls = event(
            "thread",
            summary="TLS certificate on gateway expires in 2 days",
            text=(
                "Also, unrelated, but the TLS certificate on the gateway "
                "expires in two days. Should someone be looking at that?"
            ),
            speaker="D",
            order=25,
            ts=asked_at,
            addressee=None,
        )
        m.apply(tls)
        m.apply(event(
            "action",
            summary="Roll back the deploy",
            text="I'll roll back the deploy now.",
            speaker="B",
            order=28,
            ts=184_000,
        ))
        diff = m.apply(event(
            "resolution",
            summary="Incident declared resolved",
            text="Declaring this resolved.",
            speaker="A",
            order=40,
            ts=resolved_at,
        ))

        thread = m.threads[tls.event_id]
        self.assertFalse(thread.answered)
        self.assertFalse(thread.closed)
        self.assertEqual(thread.unanswered_age_ms, resolved_at - asked_at)

        silence_ops = [o for o in diff.ops if o.op == "silence_summary"]
        self.assertEqual(len(silence_ops), 1)
        summary = silence_ops[0].value
        self.assertEqual(summary.questions_asked, 1)
        self.assertEqual(summary.questions_unanswered, 1)
        self.assertEqual(summary.longest_unanswered_ms, resolved_at - asked_at)
        self.assertEqual(summary.unanswered_addressees, [])
        self.assertEqual(len(summary.open_threads), 1)
        self.assertEqual(summary.open_threads[0].thread_id, tls.event_id)
        self.assertEqual(
            summary.open_threads[0].unanswered_age_ms, resolved_at - asked_at
        )
        self.assertIsNotNone(m.silence)
        snap = m.snapshot()
        self.assertEqual(snap["silence"]["questions_unanswered"], 1)
        self.assertEqual(snap["contradictions"], [])


class TestSilenceWireShape(unittest.TestCase):
    def test_silence_summary_reaches_diff_message(self):
        m = IncidentMachine(incident_id="inc")
        tls = event(
            "thread",
            summary="TLS cert",
            text="TLS cert expires in two days.",
            speaker="D",
            order=1,
            ts=100_000,
            addressee="Sam",
        )
        m.apply(tls)
        diff = m.apply(event(
            "resolution",
            summary="Resolved",
            text="Declaring this resolved.",
            speaker="A",
            order=2,
            ts=200_000,
        ))
        msg = diff_message(diff)
        self.assertIsNotNone(msg)
        assert msg is not None
        silence_entries = [o for o in msg["ops"] if o["op"] == "silence_summary"]
        self.assertEqual(len(silence_entries), 1)
        value = silence_entries[0]["value"]
        self.assertIsInstance(value, dict)
        self.assertEqual(value["questions_asked"], 1)
        self.assertEqual(value["questions_unanswered"], 1)
        self.assertEqual(value["longest_unanswered_ms"], 100_000)
        self.assertEqual(value["unanswered_addressees"], ["Sam"])
        self.assertEqual(value["open_threads"][0]["addressee"], "Sam")
        self.assertEqual(value["open_threads"][0]["unanswered_age_ms"], 100_000)
        # Wire has no key for silence_summary (board-level op).
        self.assertIsNone(silence_entries[0]["key"])


if __name__ == "__main__":
    unittest.main()
