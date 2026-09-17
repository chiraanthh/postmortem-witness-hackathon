import { useMemo } from "react";
import { useIncident } from "./state/useIncident";
import {
  selectTimeline,
  selectHypothesesByState,
  selectThreads,
  selectActions,
  selectStats,
} from "./state/selectors";
import { TopBar } from "./components/TopBar";
import { IncidentHero } from "./components/IncidentHero";
import { StatStrip } from "./components/StatStrip";
import { HypothesisBoard } from "./components/HypothesisBoard";
import { Timeline } from "./components/Timeline";
import { OpenThreads } from "./components/OpenThreads";
import { ActionsPanel } from "./components/ActionsPanel";
import { LatencyOverlay } from "./components/LatencyOverlay";

export default function App() {
  const { state, tick, replay } = useIncident();

  const timeline = useMemo(() => selectTimeline(state), [state]);
  const columns = useMemo(() => selectHypothesesByState(state), [state]);
  const threads = useMemo(() => selectThreads(state), [state]);
  const actions = useMemo(() => selectActions(state), [state]);
  const stats = useMemo(() => selectStats(state), [state]);

  const speakers = useMemo(() => {
    const set = new Set(timeline.map((r) => r.speaker_label));
    return set.size;
  }, [timeline]);

  const confirmedCause = useMemo(() => {
    const c = columns.confirmed[0];
    return c ? c.text : null;
  }, [columns]);

  return (
    <div className="min-h-screen">
      <TopBar
        incidentId={state.incident_id}
        status={stats.status}
        clockMs={state.clock_ms}
        onReplay={replay}
      />

      <main className="mx-auto flex max-w-[1500px] flex-col gap-5 px-4 py-6 sm:px-6 sm:py-8">
        <IncidentHero
          incidentId={state.incident_id}
          stats={stats}
          clockMs={state.clock_ms}
          speakers={speakers}
          ingested={state.ingested}
          confirmedCause={confirmedCause}
        />

        <StatStrip stats={stats} clockMs={state.clock_ms} />

        {/* Centerpiece */}
        <HypothesisBoard columns={columns} tick={tick} />

        {/* Supporting panels */}
        <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-12">
          <div className="lg:col-span-6">
            <Timeline rows={timeline} tick={tick} />
          </div>
          <div className="lg:col-span-3">
            <OpenThreads threads={threads} />
          </div>
          <div className="lg:col-span-3">
            <ActionsPanel actions={actions} />
          </div>
        </div>

        <footer className="pb-16 pt-2 text-center text-[11px] text-inkFaint">
          Postmortem Witness · listener-only · mock replay ·{" "}
          <span className="text-inkMute">contract v1.1.0</span>
        </footer>
      </main>

      <LatencyOverlay latency={state.latency} />
    </div>
  );
}
