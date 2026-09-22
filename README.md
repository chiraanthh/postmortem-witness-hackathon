# Postmortem Witness

Tracks which theories the room ruled out, and which questions nobody
answered, while the incident call is still happening.

- **Live demo:** https://web-production-88f09e.up.railway.app
- **Demo video:** _TODO_
- **Repo:** https://github.com/chiraanthh/postmortem-witness

Built for the [AssemblyAI Voice Agent Hackathon](https://lablab.ai) on
lablab.ai. Path: Realtime Speech-to-Text API with our own orchestration
(no AssemblyAI agent runtime).

## What it does

Listener-only agent on an incident bridge call. It never speaks. It
transcribes a multi-speaker outage call, extracts structured events, and
renders a live incident record: timeline, hypothesis board, open threads,
and actions with owners. At resolution it reconciles speaker attribution,
emits a silence summary, and exports a finished postmortem markdown.
Voice in, artifact out.

## Try it

1. Open https://web-production-88f09e.up.railway.app — you land on the
   **portal**, not the board.
2. **Run the live pipeline** (capped at 2 concurrent seats; counter
   updates live) or **Recorded run of the live pipeline** (uncapped
   replay of a real temp-0 capture — zero API seats). When seats are
   full, the recorded run is the primary action — try again in a few
   minutes for live.
3. Live call is **282.4 s** (~4.7 min). Use the scrubber to seek; jump
   near the end (~4:33) to re-fire reconciliation.
4. **Export** downloads `/export`. **Portal** returns to the opening
   screen and releases a live seat.

The audio is a **scripted** incident (`demo/script/incident_01.yaml`) with
**synthesised** voices via Sarvam **Bulbul v3** (four speakers: Arjun,
Disha, Priya, Rohan). Several lines are flagged `overlap: true` for
deliberate crosstalk. It is not a live mic capture.

## Named mechanics

**Hypothesis board.** Spoken hypotheses land as `open`. Explicit rule-outs
and confirmations move cards to `ruled_out` / `confirmed`. A
`status_change` that names an id nobody raised creates an **implicit**
hypothesis (`Hypothesis.implicit = true`) so the board still shows what
was ruled out.

**Silence detector.** Directed questions become threads with live
`unanswered_age_ms`. At resolution a `silence_summary` DiffOp freezes
counts. On the demo audio the TLS certificate thread stayed unanswered
**98885 ms** (~98.9 s) through resolution
(`demo/runs/verify_demo_v15b.txt` export).

**Grounding guard.** A `status_change` is applied only if the model
supplies an `evidence_quote` that code checks appears verbatim in the
utterance (`quote_grounded_against_utterance` in
`backend/extraction/worker.py`). Prompting alone is not trusted. On the
demo call the guard recorded **0 refusals** — Haiku classifies the
unevidenced cache hedge as `noise` before a status_change is proposed
(`demo/runs/verify_grounding_guard.txt`). It fires under test: see
`backend/tests/test_extraction.py` (ungrounded quote → noise +
rejection) and `backend/tests/test_grounding_refusal.py` (refusal
sidecar on the wire, board unchanged).

**Reconciliation pass.** Live labels are provisional. Streaming
`SpeakerRevision` items arrive as **one batch at session teardown** (not
turn-by-turn). The pipeline stores amendments by
`TurnKey(connection_epoch, turn_order)`, then at resolution emits one
`reconciliation` DiffOp that rewrites event speakers and recomputes
first-person action owners for the board and export.

**Unowned actions.** If no spoken owner and the line is not a
first-person commitment, `Action.unowned = true`. The demo embeds exactly
one (`someone needs to check the rate limit…`).

## Architecture

```
WAV / upload
  → AssemblyAI Universal Streaming v3
      (speaker_labels, keyterms_prompt, format_turns, pcm_s16le @ 16 kHz)
  → utterance buffer (SpeakerRevision join on TurnKey)
  → extraction worker on dedicated `incident-extract` thread
      (Claude Haiku 4.5, schema-constrained JSON, temperature 0)
  → grounding guard
  → in-memory state machine
  → WebSocket StateDiff ops
  → React dashboard
  → markdown /export
```

**Stack.** Backend: Python 3.11, FastAPI, pydantic v2, AssemblyAI
streaming SDK, Anthropic SDK. Frontend: React + Vite + TypeScript +
Tailwind. No database, no auth — one in-memory incident per process.

**Contract.** `shared/schema.json` **v1.5.0** (mirrored by
`shared/events.ts`, `backend/state/models.py`). Join key is always the
composite `TurnKey(connection_epoch, turn_order)`, never bare
`turn_order`.

**Providers.** Default `EXTRACTION_PROVIDER=anthropic`
(`AnthropicDirect`). `assemblyai_gateway` is implemented
(OpenAI-compatible); our account returned HTTP 400 without model access
during benchmarking — no silent fallback to Anthropic. Optional Qwen
cleanup (`qwen3.5-4b-32k-fast`) is off by default. Picker overrides reset
to Haiku on every fresh `/incident/start` and `/incident/restart`.

## Measured results

All extraction numbers at **temperature 0**.

| Metric | Value | Source |
|---|---|---|
| Extraction recall | **9/10** | `demo/runs/incident_01_v15b` via `extract_run` + `score_recall`. Miss: unowned “someone needs to…” mistyped as `thread`. |
| Labelled set | **14/15** strict, **15/15** tolerant, **0/4** state-change FPs | `demo/runs/provider_benchmark.json` → `haiku_direct` |
| Diarization (Hungarian vs GT) | **63.8%** live / **92.0%** post-revision | `demo/runs/incident_01/diarization.json` vs `demo/audio/incident_01.groundtruth.json` |
| Latency p50 | ASR **1343 ms**, extract **1238 ms**, e2e **2803 ms** | `demo/runs/verify_demo_v15b.txt` export; offline extract p50 **1253 ms** in `incident_01_v15b/extraction.json`; final queue wait **0 ms** |
| Keyterms | Fixed `BNS`→`DNS`, `De Broglie`→`deploy` | Without: `demo/runs/incident_01/stream.jsonl`. With `demo/script/keyterms.txt`: `demo/runs/incident_01_keyterms/stream.jsonl` |

Reproduce:

```bash
python -m backend.tools.extract_run --run demo/runs/incident_01_v15b
python -m backend.tools.score_recall --run demo/runs/incident_01_v15b

python -m unittest backend.tests.test_extraction.TestAccuracy -v

python -m backend.tools.score_diarization --run demo/runs/incident_01 \
  --groundtruth demo/audio/incident_01.groundtruth.json

# Fresh streaming run (costs AssemblyAI minutes):
python -m backend.tools.spike_run --file demo/audio/incident_01.wav \
  --out demo/runs/NAME
```

## What we learned about the API

- Streaming diarization exists on Universal Streaming v3
  (`speaker_labels=true`).
- `SpeakerRevision` items arrived as **one message at session teardown**
  (e.g. 18 items / 13 label changes on `incident_01_v15b`), not live —
  that drove the reconciliation design.
- `turn_order` and word timestamps **reset on reconnect** → every join
  key carries `connection_epoch`.
- With `format_turns=true`, the documented unformatted-then-formatted
  two-phase final did not appear: every `end_of_turn=true` turn was
  already `turn_is_formatted=true` (`dropped_unformatted: 0`).
- `PENDING` is a real speaker label (shows up unmatched in live
  diarization scoring).
- Running extraction on the ASR callback / pump path stalled PCM send
  and produced WebSocket **1006** closes mid-call; extraction is serial
  on `incident-extract` only.
- Leaving temperature unset made Haiku sample at 1.0; three runs over
  the same transcript scored differently. Temperature is pinned to **0**
  via `extra_body` (`anthropic==1.4.0` has no `temperature` on
  `messages.parse()`).

## Honest limitations

- Synthetic audio: true overlap is small (~2.3–2.9 s of frames in scored
  runs), so diarization looks better than a messy real bridge.
- Live labels wrong on about **36%** of labelled frames until
  reconciliation (100% − 63.8%).
- Contradiction detection is implemented and unit-tested but **not
  shipped** in the UI — requiring grounded `claim_*` fields dropped
  recall to 8/10 on `incident_01_claims`.
- Real uploaded audio will score lower than this scripted demo.
- Single in-memory incident; concurrent visitors join the running one.

## Run locally

```bash
git clone https://github.com/chiraanthh/postmortem-witness.git
cd postmortem-witness
python3.11 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
# pip install -r backend/requirements-dev.txt   # TTS / scoring tools

cp .env.example .env   # ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY
                       # SARVAM_API_KEY only to regenerate demo audio

cd frontend && npm ci && npm run build && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000
# open http://127.0.0.1:8000
```

```bash
python -m unittest discover -s backend/tests -t .
```

## Roadmap

- Live mic input and meeting-bot join (Zoom/Meet) instead of file replay
- Searchable incident history across calls
- Action sync into ticketing (Linear/Jira)
- Ship contradiction detection once claims can be populated without
  costing recall

## Team

Chiraanth S — backend and pipeline. Disha — frontend. RVCE and RVU.

## Deployment

Hosted on **Railway** as one long-running process (API + built frontend,
same origin). Not Vercel functions — WebSockets and in-memory state need
a persistent process.

- Public URL: https://web-production-88f09e.up.railway.app
- Required env: `ASSEMBLYAI_API_KEY`, `ANTHROPIC_API_KEY` (`SARVAM_API_KEY`
  optional, TTS regen only).
- Second browser hitting Start joins the running incident
  (`joined: true`); it does not start a second pipeline. Live seats are
  capped at 2 (`LIVE_PIPELINE_CAP`); recorded live-pipeline replay is uncapped.
- State is in-memory only — sleep or restart wipes the board.
- Railway Hobby stays warm when **Serverless** (formerly App Sleeping)
  is **off** (service Settings → Deploy). Optional:
  `.github/workflows/keepalive.yml` pings `/health` every 5 minutes when
  repo variable `KEEPALIVE_URL` is set.
- If the WebSocket drops mid-incident, the client reconnects and resyncs
  from snapshot; if reconnect fails it shows a banner instead of a
  frozen board.
