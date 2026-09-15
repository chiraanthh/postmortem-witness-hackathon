/**
 * Postmortem Witness — shared event contract. v1.3.0
 *
 * Generated from shared/schema.json. That file is the source of truth and is
 * FROZEN: if this file and the schema ever disagree, the schema wins.
 *
 * Frontend imports these types. Do not edit by hand to "fix" a mismatch —
 * raise it with the backend owner instead.
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
