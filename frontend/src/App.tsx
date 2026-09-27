import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useIncident, type SessionMode } from "./state/useIncident";
import {
  selectTimelineTurns,
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
import { PlayerBar } from "./components/PlayerBar";
import { Portal } from "./components/Portal";
import { UploadPage } from "./components/UploadPage";
import {
  ReconciliationBeat,
  useReconciliationDim,
} from "./components/ReconciliationBeat";
import { cx } from "./lib/cx";
import { apiBase, postJson, sessionPath } from "./lib/api";
import { fetchReplayExportMarkdown, downloadMarkdown } from "./lib/export";
import {
  portalReplaySpeed,
  REPLAY_AUDIO_URL,
  replayDurationMs,
  type ReplaySpeed,
} from "./replay/emitter";

type PortalView = "home" | "upload";

export default function App() {
  const [portalView, setPortalView] = useState<PortalView>("home");
  // BYOK: visitor-supplied keys for live/upload only. Component state,
  // never persisted — see Portal.tsx and backend/byok.py.
  const [assemblyaiKey, setAssemblyaiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [mode, setMode] = useState<SessionMode>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [leaseId, setLeaseId] = useState<string | null>(null);
  const [audioArmed, setAudioArmed] = useState(false);
  const [userPaused, setUserPaused] = useState(false);
  const [audioClockMs, setAudioClockMs] = useState(0);
  const [replaySpeed, setReplaySpeed] = useState<ReplaySpeed>(() => {
    const n = portalReplaySpeed();
    if (n === 2 || n === 3) return n;
    return 1;
  });
  const replayOriginRef = useRef(0);
  const replayStartWallRef = useRef(0);

  const {
    state,
    tick,
    replay,
    jumpToReconciliation,
    pauseReplay,
    resumeReplay,
    seekReplay,
  } = useIncident(mode, sessionId, leaseId, replaySpeed);

  const timelineTurns = useMemo(() => selectTimelineTurns(state), [state]);
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

  // Replay has no server-side session to hit /s/<id>/export — render from
  // the board the client already reconstructed from replayed diffs.
  const onExportReplay = useCallback(async () => {
    const markdown = await fetchReplayExportMarkdown(state);
    downloadMarkdown(`postmortem-${state.incident_id || "incident"}.md`, markdown);
  }, [state]);

  const leave = () => {
    setMode(null);
    setSessionId(null);
    setLeaseId(null);
    setAudioArmed(false);
    setUserPaused(false);
    setAudioClockMs(0);
    setPortalView("home");
  };

  useEffect(() => {
    if (mode !== "replay") return;
    replayStartWallRef.current = performance.now();
    replayOriginRef.current = 0;
    setUserPaused(false);
    setAudioClockMs(0);
  }, [mode]);

  // Server / replay target for audio drift correction only — not the UI clock.
  //
  // Always blend the wall-clock estimate with state.clock_ms via Math.max,
  // rather than switching to state.clock_ms the instant it goes nonzero.
  // This fixture's own position tracker reports 0 for the call's first ~3s,
  // then catches up gradually — switching branches on ">0" would freeze the
  // target at whatever the (already-advanced) wall-clock estimate reached
  // right as real data arrives, while native <audio> playback keeps
  // climbing past it, so drift correction would repeatedly yank it back
  // every ~250ms until the real value caught up. Both sources only ever
  // increase within a run (reset together on restart), so their max is
  // monotonic by construction — no separate clamp/ref needed.
  const getTargetMs = useCallback((): number | null => {
    if (mode === "live") return state.playback.position_ms;
    if (mode === "replay") {
      const elapsed = performance.now() - replayStartWallRef.current;
      const wallClockEstimate = replayOriginRef.current + elapsed * replaySpeed;
      return Math.max(state.clock_ms, wallClockEstimate);
    }
    return null;
  }, [mode, state.clock_ms, state.playback.position_ms, replaySpeed]);

  const audioSrc =
    mode === "replay"
      ? `${apiBase()}${REPLAY_AUDIO_URL}`
      : sessionId
        ? `${apiBase()}/s/${sessionId}/audio`
        : null;

  const sessionKey =
    mode === "replay" ? "replay" : sessionId ? `s:${sessionId}` : "none";

  // One clock: prefer the audio element's currentTime; fall back to WS cursor.
  const clockMs =
    audioClockMs > 0
      ? audioClockMs
      : mode === "live"
        ? state.playback.position_ms || state.clock_ms
        : state.clock_ms;

  const durationMs =
    mode === "replay"
      ? replayDurationMs()
      : state.playback.duration_ms > 0
        ? state.playback.duration_ms
        : 282000;

  const paused =
    mode === "replay"
      ? userPaused
      : userPaused || state.playback.paused;

  const displayIncidentId =
    state.incident_id === "…" || state.incident_id === "boot"
      ? stats.status === "CONNECTING"
        ? "Connecting…"
        : "…"
      : state.incident_id;

  if (mode === null) {
    if (portalView === "upload") {
      return (
        <UploadPage
          assemblyaiKey={assemblyaiKey}
          anthropicKey={anthropicKey}
          onCancel={() => setPortalView("home")}
          onStarted={(sid, lid) => {
            setSessionId(sid);
            setLeaseId(lid);
            setAudioArmed(true);
            setUserPaused(false);
            setAudioClockMs(0);
            setMode("live");
          }}
        />
      );
    }
    return (
      <Portal
        assemblyaiKey={assemblyaiKey}
        anthropicKey={anthropicKey}
        onAssemblyaiKeyChange={setAssemblyaiKey}
        onAnthropicKeyChange={setAnthropicKey}
        onEnterLive={(sid, lid) => {
          setSessionId(sid);
          setLeaseId(lid);
          setAudioArmed(true);
          setUserPaused(false);
          setAudioClockMs(0);
          setMode("live");
        }}
        onEnterReplay={() => {
          setSessionId(null);
          setLeaseId(null);
          setAudioArmed(true);
          setUserPaused(false);
          setAudioClockMs(0);
          replayStartWallRef.current = performance.now();
          setMode("replay");
        }}
        onEnterUpload={() => setPortalView("upload")}
      />
    );
  }

  return (
    <div className="flex min-h-screen flex-col overflow-x-clip">
      <TopBar
        incidentId={displayIncidentId}
        status={stats.status}
        clockMs={clockMs}
        onReplay={() => {
          replay();
          setUserPaused(false);
          setAudioClockMs(0);
          if (mode === "replay") {
            replayStartWallRef.current = performance.now();
          }
        }}
        onLeave={leave}
        sessionLabel={
          mode === "replay"
            ? "recorded"
            : stats.status === "CONNECTING"
              ? "Connecting…"
              : sessionId
                ? `live · ${sessionId.slice(0, 6)}`
                : "live"
        }
        sessionId={sessionId}
        latencyChip={
          <LatencyOverlay
            latency={state.latency}
            source={mode === "replay" ? "recorded" : "live"}
          />
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
          "mx-auto flex w-full max-w-[1500px] flex-1 flex-col gap-5 overflow-x-clip px-4 py-6 sm:px-6 sm:py-8 transition-[filter,opacity] duration-700",
          dimming && "opacity-70 saturate-50"
        )}
      >
        <HypothesisBoard columns={columns} tick={tick} />

        <IncidentHero
          incidentId={displayIncidentId}
          stats={stats}
          clockMs={clockMs}
          speakers={speakers}
          ingested={state.ingested}
          confirmedCause={confirmedCause}
        />

        <StatStrip stats={stats} clockMs={clockMs} />

        <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-12">
          <div className="flex min-w-0 flex-col gap-5 lg:col-span-6">
            <Timeline
              turns={timelineTurns}
              tick={tick}
              reconBeatAt={state.reconciliationBeatAt}
              partialCaption={mode === "live" ? state.partialCaption : null}
            />
            <RefusalPanel refusals={state.refusals} />
          </div>
          <div className="flex min-w-0 flex-col gap-5 lg:col-span-3">
            <OpenThreads
              threads={threads}
              clockMs={clockMs}
              silence={state.silence}
              resolved={state.resolved}
            />
          </div>
          <div className="min-w-0 lg:col-span-3">
            <ActionsPanel actions={actions} />
          </div>
        </div>

        <footer className="pb-24 pt-2 text-center text-[11px] font-medium text-inkFaint">
          Postmortem Witness · listener-only ·{" "}
          {mode === "replay"
            ? "recorded run of the live pipeline"
            : "live pipeline"}{" "}
          · <span className="text-inkMute">contract v{CONTRACT_VERSION}</span>
          {state.resolved ? " · resolved" : ""}
        </footer>
      </main>

      <PlayerBar
        mode={mode === "replay" ? "replay" : "live"}
        sessionId={sessionId}
        leaseId={leaseId}
        playback={
          mode === "replay"
            ? {
                position_ms: clockMs,
                duration_ms: durationMs,
                paused: userPaused,
                status: userPaused ? "paused" : "replay",
                running: true,
                finished: state.resolved,
              }
            : state.playback
        }
        wsConnected={mode === "live" ? state.wsConnected : true}
        clockMs={clockMs}
        durationMs={durationMs}
        speed={replaySpeed}
        onSpeed={setReplaySpeed}
        paused={paused}
        onPauseToggle={() => {
          if (mode === "replay") {
            if (userPaused) {
              resumeReplay();
              setUserPaused(false);
            } else {
              pauseReplay();
              setUserPaused(true);
            }
            return;
          }
          const next = !paused;
          setUserPaused(next);
          if (!sessionId) return;
          const path = next
            ? sessionPath(sessionId, "/incident/pause")
            : sessionPath(sessionId, "/incident/resume");
          void postJson(path);
        }}
        onRestart={() => {
          replay();
          setUserPaused(false);
          setAudioClockMs(0);
          if (mode === "replay") {
            replayStartWallRef.current = performance.now();
          }
        }}
        onJumpReconciliation={
          mode === "replay" ? jumpToReconciliation : undefined
        }
        onSeek={
          mode === "replay"
            ? (ms) => {
                seekReplay(ms);
                setAudioClockMs(ms);
              }
            : undefined
        }
        onExportReplay={mode === "replay" ? onExportReplay : undefined}
        audioSrc={audioSrc}
        sessionKey={sessionKey}
        getTargetMs={getTargetMs}
        onAudioClock={setAudioClockMs}
        audioArmed={audioArmed}
      />
    </div>
  );
}
