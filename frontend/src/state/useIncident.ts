import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { dashboardReducer, makeInitialState } from "./reducer";
import { startTransport, type Transport } from "../transport";
import {
  heartbeatLiveLease,
  releaseLiveLease,
} from "../lib/api";

const BOOT_ID = "boot";
const HEARTBEAT_MS = 15_000;

export type SessionMode = "live" | "replay" | null;

/**
 * Owns the incident reducer and the transport lifecycle.
 * Transport stays down until `mode` is set. Live mode heartbeats a seat
 * lease; leaving or unmount releases it so abandoned tabs free capacity.
 * Live WebSocket is scoped to `/s/<sessionId>/ws`.
 */
export function useIncident(
  mode: SessionMode,
  sessionId: string | null,
  leaseId: string | null
) {
  const [state, dispatch] = useReducer(
    dashboardReducer,
    BOOT_ID,
    makeInitialState
  );
  const [tick, setTick] = useState(() => Date.now());
  const [runId, setRunId] = useState(0);
  const transportRef = useRef<Transport | null>(null);

  const active = mode !== null;

  useEffect(() => {
    if (!active) {
      transportRef.current?.stop();
      transportRef.current = null;
      dispatch({ type: "RESET", incidentId: BOOT_ID });
      return;
    }
    if (mode === "live" && !sessionId) {
      return;
    }
    dispatch({ type: "RESET", incidentId: BOOT_ID });
    const transport = startTransport(dispatch, {
      forceReplay: mode === "replay",
      // Hand-authored mock is VITE_MOCK-only (dev). Portal replay never uses it.
      sessionId: mode === "live" ? sessionId : null,
    });
    transportRef.current = transport;
    return () => {
      transport.stop();
      transportRef.current = null;
    };
  }, [runId, active, mode, sessionId]);

  // Live seat heartbeat + release on leave / unload.
  useEffect(() => {
    if (mode !== "live" || !leaseId) return;

    const beat = () => {
      void heartbeatLiveLease(leaseId);
    };
    beat();
    const id = window.setInterval(beat, HEARTBEAT_MS);

    const onUnload = () => {
      const base =
        typeof window !== "undefined" ? window.location.origin : "";
      try {
        navigator.sendBeacon?.(
          `${base}/live/release`,
          new Blob([JSON.stringify({ lease_id: leaseId })], {
            type: "application/json",
          })
        );
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("pagehide", onUnload);

    return () => {
      window.clearInterval(id);
      window.removeEventListener("pagehide", onUnload);
      void releaseLiveLease(leaseId);
    };
  }, [mode, leaseId]);

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setTick(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [active]);

  const replay = useCallback(() => setRunId((n) => n + 1), []);

  return { state, tick, replay };
}
