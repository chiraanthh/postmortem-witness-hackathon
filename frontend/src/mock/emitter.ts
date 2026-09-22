/**
 * Hand-authored scaffold fixture for frontend-only development.
 * Enabled only when VITE_MOCK=true — never reachable from the portal.
 * Portal "recorded run" uses frontend/src/replay/incident_01.fixture.json
 * (a live IncidentHub capture of demo/audio/incident_01.wav).
 */
import type { Dispatch } from "react";
import type { DiffOp, Event, Latency, ReconciliationSummary } from "../contract";
import type { DashboardAction, GroundingRefusal } from "../state/types";
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

function sampleLatency(prev: Latency): Latency {
  const jitter = (base: number, spread: number) =>
    Math.max(1, base + (Math.random() - 0.5) * spread);
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
 * Replay the fixture, then land a reconciliation DiffOp and a grounding
 * refusal — same shapes the live backend sends — so the visible beats need
 * no server.
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
      pushLatency();
      const raw = item.message as Event & { connection_epoch?: number };
      const event: Event = {
        ...raw,
        connection_epoch: raw.connection_epoch ?? 0,
      };
      dispatch({ type: "INGEST", event });
    }, item.emit_at_ms / s);
    timers.push(t);
  }

  // Mid-call: unevidenced status_change declined by the grounding guard.
  const refusalAt = 42000;
  timers.push(
    window.setTimeout(() => {
      if (stopped) return;
      const refusal: GroundingRefusal = {
        kind: "grounding_refusal",
        recorded: false,
        claimed_hypothesis_id: "cache",
        claimed_new_state: "ruled_out",
        claimed_quote: "we ruled out the cache",
        reason:
          "evidence quote is not in the utterance (claimed 'we ruled out the cache')",
        utterance_text:
          "So it's probably not the cache, I'd say we drop that one.",
        speaker_label: "C",
        timestamp_ms: 98223,
        connection_epoch: 0,
        turn_order: 19,
        provider: "anthropic",
        model: "claude-haiku-4-5-20251001",
      };
      dispatch({ type: "DIFF", ops: [], refusals: [refusal] });
    }, refusalAt / s)
  );

  // Teardown beat: same ReconciliationSummary shape the live machine emits.
  const lastEmit = messages.reduce((m, x) => Math.max(m, x.emit_at_ms), 0);
  const reconAt = lastEmit + 1800;
  timers.push(
    window.setTimeout(() => {
      if (stopped) return;
      const summary: ReconciliationSummary = {
        events_touched: 2,
        speakers: [
          {
            turn_key: "e0/t4",
            event_id: "8f2a1c00-0005-4a10-8a01-000000000000",
            previous_speaker_label: "A",
            speaker_label: "C",
          },
          {
            turn_key: "e0/t1",
            event_id: "8f2a1c00-0002-4a10-8a01-000000000000",
            previous_speaker_label: "A",
            speaker_label: "B",
          },
        ],
        owners: [],
      };
      const ops: DiffOp[] = [
        { op: "reconciliation", key: null, value: summary },
      ];
      dispatch({ type: "DIFF", ops });
    }, reconAt / s)
  );

  // Optional offline silence_summary so the threads panel shows resolution stats.
  const silenceAt = lastEmit + 2200;
  timers.push(
    window.setTimeout(() => {
      if (stopped) return;
      const openThreads = [
        {
          thread_id: "8f2a1c00-0017-4a10-8a01-000000000000",
          text: "Customer comms owner — is a post going out?",
          asked_at_ms: 52400,
          addressee: null,
          unanswered_age_ms: 20000,
        },
      ];
      const ops: DiffOp[] = [
        {
          op: "silence_summary",
          key: null,
          value: {
            questions_asked: 3,
            questions_unanswered: 1,
            longest_unanswered_ms: 20000,
            unanswered_addressees: [],
            open_threads: openThreads,
          },
        },
      ];
      dispatch({ type: "DIFF", ops });
    }, silenceAt / s)
  );

  return {
    stop: () => {
      stopped = true;
      timers.forEach((id) => window.clearTimeout(id));
      window.clearInterval(idle);
    },
  };
}

export function mockDurationMs(speed = 1): number {
  const last = fixture.messages.reduce((m, x) => Math.max(m, x.emit_at_ms), 0);
  return (last + 2500) / (speed > 0 ? speed : 1);
}
