/** Same-origin API helpers for the portal and live sessions at /s/<id>. */

const DEMO_FILE = "demo/audio/incident_01.wav";

export function apiBase(): string {
  const ws = import.meta.env.VITE_WS_URL as string | undefined;
  if (ws && ws.trim()) {
    try {
      const u = new URL(ws.trim());
      const http = u.protocol === "wss:" ? "https:" : "http:";
      return `${http}//${u.host}`;
    } catch {
      /* fall through */
    }
  }
  return "";
}

export function sessionPath(sessionId: string, suffix: string): string {
  return `/s/${sessionId}${suffix}`;
}

export async function postJson(
  path: string,
  body?: unknown
): Promise<Response> {
  return fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export type LivePipelineStatus = {
  used: number;
  cap: number;
  available: number;
};

export type Health = {
  status?: string;
  ok?: boolean;
  running?: boolean;
  finished?: boolean;
  resolved?: boolean;
  clients?: number;
  playback_position_ms?: number;
  playback_duration_ms?: number;
  file?: string | null;
  live_pipeline?: LivePipelineStatus;
  sessions?: Array<{
    session_id: string;
    kind: string;
    running?: boolean;
    finished?: boolean;
  }>;
};

export async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${apiBase()}/health`);
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export type LiveSession = {
  session_id: string;
  lease_id: string;
  status: LivePipelineStatus;
};

export async function createLiveSession(): Promise<
  { ok: true } & LiveSession | { ok: false; full: true; status: LivePipelineStatus }
> {
  const res = await postJson("/sessions", { kind: "live" });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  const detail = (data.detail ?? data) as Record<string, unknown>;
  const status: LivePipelineStatus = {
    used: Number(detail.used ?? data.used ?? 0),
    cap: Number(detail.cap ?? data.cap ?? 2),
    available: Number(detail.available ?? data.available ?? 0),
  };
  if (res.status === 503 || detail.full === true) {
    return { ok: false, full: true, status };
  }
  if (
    !res.ok ||
    typeof data.session_id !== "string" ||
    typeof data.lease_id !== "string"
  ) {
    throw new Error(
      typeof detail.message === "string"
        ? detail.message
        : `create session failed (${res.status})`
    );
  }
  return {
    ok: true,
    session_id: data.session_id,
    lease_id: data.lease_id,
    status: {
      used: Number(data.used ?? status.used),
      cap: Number(data.cap ?? status.cap),
      available: Number(data.available ?? status.available),
    },
  };
}

/** @deprecated prefer createLiveSession — alias kept for older call sites */
export async function acquireLiveLease(): Promise<
  | { ok: true; lease_id: string; session_id?: string; status: LivePipelineStatus }
  | { ok: false; full: true; status: LivePipelineStatus }
> {
  const seat = await createLiveSession();
  if (!seat.ok) return seat;
  return {
    ok: true,
    lease_id: seat.lease_id,
    session_id: seat.session_id,
    status: seat.status,
  };
}

export async function heartbeatLiveLease(leaseId: string): Promise<boolean> {
  const res = await postJson("/live/heartbeat", { lease_id: leaseId });
  return res.ok;
}

export async function releaseLiveLease(leaseId: string): Promise<void> {
  try {
    await postJson("/live/release", { lease_id: leaseId });
  } catch {
    /* best-effort on unload */
  }
}

export async function startLiveIncident(
  sessionId: string,
  leaseId: string
): Promise<Response> {
  return postJson(sessionPath(sessionId, "/incident/start"), {
    file: DEMO_FILE,
    lease_id: leaseId,
  });
}

export { DEMO_FILE };
