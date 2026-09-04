# Postmortem Witness

Listener-only voice agent for incident bridge calls. It transcribes a
multi-speaker outage call, extracts structured events, and renders a live
incident dashboard that exports a finished postmortem.

## Hard rules
- Backend is owned by Chiraanth. Frontend is owned by Disha.
  NEVER edit files outside your own directory.
- shared/schema.json is a frozen contract. Do not modify it. If a change
  seems necessary, stop and tell the user.
- No database. No auth. No user accounts. State is in memory, single incident.
- Every LLM extraction call must return schema-valid JSON. "noise" is a
  valid and common answer. Never invent a hypothesis state change that was
  not explicitly spoken.
- Measure latency at every stage and expose it. It is a demo feature.

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

**The two-phase final.** A turn does not finalize once, it finalizes twice.
With `format_turns=true` you first get an unformatted final
(`end_of_turn=true`, `turn_is_formatted=false`), then a second message for the
*same* `turn_order` carrying the punctuated and cased text
(`turn_is_formatted=true`).

  - Latency is measured against the **unformatted** final. That is the moment
    the words actually existed.
  - Extraction runs **only** on the formatted final. Never extract twice off
    one turn, and never extract off an unformatted one.

**SpeakerRevision.** Diarization is not final when you first receive it. The
server sends `SpeakerRevision` messages that retroactively reassign the
speaker of turns it has *already* sent, keyed by `turn_order`.

  - This is why `turn_order` is carried end to end on every utterance and
    every event. It is the join key for amendments.
  - We do **not** buffer output to hide revisions. A revision emits a visible
    `speaker_amended` event carrying `previous_speaker_label`, and the UI
    shows the correction as a correction.
  - `SpeakerRevisionItem` has no timestamp of its own, so revision delay is
    measured locally: arrival time of the original turn, against arrival time
    of its revision.

**Diarization flags.** `speaker_labels=true` enables it; `max_speakers` (1–10)
caps the count when known in advance. Supported on `universal-3-5-pro`,
`universal-streaming-english` and `universal-streaming-multilingual`. Billed as
an add-on on top of streaming.

**Labels are not identities.** Streaming gives `A`, `B`, `C` and never a name.
Hence `speaker_label` and `speaker_name` are separate fields; `speaker_name`
stays null until a roll-call pass fills it in. That pass is not built yet.

**Relevant SDK surface** (`assemblyai==1.3.0`, `assemblyai.streaming.v3`):
`StreamingClient`, `StreamingClientOptions`, `StreamingParameters`
(`speaker_labels`, `max_speakers`, `format_turns`, `sample_rate`, `encoding`),
`StreamingEvents.{Begin,Turn,SpeakerRevision,Termination,Error}`, `TurnEvent`
(`turn_order`, `turn_is_formatted`, `end_of_turn`, `transcript`,
`speaker_label`, `words`), `Word` (`text`, `start`, `end`, `confidence`,
`word_is_final`, `speaker`), `SpeakerRevisionEvent.revisions` of
`SpeakerRevisionItem` (`turn_order`, `speaker_label`, `words`).

## Local setup (backend)
    python3.11 -m venv venv && source venv/bin/activate
    pip install -r backend/requirements.txt
    cp .env.example .env   # then fill in the keys

Run the ASR spike against a recorded call:

    python -m backend.transcription.stream --file demo/audio/call.wav
