# Postmortem Witness

Listener-only voice agent for incident bridge calls. It transcribes a
multi-speaker outage call, extracts structured events, and renders a live
incident dashboard that exports a finished postmortem.

## Hard rules
- Chiraanth owns the whole project (backend and frontend). The earlier
  directory-ownership split with Disha is retired — edit any path that
  the work requires.
- shared/schema.json is a frozen contract. Do not modify it. If a change
  seems necessary, stop and tell the user. It is currently at **v1.5.0**;
  see "Contract v1.5.0", "Contract v1.4.0" and "Contract v1.3.0" below for
  what changed and why.
- No database. No auth. No user accounts. State is in memory, single incident.
- Every LLM extraction call must return schema-valid JSON. "noise" is a
  valid and common answer. Never invent a hypothesis state change that was
  not explicitly spoken.
- Measure latency at every stage and expose it. It is a demo feature.

## Contract v1.5.0

One coherent bump covering silence accounting and claim contradictions so
we do not need a follow-up v1.6. `shared/schema.json` is the source of
truth; `shared/events.ts` and `backend/state/models.py` follow it. The
default extraction model stays Haiku (`EXTRACTION_MODEL` /
`claude-haiku-4-5-20251001`) — this bump does not change prompts or the
state machine yet, only the wire types those features will need.

**Event claim and address fields (required nullable).** Five fields land on
every event so a mid-call snapshot matches a fully streamed board:

- `addressee` — named person a direct question is addressed to; null when
  not a directed question or no name was spoken.
- `answers_thread_id` — set only when extraction explicitly links this
  event as the answer to an open thread. Clients must never infer it.
- `claim_subject` / `claim_assertion` / `claim_quote` — factual claim
  triple. Prefer an existing `hypothesis_id` as subject when the claim is
  about a board hypothesis. All three are null when there is no claim;
  when `claim_subject` is set, `claim_assertion` and `claim_quote` are
  required non-null (schema `allOf` plus worker enforcement).

**Thread silence fields (required).** Threads carry directed-question and
ageing metadata alongside the existing `thread_id` / `text` / `owner` /
`opened_at_ms` / `closed`:

- `asked_at_ms` — when the question opened (usually equals `opened_at_ms`).
- `addressee` — named person asked, if any.
- `answered` — true only when an event with matching `answers_thread_id`
  arrived (default false).
- `answered_at_ms` — when answered; null while unanswered.
- `unanswered_age_ms` — age at resolution for still-open/unanswered
  threads, else null while the incident is open (client may compute live
  as `clock_ms - asked_at_ms`).

**`silence_summary` DiffOp.** Emitted once at resolution. Value is a
`SilenceSummary`: question counts, longest unanswered duration, named
addressees who never answered, and the still-open threads. Stored on
`IncidentState.silence` (null until then).

**`contradiction` DiffOp.** Value is a `Contradiction` (`subject`,
`earlier` / `later` `ContradictionClaim`s keyed by turn). Appended to
`IncidentState.contradictions`. Claims come from the Event claim fields
above; detection logic is not part of this bump.

## Contract v1.4.0

Three board-facing changes, agreed with both owners. Driven by the state
machine: fields the live stream already carried have to be on the contract
so a client that loads a fresh `IncidentState` sees the same board as one
that streamed every `StateDiff` from the start.

**`Hypothesis.implicit` (bool, default false, required).** True when the
hypothesis was created by a `status_change` that named an id nobody had
raised. False for every hypothesis that arrived as a `hypothesis` event.
Must appear in both diffs and `snapshot()`.

**`Action.unowned` (bool, default false, required).** True when no spoken
owner was given and the utterance was not a first-person commitment, so
ownership could not be derived from `speaker_label`. The demo call embeds
exactly one of these. Must appear in both diffs and `snapshot()`.

**`reconciliation` is a first-class `DiffOpKind`.** The teardown
`SpeakerRevision` batch re-attributes every stored event by
`(connection_epoch, turn_order)`, recomputes first-person action ownership,
and emits one `DiffOp` whose `value` is a `ReconciliationSummary` listing
every speaker and owner change. Live traffic is `StateDiff` (a list of
ops), never a full `IncidentState`. A reconnect mid-call loads
`IncidentState` once, then resumes on diffs — those two views must agree.
`shared/events.ts` ships `applyDiff` / `applyDiffOp` for the frontend.

## Contract v1.3.0

Two changes, agreed with both owners. `shared/schema.json` is the source of
truth; `shared/events.ts` and `backend/state/models.py` follow it.

**`resolution` is a new EventType.** It marks an explicit spoken declaration
that the incident is over — "declaring this resolved at three fifteen". It is
a statement about the *incident*, not about any one hypothesis, so it carries
no `hypothesis_id` and no `new_state`.

Before this existed there was nowhere for that sentence to go, so the end of
the call came out as noise and the exported postmortem had no closing entry.

It must have been *said* - a call going quiet is not a resolution - but
unlike `status_change` it is not quote-gated in code, only in the prompt. A
resolution is one unmistakable sentence at the end of a call rather than a
claim about causation, and a second grounding gate would mostly reject valid
ones. If it starts hallucinating resolutions, gate it.

**One utterance now produces a list of events, not one.** A single line can
genuinely be two things — "yeah, it was the deploy, I'll revert it properly
and put a test around it" is a `status_change` and an `action`, and the old
one-event-per-utterance shape silently dropped the action.

  - The extractor returns a list. Empty and single-element lists are both
    valid. Most utterances still yield exactly one `noise` event.
  - Every event from one utterance carries the same
    `(connection_epoch, turn_order)`. **That pair identifies a turn, not an
    event.** It is still the join key a speaker amendment uses - all events
    from the turn get amended together - but the unique key is `event_id`,
    and that is what a list render must key on.
  - A degraded extraction still yields exactly one `noise` event, never an
    empty list. Empty means "the model saw nothing here"; a failure must not
    be indistinguishable from that.

## Stack
Python 3.11, FastAPI, websockets, pydantic v2, AssemblyAI streaming SDK,
Anthropic SDK for extraction. Frontend is React + Vite (not your concern).

## Deadline
Hackathon submission Sep 28 2026. Scope-cut aggressively.

## Known API behaviours

Verified against the live docs on 2026-09-04. Do not rewrite the streaming
client from memory — these details changed in the last year and the old
answers are still all over the internet.

Docs:
- https://www.assemblyai.com/docs/speech-to-text/universal-streaming
- https://www.assemblyai.com/docs/api-reference/streaming-api/streaming-api
- https://www.assemblyai.com/docs/faq/can-i-use-speaker-diarization-with-live-audio-transcription

**Endpoint.** `wss://streaming.assemblyai.com/v3/ws`. v3 is current.

**Auth.** API key in the `Authorization` header, with **no `Bearer` prefix**.
The `?token=` query parameter is for short-lived browser tokens only; we are
server-side, so we use the header. The key comes from `.env`, never a literal.

**Audio format.** Mono 16-bit PCM (`encoding=pcm_s16le`), `sample_rate=16000`.
Audio goes as **binary** WebSocket frames of 50–1000 ms each. Anything else
gets transcoded to that before it is sent.

**The two-phase final (docs vs our config).** AssemblyAI's docs still describe
a two-phase final with `format_turns=true`: an unformatted final first
(`end_of_turn=true`, `turn_is_formatted=false`), then a second message for the
*same* `turn_order` with punctuated/cased text (`turn_is_formatted=true`).

**That second phase is all we ever see.** On both the AMI spike and
`incident_01.wav`, every `end_of_turn=true` message arrives with
`turn_is_formatted=true`. The unformatted pass does not occur on our
configuration (`format_turns=true`, speaker labels on, current streaming
model). `dropped_unformatted` stays 0 and `turn_event_shapes` never lists
`formatted=false`.

  - **ASR latency** is therefore audio-chunk-sent → **formatted** final
    received. Timing the unformatted final left `asr_ms` with zero samples.
  - Extraction still runs **only** on the formatted final. If an unformatted
    pass ever reappears, do not extract from it, and do not double-count ASR
    on the formatted follow-up.

**SpeakerRevision.** Diarization is not final when you first receive it. The
server sends `SpeakerRevision` messages that retroactively reassign the
speaker of turns it has *already* sent, keyed by `turn_order`.

  - This is why `turn_order` is carried end to end on every utterance and
    every event. It is the join key for amendments - but only ever as half
    of one; see "The turn key" below.
  - We do **not** buffer output to hide revisions. A revision emits a visible
    `speaker_amended` event carrying `previous_speaker_label`, and the UI
    shows the correction as a correction.
  - `SpeakerRevisionItem` has no timestamp of its own, so revision delay is
    measured locally: arrival time of the original turn, against arrival time
    of its revision.

**The turn key is a pair, never a bare number.** `turn_order` is assigned by
the server and **restarts at zero on every new connection**. So does the word
timestamp clock. Turn 0 of the second connection is a completely different
turn from turn 0 of the first.

Since `turn_order` is the join key a speaker revision uses to find events that
were already emitted, treating it as unique would, after a single mid-call
reconnect, silently reassign one person's words to another. Nothing would
error. The transcript would just quietly become wrong.

  - Every event and every utterance carries `connection_epoch`, starting at 0
    and incremented on each reconnect.
  - **The join key everywhere downstream is the composite
    `(connection_epoch, turn_order)`.** Never look anything up by bare
    `turn_order`. In Python use `TurnKey` from `backend/state/models.py`; in
    the frontend use `turnKey()` from `shared/events.ts`.
  - `turn_order` is left exactly as the server sent it, rather than being
    rewritten into a global counter. What we log matches what the API said,
    which matters when reading a session back.
  - Word timestamps are rebased onto the audio clock separately, because those
    do need to be globally comparable.

**Diarization flags.** `speaker_labels=true` enables it; `max_speakers` (1–10)
caps the count when known in advance. Supported on `universal-3-5-pro`,
`universal-streaming-english` and `universal-streaming-multilingual`. Billed as
an add-on on top of streaming.

**Labels are not identities.** Streaming gives `A`, `B`, `C` and never a name.
Hence `speaker_label` and `speaker_name` are separate fields; `speaker_name`
stays null until a roll-call pass fills it in. That pass is not built yet.

**Relevant SDK surface** (`assemblyai==1.3.0`, `assemblyai.streaming.v3`):
`StreamingClient`, `StreamingClientOptions`, `StreamingParameters`
(`speaker_labels`, `max_speakers`, `format_turns`, `sample_rate`, `encoding`,
`keyterms_prompt`), `StreamingEvents.{Begin,Turn,SpeakerRevision,Termination,Error}`,
`TurnEvent` (`turn_order`, `turn_is_formatted`, `end_of_turn`, `transcript`,
`speaker_label`, `words`), `Word` (`text`, `start`, `end`, `confidence`,
`word_is_final`, `speaker`), `SpeakerRevisionEvent.revisions` of
`SpeakerRevisionItem` (`turn_order`, `speaker_label`, `words`).

**Keyterms.** v3 streaming supports word boosting via `keyterms_prompt` (array
of strings, max 100, each ≤50 chars). Loaded from `demo/script/keyterms.txt`,
not hardcoded. Docs:
https://www.assemblyai.com/docs/streaming/prompting-and-keyterms

## Local setup (backend)
    python3.11 -m venv venv && source venv/bin/activate
    pip install -r backend/requirements.txt
    cp .env.example .env   # then fill in the keys

Run the ASR spike against a recorded call:

    python -m backend.transcription.stream --file demo/audio/call.wav

Score the extractor against the hand-labelled set (needs ANTHROPIC_API_KEY):

    python -m unittest backend.tests.test_extraction -v

The extraction model is `EXTRACTION_MODEL` in the environment, defaulting to
`claude-haiku-4-5-20251001`. It is resolved in `backend/config.py` and never
named at a call site, so swapping models is one env var and no code change.

**Extraction runs at temperature 0** (`EXTRACTION_TEMPERATURE`). Leave it
there. It was unset once, which means the SDK sampled at 1.0, and three
extraction runs over the *same recorded transcript* scored 8/10, 5/10 and
7/10 against the demo script's embedded events. Nothing about the pipeline
had changed. Every accuracy and recall number was unfalsifiable, because any
regression could be waved away as sampling. Pinned, two consecutive runs
classify all 41 utterances identically.

`messages.parse()` has no `temperature` parameter in `anthropic==1.4.0`, so
it is passed through `extra_body`. `TestSdkIntegration` asserts it reaches
the request body, so an SDK upgrade that starts accepting it directly will
fail loudly instead of quietly resuming sampling at 1.0.

## Replaying the demo call

The synthetic incident call is the only thing that measures recall, since it
is the only audio whose contents are known. Three steps, and only the first
two cost money:

    python -m backend.tools.make_incident_audio        # cached, usually free
    python -m backend.tools.spike_run --file demo/audio/incident_01.wav \
        --out demo/runs/NAME                           # real time, ~4.5 min
    python -m backend.tools.extract_run --run demo/runs/NAME
    python -m backend.tools.score_diarization --run demo/runs/NAME \
        --groundtruth demo/audio/incident_01.groundtruth.json
    python -m backend.tools.score_recall --run demo/runs/NAME

`score_recall` is the one that matters: the type distribution can look
healthy while every real event is missed. If the script's timings are edited,
run `retime_script` rather than hand-editing `start_ms` - the overlaps are
solved from the measured synthesis durations, not guessed.
