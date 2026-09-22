import type { Dispatch } from "react";
import type { DiffOp, IncidentState } from "../contract";
import { CONTRACT_VERSION } from "../contract";
import type { DashboardAction, GroundingRefusal } from "../state/types";
import fixtureRaw from "./incident_01.fixture.json";

/**
 * Timed WebSocket frames captured from a live IncidentHub run of
 * demo/audio/incident_01.wav at EXTRACTION_TEMPERATURE=0.
 * Portal "Watch recorded run" replays this — not the hand-authored mock.
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
}

const fixture = fixtureRaw as unknown as ReplayFixture;

export const REPLAY_INCIDENT_ID = fixture.incident_id;

export interface ReplayController {
  stop: () => void;
}

/**
 * Replay captured live-pipeline WebSocket frames on the same wall-clock
 * offsets they arrived during capture. Same dispatch path as the live
 * transport — snapshot then diffs — so the board matches a real run.
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
      message:
        `replay fixture contract ${fixture.contract_version} ≠ client ${CONTRACT_VERSION}`,
    });
    return { stop: () => undefined };
  }

  const frames = [...fixture.messages].sort(
    (a, b) => a.emit_at_ms - b.emit_at_ms
  );

  for (const frame of frames) {
    const t = window.setTimeout(() => {
      if (stopped) return;
      const msg = frame.message;
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
    }, frame.emit_at_ms / s);
    timers.push(t);
  }

  return {
    stop: () => {
      stopped = true;
      timers.forEach((id) => window.clearTimeout(id));
    },
  };
}

export function replayDurationMs(speed = 1): number {
  const last = fixture.messages.reduce(
    (m, x) => Math.max(m, x.emit_at_ms),
    0
  );
  return last / (speed > 0 ? speed : 1);
}
