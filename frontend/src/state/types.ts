import type {
  Event,
  Hypothesis,
  Thread,
  Action,
  Latency,
  IncidentState,
  DiffOp,
  ReconciliationSummary,
  SilenceSummary,
  Contradiction,
} from "../contract";

/**
 * State is stored as KEYED MAPS, never append-only arrays. Sorted arrays are
 * derived at render time (see selectors.ts). Keys:
 *   - timeline    : by event_id  (unique; several events can share one turn)
 *   - hypotheses  : by hypothesis_id
 *   - threads     : by thread_id
 *   - actions     : by action_id
 *
 * Speaker amendments join on turnKey(connection_epoch, turn_order), not bare
 * turn_order — turn_order restarts at 0 on every reconnect.
 */

export interface TimelineRow {
  connection_epoch: number;
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
  /** Extraction backend that produced this event (sidecar, not on Event). */
  extractionModel: string | null;
  extractionProvider: string | null;
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

  timelineById: Record<string, TimelineRow>;
  hypothesesById: Record<string, HypothesisNode>;
  threadsById: Record<string, Thread>;
  actionsById: Record<string, Action>;
  latency: Latency;

  /** turnKey → hypothesis_id, so a speaker amendment can re-attribute a card. */
  hypothesisRaisedTurn: Record<string, string>;

  /** Last teardown reconciliation summary, if any. */
  reconciliation: ReconciliationSummary | null;

  /** Silence accounting; null until resolution emits silence_summary. */
  silence: SilenceSummary | null;

  /** Detected claim contradictions, in emission order. */
  contradictions: Contradiction[];

  /** Loud transport failure (e.g. contract version mismatch). */
  transportError: string | null;

  /** Loud extraction provider failure (no silent cross-provider fallback). */
  providerError: string | null;

  /** Grounding-guard declines (sidecar). Not on the frozen Event schema. */
  refusals: GroundingRefusal[];

  /** event_id → which model answered. Outside frozen Event schema. */
  provenanceById: Record<
    string,
    { provider: string; model: string; request_id?: string | null }
  >;

  /**
   * Wall-clock ms when the latest reconciliation op arrived. Drives the
   * visible beat; increments on every reconciliation so seek can re-play it.
   */
  reconciliationBeatAt: number | null;
  reconciliationBeatId: number;

  /**
   * Wall-clock ms when the latest contradiction op arrived. Seek re-plays
   * the highlight the same way reconciliation does.
   */
  contradictionBeatAt: number | null;
  contradictionBeatId: number;

  /** Furthest incident-audio time seen, drives the "elapsed" clock. */
  clock_ms: number;
  /** How many wire messages / ops have been ingested — a liveness counter. */
  ingested: number;
}


/**
 * Reducer actions mirror the live wire: handshake-gated snapshot, then diffs.
 * INGEST remains for the mock emitter (single Events). LATENCY is unused on
 * the live path (set_latency arrives as a DiffOp).
 */
export type EventProvenance = {
  provider: string;
  model: string;
  request_id?: string | null;
};

/** Sidecar from the grounding guard — claim declined, board unchanged. */
export type GroundingRefusal = {
  kind: "grounding_refusal";
  recorded: false | boolean;
  claimed_hypothesis_id: string | null;
  claimed_new_state: string | null;
  claimed_quote: string | null;
  reason: string;
  utterance_text: string;
  speaker_label: string;
  timestamp_ms: number;
  connection_epoch?: number;
  turn_order?: number;
  provider?: string | null;
  model?: string | null;
};

export type DashboardAction =
  | { type: "INGEST"; event: Event }
  | {
      type: "SNAPSHOT";
      state: IncidentState;
      provenance?: Record<string, EventProvenance>;
      refusals?: GroundingRefusal[];
    }
  | {
      type: "DIFF";
      ops: DiffOp[];
      provenance?: Record<string, EventProvenance>;
      refusals?: GroundingRefusal[];
    }
  | { type: "LATENCY"; latency: Latency }
  | { type: "TRANSPORT_ERROR"; message: string }
  | { type: "PROVIDER_ERROR"; message: string }
  | { type: "RESET"; incidentId: string };
