/**
 * Postmortem Witness — shared event contract. v1.5.0
 *
 * Generated from shared/schema.json. That file is the source of truth and is
 * FROZEN: if this file and the schema ever disagree, the schema wins.
 *
 * Frontend imports these types. Do not edit by hand to "fix" a mismatch —
 * raise it with the backend owner instead.
 *
 * CHANGED IN 1.5.0 — silence accounting + claim contradictions, one bump:
 *
 *  1. Event gains addressee, answers_thread_id, claim_subject,
 *     claim_assertion, claim_quote (all required nullable). claim_quote is
 *     non-null when claim_subject is set (schema allOf + worker).
 *  2. Thread gains asked_at_ms, addressee, answered, answered_at_ms,
 *     unanswered_age_ms for directed questions and resolution ageing.
 *  3. DiffOpKind adds silence_summary (SilenceSummary at resolution) and
 *     contradiction (Contradiction pairs). IncidentState carries silence
 *     (null until then) and contradictions[].
 *
 * CHANGED IN 1.4.0 — board fields the live stream already carried, now on
 * the contract so a fresh load matches a streamed board:
 *
 *  1. `Hypothesis.implicit` — true when a status_change named an id nobody
 *     had raised. Required; default false.
 *  2. `Action.unowned` — true when no spoken owner and not a first-person
 *     commitment. Required; default false.
 *  3. `reconciliation` is a first-class DiffOpKind. The teardown
 *     SpeakerRevision batch emits one DiffOp whose value is a
 *     ReconciliationSummary listing every speaker and first-person-owner
 *     change. Live traffic is StateDiff (ops), never a full IncidentState.
 *     snapshot() and the accumulation of every DiffOp from t=0 must agree.
 *
 * CHANGED IN 1.3.0 — two things, one of which will break a naive list render:
 *
 *  1. `resolution` is a new EventType.
 *  2. One utterance can now produce SEVERAL events. "It was the deploy, I'll
 *     revert it properly" is a status_change and an action, and both are
 *     emitted. They share a (connection_epoch, turn_order) pair, so
 *     `turnKey()` is no longer unique per event — use `event_id` as the list
 *     key. `turnKey()` is still correct for grouping and for applying speaker
 *     amendments, which hit every event from the turn at once.
 */

/**
 * action          — something a human did or is doing.
 * hypothesis      — a proposed cause.
 * status_change   — a hypothesis moving between states.
 * thread          — an open question or loose end nobody has closed.
 * resolution      — an explicit spoken declaration that the incident is over
 *                   ("declaring this resolved"). A statement about the
 *                   incident rather than about one hypothesis, so it carries
 *                   no hypothesis_id.
 * noise           — everything else, which is most of a bridge call.
 * speaker_amended — the ASR retroactively reassigned an earlier turn to a
 *                   different speaker. Surfaced deliberately: corrections are
 *                   shown to the user, never quietly swapped in.
 */
export type EventType =
  | "action"
  | "hypothesis"
  | "status_change"
  | "thread"
  | "resolution"
  | "noise"
  | "speaker_amended";

export type HypothesisState = "open" | "ruled_out" | "confirmed";

export interface Event {
  /**
   * Stable identity, and the only unique key on an event. An event may be
   * re-emitted with the SAME event_id when it is corrected — most often after
   * a speaker revision. Upsert on this key. Do not blindly append.
   *
   * This is the React list key. Since 1.3.0 several events can share one turn
   * key, so keying a list on `turnKey()` will drop siblings.
   */
  event_id: string;

  type: EventType;

  /**
   * Which ASR connection this event came from. Starts at 0, increments on
   * every reconnect.
   *
   * turn_order is assigned by the server and restarts at 0 on every new
   * connection, so it is ambiguous on its own: turn 0 of the second
   * connection is a different turn from turn 0 of the first. Always key on
   * the pair.
   */
  connection_epoch: number;

  /**
   * The ASR turn this event came from, as numbered by the server. Unique only
   * within one connection_epoch.
   *
   * The join key is the composite (connection_epoch, turn_order) - never bare
   * turn_order. When the ASR revises who was speaking it names both, and
   * every event sharing that pair is amended together. Use `turnKey` to build
   * a stable string key for grouping and for amendment lookups.
   *
   * It identifies a TURN, not an event: since 1.3.0 one utterance can yield
   * several events that all carry this same pair. Use `event_id` for list
   * keys.
   */
  turn_order: number;

  /**
   * Diarization label such as "A". Always a label, never a name. May be
   * corrected after first emission.
   */
  speaker_label: string;

  /**
   * Human name, once known. Always null for now — a later roll-call pass maps
   * labels to names. Render the name when present, fall back to the label.
   */
  speaker_name: string | null;

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

  /**
   * Who owns the action or thread, when someone was named. Carries whatever
   * was spoken — usually a name heard on the call, not a diarization label.
   */
  owner: string | null;

  /** Extractor confidence, 0.0–1.0. The UI may dim or gate on low values. */
  confidence: number;

  /**
   * Null except on `speaker_amended`, where it holds the label the turn was
   * previously attributed to — so the UI can render "A → B" rather than
   * silently changing what the user already read.
   */
  previous_speaker_label: string | null;

  /**
   * Named person a direct question is addressed to. Null when not a directed
   * question or no name was spoken.
   */
  addressee: string | null;

  /**
   * When set, this event explicitly answers that open thread. Never inferred
   * by the client — only when extraction linked it.
   */
  answers_thread_id: string | null;

  /**
   * Subject of a factual claim; prefer an existing hypothesis_id when the
   * claim is about a board hypothesis. Null when the event makes no claim.
   */
  claim_subject: string | null;

  /**
   * Short normalized assertion about claim_subject. Null when claim_subject
   * is null.
   */
  claim_assertion: string | null;

  /**
   * Verbatim quote grounding the claim. Required non-null when claim_subject
   * is set. Null otherwise.
   */
  claim_quote: string | null;
}

/** A `status_change` event always carries both hypothesis_id and new_state. */
export type StatusChangeEvent = Event & {
  type: "status_change";
  hypothesis_id: string;
  new_state: HypothesisState;
};

/** A `speaker_amended` event always carries previous_speaker_label. */
export type SpeakerAmendedEvent = Event & {
  type: "speaker_amended";
  previous_speaker_label: string;
};

export function isStatusChange(e: Event): e is StatusChangeEvent {
  return e.type === "status_change";
}

export function isSpeakerAmended(e: Event): e is SpeakerAmendedEvent {
  return e.type === "speaker_amended";
}

export function isResolution(e: Event): boolean {
  return e.type === "resolution";
}

/**
 * Stable string form of the composite turn key, e.g. "e0/t14".
 *
 * Use this anywhere you would otherwise key on turn_order alone - map keys,
 * grouping, lookups when applying a speaker amendment. Keying on bare
 * turn_order will silently merge turns from different connections.
 *
 * NOT a unique event key: several events can come from one turn. For list
 * keys use `event_id`.
 */
export function turnKey(e: Pick<Event, "connection_epoch" | "turn_order">): string {
  return `e${e.connection_epoch}/t${e.turn_order}`;
}

/**
 * Group events by the turn they came from, preserving order within a turn.
 *
 * Useful for rendering the several events one utterance can now produce under
 * a single quoted line, and for applying a speaker amendment to all of them.
 */
export function groupByTurn(events: Event[]): Map<string, Event[]> {
  const out = new Map<string, Event[]>();
  for (const e of events) {
    const key = turnKey(e);
    const bucket = out.get(key);
    if (bucket) bucket.push(e);
    else out.set(key, [e]);
  }
  return out;
}

/** True when both events came from the same ASR turn. */
export function sameTurn(
  a: Pick<Event, "connection_epoch" | "turn_order">,
  b: Pick<Event, "connection_epoch" | "turn_order">,
): boolean {
  return (
    a.connection_epoch === b.connection_epoch && a.turn_order === b.turn_order
  );
}

/** Display helper: name when we have one, label otherwise. */
export function speakerDisplay(e: Event): string {
  return e.speaker_name ?? e.speaker_label;
}

export interface Hypothesis {
  hypothesis_id: string;
  /** Normalized statement of the proposed cause. */
  text: string;
  state: HypothesisState;
  /** Diarization label of whoever raised it. Subject to speaker revision. */
  raised_by_label: string;
  raised_at_ms: number;
  resolved_at_ms: number | null;
  /**
   * True when created by a status_change that named an id nobody had raised.
   * Required; false for every hypothesis that arrived as a hypothesis event.
   */
  implicit: boolean;
}

export interface Thread {
  thread_id: string;
  text: string;
  owner: string | null;
  opened_at_ms: number;
  closed: boolean;
  /** When the question/thread was opened. Usually equals opened_at_ms. */
  asked_at_ms: number;
  /** Named person asked, if any. */
  addressee: string | null;
  /**
   * True only when an event with answers_thread_id linked to this thread
   * arrived.
   */
  answered: boolean;
  /** When answered; null while unanswered. */
  answered_at_ms: number | null;
  /**
   * Milliseconds unanswered at resolution, or null while the incident is
   * open (client may compute live as clock_ms - asked_at_ms).
   */
  unanswered_age_ms: number | null;
}

export interface Action {
  action_id: string;
  text: string;
  owner: string | null;
  at_ms: number;
  /**
   * True when no spoken owner was given and the utterance was not a
   * first-person commitment. Required; false otherwise.
   */
  unowned: boolean;
}

/** Rolling p50 values in milliseconds, surfaced live in the dashboard. */
export interface Latency {
  asr_ms: number;
  extract_ms: number;
  e2e_ms: number;
}

/** One unanswered thread listed inside a SilenceSummary. */
export interface SilenceOpenThread {
  thread_id: string;
  text: string;
  asked_at_ms: number;
  addressee: string | null;
  unanswered_age_ms: number | null;
}

/**
 * Postmortem silence stats. Emitted once at resolution as DiffOp
 * op="silence_summary".
 */
export interface SilenceSummary {
  questions_asked: number;
  questions_unanswered: number;
  longest_unanswered_ms: number;
  /** Named people who were asked something and never answered. */
  unanswered_addressees: string[];
  open_threads: SilenceOpenThread[];
}

/** One side of a detected factual contradiction. */
export interface ContradictionClaim {
  /** Composite turn key as "e{epoch}/t{order}", e.g. "e0/t28". */
  turn_key: string;
  event_id: string;
  speaker_label: string;
  timestamp_ms: number;
  assertion: string;
  quote: string;
}

/** Two claims about the same subject that disagree. */
export interface Contradiction {
  subject: string;
  earlier: ContradictionClaim;
  later: ContradictionClaim;
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
  /** Null until resolution emits silence_summary. */
  silence: SilenceSummary | null;
  /** Detected claim contradictions, in emission order. */
  contradictions: Contradiction[];
}

/**
 * One change the state machine emits over the WebSocket. Apply in order.
 * A client that missed the stream loads IncidentState once, then resumes on
 * diffs — the two views must be equivalent.
 */
export type DiffOpKind =
  | "upsert_hypothesis"
  | "upsert_thread"
  | "upsert_action"
  | "upsert_event"
  | "set_resolved"
  | "set_latency"
  | "reconciliation"
  | "silence_summary"
  | "contradiction";

/** One timeline event whose speaker_label moved during a revision batch. */
export interface SpeakerChange {
  /** Composite turn key as "e{epoch}/t{order}", e.g. "e0/t28". */
  turn_key: string;
  event_id: string;
  previous_speaker_label: string;
  speaker_label: string;
}

/**
 * A first-person action whose owner was recomputed after its speaker_label
 * moved. Spoken names are never rewritten.
 */
export interface OwnerChange {
  action_id: string;
  previous_owner: string | null;
  owner: string | null;
}

/**
 * What one SpeakerRevision batch changed on the board. Emitted once at
 * teardown as a DiffOp with op="reconciliation".
 */
export interface ReconciliationSummary {
  events_touched: number;
  speakers: SpeakerChange[];
  owners: OwnerChange[];
}

export interface DiffOp {
  op: DiffOpKind;
  /**
   * Map key for upsert_* ops (hypothesis_id, thread_id, action_id, or
   * event_id). Null for set_resolved, set_latency, reconciliation,
   * silence_summary, and contradiction.
   */
  key?: string | null;
  /**
   * Payload shape depends on op: Hypothesis, Thread, Action, Event,
   * boolean (set_resolved), Latency, ReconciliationSummary, SilenceSummary,
   * or Contradiction.
   */
  value: unknown;
}

export type ReconciliationDiffOp = DiffOp & {
  op: "reconciliation";
  key?: null;
  value: ReconciliationSummary;
};

export type SilenceSummaryDiffOp = DiffOp & {
  op: "silence_summary";
  key?: null;
  value: SilenceSummary;
};

export type ContradictionDiffOp = DiffOp & {
  op: "contradiction";
  key?: null;
  value: Contradiction;
};

export function isReconciliation(op: DiffOp): op is ReconciliationDiffOp {
  return op.op === "reconciliation";
}

export function isSilence(op: DiffOp): op is SilenceSummaryDiffOp {
  return op.op === "silence_summary";
}

export function isContradiction(op: DiffOp): op is ContradictionDiffOp {
  return op.op === "contradiction";
}

/**
 * A batch of DiffOps for the WebSocket. Never send a full IncidentState over
 * the live channel — reconnect mid-call loads IncidentState once, then
 * resumes on diffs.
 */
export interface StateDiff {
  ops: DiffOp[];
}

/**
 * Fold a DiffOp into a client-side board. Upserts overwrite by key;
 * reconciliation is informational once the preceding upserts have landed
 * (the machine already applied the corrections before emitting the summary).
 * silence_summary is stored on the board; contradiction is appended.
 */
export function applyDiffOp(
  state: IncidentState,
  op: DiffOp,
): IncidentState {
  switch (op.op) {
    case "upsert_hypothesis": {
      const h = op.value as Hypothesis;
      const rest = state.hypotheses.filter((x) => x.hypothesis_id !== h.hypothesis_id);
      return { ...state, hypotheses: [...rest, h] };
    }
    case "upsert_thread": {
      const t = op.value as Thread;
      const rest = state.threads.filter((x) => x.thread_id !== t.thread_id);
      return { ...state, threads: [...rest, t] };
    }
    case "upsert_action": {
      const a = op.value as Action;
      const rest = state.actions.filter((x) => x.action_id !== a.action_id);
      return { ...state, actions: [...rest, a] };
    }
    case "upsert_event": {
      const e = op.value as Event;
      const rest = state.timeline.filter((x) => x.event_id !== e.event_id);
      return { ...state, timeline: [...rest, e] };
    }
    case "set_resolved":
      return { ...state, resolved: op.value as boolean };
    case "set_latency":
      return { ...state, latency: op.value as Latency };
    case "reconciliation":
      // Corrections were already applied as upsert_* ops ahead of this
      // summary. The summary is for the UI to show what moved.
      return state;
    case "silence_summary":
      return { ...state, silence: op.value as SilenceSummary };
    case "contradiction":
      return {
        ...state,
        contradictions: [...state.contradictions, op.value as Contradiction],
      };
    default: {
      const _exhaustive: never = op.op;
      void _exhaustive;
      return state;
    }
  }
}

export function applyDiff(state: IncidentState, diff: StateDiff): IncidentState {
  return diff.ops.reduce(applyDiffOp, state);
}

export function emptyIncidentState(
  incident_id: string,
  started_at_ms = 0,
): IncidentState {
  return {
    incident_id,
    started_at_ms,
    resolved: false,
    timeline: [],
    hypotheses: [],
    threads: [],
    actions: [],
    latency: { asr_ms: 0, extract_ms: 0, e2e_ms: 0 },
    silence: null,
    contradictions: [],
  };
}
