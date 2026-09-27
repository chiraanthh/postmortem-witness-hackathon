import { postJson } from "./api";
import type { DashboardState } from "../state/types";

/**
 * Wire-shaped IncidentState from the client's own DashboardState.
 *
 * Recorded replay never opens a server-side session, so there is no
 * `machine.snapshot()` to export from — the client has already built the
 * exact same shape by applying every replayed diff. render_postmortem()
 * only reads a known subset of fields (see backend/export/__init__.py), all
 * of which exist on the wire types TimelineRow/HypothesisNode/Thread/Action
 * are built from, so this reconstruction renders byte-identical markdown to
 * a live session's export of the same incident.
 */
export function incidentStateForExport(state: DashboardState): unknown {
  return {
    incident_id: state.incident_id,
    started_at_ms: state.started_at_ms,
    resolved: state.resolved,
    timeline: Object.values(state.timelineById),
    hypotheses: Object.values(state.hypothesesById),
    threads: Object.values(state.threadsById),
    actions: Object.values(state.actionsById),
    latency: state.latency,
    silence: state.silence,
    contradictions: state.contradictions,
  };
}

export async function fetchReplayExportMarkdown(
  state: DashboardState
): Promise<string> {
  const res = await postJson("/export/render", incidentStateForExport(state));
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `export failed (${res.status})`);
  }
  return res.text();
}

export function downloadMarkdown(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
