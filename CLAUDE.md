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

## Local setup (backend)
    python3.11 -m venv venv && source venv/bin/activate
    pip install -r backend/requirements.txt
    cp .env.example .env   # then fill in the keys

Run the ASR spike against a recorded call:

    python -m backend.transcription.stream --file demo/audio/call.wav
