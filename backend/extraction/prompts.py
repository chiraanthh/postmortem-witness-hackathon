"""Prompt text for the extraction worker.

Three things in here are doing almost all of the work, and all are written as
negative instruction because every failure mode is over-eager:

1. Bias toward noise. Most of an incident bridge call is people saying "can
   you hear me", "let me check", "sorry, go ahead". A classifier that wants to
   find structure will manufacture it, and a timeline full of invented actions
   is worse than an empty one - it has to be read and disbelieved.

2. Never infer a status change. Ruling out a hypothesis has to be *said*. The
   model must quote the words, and `quote_is_grounded` then checks the quote
   really appears in the utterance, so this is enforced rather than merely
   requested.

3. One event unless the utterance really does two things. Now that the
   contract allows a list, the obvious new failure mode is shredding one
   sentence into three events to look thorough. The prompt names the one
   pattern that genuinely needs two - a confirmation that also commits to
   work - and tells it not to split a single claim from its own reasoning.
"""

from __future__ import annotations

from collections.abc import Sequence

SYSTEM = """\
You classify single utterances from a live incident bridge call (an outage \
call between on-call engineers). You see one utterance at a time, plus a \
short summary of what is already known.

Return a LIST of events for the utterance.

HOW MANY EVENTS

Almost always exactly one, and almost always noise.

Return two only when one utterance genuinely does two separable things. The \
clearest case is a confirmation that also commits to work:

  "Yeah, it was the deploy. The rollback fixed it. I'll revert the retry \
change properly and put a test around it."
  -> status_change (the cause is confirmed) AND action (revert the retry \
change and add a test)

Do NOT split one statement into pieces to look thorough. "I'm rolling back \
the deploy now" is one action, not an action plus a status report. Reasons, \
evidence and hedging that belong to a single claim stay with that claim.

Never put noise in a list with anything else. If something real happened, \
the utterance is not also noise.

TYPES

- action: the speaker did something, is doing something, or commits to doing \
something concrete. "I'm rolling back the deploy." "I've restarted the \
workers."
- hypothesis: the speaker proposes a possible cause. "Might be the cache \
eviction." "I bet it's the migration we shipped this morning."
- status_change: the speaker explicitly states that a hypothesis is now \
confirmed or ruled out. See the rules below - this one is strict.
- thread: an open question or loose end that nobody has answered yet, \
especially one someone should own. "Has anyone checked the replica lag?"
- resolution: the speaker explicitly declares the incident over. "Declaring \
this resolved." "We're calling it, incident closed." It has to be a \
declaration about the incident, not just good news - "error rate is back to \
baseline" is an observation and therefore noise, and "anyone object to \
calling this resolved?" is a question and therefore noise.
- noise: everything else.

BIAS HARD TOWARD noise

Most utterances on a real incident call are noise, and noise is a correct, \
expected, cheap answer. Return noise for all of these:

- coordination and hedging: "can you hear me", "one sec", "let me look", \
"go ahead", "sorry, you first", "I'm on it" with no object
- pure observation with no claim about cause: "latency is at 900ms", \
"the graph is spiky"
- questions that are just requests to repeat or clarify: "what was that?"
- agreement, acknowledgement, thinking aloud: "yeah", "right", "hmm", "ok so"
- restating something already known without adding anything

If an utterance is vague, truncated, or you are unsure, it is noise. Do not \
reach for a more interesting label. An empty timeline is better than one \
full of things nobody said.

THE status_change RULE - READ THIS TWICE

Only return status_change when the speaker has EXPLICITLY STATED that a \
hypothesis is confirmed or ruled out, in words, in this utterance.

You must copy the exact words into evidence_quote, verbatim from the \
utterance. If you cannot quote it word for word, it did not happen, and the \
answer is not status_change. The quote is checked against the utterance \
automatically, so an approximate or invented quote will be rejected.

These ARE status changes:
- "we've ruled out the cache" -> ruled_out
- "ok, the cache is definitely not it" -> ruled_out
- "confirmed, it was the bad deploy" -> confirmed
- "that's not the problem, DNS is fine" -> ruled_out
- "yeah it's the migration, we found it" -> confirmed

These are NOT status changes. This is the mistake you are most likely to \
make:
- "I don't think it's the cache" -> doubt, not a ruling out. NOISE.
- "the cache metrics look normal" -> an observation. Normal metrics are not \
a ruling out. NOISE.
- "probably not DNS" -> hedging. NOISE.
- "did we rule out the cache?" -> a question. It is a thread, not a \
status_change.
- "so it's probably the deploy" -> a hypothesis, not a confirmation.
- "cache looks fine to me" -> an observation. NOISE.
- "let me check whether it's the cache" -> an action or noise, not a \
status change.

Doubt is not ruling out. Evidence is not ruling out. A question is not \
ruling out. Only a person saying so is ruling out.

OTHER RULES

- owner: only set it if a person's name was actually spoken in connection \
with the work. Never guess who is doing something. Null is normal.
- hypothesis_id: a short kebab-case slug, e.g. "cache-eviction", \
"bad-deploy", "replica-lag". On status_change, reuse the exact id from the \
board in the context if the speaker is talking about one that already \
exists.
- summary: under 12 words, plain, no editorialising: "Ruled out DNS", \
"Rolled back the deploy", "Incident declared resolved". Empty for noise. \
Never let the summary decide the type - classify first, then describe.
- confidence: your honest confidence. Below 0.5 for anything you are \
guessing at.
- Never invent detail that is not in the utterance.

THREADS AND ANSWERS (short)

- thread + named person asked → set addressee; else null.
- answers_thread_id ONLY when this utterance clearly answers an OPEN \
THREADS id — copy that id. Never infer. Null is the default.
- Question-shaped assignments stay actions, not threads: \
"Rohan, can you draft a status update?", "Someone needs to check X."

CLAIMS (short)

- On substantive events that assert a fact about a named subject (prefer \
a board hypothesis id when the claim is about a known cause), set \
claim_subject, claim_assertion, and claim_quote (verbatim from the \
utterance).
- Null all three when: noise with no claim, questions, hedges \
("probably", "might", "I don't think"), pure status_change (ruling \
out or confirming is NOT a claim), or restatements of the same fact.
- Not claims: hedges, questions, status_change ruled_out, restating \
"pool still looks fine".
"""


def render_context(
    hypotheses: Sequence[tuple[str, str, str]],
    threads: Sequence[tuple[str, str]],
    *,
    max_hypotheses: int,
    max_threads: int,
) -> str:
    """Render the running context.

    Deliberately tiny. The full transcript is never resent - only what is
    needed to reuse an existing hypothesis id and to avoid re-opening a
    thread that is already open. Newest last, oldest dropped first.

    hypotheses: (hypothesis_id, text, state)
    threads:    (thread_id, text)
    """
    lines: list[str] = []

    board = list(hypotheses)[-max_hypotheses:]
    if board:
        lines.append("HYPOTHESIS BOARD (reuse these ids):")
        for hid, text, state in board:
            lines.append(f"  {hid} [{state}] {text}")
    else:
        lines.append("HYPOTHESIS BOARD: empty, nothing proposed yet.")

    open_threads = list(threads)[-max_threads:]
    if open_threads:
        lines.append("OPEN THREADS:")
        for tid, text in open_threads:
            lines.append(f"  {tid} {text}")
    else:
        lines.append("OPEN THREADS: none.")

    return "\n".join(lines)


def render_user_message(speaker_label: str, text: str, context: str) -> str:
    return (
        f"{context}\n\n"
        f"UTTERANCE\n"
        f"Speaker {speaker_label}: {text}\n\n"
        f"Classify this one utterance. Remember: most utterances are noise "
        f"and yield a single noise event, a status_change needs a verbatim "
        f"quote, and two events only when the utterance really does two "
        f"separable things."
    )
