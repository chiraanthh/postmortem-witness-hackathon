import type {
  Event,
  Hypothesis,
  Thread,
  Action,
  Latency,
  IncidentState,
} from "../contract";

/**
 * State is stored as KEYED MAPS, never append-only arrays. Sorted arrays are
 * derived at render time (see selectors.ts). Keys:
 *   - timeline    : by turn_order  (the ASR join key; amendments target it)
 *   - hypotheses  : by hypothesis_id
 *   - threads     : by thread_id (== originating event_id)
 *   - actions     : by action_id (== originating event_id)
 */

/**
 * One rendered timeline row. Derived from an Event, but enriched with UI-only
 * fields that never travel on the wire (correction highlight, etc.).
 */
export interface TimelineRow {
  turn_order: number;
  event_id: string;
  type: Event["type"];
  speaker_label: string;
  speaker_name: string | null;
  timestamp_ms: number;
  text: string;
  summary: string;
  confidence: number;
  /** Set when this row's speaker was retroactively amended. "A" in "A → C". */
  amendedFrom: string | null;
  /** Wall-clock ms of the last amendment, used to fire a one-shot highlight. */
  correctedAt: number | null;
}

/** A hypothesis plus UI-only movement bookkeeping for the board animation. */
export interface HypothesisNode extends Hypothesis {
  /** The state it held before the most recent status change. */
  prevState: Hypothesis["state"] | null;
  /** Wall-clock ms of the last column move, used to fire the move glow. */
  movedAt: number | null;
  /** Wall-clock ms of the last speaker amendment on the raising turn. */
  correctedAt: number | null;
}

export interface DashboardState {
  incident_id: string;
  started_at_ms: number;
  resolved: boolean;

  timelineByTurnOrder: Record<number, TimelineRow>;
  hypothesesById: Record<string, HypothesisNode>;
  threadsById: Record<string, Thread>;
  actionsById: Record<string, Action>;
  latency: Latency;

  /** Internal join table: which turn raised which hypothesis, for amendments. */
  hypothesisRaisedTurn: Record<number, string>;

  /** Furthest incident-audio time seen, drives the "elapsed" clock. */
  clock_ms: number;
  /** How many wire messages have been ingested — a liveness counter. */
  ingested: number;
}

/**
 * Reducer actions mirror the wire's `oneOf`: a single Event, or a full
 * IncidentState snapshot. LATENCY is a live transport signal (the contract
 * carries latency only inside IncidentState, so the transport surfaces rolling
 * samples through this action). RESET restarts a demo run.
 */
export type DashboardAction =
  | { type: "INGEST"; event: Event }
  | { type: "SNAPSHOT"; state: IncidentState }
  | { type: "LATENCY"; latency: Latency }
  | { type: "RESET"; incidentId: string };
