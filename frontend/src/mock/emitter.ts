import type { Dispatch } from "react";
import type { Event, Latency } from "../contract";
import type { DashboardAction } from "../state/types";
import fixtureRaw from "./fixture.json";

interface MockMessage {
  emit_at_ms: number;
  message: Event;
}

interface Fixture {
  incident_id: string;
  messages: MockMessage[];
}

const fixture = fixtureRaw as unknown as Fixture;

export const MOCK_INCIDENT_ID = fixture.incident_id;

/**
 * Synthesize a plausible rolling-p50 latency sample. The real backend measures
 * this end to end (ASR final -> extraction -> render). The contract only ever
 * carries latency inside IncidentState, so in the mock the transport surfaces
 * live samples through the LATENCY action — the reducer holds the value either
 * way. Extraction (an LLM call) is the heavy stage, exactly as in production.
 */
function sampleLatency(prev: Latency): Latency {
  const jitter = (base: number, spread: number) =>
    Math.max(1, base + (Math.random() - 0.5) * spread);
  // Exponential-ish smoothing so the numbers drift instead of strobing.
  const blend = (a: number, b: number) => a * 0.6 + b * 0.4;
  const asr = blend(prev.asr_ms || 300, jitter(300, 90));
  const extract = blend(prev.extract_ms || 520, jitter(540, 260));
  const e2e = blend(prev.e2e_ms || 900, asr + extract + jitter(120, 90));
  return {
    asr_ms: Math.round(asr),
    extract_ms: Math.round(extract),
    e2e_ms: Math.round(e2e),
  };
}

export interface MockController {
  stop: () => void;
}

/**
 * Replay the fixture over realistic wall-clock intervals, dispatching each
 * message through the SAME reducer pathway a live WebSocket will use. Nothing
 * loads instantly — the dashboard constructs itself as the call unfolds.
 *
 *   fixture -> emitter -> dispatch(INGEST) -> reducer -> UI
 *
 * `speed` scales wall-clock time (1 = the fixture's real ~77s pacing).
 */
export function startMockEmitter(
  dispatch: Dispatch<DashboardAction>,
  speed = 1
): MockController {
  const timers: number[] = [];
  let latency: Latency = { asr_ms: 0, extract_ms: 0, e2e_ms: 0 };
  let stopped = false;

  const s = speed > 0 ? speed : 1;

  const pushLatency = () => {
    latency = sampleLatency(latency);
    dispatch({ type: "LATENCY", latency });
  };

  // A slow idle heartbeat keeps the readout alive between utterances.
  const idle = window.setInterval(() => {
    if (!stopped) pushLatency();
  }, 1400);
  timers.push(idle);

  const messages = [...fixture.messages].sort(
    (a, b) => a.emit_at_ms - b.emit_at_ms
  );

  for (const item of messages) {
    const t = window.setTimeout(() => {
      if (stopped) return;
      // Fresh latency measurement lands with each processed turn.
      pushLatency();
      dispatch({ type: "INGEST", event: item.message });
    }, item.emit_at_ms / s);
    timers.push(t);
  }

  return {
    stop: () => {
      stopped = true;
      timers.forEach((id) => window.clearTimeout(id));
      window.clearInterval(idle);
    },
  };
}

/** Total wall-clock duration of the replay, for progress UI. */
export function mockDurationMs(speed = 1): number {
  const last = fixture.messages.reduce((m, x) => Math.max(m, x.emit_at_ms), 0);
  return last / (speed > 0 ? speed : 1);
}
