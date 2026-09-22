import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { UploadPage } from "./components/UploadPage";
import { SessionAudio } from "./components/SessionAudio";
import { ReplayControls } from "./components/ReplayControls";
import {
  ReconciliationBeat,
  useReconciliationDim,
} from "./components/ReconciliationBeat";
import { cx } from "./lib/cx";
import { apiBase } from "./lib/api";
import {
  portalReplaySpeed,
  REPLAY_AUDIO_URL,
  type ReplaySpeed,
} from "./replay/emitter";

type PortalView = "home" | "upload";

export default function App() {
  const [portalView, setPortalView] = useState<PortalView>("home");
  const [mode, setMode] = useState<SessionMode>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [leaseId, setLeaseId] = useState<string | null>(null);
  const [audioArmed, setAudioArmed] = useState(false);
  const [livePaused, setLivePaused] = useState(false);
  const [livePosMs, setLivePosMs] = useState(0);
  const [replaySpeed, setReplaySpeed] = useState<ReplaySpeed>(() => {
    const n = portalReplaySpeed();
    if (n === 2 || n === 4) return n;
    return 1;
  });
  const replayOriginRef = useRef(0);
  const replayStartWallRef = useRef(0);

  const { state, tick, replay, jumpToReconciliation } = useIncident(
    mode,
    sessionId,
    leaseId,
    replaySpeed
  );

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
    setAudioArmed(false);
    setPortalView("home");
  };

  // Replay: arm audio on the portal click (above); keep wall clock for drift.
  useEffect(() => {
    if (mode !== "replay") return;
    replayStartWallRef.current = performance.now();
    replayOriginRef.current = 0;
  }, [mode]);

  const getTargetMs = useCallback((): number | null => {
    if (mode === "live" || mode === "replay") {
      if (mode === "live") return livePosMs;
      // Prefer board clock (incident audio time). Fall back to wall*speed.
      if (state.clock_ms > 0) return state.clock_ms;
      const elapsed = performance.now() - replayStartWallRef.current;
      return replayOriginRef.current + elapsed * replaySpeed;
    }
    return null;
  }, [mode, livePosMs, state.clock_ms, replaySpeed]);

  const audioSrc =
    mode === "replay"
      ? `${apiBase()}${REPLAY_AUDIO_URL}`
      : sessionId
        ? `${apiBase()}/s/${sessionId}/audio`
        : null;

  const sessionKey =
    mode === "replay" ? "replay" : sessionId ? `s:${sessionId}` : "none";

  if (mode === null) {
    if (portalView === "upload") {
      return (
        <UploadPage
          onCancel={() => setPortalView("home")}
          onStarted={(sid, lid) => {
            setSessionId(sid);
            setLeaseId(lid);
            setAudioArmed(true);
            setMode("live");
          }}
        />
      );
    }
    return (
      <Portal
        onEnterLive={(sid, lid) => {
          setSessionId(sid);
          setLeaseId(lid);
          setAudioArmed(true);
          setMode("live");
        }}
        onEnterReplay={() => {
          setSessionId(null);
          setLeaseId(null);
          setAudioArmed(true);
          replayStartWallRef.current = performance.now();
          setMode("replay");
        }}
        onEnterUpload={() => setPortalView("upload")}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <TopBar
        incidentId={state.incident_id}
        status={stats.status}
        clockMs={state.clock_ms}
        onReplay={() => {
          replay();
          if (mode === "replay") {
            replayStartWallRef.current = performance.now();
          }
        }}
        onLeave={leave}
        sessionLabel={
          mode === "replay"
            ? "recorded live pipeline"
            : sessionId
              ? `live · ${sessionId.slice(0, 6)}`
              : "live"
        }
        sessionId={sessionId}
        audio={
          <SessionAudio
            src={audioSrc}
            sessionKey={sessionKey}
            getTargetMs={getTargetMs}
            paused={mode === "live" ? livePaused : false}
            playbackRate={mode === "replay" ? replaySpeed : 1}
            armed={audioArmed}
          />
        }
        replayControls={
          mode === "replay" ? (
            <ReplayControls
              speed={replaySpeed}
              onSpeed={setReplaySpeed}
              onJumpReconciliation={jumpToReconciliation}
            />
          ) : null
        }
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
            onPlaybackMeta={(meta) => {
              if (typeof meta.playback_position_ms === "number") {
                setLivePosMs(meta.playback_position_ms);
              }
              if (typeof meta.paused === "boolean") {
                setLivePaused(meta.paused);
              }
            }}
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

      <LatencyOverlay
        latency={state.latency}
        source={mode === "replay" ? "recorded" : "live"}
      />
    </div>
  );
}
