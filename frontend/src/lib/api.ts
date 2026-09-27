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
  queue_depth?: number;
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

/**
 * Visitor-supplied AssemblyAI + Anthropic credentials for a live/upload
 * session. Kept only in React state on the client and in this request body
 * — never written to storage, never logged. See backend/byok.py.
 */
export type ByokKeys = {
  assemblyaiKey: string;
  anthropicKey: string;
};

function byokBody(keys: ByokKeys): {
  assemblyai_api_key: string;
  anthropic_api_key: string;
} {
  return {
    assemblyai_api_key: keys.assemblyaiKey,
    anthropic_api_key: keys.anthropicKey,
  };
}

/** Thrown when the server rejects a supplied key; `which` names the key. */
export class BadKeyError extends Error {
  which: "assemblyai" | "anthropic" | null;
  constructor(message: string, which: "assemblyai" | "anthropic" | null) {
    super(message);
    this.which = which;
  }
}

function keyErrorFromDetail(detail: unknown, fallback: string): Error {
  if (typeof detail === "string") return new Error(detail);
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.message === "string") {
      const which =
        d.which === "assemblyai" || d.which === "anthropic" ? d.which : null;
      return new BadKeyError(d.message, which);
    }
  }
  return new Error(fallback);
}

export async function createLiveSession(
  keys: ByokKeys
): Promise<
  { ok: true } & LiveSession | { ok: false; full: true; status: LivePipelineStatus }
> {
  const res = await postJson("/sessions", { kind: "live", ...byokBody(keys) });
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
    throw keyErrorFromDetail(
      data.detail,
      `create session failed (${res.status})`
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
export async function acquireLiveLease(keys: ByokKeys): Promise<
  | { ok: true; lease_id: string; session_id?: string; status: LivePipelineStatus }
  | { ok: false; full: true; status: LivePipelineStatus }
> {
  const seat = await createLiveSession(keys);
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

export async function joinLiveQueue(): Promise<{
  ticket_id: string;
  position: number;
  ready: boolean;
  lease_id: string | null;
  status: LivePipelineStatus;
}> {
  const res = await postJson("/live/queue");
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok || typeof data.ticket_id !== "string") {
    throw new Error("could not join wait queue");
  }
  return {
    ticket_id: data.ticket_id,
    position: Number(data.position ?? 0),
    ready: Boolean(data.ready),
    lease_id: typeof data.lease_id === "string" ? data.lease_id : null,
    status: {
      used: Number(data.used ?? 0),
      cap: Number(data.cap ?? 2),
      available: Number(data.available ?? 0),
      queue_depth: Number(data.queue_depth ?? 0),
    },
  };
}

export async function heartbeatLiveQueue(ticketId: string): Promise<{
  position: number;
  ready: boolean;
  lease_id: string | null;
} | null> {
  const res = await postJson("/live/queue/heartbeat", { ticket_id: ticketId });
  if (res.status === 404) return null;
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) return null;
  return {
    position: Number(data.position ?? 0),
    ready: Boolean(data.ready),
    lease_id: typeof data.lease_id === "string" ? data.lease_id : null,
  };
}

export async function claimLiveQueue(
  ticketId: string,
  keys: ByokKeys
): Promise<LiveSession> {
  const res = await postJson("/live/queue/claim", {
    ticket_id: ticketId,
    ...byokBody(keys),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (
    !res.ok ||
    typeof data.session_id !== "string" ||
    typeof data.lease_id !== "string"
  ) {
    throw keyErrorFromDetail(
      data.detail,
      `claim failed (${res.status})`
    );
  }
  return {
    session_id: data.session_id,
    lease_id: data.lease_id,
    status: {
      used: Number(data.used ?? 0),
      cap: Number(data.cap ?? 2),
      available: Number(data.available ?? 0),
      queue_depth: Number(data.queue_depth ?? 0),
    },
  };
}

export async function leaveLiveQueue(ticketId: string): Promise<void> {
  try {
    await postJson("/live/queue/leave", { ticket_id: ticketId });
  } catch {
    /* best-effort */
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
