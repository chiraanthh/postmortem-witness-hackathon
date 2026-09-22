import { useMemo, useState } from "react";
import { useIncident, type SessionMode } from "./state/useIncident";
import {
  selectTimeline,
  selectHypothesesByState,
  selectThreads,
  selectActions,
  selectStats,
  selectSpeakerCount,
} from "./state/selectors";
import { CONTRACT_VERSION } from "./contract";
import { TopBar } from "./components/TopBar";
import { IncidentHero } from "./components/IncidentHero";
import { StatStrip } from "./components/StatStrip";
import { HypothesisBoard } from "./components/HypothesisBoard";
import { Timeline } from "./components/Timeline";
import { OpenThreads } from "./components/OpenThreads";
import { ActionsPanel } from "./components/ActionsPanel";
import { LatencyOverlay } from "./components/LatencyOverlay";
import { RefusalPanel } from "./components/RefusalPanel";
import { PlaybackBar } from "./components/PlaybackBar";
import { Portal } from "./components/Portal";
import {
  ReconciliationBeat,
  useReconciliationDim,
} from "./components/ReconciliationBeat";
import { cx } from "./lib/cx";

export default function App() {
  const [mode, setMode] = useState<SessionMode>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [leaseId, setLeaseId] = useState<string | null>(null);
  const { state, tick, replay } = useIncident(mode, sessionId, leaseId);

  const timeline = useMemo(() => selectTimeline(state), [state]);
  const columns = useMemo(() => selectHypothesesByState(state), [state]);
  const threads = useMemo(() => selectThreads(state), [state]);
  const actions = useMemo(() => selectActions(state), [state]);
  const stats = useMemo(() => selectStats(state), [state]);
  const speakers = useMemo(() => selectSpeakerCount(state), [state]);
  const dimming = useReconciliationDim(
    state.reconciliationBeatAt,
    state.reconciliationBeatId,
    tick
  );

  const confirmedCause = useMemo(() => {
    const c = columns.confirmed[0];
    return c ? c.text : null;
  }, [columns]);

  const leave = () => {
    setMode(null);
    setSessionId(null);
    setLeaseId(null);
  };

  if (mode === null) {
    return (
      <Portal
        onEnterLive={(sid, lid) => {
          setSessionId(sid);
          setLeaseId(lid);
          setMode("live");
        }}
        onEnterReplay={() => {
          setSessionId(null);
          setLeaseId(null);
          setMode("replay");
        }}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <TopBar
        incidentId={state.incident_id}
        status={stats.status}
        clockMs={state.clock_ms}
        onReplay={replay}
        onLeave={leave}
        sessionLabel={
          mode === "replay"
            ? "recorded live pipeline"
            : sessionId
              ? `live · ${sessionId.slice(0, 6)}`
              : "live"
        }
        sessionId={sessionId}
      />

      <ReconciliationBeat
        summary={state.reconciliation}
        beatAt={state.reconciliationBeatAt}
        beatId={state.reconciliationBeatId}
        tick={tick}
      />

      {state.transportError && (
        <div
          role="alert"
          className="border-b border-danger/50 bg-danger/15 px-4 py-3 text-center text-[13px] font-medium text-danger"
        >
          Transport failed: {state.transportError}
        </div>
      )}

      {state.providerError && (
        <div
          role="alert"
          className="border-b border-danger/40 bg-danger/10 px-4 py-2 text-center text-[12px] font-medium text-danger"
        >
          Extraction provider error (no fallback): {state.providerError}
        </div>
      )}

      <main
        className={cx(
          "mx-auto flex max-w-[1500px] flex-col gap-5 px-4 py-6 sm:px-6 sm:py-8 transition-[filter,opacity] duration-700",
          dimming && "opacity-70 saturate-50"
        )}
      >
        <HypothesisBoard columns={columns} tick={tick} />

        <IncidentHero
          incidentId={state.incident_id}
          stats={stats}
          clockMs={state.clock_ms}
          speakers={speakers}
          ingested={state.ingested}
          confirmedCause={confirmedCause}
        />

        <StatStrip stats={stats} clockMs={state.clock_ms} />

        {mode === "live" && sessionId && (
          <PlaybackBar
            onRestartTransport={replay}
            sessionId={sessionId}
            leaseId={leaseId}
          />
        )}

        <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-12">
          <div className="flex flex-col gap-5 lg:col-span-6">
            <Timeline
              rows={timeline}
              tick={tick}
              reconBeatAt={state.reconciliationBeatAt}
            />
            <RefusalPanel refusals={state.refusals} />
          </div>
          <div className="flex flex-col gap-5 lg:col-span-3">
            <OpenThreads
              threads={threads}
              clockMs={state.clock_ms}
              silence={state.silence}
              resolved={state.resolved}
            />
          </div>
          <div className="lg:col-span-3">
            <ActionsPanel actions={actions} />
          </div>
        </div>

        <footer className="pb-16 pt-2 text-center text-[11px] font-medium text-inkFaint">
          Postmortem Witness · listener-only ·{" "}
          {mode === "replay"
            ? "recorded run of the live pipeline"
            : "live pipeline"}{" "}
          · <span className="text-inkMute">contract v{CONTRACT_VERSION}</span>
          {state.resolved ? " · resolved" : ""}
        </footer>
      </main>

      <LatencyOverlay latency={state.latency} />
    </div>
  );
}
