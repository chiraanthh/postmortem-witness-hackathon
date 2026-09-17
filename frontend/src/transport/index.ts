import type { Dispatch } from "react";
import type { DashboardAction } from "../state/types";
import { startMockEmitter, MOCK_INCIDENT_ID } from "../mock/emitter";

export interface Transport {
  incidentId: string;
  stop: () => void;
}

/**
 * The single seam between the UI and the outside world.
 *
 * Today it always returns the mock emitter — the mock is the default and the
 * permanent fallback for the live demo. When the live feed is built, a
 * WebSocket client will parse each frame (an Event or an IncidentState per the
 * schema's oneOf) and dispatch INGEST / SNAPSHOT through this exact same
 * `dispatch`, so no UI or reducer code changes.
 *
 * Gate: mock is ON unless VITE_MOCK is explicitly "false".
 */
export function startTransport(dispatch: Dispatch<DashboardAction>): Transport {
  const useMock = import.meta.env.VITE_MOCK !== "false";
  const speed = Number(import.meta.env.VITE_MOCK_SPEED ?? "1") || 1;

  if (!useMock) {
    // Live transport is intentionally not built yet (see project brief).
    // Fall back to the mock so a demo is never left with a blank screen.
    // eslint-disable-next-line no-console
    console.warn(
      "[transport] VITE_MOCK=false but no live WebSocket client is built yet; using mock."
    );
  }

  const controller = startMockEmitter(dispatch, speed);
  return { incidentId: MOCK_INCIDENT_ID, stop: controller.stop };
}
