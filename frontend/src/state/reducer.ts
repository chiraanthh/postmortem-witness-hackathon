import type {
  Event,
  IncidentState,
  DiffOp,
  Hypothesis,
  Thread,
  Action,
  Latency,
  ReconciliationSummary,
  SilenceSummary,
  Contradiction,
} from "../contract";
import { turnKey } from "../contract";
import type {
  DashboardState,
  DashboardAction,
  TimelineRow,
  HypothesisNode,
  GroundingRefusal,
} from "./types";

export function makeInitialState(incidentId: string): DashboardState {
  return {
    incident_id: incidentId,
    started_at_ms: 0,
    resolved: false,
    timelineById: {},
    hypothesesById: {},
    threadsById: {},
    actionsById: {},
    latency: { asr_ms: 0, extract_ms: 0, e2e_ms: 0 },
    hypothesisRaisedTurn: {},
    reconciliation: null,
    silence: null,
    contradictions: [],
    transportError: null,
    providerError: null,
    refusals: [],
    provenanceById: {},
    reconciliationBeatAt: null,
    reconciliationBeatId: 0,
    contradictionBeatAt: null,
    contradictionBeatId: 0,
    clock_ms: 0,
    ingested: 0,
  };
}

/** Rows that earn a place on the timeline. Noise is the bulk of a call. */
function isTimelineWorthy(type: Event["type"]): boolean {
  return type !== "noise" && type !== "speaker_amended";
}

function toRow(
  e: Event,
  provenance?: { provider: string; model: string } | null
): TimelineRow {
  return {
    connection_epoch: e.connection_epoch,
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
    extractionModel: provenance?.model ?? null,
    extractionProvider: provenance?.provider ?? null,
  };
}

function amendRowsForTurn(
  timelineById: Record<string, TimelineRow>,
  epoch: number,
  order: number,
  newLabel: string,
  previousLabel: string | null,
  now: number
): Record<string, TimelineRow> {
  let changed = false;
  const next = { ...timelineById };
  for (const [id, row] of Object.entries(timelineById)) {
    if (row.connection_epoch !== epoch || row.turn_order !== order) continue;
    next[id] = {
      ...row,
      speaker_label: newLabel,
      amendedFrom: previousLabel ?? row.speaker_label,
      correctedAt: now,
    };
    changed = true;
  }
  return changed ? next : timelineById;
}

/**
 * Apply a single wire Event to the keyed maps. Pure: returns a new state.
 * Upserts on event_id. Speaker amendments hit every event sharing the turn key.
 */
function applyEvent(state: DashboardState, e: Event, now: number): DashboardState {
  const next: DashboardState = {
    ...state,
    ingested: state.ingested + 1,
    clock_ms: Math.max(state.clock_ms, e.timestamp_ms),
  };

  if (e.type === "speaker_amended") {
    next.timelineById = amendRowsForTurn(
      state.timelineById,
      e.connection_epoch,
      e.turn_order,
      e.speaker_label,
      e.previous_speaker_label,
      now
    );
    const tk = turnKey(e);
    const hypId = state.hypothesisRaisedTurn[tk];
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

  if (isTimelineWorthy(e.type)) {
    const prior = state.timelineById[e.event_id];
    const prov = state.provenanceById[e.event_id] ?? null;
    next.timelineById = {
      ...state.timelineById,
      [e.event_id]: prior
        ? {
            ...toRow(e, prov),
            speaker_label: prior.amendedFrom ? prior.speaker_label : e.speaker_label,
            amendedFrom: prior.amendedFrom,
            correctedAt: prior.correctedAt,
            extractionModel: prior.extractionModel ?? prov?.model ?? null,
            extractionProvider: prior.extractionProvider ?? prov?.provider ?? null,
          }
        : toRow(e, prov),
    };
  }

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
              implicit: false,
              prevState: null,
              movedAt: null,
              correctedAt: null,
            };
        next.hypothesesById = { ...state.hypothesesById, [e.hypothesis_id]: node };
        next.hypothesisRaisedTurn = {
          ...state.hypothesisRaisedTurn,
          [turnKey(e)]: e.hypothesis_id,
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
          implicit: true,
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
      next.threadsById = {
        ...state.threadsById,
        [e.event_id]: {
          thread_id: e.event_id,
          text: e.summary || e.text,
          owner: e.owner,
          opened_at_ms: e.timestamp_ms,
          closed: false,
          asked_at_ms: e.timestamp_ms,
          addressee: e.addressee ?? null,
          answered: false,
          answered_at_ms: null,
          unanswered_age_ms: null,
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
          unowned: !e.owner || e.owner.trim() === "",
        },
      };
      break;
    }

    case "resolution": {
      next.resolved = true;
      break;
    }

    case "noise":
    default:
      break;
  }

  // Linked answer: mark the thread answered + closed (leaves open list).
  if (e.answers_thread_id && next.threadsById[e.answers_thread_id]) {
    const tid = e.answers_thread_id;
    const existing = next.threadsById[tid];
    if (!existing.answered) {
      next.threadsById = {
        ...next.threadsById,
        [tid]: {
          ...existing,
          answered: true,
          answered_at_ms: e.timestamp_ms,
          closed: true,
          unanswered_age_ms: null,
        },
      };
    }
  }

  return next;
}

function applySnapshot(
  snap: IncidentState,
  provenance?: Record<string, { provider: string; model: string; request_id?: string | null }>,
  refusals?: GroundingRefusal[]
): DashboardState {
  let next: DashboardState = {
    ...makeInitialState(snap.incident_id),
    started_at_ms: snap.started_at_ms,
    resolved: snap.resolved,
    latency: snap.latency,
    provenanceById: provenance ? { ...provenance } : {},
    refusals: refusals ? [...refusals] : [],
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
  next.silence = snap.silence ?? null;
  next.contradictions = snap.contradictions ? [...snap.contradictions] : [];
  next.clock_ms = snap.timeline.reduce((m, e) => Math.max(m, e.timestamp_ms), 0);
  return next;
}

function upsertHypothesis(
  state: DashboardState,
  h: Hypothesis,
  now: number
): DashboardState {
  const existing = state.hypothesesById[h.hypothesis_id];
  const moved =
    existing != null && existing.state !== h.state
      ? { prevState: existing.state, movedAt: now }
      : {
          prevState: existing?.prevState ?? null,
          movedAt: existing?.movedAt ?? null,
        };
  return {
    ...state,
    ingested: state.ingested + 1,
    hypothesesById: {
      ...state.hypothesesById,
      [h.hypothesis_id]: {
        ...h,
        ...moved,
        correctedAt: existing?.correctedAt ?? null,
      },
    },
  };
}

function applyOp(state: DashboardState, op: DiffOp, now: number): DashboardState {
  switch (op.op) {
    case "upsert_event": {
      const e = op.value as Event;
      return applyEvent(state, e, now);
    }
    case "upsert_hypothesis":
      return upsertHypothesis(state, op.value as Hypothesis, now);
    case "upsert_thread": {
      const t = op.value as Thread;
      return {
        ...state,
        ingested: state.ingested + 1,
        threadsById: { ...state.threadsById, [t.thread_id]: t },
      };
    }
    case "upsert_action": {
      const a = op.value as Action;
      return {
        ...state,
        ingested: state.ingested + 1,
        actionsById: { ...state.actionsById, [a.action_id]: a },
      };
    }
    case "set_resolved":
      return {
        ...state,
        ingested: state.ingested + 1,
        resolved: op.value as boolean,
      };
    case "set_latency":
      return {
        ...state,
        latency: op.value as Latency,
      };
    case "reconciliation": {
      const summary = op.value as ReconciliationSummary;
      let timelineById = state.timelineById;
      let hypothesesById = state.hypothesesById;
      // Corrections already landed as upsert_* ops; still mark UI highlights
      // from the summary so the board shows what moved.
      for (const sc of summary.speakers) {
        const match = /^e(\d+)\/t(\d+)$/.exec(sc.turn_key);
        if (!match) continue;
        const epoch = Number(match[1]);
        const order = Number(match[2]);
        timelineById = amendRowsForTurn(
          timelineById,
          epoch,
          order,
          sc.speaker_label,
          sc.previous_speaker_label,
          now
        );
        const hypId = state.hypothesisRaisedTurn[sc.turn_key];
        if (hypId && hypothesesById[hypId]) {
          hypothesesById = {
            ...hypothesesById,
            [hypId]: {
              ...hypothesesById[hypId],
              raised_by_label: sc.speaker_label,
              correctedAt: now,
            },
          };
        }
      }
      let actionsById = state.actionsById;
      for (const oc of summary.owners) {
        const existing = actionsById[oc.action_id];
        if (!existing) continue;
        actionsById = {
          ...actionsById,
          [oc.action_id]: {
            ...existing,
            owner: oc.owner,
            unowned: oc.owner == null || oc.owner.trim() === "",
          },
        };
      }
      return {
        ...state,
        ingested: state.ingested + 1,
        timelineById,
        hypothesesById,
        actionsById,
        reconciliation: summary,
        // New beat every time the op lands (including seek re-play).
        reconciliationBeatAt: now,
        reconciliationBeatId: state.reconciliationBeatId + 1,
      };
    }
    case "silence_summary":
      return {
        ...state,
        ingested: state.ingested + 1,
        silence: op.value as SilenceSummary,
      };
    case "contradiction":
      return {
        ...state,
        ingested: state.ingested + 1,
        contradictions: [
          ...state.contradictions,
          op.value as Contradiction,
        ],
        // New beat every time the op lands (including seek re-play).
        contradictionBeatAt: now,
        contradictionBeatId: state.contradictionBeatId + 1,
      };
    default:
      return state;
  }
}

export function dashboardReducer(
  state: DashboardState,
  action: DashboardAction
): DashboardState {
  switch (action.type) {
    case "INGEST":
      return applyEvent(state, action.event, Date.now());
    case "SNAPSHOT":
      return applySnapshot(action.state, action.provenance, action.refusals);
    case "DIFF": {
      const now = Date.now();
      let next = state;
      if (action.provenance && Object.keys(action.provenance).length > 0) {
        next = {
          ...next,
          provenanceById: { ...next.provenanceById, ...action.provenance },
        };
      }
      if (action.refusals && action.refusals.length > 0) {
        next = {
          ...next,
          refusals: [...next.refusals, ...action.refusals],
        };
      }
      return action.ops.reduce((s, op) => applyOp(s, op, now), next);
    }
    case "LATENCY":
      return { ...state, latency: action.latency };
    case "TRANSPORT_ERROR":
      return { ...state, transportError: action.message };
    case "PROVIDER_ERROR":
      return { ...state, providerError: action.message };
    case "RESET":
      return makeInitialState(action.incidentId);
    default:
      return state;
  }
}
