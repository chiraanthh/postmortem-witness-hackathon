"""Extraction: plumbing offline, accuracy against the real model.

Two halves.

`TestPlumbing` and friends use a fake client and run anywhere. They cover the
things that must never break: malformed output degrades to noise, the pipeline
never raises, a status_change without a grounded quote is refused.

`TestAccuracy` calls the real model over 15 hand-labelled utterances and
reports accuracy and the status_change false-positive rate. It needs
ANTHROPIC_API_KEY and is skipped without one. Run it directly for the report:

    python -m unittest backend.tests.test_extraction -v
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass

import anthropic
import httpx2

from backend.extraction.worker import ExtractionWorker, RunningContext, _slugify
from backend.state.models import ExtractedFields, TurnKey, quote_is_grounded
from backend.transcription.buffer import Utterance


def utt(text, speaker="A", order=0, epoch=0, start=1000):
    return Utterance(
        turn_key=TurnKey(epoch, order),
        turn_keys=[TurnKey(epoch, order)],
        speaker_label=speaker,
        start_ms=start,
        end_ms=start + 2000,
        text=text,
    )


# --------------------------------------------------------------------------
# The labelled set
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    text: str
    expected: str
    # Types a reasonable annotator might also accept. Strict accuracy uses
    # `expected`; tolerant accuracy uses this.
    acceptable: frozenset[str]
    # The point of the case. A status_change here is a false positive.
    trap: bool = False
    note: str = ""


def case(text, expected, *also, trap=False, note=""):
    return Case(text, expected, frozenset({expected, *also}), trap, note)


LABELLED: list[Case] = [
    # --- clear actions ----------------------------------------------------
    case("I'm rolling back the deploy now, should be done in about two minutes.",
         "action", note="explicit, in progress"),
    case("I've restarted the worker pool in us-east.",
         "action", note="completed, past tense"),
    case("I just scaled the replicas up to twelve.",
         "action", note="completed, concrete"),

    # --- clear hypotheses -------------------------------------------------
    case("I think this might be the cache eviction problem we saw last month.",
         "hypothesis", note="hedged but a real proposed cause"),
    case("Could be the migration we shipped this morning.",
         "hypothesis", note="proposed cause, no action"),
    case("My money is on the connection pool being exhausted.",
         "hypothesis", note="idiomatic proposal"),

    # --- explicit state changes -------------------------------------------
    case("Okay, we have ruled out DNS, the resolver logs are completely clean.",
         "status_change", note="explicit ruling out, quotable"),
    case("Confirmed, it was the bad deploy. The rollback fixed it.",
         "status_change", note="explicit confirmation"),
    case("The cache is definitely not it, we ruled that out ten minutes ago.",
         "status_change", note="explicit ruling out, informal phrasing"),

    # --- traps: sound like state changes, are not -------------------------
    case("I don't think it's the cache, honestly.",
         "noise", "hypothesis", trap=True,
         note="doubt is not ruling out"),
    case("Cache hit rate looks completely normal to me.",
         "noise", trap=True,
         note="an observation, not a ruling out"),
    case("Did we ever actually rule out the replica lag?",
         "thread", "noise", trap=True,
         note="a question about ruling out is not a ruling out"),
    case("So it's probably the deploy then.",
         "hypothesis", "noise", trap=True,
         note="probably is not confirmed"),

    # --- ambiguous chatter that must be noise ------------------------------
    case("Sorry, can you hear me? I think I dropped off for a second.",
         "noise", note="pure coordination"),
    case("Yeah. Right. Okay, so.",
         "noise", note="filler, no content"),
]

TRAPS = [c for c in LABELLED if c.trap]


class TestLabelledSet(unittest.TestCase):
    """The set itself, checked before it is used to judge a model."""

    def test_fifteen_cases(self):
        self.assertEqual(len(LABELLED), 15)

    def test_covers_every_required_category(self):
        expected = {c.expected for c in LABELLED}
        self.assertTrue({"action", "hypothesis", "status_change", "noise"} <= expected)
        self.assertGreaterEqual(len([c for c in LABELLED if c.expected == "status_change"]), 3)
        self.assertGreaterEqual(len(TRAPS), 4)

    def test_no_trap_expects_a_status_change(self):
        for c in TRAPS:
            self.assertNotIn("status_change", c.acceptable, c.text)

    def test_real_state_changes_are_actually_quotable(self):
        """If our own guard would reject the true positives, it is too strict."""
        for c in LABELLED:
            if c.expected != "status_change":
                continue
            words = c.text.split()
            span = " ".join(words[: max(3, len(words) // 2)])
            self.assertTrue(quote_is_grounded(span, c.text), c.text)


# --------------------------------------------------------------------------
# Offline plumbing
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class _FakeClient:
    def __init__(self, *script):
        self.messages = _FakeMessages(script)


def fields(**kw):
    base = dict(type="noise", summary="", hypothesis_id=None, new_state=None,
                owner=None, confidence=0.5, evidence_quote=None)
    base.update(kw)
    return ExtractedFields(**base)


def _request():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def worker_with(*script, **kw):
    kw.setdefault("on_log", lambda _msg: None)
    return ExtractionWorker(client=_FakeClient(*script), model="test-model", **kw)


class TestPlumbing(unittest.TestCase):
    def test_happy_path_builds_a_valid_event(self):
        w = worker_with(fields(type="action", summary="Rolled back deploy",
                               owner="Priya", confidence=0.9))
        out = w.extract(utt("I'm rolling back the deploy.", speaker="B", order=7, epoch=2))
        self.assertEqual(out.event.type, "action")
        self.assertEqual(out.event.summary, "Rolled back deploy")
        self.assertEqual(out.event.owner, "Priya")
        # Identity comes from the utterance, never from the model.
        self.assertEqual(out.event.connection_epoch, 2)
        self.assertEqual(out.event.turn_order, 7)
        self.assertEqual(out.event.speaker_label, "B")
        self.assertEqual(out.event.text, "I'm rolling back the deploy.")
        self.assertFalse(out.degraded)
        self.assertEqual(out.attempts, 1)

    def test_model_never_supplies_identity_fields(self):
        w = worker_with(fields(type="noise"))
        out = w.extract(utt("whatever", order=3, epoch=1))
        self.assertNotIn("event_id", w._client.messages.last_kwargs.get("messages", [{}])[0].get("content", ""))
        self.assertTrue(out.event.event_id)
        self.assertEqual(out.event.turn_key, TurnKey(1, 3))

    def test_the_model_is_never_named_at_the_call_site(self):
        w = worker_with(fields())
        w.extract(utt("hello"))
        self.assertEqual(w._client.messages.last_kwargs["model"], "test-model")

    def test_system_prompt_is_sent_and_marked_cacheable(self):
        w = worker_with(fields())
        w.extract(utt("hello"))
        system = w._client.messages.last_kwargs["system"]
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_context_is_bounded_and_excludes_the_transcript(self):
        ctx = RunningContext(
            hypotheses=tuple((f"h{i}", f"cause {i}", "open") for i in range(40)),
            threads=tuple((f"t{i}", f"thread {i}") for i in range(40)),
        )
        w = worker_with(fields())
        w.extract(utt("current utterance"), ctx)
        sent = w._client.messages.last_kwargs["messages"][0]["content"]
        self.assertIn("current utterance", sent)
        self.assertIn("h39", sent)      # newest kept
        self.assertNotIn("h0 ", sent)   # oldest dropped
        self.assertLess(len(sent), 4000)

    def test_structured_output_is_enforced_by_schema_not_prose(self):
        w = worker_with(fields())
        w.extract(utt("hello"))
        self.assertIs(w._client.messages.last_kwargs["output_format"], ExtractedFields)


class TestStatusChangeGuard(unittest.TestCase):
    def test_grounded_quote_is_accepted(self):
        w = worker_with(fields(type="status_change", summary="DNS ruled out",
                               hypothesis_id="dns", new_state="ruled_out",
                               evidence_quote="we have ruled out DNS"))
        out = w.extract(utt("Okay, we have ruled out DNS, the logs are clean."))
        self.assertEqual(out.event.type, "status_change")
        self.assertEqual(out.event.new_state, "ruled_out")
        self.assertIsNone(out.rejected_status_change)
        self.assertEqual(w.rejected_status_changes, 0)

    def test_invented_quote_is_refused_and_becomes_noise(self):
        w = worker_with(fields(type="status_change", summary="Cache ruled out",
                               hypothesis_id="cache", new_state="ruled_out",
                               evidence_quote="we ruled out the cache"))
        out = w.extract(utt("The cache metrics look normal to me."))
        self.assertEqual(out.event.type, "noise")
        self.assertIsNone(out.event.new_state)
        self.assertIsNone(out.event.hypothesis_id)
        self.assertIn("not in the utterance", out.rejected_status_change)
        self.assertEqual(w.rejected_status_changes, 1)

    def test_status_change_for_an_unknown_hypothesis_is_accepted_but_counted(self):
        w = worker_with(fields(type="status_change", summary="CDN ruled out",
                               hypothesis_id="cdn", new_state="ruled_out",
                               evidence_quote="we ruled out the CDN"))
        ctx = RunningContext(hypotheses=(("cache", "Cache eviction", "open"),))
        out = w.extract(utt("Right, we ruled out the CDN as well."), ctx)
        # Kept: the speaker did say it. Flagged: it dangles until the state
        # machine creates hypotheses on first reference.
        self.assertEqual(out.event.type, "status_change")
        self.assertEqual(out.event.hypothesis_id, "cdn")
        self.assertEqual(w.unknown_hypothesis_refs, 1)
        self.assertEqual(w.rejected_status_changes, 0)

    def test_status_change_for_a_known_hypothesis_is_not_flagged(self):
        w = worker_with(fields(type="status_change", summary="Cache ruled out",
                               hypothesis_id="cache", new_state="ruled_out",
                               evidence_quote="we ruled out the cache"))
        ctx = RunningContext(hypotheses=(("cache", "Cache eviction", "open"),))
        out = w.extract(utt("Okay, we ruled out the cache."), ctx)
        self.assertEqual(out.event.type, "status_change")
        self.assertEqual(w.unknown_hypothesis_refs, 0)

    def test_status_change_without_a_quote_is_refused(self):
        w = worker_with(fields(type="status_change", hypothesis_id="cache",
                               new_state="ruled_out", evidence_quote=None))
        out = w.extract(utt("The cache is fine."))
        self.assertEqual(out.event.type, "noise")

    def test_status_change_without_new_state_is_refused(self):
        w = worker_with(fields(type="status_change", hypothesis_id="cache",
                               new_state=None, evidence_quote="the cache is fine"))
        out = w.extract(utt("The cache is fine."))
        self.assertEqual(out.event.type, "noise")
        self.assertEqual(out.rejected_status_change, "no new_state given")

    def test_non_status_types_cannot_carry_state(self):
        w = worker_with(fields(type="action", summary="did a thing",
                               hypothesis_id="cache", new_state="confirmed"))
        out = w.extract(utt("I restarted the workers."))
        self.assertIsNone(out.event.new_state)
        self.assertIsNone(out.event.hypothesis_id)

    def test_hypothesis_gets_an_id_even_if_the_model_omits_one(self):
        w = worker_with(fields(type="hypothesis", summary="Cache eviction storm"))
        out = w.extract(utt("Might be the cache evicting hot keys."))
        self.assertEqual(out.event.hypothesis_id, "cache-eviction-storm")
        self.assertIsNone(out.event.new_state)


class TestFailureHandling(unittest.TestCase):
    def test_malformed_output_retries_once_then_degrades_to_noise(self):
        w = worker_with(None, None)   # two unparsable responses
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        self.assertEqual(out.event.type, "noise")
        self.assertEqual(out.event.confidence, 0.0)
        self.assertEqual(w._client.messages.calls, 2)
        self.assertEqual(w.retries, 1)
        self.assertEqual(w.degraded, 1)

    def test_retry_succeeds_on_the_second_attempt(self):
        w = worker_with(None, fields(type="action", summary="Restarted workers"))
        out = w.extract(utt("I restarted the workers."))
        self.assertFalse(out.degraded)
        self.assertEqual(out.event.type, "action")
        self.assertEqual(out.attempts, 2)

    def test_transient_error_is_retried(self):
        err = anthropic.APIConnectionError(request=_request())
        w = worker_with(err, fields(type="noise"))
        out = w.extract(utt("hello"))
        self.assertFalse(out.degraded)
        self.assertEqual(w._client.messages.calls, 2)

    def test_fatal_error_is_not_retried(self):
        err = anthropic.AuthenticationError(
            "bad key",
            response=httpx2.Response(401, request=_request()),
            body=None,
        )
        w = worker_with(err, fields(type="action"))
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        self.assertEqual(w._client.messages.calls, 1)   # no wasted retry
        self.assertIn("AuthenticationError", out.error)

    def test_an_unexpected_exception_never_escapes(self):
        w = worker_with(ValueError("something absurd"), ValueError("again"))
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        self.assertEqual(out.event.type, "noise")

    def test_latency_is_recorded_even_when_degraded(self):
        w = worker_with(None, None)
        w.extract(utt("hello"))
        self.assertEqual(w.metrics.count("extract"), 1)
        self.assertIn("degraded", w.report())


class TestSlugify(unittest.TestCase):
    def test_slug_shapes(self):
        self.assertEqual(_slugify("Cache eviction storm"), "cache-eviction-storm")
        self.assertEqual(_slugify("  A  B  "), "a-b")
        self.assertEqual(_slugify("!!!"), "hypothesis")



class TestSdkIntegration(unittest.TestCase):
    """Against the real SDK over a mock transport - no API key, no network.

    The fake client above proves our logic. This proves the request we build
    is one the SDK actually accepts, and that structured output is enforced
    by schema rather than by parsing prose out of a reply.
    """

    def _client(self, captured, reply):
        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx2.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant",
                "model": "test-model",
                "content": [{"type": "text", "text": json.dumps(reply)}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })
        return anthropic.Anthropic(
            api_key="sk-ant-fake",
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    def test_request_uses_json_schema_structured_output(self):
        captured = {}
        client = self._client(captured, {
            "type": "action", "summary": "Rolled back deploy",
            "hypothesis_id": None, "new_state": None, "owner": "Priya",
            "confidence": 0.9, "evidence_quote": None,
        })
        w = ExtractionWorker(client=client, model="test-model",
                             on_log=lambda _m: None)
        out = w.extract(utt("I'm rolling back the deploy."))

        self.assertEqual(out.event.type, "action")
        self.assertEqual(out.event.owner, "Priya")

        body = captured["body"]
        fmt = body["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertIs(fmt["schema"]["additionalProperties"], False)
        # The schema, not the prompt, is what constrains the reply.
        self.assertIn("properties", fmt["schema"])
        self.assertIn("evidence_quote", fmt["schema"]["properties"])
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(
            body["system"][0]["cache_control"], {"type": "ephemeral"}
        )

    def test_guard_still_applies_to_a_real_sdk_response(self):
        captured = {}
        client = self._client(captured, {
            "type": "status_change", "summary": "Cache ruled out",
            "hypothesis_id": "cache-eviction", "new_state": "ruled_out",
            "owner": None, "confidence": 0.95,
            "evidence_quote": "we ruled out the cache",
        })
        w = ExtractionWorker(client=client, model="test-model",
                             on_log=lambda _m: None)
        out = w.extract(utt("The cache hit rate looks normal to me."))
        self.assertEqual(out.event.type, "noise")
        self.assertEqual(w.rejected_status_changes, 1)


# --------------------------------------------------------------------------
# Live accuracy
# --------------------------------------------------------------------------


@unittest.skipUnless(
    os.environ.get("ANTHROPIC_API_KEY"),
    "ANTHROPIC_API_KEY not set - live accuracy run skipped",
)
class TestAccuracy(unittest.TestCase):
    """Scores the real model on the labelled set and prints the report."""

    def test_accuracy_and_status_change_false_positives(self):
        worker = ExtractionWorker()
        context = RunningContext(
            hypotheses=(
                ("dns", "DNS resolution failing", "open"),
                ("cache-eviction", "Cache evicting hot keys", "open"),
                ("bad-deploy", "This morning's deploy broke it", "open"),
                ("replica-lag", "Read replicas lagging", "open"),
            ),
            threads=(("t1", "Nobody has checked the CDN yet"),),
        )

        rows, strict, tolerant, false_pos, missed = [], 0, 0, 0, 0
        for c in LABELLED:
            out = worker.extract(utt(c.text), context)
            got = out.event.type
            ok_strict = got == c.expected
            ok_tolerant = got in c.acceptable
            strict += ok_strict
            tolerant += ok_tolerant
            if c.trap and got == "status_change":
                false_pos += 1
            if c.expected == "status_change" and got != "status_change":
                missed += 1
            rows.append((c, got, ok_strict, ok_tolerant, out))

        n = len(LABELLED)
        real_changes = sum(1 for c in LABELLED if c.expected == "status_change")

        print("\n" + "=" * 74)
        print(f"EXTRACTION ACCURACY  model={worker.model}")
        print("=" * 74)
        for c, got, ok_s, ok_t, out in rows:
            mark = "OK  " if ok_s else ("~   " if ok_t else "MISS")
            flag = "  <-- FALSE POSITIVE" if (c.trap and got == "status_change") else ""
            print(f"{mark} want={c.expected:<14} got={got:<14} {c.text[:44]!r}{flag}")
            if out.rejected_status_change:
                print(f"       refused: {out.rejected_status_change[:60]}")
        print("-" * 74)
        print(f"strict accuracy      {strict}/{n}  ({100*strict/n:.0f}%)")
        print(f"tolerant accuracy    {tolerant}/{n}  ({100*tolerant/n:.0f}%)")
        print(f"state-change FP rate {false_pos}/{len(TRAPS)} traps  "
              f"({100*false_pos/len(TRAPS):.0f}%)")
        print(f"state changes missed {missed}/{real_changes}")
        print(f"{worker.report()}")
        print("=" * 74)

        # A false positive puts a state change on the board that nobody said.
        # That is the one failure this system must not have.
        self.assertEqual(false_pos, 0, "invented a state change nobody spoke")
        self.assertGreaterEqual(tolerant / n, 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
