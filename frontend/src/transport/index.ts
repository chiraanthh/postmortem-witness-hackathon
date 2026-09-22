import type { Dispatch } from "react";
import type { DiffOp, IncidentState } from "../contract";
import { CONTRACT_VERSION } from "../contract";
import type { DashboardAction, GroundingRefusal } from "../state/types";
import { startMockEmitter, MOCK_INCIDENT_ID } from "../mock/emitter";
import {
  startReplayEmitter,
  REPLAY_INCIDENT_ID,
} from "../replay/emitter";

export interface Transport {
  incidentId: string;
  stop: () => void;
  setReplaySpeed?: (speed: number) => void;
  jumpToReconciliation?: () => void;
}

type WireEnvelope =
  | { type: "handshake"; contract_version: string }
  | {
      type: "snapshot";
      state: IncidentState;
      provenance?: Record<
        string,
        { provider: string; model: string; request_id?: string | null }
      >;
      refusals?: GroundingRefusal[];
    }
  | {
      type: "diff";
      ops: DiffOp[];
      provenance?: Record<
        string,
        { provider: string; model: string; request_id?: string | null }
      >;
      refusals?: GroundingRefusal[];
    }
  | { type: "provider_error"; message: string };

function resolveWsUrl(sessionId?: string | null): string {
  const fromEnv = import.meta.env.VITE_WS_URL as string | undefined;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const path =
    sessionId && sessionId.trim()
      ? `/s/${sessionId.trim()}/ws`
      : "/ws";
  if (fromEnv && fromEnv.trim()) {
    try {
      const u = new URL(fromEnv.trim());
      // Env may point at host only or a legacy /ws path — prefer session path.
      return `${u.protocol}//${u.host}${path}`;
    } catch {
      /* fall through */
    }
  }
  return `${proto}//${host}${path}`;
}

const RECONNECT_DELAYS_MS = [800, 1600, 3200, 6400];
const MAX_RECONNECT_ATTEMPTS = RECONNECT_DELAYS_MS.length;


/**
 * Live WebSocket client. Protocol (backend/main.py):
 *   1. handshake { contract_version }
 *   2. snapshot  { state }
 *   3. diff      { ops }  (zero or more)
 *
 * Version mismatch fails loudly — no silent drift onto a stale contract.
 *
 * Mid-call disconnect (host sleep, deploy, network): reconnect and resync
 * from snapshot. If reconnects exhaust, surface a clear TRANSPORT_ERROR —
 * never hang on a frozen board with no banner.
 */
function startLiveTransport(
  dispatch: Dispatch<DashboardAction>,
  sessionId?: string | null
): Transport {
  const url = resolveWsUrl(sessionId);
  let socket: WebSocket | null = null;
  let stopped = false;
  let incidentId = "connecting…";
  let sawHandshake = false;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const fail = (message: string) => {
    // eslint-disable-next-line no-console
    console.error(`[transport] ${message}`);
    dispatch({ type: "TRANSPORT_ERROR", message });
    try {
      socket?.close();
    } catch {
      /* ignore */
    }
  };

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const scheduleReconnect = (reason: string) => {
    if (stopped) return;
    if (reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
      fail(
        `${reason}. Could not reconnect after ${MAX_RECONNECT_ATTEMPTS} attempts — ` +
          "the incident may have ended or the host went idle. Refresh the page."
      );
      return;
    }
    const delay = RECONNECT_DELAYS_MS[reconnectAttempt] ?? 6400;
    reconnectAttempt += 1;
    // eslint-disable-next-line no-console
    console.warn(
      `[transport] ${reason}; reconnect ${reconnectAttempt}/${MAX_RECONNECT_ATTEMPTS} in ${delay}ms`
    );
    dispatch({
      type: "TRANSPORT_ERROR",
      message: `Connection lost — reconnecting (${reconnectAttempt}/${MAX_RECONNECT_ATTEMPTS})…`,
    });
    clearReconnectTimer();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (!stopped) openSocket();
    }, delay);
  };

  const attachHandlers = (ws: WebSocket) => {
    ws.addEventListener("open", () => {
      // eslint-disable-next-line no-console
      console.info(`[transport] connected ${url}`);
    });

    ws.addEventListener("error", () => {
      if (stopped) return;
      // close fires after error; reconnect is driven from close.
    });

    ws.addEventListener("close", () => {
      if (stopped) return;
      if (!sawHandshake) {
        scheduleReconnect(`WebSocket closed before handshake (${url})`);
        return;
      }
      // Mid-stream drop: host sleep, deploy, or network blip.
      scheduleReconnect("WebSocket closed while listening");
    });

    ws.addEventListener("message", (ev) => {
      if (stopped) return;
      let msg: WireEnvelope;
      try {
        msg = JSON.parse(String(ev.data)) as WireEnvelope;
      } catch (err) {
        fail(`invalid JSON frame: ${String(err)}`);
        return;
      }

      if (!msg || typeof msg !== "object" || !("type" in msg)) {
        fail("frame missing type");
        return;
      }

      if (msg.type === "handshake") {
        sawHandshake = true;
        if (msg.contract_version !== CONTRACT_VERSION) {
          fail(
            `contract version mismatch: server=${msg.contract_version} client=${CONTRACT_VERSION}`
          );
          return;
        }
        return;
      }

      if (!sawHandshake) {
        fail(`expected handshake first, got ${msg.type}`);
        return;
      }

      if (msg.type === "snapshot") {
        reconnectAttempt = 0;
        incidentId = msg.state.incident_id;
        dispatch({
          type: "SNAPSHOT",
          state: msg.state,
          provenance: msg.provenance,
          refusals: msg.refusals,
        });
        return;
      }

      if (msg.type === "diff") {
        dispatch({
          type: "DIFF",
          ops: msg.ops ?? [],
          provenance: msg.provenance,
          refusals: msg.refusals,
        });
        return;
      }

      if (msg.type === "provider_error") {
        dispatch({
          type: "PROVIDER_ERROR",
          message: msg.message || "extraction provider failed",
        });
        return;
      }

      fail(`unknown frame type: ${(msg as { type: string }).type}`);
    });
  };

  const openSocket = () => {
    if (stopped) return;
    sawHandshake = false;
    try {
      socket = new WebSocket(url);
    } catch (err) {
      scheduleReconnect(`failed to open WebSocket ${url}: ${String(err)}`);
      return;
    }
    attachHandlers(socket);
  };

  openSocket();

  return {
    get incidentId() {
      return incidentId;
    },
    stop: () => {
      stopped = true;
      clearReconnectTimer();
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      socket = null;
    },
  };
}

/**
 * Seam between the UI and the outside world.
 *
 * - Portal "recorded run" → `forceReplay`: live-pipeline WS capture (zero API).
 * - `VITE_MOCK=true` → hand-authored scaffold mock (frontend-only; never the portal).
 * - Otherwise → live WebSocket to `/s/<id>/ws`.
 */
export function startTransport(
  dispatch: Dispatch<DashboardAction>,
  opts?: {
    forceReplay?: boolean;
    forceMock?: boolean;
    sessionId?: string | null;
    replaySpeed?: number;
  }
): Transport {
  const speed =
    opts?.forceReplay === true
      ? opts.replaySpeed && opts.replaySpeed > 0
        ? opts.replaySpeed
        : Number(import.meta.env.VITE_REPLAY_SPEED ?? "1") || 1
      : Number(import.meta.env.VITE_MOCK_SPEED ?? "1") || 1;

  if (opts?.forceReplay === true) {
    const controller = startReplayEmitter(dispatch, speed);
    return {
      incidentId: REPLAY_INCIDENT_ID,
      stop: controller.stop,
      setReplaySpeed: controller.setSpeed,
      jumpToReconciliation: controller.jumpToReconciliation,
    };
  }

  // Hand mock is a FE-dev flag only. Portal never sets forceMock.
  const useMock =
    opts?.forceMock === true || import.meta.env.VITE_MOCK === "true";

  if (useMock) {
    const controller = startMockEmitter(dispatch, speed);
    return { incidentId: MOCK_INCIDENT_ID, stop: controller.stop };
  }

  return startLiveTransport(dispatch, opts?.sessionId);
}
