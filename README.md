# Postmortem Witness

A listener-only voice agent for incident bridge calls.

It joins nothing and says nothing. It listens to a multi-speaker outage
call, transcribes it in real time, extracts the structured events that
actually matter — actions taken, hypotheses raised, hypotheses ruled out,
open threads — and renders them as a live incident dashboard. When the
incident closes, it exports a finished postmortem in Markdown.

The premise: the postmortem is the most valuable artifact an incident
produces and the one nobody has the energy to write at 4am. So write it
while the incident is still happening.

## Status

Pre-alpha. Hackathon build, submission 28 Sep 2026.

## Architecture

    audio ──▶ AssemblyAI streaming ──▶ utterance buffer ──▶ LLM extractor
                                                                │
                                          in-memory incident state
                                                                │
                                     WebSocket ──▶ React dashboard
                                                                │
                                                     Markdown export

- `backend/`  — Python 3.11, FastAPI, in-memory state. Owned by Chiraanth.
- `frontend/` — React + Vite dashboard. Owned by Disha.
- `shared/`   — the frozen event contract both sides code against.

No database. No auth. One incident at a time, held in memory.

## Setup

    python3.11 -m venv venv && source venv/bin/activate
    pip install -r backend/requirements.txt
    cp .env.example .env   # add ASSEMBLYAI_API_KEY and ANTHROPIC_API_KEY

## Transcription spike

Replay a recorded call through the streaming pipeline at real-time speed
and print speaker-labelled utterances with live latency stats:

    python -m backend.transcription.stream --file demo/audio/call.wav

    python -m backend.transcription.stream --mic          # live input
    python -m backend.transcription.stream --file x.wav --fast   # no throttle

## Licence

Unlicensed while private. TBD at submission.
