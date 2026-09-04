/**
 * Postmortem Witness — shared event contract.
 *
 * Generated from shared/schema.json. That file is the source of truth and is
 * FROZEN: if this file and the schema ever disagree, the schema wins.
 *
 * Frontend imports these types. Do not edit by hand to "fix" a mismatch —
 * raise it with the backend owner instead.
 */

/**
 * action        — something a human did or is doing.
 * hypothesis    — a proposed cause.
 * status_change — a hypothesis moving between states.
 * thread        — an open question or loose end nobody has closed.
 * noise         — everything else, which is most of a bridge call.
 */
export type EventType =
  | "action"
  | "hypothesis"
  | "status_change"
  | "thread"
  | "noise";

export type HypothesisState = "open" | "ruled_out" | "confirmed";

export interface Event {
  /**
   * Stable identity. An event may be re-emitted with the SAME event_id if it
   * is later corrected — e.g. when the ASR revises a speaker assignment after
   * the fact. Upsert on this key. Do not blindly append.
   */
  event_id: string;

  type: EventType;

  /** Diarization label such as "A". A label, not a name. May be corrected. */
  speaker: string;

  /** Milliseconds from the start of the incident audio, not wall clock. */
  timestamp_ms: number;

  /** Verbatim utterance. Never paraphrased — this is what gets quoted. */
  text: string;

  /** Short normalized phrasing for the timeline, e.g. "Rolled back 4c21f". */
  summary: string;

  /**
   * Null except on `hypothesis` (names the hypothesis raised) and
   * `status_change` (names the hypothesis changed, and is required there).
   */
  hypothesis_id: string | null;

  /**
   * Null except on `status_change`. Never inferred — only ever set when the
   * change was explicitly spoken out loud on the call.
   */
  new_state: HypothesisState | null;

  /** Speaker who owns the action or thread, when one was named. */
  owner: string | null;

  /** Extractor confidence, 0.0–1.0. The UI may dim or gate on low values. */
  confidence: number;
}

/** A `status_change` event always carries both hypothesis_id and new_state. */
export type StatusChangeEvent = Event & {
  type: "status_change";
  hypothesis_id: string;
  new_state: HypothesisState;
};

export function isStatusChange(e: Event): e is StatusChangeEvent {
  return e.type === "status_change";
}

export interface Hypothesis {
  hypothesis_id: string;
  /** Normalized statement of the proposed cause. */
  text: string;
  state: HypothesisState;
  raised_by: string;
  raised_at_ms: number;
  resolved_at_ms: number | null;
}

export interface Thread {
  thread_id: string;
  text: string;
  owner: string | null;
  opened_at_ms: number;
  closed: boolean;
}

export interface Action {
  action_id: string;
  text: string;
  owner: string | null;
  at_ms: number;
}

/** Rolling p50 values in milliseconds, surfaced live in the dashboard. */
export interface Latency {
  asr_ms: number;
  extract_ms: number;
  e2e_ms: number;
}

export interface IncidentState {
  incident_id: string;
  started_at_ms: number;
  resolved: boolean;
  timeline: Event[];
  hypotheses: Hypothesis[];
  threads: Thread[];
  actions: Action[];
  latency: Latency;
}
