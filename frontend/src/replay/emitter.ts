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

/** Speeds offered in the replay chrome. Default is realtime (1). */
export const REPLAY_SPEEDS = [1, 2, 3] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];

function isBoardContent(msg: WireEnvelope): boolean {
  if (msg.type !== "diff") return false;
  return (msg.ops ?? []).some((o) => o.op !== "set_latency");
}

function isReconciliation(msg: WireEnvelope): boolean {
  if (msg.type !== "diff") return false;
  return (msg.ops ?? []).some((o) => o.op === "reconciliation");
}

function reconciliationEventsTouched(msg: WireEnvelope): number | null {
  if (msg.type !== "diff") return null;
  for (const op of msg.ops ?? []) {
    if (op.op === "reconciliation" && op.value && typeof op.value === "object") {
      const n = (op.value as { events_touched?: unknown }).events_touched;
      return typeof n === "number" ? n : null;
    }
  }
  return null;
}

const sortedFrames = [...fixture.messages].sort(
  (a, b) => a.emit_at_ms - b.emit_at_ms
);
const firstContent = sortedFrames.find((fr) => isBoardContent(fr.message));
const ORIGIN_EMIT_AT_MS = firstContent?.emit_at_ms ?? 0;
const reconFrame = sortedFrames.find((fr) => isReconciliation(fr.message));
const RECON_EMIT_AT_MS = reconFrame?.emit_at_ms ?? 0;
const RECON_EVENTS_TOUCHED =
  (reconFrame && reconciliationEventsTouched(reconFrame.message)) ?? 0;

/**
 * Board-facing reconciliation count from this capture.
 * Distinct from ASR SpeakerRevision "label changes" (see README): only
 * turns that produced stored board events are rewritten.
 */
export const REPLAY_RECONCILIATION = {
  events_touched: RECON_EVENTS_TOUCHED,
  emit_at_ms: RECON_EMIT_AT_MS,
  /** Capture-clock ms after lead-in trim (audio/board time ≈ this). */
  relative_ms: Math.max(0, RECON_EMIT_AT_MS - ORIGIN_EMIT_AT_MS),
} as const;

export interface ReplayController {
  stop: () => void;
  setSpeed: (speed: number) => void;
  pause: () => void;
  resume: () => void;
  seek: (relativeMs: number) => void;
  /** Apply every frame through the reconciliation DiffOp, then continue. */
  jumpToReconciliation: () => void;
  /** Wall-clock duration of the timed replay after lead-in trim, at speed=1. */
  durationMs: number;
  /** Audio/board origin in the original capture (trimmed lead-in). */
  originEmitAtMs: number;
  reconciliationRelMs: number;
  /** Current capture-relative position (ms after lead-in). */
  positionMs: () => number;
  isPaused: () => boolean;
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
  let stopped = false;
  let speedFactor = speed > 0 ? speed : 1;
  let timers: number[] = [];
  /** Capture-clock cursor: frames with emit_at_ms <= this are applied. */
  let cursorEmit = ORIGIN_EMIT_AT_MS;
  /** Wall time when cursorEmit was last anchored. */
  let anchorWall = performance.now();
  let paused = false;
  const applied = new Set<number>();

  if (fixture.contract_version !== CONTRACT_VERSION) {
    dispatch({
      type: "TRANSPORT_ERROR",
      message: `replay fixture contract ${fixture.contract_version} ≠ client ${CONTRACT_VERSION}`,
    });
    return {
      stop: () => undefined,
      setSpeed: () => undefined,
      pause: () => undefined,
      resume: () => undefined,
      seek: () => undefined,
      jumpToReconciliation: () => undefined,
      durationMs: 0,
      originEmitAtMs: 0,
      reconciliationRelMs: 0,
      positionMs: () => 0,
      isPaused: () => false,
    };
  }

  // Timed queue: everything after lead-in trim except handshake/snapshot
  // (those apply immediately like a live /ws connect).
  const queue: { index: number; emit_at_ms: number; message: WireEnvelope }[] =
    [];
  sortedFrames.forEach((frame, index) => {
    if (
      frame.message.type === "handshake" ||
      frame.message.type === "snapshot"
    ) {
      dispatchFrame(dispatch, frame.message);
      applied.add(index);
      return;
    }
    if (frame.emit_at_ms < ORIGIN_EMIT_AT_MS && !isBoardContent(frame.message)) {
      applied.add(index);
      return;
    }
    queue.push({ index, emit_at_ms: frame.emit_at_ms, message: frame.message });
  });

  const lastEmit = queue.reduce(
    (m, f) => Math.max(m, f.emit_at_ms),
    ORIGIN_EMIT_AT_MS
  );
  const durationMs = Math.max(0, lastEmit - ORIGIN_EMIT_AT_MS);

  function clearTimers() {
    timers.forEach((id) => window.clearTimeout(id));
    timers = [];
  }

  function applyThrough(emitAtInclusive: number) {
    for (const item of queue) {
      if (applied.has(item.index)) continue;
      if (item.emit_at_ms > emitAtInclusive) break;
      dispatchFrame(dispatch, item.message);
      applied.add(item.index);
    }
    cursorEmit = Math.max(cursorEmit, emitAtInclusive);
  }

  function readCursorEmit(): number {
    const wallElapsed = performance.now() - anchorWall;
    return cursorEmit + wallElapsed * speedFactor;
  }

  function scheduleFromCursor() {
    clearTimers();
    if (stopped || paused) return;
    anchorWall = performance.now();
    const base = cursorEmit;
    for (const item of queue) {
      if (applied.has(item.index)) continue;
      const delay = Math.max(0, (item.emit_at_ms - base) / speedFactor);
      const t = window.setTimeout(() => {
        if (stopped || paused || applied.has(item.index)) return;
        dispatchFrame(dispatch, item.message);
        applied.add(item.index);
        cursorEmit = item.emit_at_ms;
        anchorWall = performance.now();
      }, delay);
      timers.push(t);
    }
  }

  function setSpeed(next: number) {
    if (stopped) return;
    const nowEmit = paused ? cursorEmit : readCursorEmit();
    // Catch up any frames that should already have fired at the old speed.
    applyThrough(nowEmit);
    speedFactor = next > 0 ? next : 1;
    cursorEmit = nowEmit;
    if (!paused) scheduleFromCursor();
  }

  function pause() {
    if (stopped || paused) return;
    cursorEmit = readCursorEmit();
    paused = true;
    clearTimers();
  }

  function resume() {
    if (stopped || !paused) return;
    paused = false;
    scheduleFromCursor();
  }

  function seek(relativeMs: number) {
    if (stopped) return;
    const target = ORIGIN_EMIT_AT_MS + Math.max(0, relativeMs);
    // Forward-only apply; for rewind, restart from snapshot via full remount.
    if (target < cursorEmit && !paused) {
      // Cannot rewind mid-queue without remount — leave to App restart.
    }
    applyThrough(target);
    cursorEmit = Math.max(cursorEmit, target);
    if (!paused) scheduleFromCursor();
  }

  function jumpToReconciliation() {
    if (stopped) return;
    if (RECON_EMIT_AT_MS <= 0) return;
    applyThrough(RECON_EMIT_AT_MS);
    cursorEmit = RECON_EMIT_AT_MS;
    if (!paused) scheduleFromCursor();
  }

  scheduleFromCursor();

  return {
    durationMs,
    originEmitAtMs: ORIGIN_EMIT_AT_MS,
    reconciliationRelMs: REPLAY_RECONCILIATION.relative_ms,
    setSpeed,
    pause,
    resume,
    seek,
    jumpToReconciliation,
    positionMs: () =>
      Math.max(0, (paused ? cursorEmit : readCursorEmit()) - ORIGIN_EMIT_AT_MS),
    isPaused: () => paused,
    stop: () => {
      stopped = true;
      clearTimers();
    },
  };
}

/** Portal default: realtime. Override with VITE_REPLAY_SPEED if set. */
export function portalReplaySpeed(): number {
  const raw = import.meta.env.VITE_REPLAY_SPEED;
  if (raw === undefined || raw === "") return 1;
  const fromEnv = Number(raw);
  return fromEnv > 0 ? fromEnv : 1;
}

export function replayDurationMs(speed = 1): number {
  return (
    Math.max(0, (fixture.duration_ms ?? 0) - ORIGIN_EMIT_AT_MS) /
    (speed > 0 ? speed : 1)
  );
}
