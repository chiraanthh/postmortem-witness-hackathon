"""Contradiction detection (contract v1.5.0 Feature 2).

Offline only — no live Haiku/ASR. Covers polarity lexicon, subject matching,
worker claim grounding, and status_change non-claims.
"""

from __future__ import annotations

import unittest
import uuid

from backend.extraction.worker import ExtractionWorker, RunningContext
from backend.state.machine import IncidentMachine
from backend.state.models import Event, ExtractedEvents, ExtractedFields, TurnKey
from backend.transcription.buffer import Utterance


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
    claim_subject: str | None = None,
    claim_assertion: str | None = None,
    claim_quote: str | None = None,
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
        claim_subject=claim_subject,
        claim_assertion=claim_assertion,
        claim_quote=claim_quote,
    )


def utt(text: str, speaker: str = "A", order: int = 0, epoch: int = 0) -> Utterance:
    return Utterance(
        turn_key=TurnKey(epoch, order),
        turn_keys=[TurnKey(epoch, order)],
        speaker_label=speaker,
        start_ms=1000,
        end_ms=3000,
        text=text,
    )


class _FakeParse:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"
        self.id = "req_test"


class _FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.last_kwargs = {}

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeParse(self._script.pop(0))


class _FakeClient:
    def __init__(self, *script):
        self.messages = _FakeMessages(script)


def fields(**kw) -> ExtractedFields:
    base = dict(
        type="noise",
        summary="",
        hypothesis_id=None,
        new_state=None,
        owner=None,
        confidence=0.5,
        evidence_quote=None,
        addressee=None,
        answers_thread_id=None,
        claim_subject=None,
        claim_assertion=None,
        claim_quote=None,
    )
    base.update(kw)
    return ExtractedFields(**base)


def one(**kw) -> ExtractedEvents:
    return ExtractedEvents(events=[fields(**kw)])


def worker_with(*script):
    return ExtractionWorker(
        client=_FakeClient(*script), model="test-model", on_log=lambda _m: None
    )


class TestOppositePolarity(unittest.TestCase):
    def test_healthy_then_pressure_emits_contradiction(self):
        m = IncidentMachine(incident_id="inc")
        first = event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool looks fine",
            text="The connection pool still looks fine, nowhere near the limit.",
            speaker="A",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="healthy",
            claim_quote="pool still looks fine, nowhere near the limit",
        )
        second = event(
            "action",
            summary="Pool under pressure",
            text="Actually the pool is under pressure and hammering connections.",
            speaker="B",
            order=2,
            ts=20_000,
            claim_subject="h-dbpool",
            claim_assertion="under pressure",
            claim_quote="pool is under pressure and hammering connections",
        )
        d1 = m.apply(first)
        self.assertFalse(any(o.op == "contradiction" for o in d1.ops))
        d2 = m.apply(second)
        contras = [o for o in d2.ops if o.op == "contradiction"]
        self.assertEqual(len(contras), 1)
        value = contras[0].value
        self.assertEqual(value.subject, "h-dbpool")
        self.assertEqual(value.earlier.event_id, first.event_id)
        self.assertEqual(value.later.event_id, second.event_id)
        # Both events remain on the board.
        self.assertIn(first.event_id, m.timeline)
        self.assertIn(second.event_id, m.timeline)
        snap = m.snapshot()
        self.assertEqual(len(snap["contradictions"]), 1)
        self.assertEqual(snap["contradictions"][0]["subject"], "h-dbpool")


class TestSamePolarity(unittest.TestCase):
    def test_restatement_does_not_contradict(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool fine",
            text="Pool utilisation is low, sitting at forty percent.",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="utilisation low",
            claim_quote="utilisation is low, sitting at forty percent",
        ))
        d = m.apply(event(
            "action",
            summary="Pool still fine",
            text="Yeah the pool still looks fine and healthy.",
            speaker="B",
            order=2,
            ts=20_000,
            claim_subject="h-dbpool",
            claim_assertion="healthy",
            claim_quote="pool still looks fine and healthy",
        ))
        self.assertFalse(any(o.op == "contradiction" for o in d.ops))
        self.assertEqual(m.contradictions, [])


class TestDifferentSubjects(unittest.TestCase):
    def test_different_subjects_no_contradiction(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool fine",
            text="The pool looks fine.",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="fine",
            claim_quote="pool looks fine",
        ))
        d = m.apply(event(
            "hypothesis",
            hypothesis_id="h-cache",
            summary="Cache exhausted",
            text="The cache is exhausted.",
            order=2,
            ts=20_000,
            claim_subject="h-cache",
            claim_assertion="exhausted",
            claim_quote="cache is exhausted",
        ))
        self.assertFalse(any(o.op == "contradiction" for o in d.ops))


class TestUngroundedClaim(unittest.TestCase):
    def test_ungrounded_quote_cleared_no_contradiction(self):
        w = worker_with(one(
            type="hypothesis",
            summary="Pool healthy",
            hypothesis_id="h-dbpool",
            confidence=0.9,
            claim_subject="h-dbpool",
            claim_assertion="healthy",
            claim_quote="pool looks healthy",
        ))
        out = w.extract(
            utt("The connection pool is sitting at forty percent."),
            RunningContext(),
        )
        self.assertEqual(len(out.events), 1)
        ev = out.events[0]
        self.assertIsNone(ev.claim_subject)
        self.assertIsNone(ev.claim_assertion)
        self.assertIsNone(ev.claim_quote)

        m = IncidentMachine(incident_id="inc")
        # Earlier grounded healthy claim on the board.
        m.apply(event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool fine",
            text="The pool looks fine.",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="fine",
            claim_quote="pool looks fine",
        ))
        # Worker-cleared event has no claim → no contradiction.
        d = m.apply(ev)
        self.assertFalse(any(o.op == "contradiction" for o in d.ops))


class TestStatusChangeNotClaim(unittest.TestCase):
    def test_ruled_out_does_not_contradict_prior_claim(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool exhausted",
            text="I think the pool is exhausted.",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="exhausted",
            claim_quote="pool is exhausted",
        ))
        d = m.apply(event(
            "status_change",
            hypothesis_id="h-dbpool",
            new_state="ruled_out",
            summary="Ruled out pool",
            text="We're ruling out the pool.",
            order=2,
            ts=20_000,
            # Even if a client wrongly attached claims, machine ignores them.
            claim_subject="h-dbpool",
            claim_assertion="ruled out",
            claim_quote="ruling out the pool",
        ))
        self.assertFalse(any(o.op == "contradiction" for o in d.ops))
        self.assertEqual(m.contradictions, [])

    def test_worker_clears_claims_on_status_change(self):
        w = worker_with(one(
            type="status_change",
            summary="Ruled out pool",
            hypothesis_id="h-dbpool",
            new_state="ruled_out",
            confidence=0.9,
            evidence_quote="ruling out the pool",
            claim_subject="h-dbpool",
            claim_assertion="ruled out",
            claim_quote="ruling out the pool",
        ))
        ctx = RunningContext(
            hypotheses=(("h-dbpool", "DB pool", "open"),),
        )
        out = w.extract(utt("We're ruling out the pool."), ctx)
        ev = out.events[0]
        self.assertEqual(ev.type, "status_change")
        self.assertIsNone(ev.claim_subject)
        self.assertIsNone(ev.claim_assertion)
        self.assertIsNone(ev.claim_quote)


class TestPolarityLexicon(unittest.TestCase):
    def test_pressure_word_in_hedged_quote_still_flags(self):
        """Machine precision is lexicon-based; 'pressure' matches even in hedges."""
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="pool",
            summary="Pool healthy",
            text="The pool looks healthy.",
            order=1,
            ts=10_000,
            claim_subject="pool",
            claim_assertion="healthy",
            claim_quote="pool looks healthy",
        ))
        d = m.apply(event(
            "action",
            summary="Probably under pressure",
            text="It's probably under pressure now.",
            speaker="B",
            order=2,
            ts=20_000,
            claim_subject="pool",
            claim_assertion="probably under pressure",
            claim_quote="probably under pressure",
        ))
        self.assertTrue(any(o.op == "contradiction" for o in d.ops))

    def test_hypothesis_id_form_matches(self):
        m = IncidentMachine(incident_id="inc")
        m.apply(event(
            "hypothesis",
            hypothesis_id="h-dbpool",
            summary="Pool fine",
            text="The pool looks fine.",
            order=1,
            ts=10_000,
            claim_subject="h-dbpool",
            claim_assertion="fine",
            claim_quote="pool looks fine",
        ))
        d = m.apply(event(
            "action",
            summary="Pool exhausted",
            text="The pool is exhausted.",
            order=2,
            ts=20_000,
            claim_subject="dbpool",
            claim_assertion="exhausted",
            claim_quote="pool is exhausted",
        ))
        self.assertTrue(any(o.op == "contradiction" for o in d.ops))


class TestGroundedClaimPassesThrough(unittest.TestCase):
    def test_worker_keeps_grounded_triple(self):
        w = worker_with(one(
            type="hypothesis",
            summary="Pool fine",
            hypothesis_id="h-dbpool",
            confidence=0.9,
            claim_subject="h-dbpool",
            claim_assertion="fine",
            claim_quote="pool looks fine",
        ))
        out = w.extract(utt("The pool looks fine at forty percent."), RunningContext())
        ev = out.events[0]
        self.assertEqual(ev.claim_subject, "h-dbpool")
        self.assertEqual(ev.claim_assertion, "fine")
        self.assertEqual(ev.claim_quote, "pool looks fine")


if __name__ == "__main__":
    unittest.main()
