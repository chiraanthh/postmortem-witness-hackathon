import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { dashboardReducer, makeInitialState } from "./reducer";
import { startTransport, type Transport } from "../transport";
import {
  heartbeatLiveLease,
  releaseLiveLease,
} from "../lib/api";

const BOOT_ID = "…";
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
  leaseId: string | null,
  replaySpeed = 1
) {
  const [state, dispatch] = useReducer(
    dashboardReducer,
    BOOT_ID,
    makeInitialState
  );
  const [tick, setTick] = useState(() => Date.now());
  const [runId, setRunId] = useState(0);
  const transportRef = useRef<Transport | null>(null);
  const speedRef = useRef(replaySpeed);
  speedRef.current = replaySpeed;

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
    // Never flash "boot" — replay knows the id; live shows … until snapshot.
    const initialId = mode === "replay" ? "incident_01" : "…";
    dispatch({ type: "RESET", incidentId: initialId });
    const transport = startTransport(dispatch, {
      forceReplay: mode === "replay",
      sessionId: mode === "live" ? sessionId : null,
      replaySpeed: speedRef.current,
    });
    transportRef.current = transport;
    return () => {
      transport.stop();
      transportRef.current = null;
    };
  }, [runId, active, mode, sessionId]);

  useEffect(() => {
    if (mode !== "replay") return;
    transportRef.current?.setReplaySpeed?.(replaySpeed);
  }, [mode, replaySpeed]);

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
    const syncHidden = () => {
      document.documentElement.classList.toggle("tab-hidden", document.hidden);
      if (!document.hidden) setTick(Date.now());
    };
    const tickOnce = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      setTick(Date.now());
    };
    syncHidden();
    tickOnce();
    const id = window.setInterval(tickOnce, 250);
    document.addEventListener("visibilitychange", syncHidden);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", syncHidden);
      document.documentElement.classList.remove("tab-hidden");
    };
  }, [active]);

  const replay = useCallback(() => setRunId((n) => n + 1), []);

  const jumpToReconciliation = useCallback(() => {
    transportRef.current?.jumpToReconciliation?.();
  }, []);

  const pauseReplay = useCallback(() => {
    transportRef.current?.pauseReplay?.();
  }, []);

  const resumeReplay = useCallback(() => {
    transportRef.current?.resumeReplay?.();
  }, []);

  const seekReplay = useCallback((ms: number) => {
    transportRef.current?.seekReplay?.(ms);
  }, []);

  return {
    state,
    tick,
    replay,
    jumpToReconciliation,
    pauseReplay,
    resumeReplay,
    seekReplay,
  };
}
