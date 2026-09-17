# Postmortem Witness — Frontend

Live incident-response dashboard. Four engineers talk through an outage; the
screen assembles the incident record in real time — timeline, hypotheses, open
threads, actions, and end-to-end latency. Nobody types into it.

> Owned by Disha. Backend (`/backend`) and the frozen contract (`/shared`) are
> owned separately and are never modified from here.

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

```bash
npm run build      # typecheck + production build
npm run typecheck  # tsc --noEmit
```

## How it works

```
fixture.json → mock emitter → dispatch → reducer → UI
```

The mock emitter (`src/mock/`) replays a ~77s payment outage over wall-clock
time, dispatching schema-valid `Event`s through the **same** reducer pathway a
live WebSocket will use. Swapping the mock for a real socket touches only
`src/transport/`; no UI or reducer changes.

- **State** (`src/state/reducer.ts`) is keyed maps — `timelineByTurnOrder`,
  `hypothesesById`, `threadsById`, `actionsById` — never append-only arrays.
  Sorted arrays are derived in `selectors.ts`.
- **Speaker labels** render as `A`–`D` while `speaker_name` is null (by
  contract). A later `speaker_amended` event upserts the existing turn in place
  by `turn_order` — no duplicate row — and briefly highlights the correction.
- **Hypothesis board** is the centerpiece: cards move between OPEN / RULED OUT /
  CONFIRMED with a dependency-free FLIP animation.
- **Latency overlay** reads the reducer's `latency` state and is always visible.

Types come from `../shared/events.ts` (contract v1.1.0), re-exported via
`src/contract.ts`. The contract is frozen and never edited here.

## Config

`.env` (see `.env.example`):

- `VITE_MOCK` — mock is on unless explicitly `false` (permanent demo fallback).
- `VITE_MOCK_SPEED` — playback multiplier; `1` = real ~77s pacing.
