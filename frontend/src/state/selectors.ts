import type { HypothesisState } from "../contract";
import type { DashboardState, TimelineRow, HypothesisNode } from "./types";

/** Timeline in chronological order — oldest first, newest last (append down). */
export function selectTimeline(s: DashboardState): TimelineRow[] {
  return Object.values(s.timelineByTurnOrder).sort(
    (a, b) => a.turn_order - b.turn_order
  );
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

/** Open threads, most recent last. Nothing is ever silently dropped. */
export function selectThreads(s: DashboardState) {
  return Object.values(s.threadsById).sort(
    (a, b) => a.opened_at_ms - b.opened_at_ms
  );
}

export function selectActions(s: DashboardState) {
  return Object.values(s.actionsById).sort((a, b) => a.at_ms - b.at_ms);
}

export interface IncidentStats {
  hypotheses: number;
  open: number;
  ruledOut: number;
  confirmed: number;
  openThreads: number;
  actions: number;
  unownedActions: number;
  status: "STANDBY" | "INVESTIGATING" | "IDENTIFIED";
}

export function selectStats(s: DashboardState): IncidentStats {
  const hyps = Object.values(s.hypothesesById);
  const confirmed = hyps.filter((h) => h.state === "confirmed").length;
  const open = hyps.filter((h) => h.state === "open").length;
  const ruledOut = hyps.filter((h) => h.state === "ruled_out").length;
  const threads = Object.values(s.threadsById).filter((t) => !t.closed).length;
  const actions = Object.values(s.actionsById);
  const unowned = actions.filter((a) => !a.owner || a.owner.trim() === "").length;

  let status: IncidentStats["status"] = "STANDBY";
  if (confirmed > 0) status = "IDENTIFIED";
  else if (s.ingested > 0) status = "INVESTIGATING";

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
