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
from pathlib import Path

import anthropic
import httpx2

from backend.extraction.worker import ExtractionWorker, RunningContext, _slugify
from backend.state.models import (
    ExtractedEvents,
    ExtractedFields,
    TurnKey,
    quote_is_grounded,
)
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


def fields(**kw) -> ExtractedFields:
    """One candidate event, as the model would describe it."""
    base = dict(type="noise", summary="", hypothesis_id=None, new_state=None,
                owner=None, confidence=0.5, evidence_quote=None)
    base.update(kw)
    return ExtractedFields(**base)


def one(**kw) -> ExtractedEvents:
    """A model reply carrying a single candidate. The common case."""
    return ExtractedEvents(events=[fields(**kw)])


def many(*candidates: dict) -> ExtractedEvents:
    """A model reply carrying several candidates from one utterance."""
    return ExtractedEvents(events=[fields(**c) for c in candidates])


def only(out) -> object:
    """The single event an outcome produced.

    Asserts the count as a side effect: most utterances must still yield
    exactly one event, and a test that silently read events[0] out of a list
    of three would pass while the worker was over-splitting.
    """
    assert len(out.events) == 1, f"expected exactly 1 event, got {len(out.events)}"
    return out.events[0]


def _request():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def worker_with(*script, **kw):
    kw.setdefault("on_log", lambda _msg: None)
    return ExtractionWorker(client=_FakeClient(*script), model="test-model", **kw)


class TestPlumbing(unittest.TestCase):
    def test_happy_path_builds_a_valid_event(self):
        w = worker_with(one(type="action", summary="Rolled back deploy",
                               owner="Priya", confidence=0.9))
        out = w.extract(utt("I'm rolling back the deploy.", speaker="B", order=7, epoch=2))
        self.assertEqual(only(out).type, "action")
        self.assertEqual(only(out).summary, "Rolled back deploy")
        self.assertEqual(only(out).owner, "Priya")
        # Identity comes from the utterance, never from the model.
        self.assertEqual(only(out).connection_epoch, 2)
        self.assertEqual(only(out).turn_order, 7)
        self.assertEqual(only(out).speaker_label, "B")
        self.assertEqual(only(out).text, "I'm rolling back the deploy.")
        self.assertFalse(out.degraded)
        self.assertEqual(out.attempts, 1)

    def test_model_never_supplies_identity_fields(self):
        w = worker_with(one(type="noise"))
        out = w.extract(utt("whatever", order=3, epoch=1))
        self.assertNotIn("event_id", w._client.messages.last_kwargs.get("messages", [{}])[0].get("content", ""))
        self.assertTrue(only(out).event_id)
        self.assertEqual(only(out).turn_key, TurnKey(1, 3))

    def test_the_model_is_never_named_at_the_call_site(self):
        w = worker_with(one())
        w.extract(utt("hello"))
        self.assertEqual(w._client.messages.last_kwargs["model"], "test-model")

    def test_system_prompt_is_sent_and_marked_cacheable(self):
        w = worker_with(one())
        w.extract(utt("hello"))
        system = w._client.messages.last_kwargs["system"]
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_context_is_bounded_and_excludes_the_transcript(self):
        ctx = RunningContext(
            hypotheses=tuple((f"h{i}", f"cause {i}", "open") for i in range(40)),
            threads=tuple((f"t{i}", f"thread {i}") for i in range(40)),
        )
        w = worker_with(one())
        w.extract(utt("current utterance"), ctx)
        sent = w._client.messages.last_kwargs["messages"][0]["content"]
        self.assertIn("current utterance", sent)
        self.assertIn("h39", sent)      # newest kept
        self.assertNotIn("h0 ", sent)   # oldest dropped
        self.assertLess(len(sent), 4000)

    def test_structured_output_is_enforced_by_schema_not_prose(self):
        w = worker_with(one())
        w.extract(utt("hello"))
        self.assertIs(w._client.messages.last_kwargs["output_format"], ExtractedEvents)


class TestStatusChangeGuard(unittest.TestCase):
    def test_grounded_quote_is_accepted(self):
        w = worker_with(one(type="status_change", summary="DNS ruled out",
                               hypothesis_id="dns", new_state="ruled_out",
                               evidence_quote="we have ruled out DNS"))
        out = w.extract(utt("Okay, we have ruled out DNS, the logs are clean."))
        self.assertEqual(only(out).type, "status_change")
        self.assertEqual(only(out).new_state, "ruled_out")
        self.assertEqual(out.rejections, [])
        self.assertEqual(w.rejected_status_changes, 0)

    def test_invented_quote_is_refused_and_becomes_noise(self):
        w = worker_with(one(type="status_change", summary="Cache ruled out",
                               hypothesis_id="cache", new_state="ruled_out",
                               evidence_quote="we ruled out the cache"))
        out = w.extract(utt("The cache metrics look normal to me."))
        self.assertEqual(only(out).type, "noise")
        self.assertIsNone(only(out).new_state)
        self.assertIsNone(only(out).hypothesis_id)
        self.assertIn("not in the utterance", out.rejections[0].reason)
        self.assertEqual(out.rejections[0].claimed_quote, "we ruled out the cache")
        self.assertEqual(w.rejected_status_changes, 1)

    def test_quote_matching_cleaned_text_passes_grounding(self):
        """Cleanup rewrote the line; quote is in cleaned, not raw — still OK."""
        from backend.extraction.worker import quote_grounded_against_utterance

        raw = utt("I think it's the De Broglie.")
        raw.cleaned_text = "I think it's the deploy."
        self.assertTrue(
            quote_grounded_against_utterance("the deploy", raw)
        )
        self.assertFalse(
            quote_grounded_against_utterance("the deploy", utt("I think it's the De Broglie."))
        )

        w = worker_with(one(
            type="status_change",
            summary="Deploy confirmed",
            hypothesis_id="bad-deploy",
            new_state="confirmed",
            evidence_quote="it was the deploy",
        ))
        u = utt("Yeah, it was the De Broglie. Rolling back.")
        u.cleaned_text = "Yeah, it was the deploy. Rolling back."
        out = w.extract(u)
        self.assertEqual(only(out).type, "status_change")
        # Wire event still carries the raw ASR text.
        self.assertEqual(only(out).text, u.text)

    def test_status_change_for_an_unknown_hypothesis_is_accepted_but_counted(self):
        w = worker_with(one(type="status_change", summary="CDN ruled out",
                               hypothesis_id="cdn", new_state="ruled_out",
                               evidence_quote="we ruled out the CDN"))
        ctx = RunningContext(hypotheses=(("cache", "Cache eviction", "open"),))
        out = w.extract(utt("Right, we ruled out the CDN as well."), ctx)
        # Kept: the speaker did say it. Flagged: it dangles until the state
        # machine creates hypotheses on first reference.
        self.assertEqual(only(out).type, "status_change")
        self.assertEqual(only(out).hypothesis_id, "cdn")
        self.assertEqual(w.unknown_hypothesis_refs, 1)
        self.assertEqual(w.rejected_status_changes, 0)

    def test_status_change_for_a_known_hypothesis_is_not_flagged(self):
        w = worker_with(one(type="status_change", summary="Cache ruled out",
                               hypothesis_id="cache", new_state="ruled_out",
                               evidence_quote="we ruled out the cache"))
        ctx = RunningContext(hypotheses=(("cache", "Cache eviction", "open"),))
        out = w.extract(utt("Okay, we ruled out the cache."), ctx)
        self.assertEqual(only(out).type, "status_change")
        self.assertEqual(w.unknown_hypothesis_refs, 0)

    def test_status_change_without_a_quote_is_refused(self):
        w = worker_with(one(type="status_change", hypothesis_id="cache",
                               new_state="ruled_out", evidence_quote=None))
        out = w.extract(utt("The cache is fine."))
        self.assertEqual(only(out).type, "noise")

    def test_status_change_without_new_state_is_refused(self):
        w = worker_with(one(type="status_change", hypothesis_id="cache",
                               new_state=None, evidence_quote="the cache is fine"))
        out = w.extract(utt("The cache is fine."))
        self.assertEqual(only(out).type, "noise")
        self.assertEqual(out.rejections[0].reason, "no new_state given")

    def test_non_status_types_cannot_carry_state(self):
        w = worker_with(one(type="action", summary="did a thing",
                               hypothesis_id="cache", new_state="confirmed"))
        out = w.extract(utt("I restarted the workers."))
        self.assertIsNone(only(out).new_state)
        self.assertIsNone(only(out).hypothesis_id)

    def test_a_blank_summary_on_a_real_event_falls_back_and_is_counted(self):
        """summary is the only text the timeline shows; blank is invisible."""
        w = worker_with(one(type="status_change", summary="  ",
                            hypothesis_id="dns", new_state="ruled_out",
                            evidence_quote="DNS is fine"))
        out = w.extract(utt("DNS is fine, the resolver logs are clean."))
        self.assertEqual(only(out).type, "status_change")
        self.assertEqual(only(out).summary,
                         "DNS is fine, the resolver logs are clean.")
        self.assertEqual(w.blank_summaries, 1)
        self.assertIn("blank summaries", w.report())

    def test_the_fallback_summary_is_truncated(self):
        w = worker_with(one(type="action", summary=""))
        long = " ".join(f"word{i}" for i in range(30))
        out = w.extract(utt(long))
        self.assertTrue(only(out).summary.endswith("..."))
        self.assertEqual(len(only(out).summary.split()), 12)
        self.assertTrue(only(out).summary.startswith("word0 word1 "))

    def test_a_blank_summary_on_noise_is_expected_and_not_counted(self):
        w = worker_with(one(type="noise", summary=""))
        out = w.extract(utt("Yeah. Right. Okay."))
        self.assertEqual(only(out).summary, "")
        self.assertEqual(w.blank_summaries, 0)

    def test_summary_is_required_in_the_schema_handed_to_the_model(self):
        """Optional is why the model started omitting it inside a list."""
        schema = ExtractedEvents.model_json_schema()
        event = schema["$defs"]["ExtractedFields"]
        self.assertIn("summary", event["required"])
        self.assertIn("type", event["required"])

    def test_hypothesis_gets_an_id_even_if_the_model_omits_one(self):
        w = worker_with(one(type="hypothesis", summary="Cache eviction storm"))
        out = w.extract(utt("Might be the cache evicting hot keys."))
        self.assertEqual(only(out).hypothesis_id, "cache-eviction-storm")
        self.assertIsNone(only(out).new_state)


class TestEventList(unittest.TestCase):
    """Contract v1.3.0: one utterance, zero or more events."""

    def test_one_utterance_can_yield_a_status_change_and_an_action(self):
        w = worker_with(many(
            dict(type="status_change", summary="Deploy confirmed as cause",
                 hypothesis_id="bad-deploy", new_state="confirmed",
                 evidence_quote="it was the deploy"),
            dict(type="action", summary="Revert the retry change",
                 confidence=0.9),
        ))
        out = w.extract(utt(
            "Yeah, it was the deploy. I'll revert the retry change properly.",
            order=4, epoch=1,
        ))
        self.assertEqual([e.type for e in out.events],
                         ["status_change", "action"])
        # Both came from one turn, so they share the join key but not identity.
        self.assertEqual({e.turn_key for e in out.events}, {TurnKey(1, 4)})
        self.assertEqual(len({e.event_id for e in out.events}), 2)
        self.assertEqual(w.multi_event_utterances, 1)

    def test_an_empty_list_is_valid_and_yields_no_events(self):
        w = worker_with(ExtractedEvents(events=[]))
        out = w.extract(utt("..."))
        self.assertEqual(out.events, [])
        self.assertFalse(out.degraded)
        self.assertEqual(w.events_emitted, 0)

    def test_noise_alongside_a_real_event_is_dropped(self):
        w = worker_with(many(
            dict(type="action", summary="Restarted the workers"),
            dict(type="noise"),
        ))
        out = w.extract(utt("I restarted the workers."))
        self.assertEqual(only(out).type, "action")

    def test_repeated_noise_collapses_to_one_event(self):
        w = worker_with(many(dict(type="noise"), dict(type="noise")))
        out = w.extract(utt("Yeah. Right. Okay."))
        self.assertEqual(only(out).type, "noise")

    def test_the_same_event_twice_is_deduplicated(self):
        w = worker_with(many(
            dict(type="action", summary="Rolled back the deploy"),
            dict(type="action", summary="rolled back the DEPLOY"),
        ))
        out = w.extract(utt("I'm rolling back the deploy now."))
        self.assertEqual(only(out).summary, "Rolled back the deploy")

    def test_a_refused_status_change_does_not_leave_noise_beside_a_sibling(self):
        """The downgrade must not add a junk row when something real survived."""
        w = worker_with(many(
            dict(type="status_change", summary="Cache ruled out",
                 hypothesis_id="cache", new_state="ruled_out",
                 evidence_quote="we ruled out the cache"),
            dict(type="action", summary="Checked the cache metrics"),
        ))
        out = w.extract(utt("I had a look at the cache metrics."))
        self.assertEqual(only(out).type, "action")
        self.assertEqual(w.rejected_status_changes, 1)
        self.assertIn("not in the utterance", out.rejections[0].reason)

    def test_a_refused_status_change_alone_still_becomes_noise(self):
        """With nothing else in the list, the utterance still happened."""
        w = worker_with(one(type="status_change", summary="Cache ruled out",
                            hypothesis_id="cache", new_state="ruled_out",
                            evidence_quote="we ruled out the cache"))
        out = w.extract(utt("The cache metrics look normal to me."))
        self.assertEqual(only(out).type, "noise")


class TestResolution(unittest.TestCase):
    """Contract v1.3.0: the incident being declared over is now an event."""

    def test_resolution_is_accepted(self):
        w = worker_with(one(type="resolution",
                            summary="Incident declared resolved",
                            confidence=0.95))
        out = w.extract(utt("Okay, declaring this resolved at 3:15."))
        self.assertEqual(only(out).type, "resolution")
        self.assertEqual(only(out).summary, "Incident declared resolved")

    def test_resolution_carries_no_hypothesis_state_or_owner(self):
        """It is about the incident, so all three are meaningless on it."""
        w = worker_with(one(type="resolution", summary="Resolved",
                            hypothesis_id="bad-deploy", new_state="confirmed",
                            owner="Arjun"))
        out = w.extract(utt("We're calling it, incident closed."))
        self.assertIsNone(only(out).hypothesis_id)
        self.assertIsNone(only(out).new_state)
        self.assertIsNone(only(out).owner)

    def test_a_resolution_needs_no_quote(self):
        """Unlike status_change, it is not quote-gated. See CLAUDE.md."""
        w = worker_with(one(type="resolution", summary="Resolved",
                            evidence_quote=None))
        out = w.extract(utt("Declaring this resolved."))
        self.assertEqual(only(out).type, "resolution")
        self.assertEqual(w.rejected_status_changes, 0)


class TestFailureHandling(unittest.TestCase):
    def test_malformed_output_retries_once_then_degrades_to_noise(self):
        w = worker_with(None, None)   # two unparsable responses
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        # Exactly one noise event, never an empty list: "the model saw nothing
        # here" and "the call failed" must not look the same downstream.
        self.assertEqual(only(out).type, "noise")
        self.assertEqual(only(out).confidence, 0.0)
        self.assertEqual(w._client.messages.calls, 2)
        self.assertEqual(w.retries, 1)
        self.assertEqual(w.degraded, 1)

    def test_retry_succeeds_on_the_second_attempt(self):
        w = worker_with(None, one(type="action", summary="Restarted workers"))
        out = w.extract(utt("I restarted the workers."))
        self.assertFalse(out.degraded)
        self.assertEqual(only(out).type, "action")
        self.assertEqual(out.attempts, 2)

    def test_transient_error_is_retried(self):
        err = anthropic.APIConnectionError(request=_request())
        w = worker_with(err, one(type="noise"))
        out = w.extract(utt("hello"))
        self.assertFalse(out.degraded)
        self.assertEqual(w._client.messages.calls, 2)

    def test_fatal_error_is_not_retried(self):
        err = anthropic.AuthenticationError(
            "bad key",
            response=httpx2.Response(401, request=_request()),
            body=None,
        )
        w = worker_with(err, one(type="action"))
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        self.assertEqual(w._client.messages.calls, 1)   # no wasted retry
        self.assertIn("AuthenticationError", out.error)

    def test_an_unexpected_exception_never_escapes(self):
        w = worker_with(ValueError("something absurd"), ValueError("again"))
        out = w.extract(utt("hello"))
        self.assertTrue(out.degraded)
        self.assertEqual(only(out).type, "noise")

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
        client = self._client(captured, {"events": [{
            "type": "action", "summary": "Rolled back deploy",
            "hypothesis_id": None, "new_state": None, "owner": "Priya",
            "confidence": 0.9, "evidence_quote": None,
        }]})
        w = ExtractionWorker(client=client, model="test-model",
                             on_log=lambda _m: None)
        out = w.extract(utt("I'm rolling back the deploy."))

        self.assertEqual(only(out).type, "action")
        self.assertEqual(only(out).owner, "Priya")

        body = captured["body"]
        fmt = body["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertIs(fmt["schema"]["additionalProperties"], False)
        # The schema, not the prompt, is what constrains the reply. The reply
        # is now a list, so the per-event fields sit one level down.
        self.assertIn("events", fmt["schema"]["properties"])
        self.assertIn("evidence_quote", json.dumps(fmt["schema"]))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(
            body["system"][0]["cache_control"], {"type": "ephemeral"}
        )
        # Classification, not writing. Left unset the SDK samples at 1.0 and
        # recall stops being reproducible. parse() has no temperature
        # parameter, so this rides in via extra_body - assert it reaches the
        # wire, or an SDK upgrade could silently drop it.
        self.assertEqual(body["temperature"], 0)

    def test_guard_still_applies_to_a_real_sdk_response(self):
        captured = {}
        client = self._client(captured, {"events": [{
            "type": "status_change", "summary": "Cache ruled out",
            "hypothesis_id": "cache-eviction", "new_state": "ruled_out",
            "owner": None, "confidence": 0.95,
            "evidence_quote": "we ruled out the cache",
        }]})
        w = ExtractionWorker(client=client, model="test-model",
                             on_log=lambda _m: None)
        out = w.extract(utt("The cache hit rate looks normal to me."))
        self.assertEqual(only(out).type, "noise")
        self.assertEqual(w.rejected_status_changes, 1)

    def test_gateway_request_carries_temperature_zero_and_json_schema(self):
        captured = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            captured["body"] = json.loads(request.content)
            captured["headers"] = dict(request.headers)
            return httpx2.Response(
                200,
                json={
                    "request_id": "gw_test_1",
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"events": [{
                                "type": "noise",
                                "summary": "",
                                "hypothesis_id": None,
                                "new_state": None,
                                "owner": None,
                                "confidence": 0.5,
                                "evidence_quote": None,
                            }]}),
                        },
                        "finish_reason": "stop",
                    }],
                },
            )

        from backend.extraction.providers.assemblyai_gateway import AssemblyAIGateway

        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        provider = AssemblyAIGateway(
            model="claude-sonnet-4-6",
            api_key="aai-fake",
            http_client=client,
            call_log=Path("/tmp/pw_gw_test.jsonl"),
        )
        w = ExtractionWorker(provider=provider, on_log=lambda _m: None)
        out = w.extract(utt("hello there"))
        self.assertEqual(only(out).type, "noise")
        body = captured["body"]
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertIn("events", body["response_format"]["json_schema"]["schema"]["properties"])
        # No Bearer prefix — raw key in authorization header.
        self.assertEqual(captured["headers"].get("authorization"), "aai-fake")
        self.assertNotIn("Bearer", captured["headers"].get("authorization", ""))

    def test_a_real_sdk_response_can_carry_two_events(self):
        captured = {}
        client = self._client(captured, {"events": [
            {
                "type": "status_change", "summary": "Deploy confirmed",
                "hypothesis_id": "bad-deploy", "new_state": "confirmed",
                "owner": None, "confidence": 0.9,
                "evidence_quote": "it was the deploy",
            },
            {
                "type": "action", "summary": "Revert and add a test",
                "hypothesis_id": None, "new_state": None,
                "owner": "A", "confidence": 0.85, "evidence_quote": None,
            },
        ]})
        w = ExtractionWorker(client=client, model="test-model",
                             on_log=lambda _m: None)
        out = w.extract(utt("Yeah, it was the deploy, I'll revert it and put a test around it."))
        types = [e.type for e in out.events]
        self.assertEqual(types, ["status_change", "action"])


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
            types = [e.type for e in out.events]
            # Scored on the set, not on a single answer: since v1.3.0 an
            # utterance can yield several events, and an extra one alongside
            # the right one is a precision problem, not a recall miss. Taking
            # only the first would score a correct answer as wrong purely
            # because of ordering.
            ok_strict = c.expected in types
            ok_tolerant = bool(c.acceptable & set(types))
            got = c.expected if ok_strict else (
                next((t for t in types if t != "noise"), "noise")
            )
            strict += ok_strict
            tolerant += ok_tolerant
            if c.trap and "status_change" in types:
                false_pos += 1
            if c.expected == "status_change" and "status_change" not in types:
                missed += 1
            rows.append((c, got, types, ok_strict, ok_tolerant, out))

        n = len(LABELLED)
        real_changes = sum(1 for c in LABELLED if c.expected == "status_change")

        print("\n" + "=" * 74)
        print(f"EXTRACTION ACCURACY  model={worker.model}")
        print("=" * 74)
        for c, got, types, ok_s, ok_t, out in rows:
            mark = "OK  " if ok_s else ("~   " if ok_t else "MISS")
            flag = "  <-- FALSE POSITIVE" if (c.trap and "status_change" in types) else ""
            extra = f"  (+{len(types) - 1} more: {types})" if len(types) > 1 else ""
            print(f"{mark} want={c.expected:<14} got={got:<14} "
                  f"{c.text[:44]!r}{flag}{extra}")
            for rejection in out.rejections:
                print(f"       refused: {rejection.reason[:60]}")
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
