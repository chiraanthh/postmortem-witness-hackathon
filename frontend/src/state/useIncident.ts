import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { dashboardReducer, makeInitialState } from "./reducer";
import { startTransport, type Transport } from "../transport";
import { MOCK_INCIDENT_ID } from "../mock/emitter";

/**
 * Owns the incident reducer and the transport lifecycle. Returns the live
 * state, a monotonically increasing `tick` (wall-clock ms, ~4Hz) so components
 * can expire their one-shot highlights, and a `replay` to restart the demo.
 */
export function useIncident() {
  const [state, dispatch] = useReducer(
    dashboardReducer,
    MOCK_INCIDENT_ID,
    makeInitialState
  );
  const [tick, setTick] = useState(() => Date.now());
  const [runId, setRunId] = useState(0);
  const transportRef = useRef<Transport | null>(null);

  // Start (and restart, on replay) the transport.
  useEffect(() => {
    dispatch({ type: "RESET", incidentId: MOCK_INCIDENT_ID });
    const transport = startTransport(dispatch);
    transportRef.current = transport;
    return () => {
      transport.stop();
      transportRef.current = null;
    };
  }, [runId]);

  // A low-frequency heartbeat drives elapsed clocks and lets time-based
  // highlights fade without each component owning its own timer.
  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);

  const replay = useCallback(() => setRunId((n) => n + 1), []);

  return { state, tick, replay };
}
