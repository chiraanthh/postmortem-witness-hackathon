import type { HypothesisState } from "../contract";
import type { DashboardState, TimelineRow, HypothesisNode } from "./types";

/** Timeline in chronological order — oldest first, newest last (append down). */
export function selectTimeline(s: DashboardState): TimelineRow[] {
  return Object.values(s.timelineById).sort((a, b) => {
    if (a.timestamp_ms !== b.timestamp_ms) return a.timestamp_ms - b.timestamp_ms;
    if (a.connection_epoch !== b.connection_epoch) {
      return a.connection_epoch - b.connection_epoch;
    }
    if (a.turn_order !== b.turn_order) return a.turn_order - b.turn_order;
    return a.event_id.localeCompare(b.event_id);
  });
}

export interface TimelineTurn {
  key: string;
  connection_epoch: number;
  turn_order: number;
  timestamp_ms: number;
  speaker_label: string;
  speaker_name: string | null;
  text: string;
  amendedFrom: string | null;
  correctedAt: number | null;
  events: TimelineRow[];
}

/** Timeline grouped by turn — one utterance row; events nest underneath. */
export function selectTimelineTurns(s: DashboardState): TimelineTurn[] {
  const rows = selectTimeline(s);
  const byKey = new Map<string, TimelineTurn>();
  for (const row of rows) {
    const key = `${row.connection_epoch}/${row.turn_order}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, {
        key,
        connection_epoch: row.connection_epoch,
        turn_order: row.turn_order,
        timestamp_ms: row.timestamp_ms,
        speaker_label: row.speaker_label,
        speaker_name: row.speaker_name,
        text: row.text,
        amendedFrom: row.amendedFrom,
        correctedAt: row.correctedAt,
        events: [row],
      });
    } else {
      existing.events.push(row);
      if ((row.text?.length ?? 0) > (existing.text?.length ?? 0)) {
        existing.text = row.text;
      }
      if (row.amendedFrom) {
        existing.amendedFrom = row.amendedFrom;
        existing.correctedAt = row.correctedAt;
        existing.speaker_label = row.speaker_label;
      }
    }
  }
  return [...byKey.values()].sort((a, b) => {
    if (a.timestamp_ms !== b.timestamp_ms) return a.timestamp_ms - b.timestamp_ms;
    if (a.connection_epoch !== b.connection_epoch) {
      return a.connection_epoch - b.connection_epoch;
    }
    return a.turn_order - b.turn_order;
  });
}

export function selectHypothesesByState(
  s: DashboardState
): Record<HypothesisState, HypothesisNode[]> {
  const cols: Record<HypothesisState, HypothesisNode[]> = {
    open: [],
    ruled_out: [],
    confirmed: [],
  };
  for (const h of Object.values(s.hypothesesById)) cols[h.state].push(h);
  (Object.keys(cols) as HypothesisState[]).forEach((k) =>
    cols[k].sort((a, b) => a.raised_at_ms - b.raised_at_ms)
  );
  return cols;
}

/** Open (unclosed) threads, oldest first. Answered threads set closed=True. */
export function selectThreads(s: DashboardState) {
  return Object.values(s.threadsById)
    .filter((t) => !t.closed)
    .sort((a, b) => a.opened_at_ms - b.opened_at_ms);
}

export function selectActions(s: DashboardState) {
  return Object.values(s.actionsById).sort((a, b) => a.at_ms - b.at_ms);
}

/**
 * Distinct diarization labels currently attributed on the board.
 * Excludes provisional labels (PENDING / ?) — those are not people on call.
 */
export function selectSpeakerCount(s: DashboardState): number {
  const labels = new Set<string>();
  for (const row of Object.values(s.timelineById)) {
    if (row.speaker_label && row.speaker_label !== "PENDING" && row.speaker_label !== "?") {
      labels.add(row.speaker_label);
    }
  }
  for (const h of Object.values(s.hypothesesById)) {
    if (
      h.raised_by_label &&
      h.raised_by_label !== "PENDING" &&
      h.raised_by_label !== "?"
    ) {
      labels.add(h.raised_by_label);
    }
  }
  return labels.size;
}

export interface IncidentStats {
  hypotheses: number;
  open: number;
  ruledOut: number;
  confirmed: number;
  openThreads: number;
  actions: number;
  unownedActions: number;
  status:
    | "CONNECTING"
    | "STANDBY"
    | "INVESTIGATING"
    | "IDENTIFIED"
    | "RESOLVED";
}

export function selectStats(s: DashboardState): IncidentStats {
  const hyps = Object.values(s.hypothesesById);
  const confirmed = hyps.filter((h) => h.state === "confirmed").length;
  const open = hyps.filter((h) => h.state === "open").length;
  const ruledOut = hyps.filter((h) => h.state === "ruled_out").length;
  const threads = Object.values(s.threadsById).filter((t) => !t.closed).length;
  const actions = Object.values(s.actionsById);
  const unowned = actions.filter((a) => a.unowned).length;

  const connecting =
    !s.resolved &&
    s.ingested === 0 &&
    (s.incident_id === "…" ||
      s.incident_id === "boot" ||
      s.incident_id === "connecting…" ||
      (!s.wsConnected && s.playback.status === "idle"));

  let status: IncidentStats["status"] = "STANDBY";
  if (s.resolved) status = "RESOLVED";
  else if (confirmed > 0) status = "IDENTIFIED";
  else if (s.ingested > 0) status = "INVESTIGATING";
  else if (connecting) status = "CONNECTING";

  return {
    hypotheses: hyps.length,
    open,
    ruledOut,
    confirmed,
    openThreads: threads,
    actions: actions.length,
    unownedActions: unowned,
    status,
  };
}
