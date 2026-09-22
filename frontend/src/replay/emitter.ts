import type { Dispatch } from "react";
import type { DiffOp, IncidentState } from "../contract";
import { CONTRACT_VERSION } from "../contract";
import type { DashboardAction, GroundingRefusal } from "../state/types";
import fixtureRaw from "./incident_01.fixture.json";

/**
 * Timed WebSocket frames captured from a live IncidentHub run of
 * demo/audio/incident_01.wav at EXTRACTION_TEMPERATURE=0.
 * Portal "Recorded run of the live pipeline" replays this — zero API.
 */

type WireEnvelope =
  | { type: "handshake"; contract_version: string }
  | {
      type: "snapshot";
      state: IncidentState;
      provenance?: Record<
        string,
        { provider: string; model: string; request_id?: string | null }
      >;
      refusals?: GroundingRefusal[];
    }
  | {
      type: "diff";
      ops: DiffOp[];
      provenance?: Record<
        string,
        { provider: string; model: string; request_id?: string | null }
      >;
      refusals?: GroundingRefusal[];
    }
  | { type: "provider_error"; message: string };

interface TimedFrame {
  emit_at_ms: number;
  message: WireEnvelope;
}

interface ReplayFixture {
  incident_id: string;
  contract_version: string;
  messages: TimedFrame[];
  duration_ms?: number;
}

const fixture = fixtureRaw as unknown as ReplayFixture;

export const REPLAY_INCIDENT_ID = fixture.incident_id;

/** Demo WAV the live capture was taken from — also used for audible replay. */
export const REPLAY_AUDIO_URL = "/media/demo/incident_01.wav";

export interface ReplayController {
  stop: () => void;
  /** Wall-clock duration of the timed replay after lead-in trim, at speed=1. */
  durationMs: number;
  /** Audio/board origin in the original capture (trimmed lead-in). */
  originEmitAtMs: number;
}

function isBoardContent(msg: WireEnvelope): boolean {
  if (msg.type !== "diff") return false;
  return (msg.ops ?? []).some((o) => o.op !== "set_latency");
}

function dispatchFrame(
  dispatch: Dispatch<DashboardAction>,
  msg: WireEnvelope
): void {
  if (!msg || typeof msg !== "object" || !("type" in msg)) return;

  if (msg.type === "handshake") {
    if (msg.contract_version !== CONTRACT_VERSION) {
      dispatch({
        type: "TRANSPORT_ERROR",
        message: `contract mismatch in replay: ${msg.contract_version}`,
      });
    }
    return;
  }

  if (msg.type === "snapshot") {
    dispatch({
      type: "SNAPSHOT",
      state: msg.state,
      provenance: msg.provenance,
      refusals: msg.refusals,
    });
    return;
  }

  if (msg.type === "diff") {
    dispatch({
      type: "DIFF",
      ops: msg.ops ?? [],
      provenance: msg.provenance,
      refusals: msg.refusals,
    });
    return;
  }

  if (msg.type === "provider_error") {
    dispatch({
      type: "PROVIDER_ERROR",
      message: msg.message || "extraction provider failed",
    });
  }
}

/**
 * Replay captured live-pipeline WebSocket frames.
 *
 * The raw capture has ~43s of latency-only heartbeats before the first board
 * op (empty call open). We trim that lead-in so the portal is not blank, then
 * play remaining frames with relative timing preserved. Snapshot applies
 * synchronously so the shell is never stuck on the boot incident id.
 */
export function startReplayEmitter(
  dispatch: Dispatch<DashboardAction>,
  speed = 1
): ReplayController {
  const timers: number[] = [];
  let stopped = false;
  const s = speed > 0 ? speed : 1;

  if (fixture.contract_version !== CONTRACT_VERSION) {
    dispatch({
      type: "TRANSPORT_ERROR",
      message: `replay fixture contract ${fixture.contract_version} ≠ client ${CONTRACT_VERSION}`,
    });
    return { stop: () => undefined, durationMs: 0, originEmitAtMs: 0 };
  }

  const frames = [...fixture.messages].sort(
    (a, b) => a.emit_at_ms - b.emit_at_ms
  );

  // Immediate openers — same as a live /ws connect.
  for (const fr of frames) {
    if (fr.message.type === "handshake" || fr.message.type === "snapshot") {
      dispatchFrame(dispatch, fr.message);
    }
  }

  const firstContent = frames.find((fr) => isBoardContent(fr.message));
  const origin = firstContent?.emit_at_ms ?? 0;

  let lastRel = 0;
  for (const frame of frames) {
    if (
      frame.message.type === "handshake" ||
      frame.message.type === "snapshot"
    ) {
      continue;
    }
    // Drop pure latency heartbeats before first board content.
    if (frame.emit_at_ms < origin && !isBoardContent(frame.message)) {
      continue;
    }
    const rel = Math.max(0, frame.emit_at_ms - origin);
    lastRel = Math.max(lastRel, rel);
    const t = window.setTimeout(() => {
      if (stopped) return;
      dispatchFrame(dispatch, frame.message);
    }, rel / s);
    timers.push(t);
  }

  return {
    durationMs: lastRel,
    originEmitAtMs: origin,
    stop: () => {
      stopped = true;
      timers.forEach((id) => window.clearTimeout(id));
    },
  };
}

/** Portal default: faster than realtime so the ~4.7 min capture is demoable. */
export function portalReplaySpeed(): number {
  const fromEnv = Number(import.meta.env.VITE_REPLAY_SPEED ?? "4");
  return fromEnv > 0 ? fromEnv : 4;
}

export function replayDurationMs(speed = 1): number {
  const frames = fixture.messages;
  const first = frames.find((fr) => isBoardContent(fr.message));
  const origin = first?.emit_at_ms ?? 0;
  const last = frames.reduce((m, x) => Math.max(m, x.emit_at_ms), 0);
  return Math.max(0, last - origin) / (speed > 0 ? speed : 1);
}
