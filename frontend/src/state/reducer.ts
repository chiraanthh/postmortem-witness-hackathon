import type { Event, IncidentState } from "../contract";
import type {
  DashboardState,
  DashboardAction,
  TimelineRow,
  HypothesisNode,
} from "./types";

export function makeInitialState(incidentId: string): DashboardState {
  return {
    incident_id: incidentId,
    started_at_ms: 0,
    resolved: false,
    timelineByTurnOrder: {},
    hypothesesById: {},
    threadsById: {},
    actionsById: {},
    latency: { asr_ms: 0, extract_ms: 0, e2e_ms: 0 },
    hypothesisRaisedTurn: {},
    clock_ms: 0,
    ingested: 0,
  };
}

/** Rows that earn a place on the timeline. Noise is the bulk of a call. */
function isTimelineWorthy(type: Event["type"]): boolean {
  return type !== "noise" && type !== "speaker_amended";
}

function toRow(e: Event): TimelineRow {
  return {
    turn_order: e.turn_order,
    event_id: e.event_id,
    type: e.type,
    speaker_label: e.speaker_label,
    speaker_name: e.speaker_name,
    timestamp_ms: e.timestamp_ms,
    text: e.text,
    summary: e.summary,
    confidence: e.confidence,
    amendedFrom: null,
    correctedAt: null,
  };
}

/**
 * Apply a single wire Event to the keyed maps. Pure: returns a new state.
 * Every branch upserts on a stable key so a re-emitted event_id / turn_order
 * corrects in place instead of duplicating.
 */
function applyEvent(state: DashboardState, e: Event, now: number): DashboardState {
  const next: DashboardState = {
    ...state,
    ingested: state.ingested + 1,
    clock_ms: Math.max(state.clock_ms, e.timestamp_ms),
  };

  // --- Retroactive speaker amendment -------------------------------------
  // Find the already-shown turn by turn_order and correct it IN PLACE. Never
  // create a new row; keep the original timestamp, summary and text.
  if (e.type === "speaker_amended") {
    const existing = state.timelineByTurnOrder[e.turn_order];
    if (existing) {
      next.timelineByTurnOrder = {
        ...state.timelineByTurnOrder,
        [e.turn_order]: {
          ...existing,
          speaker_label: e.speaker_label,
          amendedFrom: e.previous_speaker_label ?? existing.speaker_label,
          correctedAt: now,
        },
      };
    }
    // If that turn raised a hypothesis, re-attribute it too (labels are subject
    // to revision; the board should reflect the corrected speaker).
    const hypId = state.hypothesisRaisedTurn[e.turn_order];
    if (hypId && state.hypothesesById[hypId]) {
      next.hypothesesById = {
        ...state.hypothesesById,
        [hypId]: {
          ...state.hypothesesById[hypId],
          raised_by_label: e.speaker_label,
          correctedAt: now,
        },
      };
    }
    return next;
  }

  // --- Meaningful utterances become / refresh a timeline row -------------
  if (isTimelineWorthy(e.type)) {
    const prior = state.timelineByTurnOrder[e.turn_order];
    next.timelineByTurnOrder = {
      ...state.timelineByTurnOrder,
      [e.turn_order]: prior
        ? // Upsert: keep any correction highlight already applied to this turn.
          {
            ...toRow(e),
            speaker_label: prior.amendedFrom ? prior.speaker_label : e.speaker_label,
            amendedFrom: prior.amendedFrom,
            correctedAt: prior.correctedAt,
          }
        : toRow(e),
    };
  }

  // --- Entity side-effects ------------------------------------------------
  switch (e.type) {
    case "hypothesis": {
      if (e.hypothesis_id) {
        const existing = state.hypothesesById[e.hypothesis_id];
        const node: HypothesisNode = existing
          ? { ...existing, text: e.summary || existing.text }
          : {
              hypothesis_id: e.hypothesis_id,
              text: e.summary || e.text,
              state: "open",
              raised_by_label: e.speaker_label,
              raised_at_ms: e.timestamp_ms,
              resolved_at_ms: null,
              prevState: null,
              movedAt: null,
              correctedAt: null,
            };
        next.hypothesesById = { ...state.hypothesesById, [e.hypothesis_id]: node };
        next.hypothesisRaisedTurn = {
          ...state.hypothesisRaisedTurn,
          [e.turn_order]: e.hypothesis_id,
        };
      }
      break;
    }

    case "status_change": {
      if (e.hypothesis_id && e.new_state) {
        const existing = state.hypothesesById[e.hypothesis_id];
        const base: HypothesisNode = existing ?? {
          hypothesis_id: e.hypothesis_id,
          text: e.summary || e.text,
          state: "open",
          raised_by_label: e.speaker_label,
          raised_at_ms: e.timestamp_ms,
          resolved_at_ms: null,
          prevState: null,
          movedAt: null,
          correctedAt: null,
        };
        next.hypothesesById = {
          ...state.hypothesesById,
          [e.hypothesis_id]: {
            ...base,
            prevState: base.state,
            state: e.new_state,
            movedAt: now,
            resolved_at_ms: e.new_state === "open" ? null : e.timestamp_ms,
          },
        };
      }
      break;
    }

    case "thread": {
      // thread_id keyed on the originating event_id (stable, upsertable).
      next.threadsById = {
        ...state.threadsById,
        [e.event_id]: {
          thread_id: e.event_id,
          text: e.summary || e.text,
          owner: e.owner,
          opened_at_ms: e.timestamp_ms,
          closed: false,
        },
      };
      break;
    }

    case "action": {
      next.actionsById = {
        ...state.actionsById,
        [e.event_id]: {
          action_id: e.event_id,
          text: e.summary || e.text,
          owner: e.owner,
          at_ms: e.timestamp_ms,
        },
      };
      break;
    }

    case "noise":
    default:
      break;
  }

  return next;
}

/**
 * Rebuild keyed maps from a full IncidentState snapshot. This is the path the
 * live backend uses for catch-up / authoritative sync. Not used by the mock,
 * but wired so the same reducer serves both transports unchanged.
 */
function applySnapshot(snap: IncidentState): DashboardState {
  let next: DashboardState = {
    ...makeInitialState(snap.incident_id),
    started_at_ms: snap.started_at_ms,
    resolved: snap.resolved,
    latency: snap.latency,
  };
  const now = Date.now();
  for (const e of snap.timeline) next = applyEvent(next, e, now);
  // Snapshot entity arrays are authoritative over anything the events implied.
  for (const h of snap.hypotheses) {
    next.hypothesesById[h.hypothesis_id] = {
      ...h,
      prevState: null,
      movedAt: null,
      correctedAt: null,
    };
  }
  for (const t of snap.threads) next.threadsById[t.thread_id] = t;
  for (const a of snap.actions) next.actionsById[a.action_id] = a;
  next.clock_ms = snap.timeline.reduce((m, e) => Math.max(m, e.timestamp_ms), 0);
  return next;
}

export function dashboardReducer(
  state: DashboardState,
  action: DashboardAction
): DashboardState {
  switch (action.type) {
    case "INGEST":
      return applyEvent(state, action.event, Date.now());
    case "SNAPSHOT":
      return applySnapshot(action.state);
    case "LATENCY":
      return { ...state, latency: action.latency };
    case "RESET":
      return makeInitialState(action.incidentId);
    default:
      return state;
  }
}
