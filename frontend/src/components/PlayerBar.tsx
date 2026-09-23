import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, DEMO_FILE, postJson, sessionPath } from "../lib/api";
import { REPLAY_SPEEDS, type ReplaySpeed } from "../replay/emitter";
import { SessionAudio } from "./SessionAudio";
import { cx } from "../lib/cx";

export type PlaybackMeta = {
  status?: string;
  running?: boolean;
  finished?: boolean;
  paused?: boolean;
  playback_position_ms?: number;
  playback_duration_ms?: number;
};

function fmt(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

/**
 * One transport for live, upload, and recorded modes.
 * Order: play/pause · speed · mute · progress · time · Restart · Export · Jump.
 *
 * Live/upload: cursor from WS `playback` frames. HTTP GET /incident only when
 * the socket is down — 1s cadence, one in-flight (AbortController), paused
 * while document.hidden.
 */
export function PlayerBar({
  mode,
  sessionId,
  leaseId,
  playback,
  wsConnected,
  clockMs,
  durationMs,
  speed,
  onSpeed,
  paused,
  onPauseToggle,
  onRestart,
  onJumpReconciliation,
  onSeek,
  audioSrc,
  sessionKey,
  getTargetMs,
  onAudioClock,
  audioArmed,
}: {
  mode: "live" | "replay";
  sessionId: string | null;
  leaseId: string | null;
  playback: {
    position_ms: number;
    duration_ms: number;
    paused: boolean;
    status: string;
    running: boolean;
    finished: boolean;
  };
  wsConnected: boolean;
  /** Single UI clock — must match Elapsed and the scrubber. */
  clockMs: number;
  durationMs: number;
  speed: ReplaySpeed;
  onSpeed: (s: ReplaySpeed) => void;
  paused: boolean;
  onPauseToggle: () => void;
  onRestart: () => void;
  onJumpReconciliation?: () => void;
  onSeek?: (ms: number) => void;
  audioSrc: string | null;
  sessionKey: string;
  getTargetMs: () => number | null;
  onAudioClock?: (ms: number) => void;
  audioArmed: boolean;
}) {
  const [fallback, setFallback] = useState<PlaybackMeta>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seekMs, setSeekMs] = useState(0);
  const pendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const useWs = mode === "replay" || wsConnected;
  const position = clockMs;
  const duration =
    durationMs > 0
      ? durationMs
      : useWs
        ? playback.duration_ms
        : (fallback.playback_duration_ms ?? 0);
  const status =
    mode === "replay"
      ? paused
        ? "paused"
        : "replay"
      : useWs
        ? playback.status
        : (fallback.status ?? "idle");
  const running =
    mode === "replay"
      ? true
      : useWs
        ? playback.running
        : Boolean(fallback.running);

  useEffect(() => {
    setSeekMs(position);
  }, [position]);

  const refreshFallback = useCallback(async () => {
    if (mode !== "live" || !sessionId) return;
    if (pendingRef.current) return;
    if (typeof document !== "undefined" && document.hidden) return;
    pendingRef.current = true;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await fetch(
        `${apiBase()}${sessionPath(sessionId, "/incident")}`,
        { signal: ctrl.signal }
      );
      if (!res.ok) return;
      const data = (await res.json()) as Record<string, unknown>;
      const pos =
        typeof data.playback_position_ms === "number"
          ? data.playback_position_ms
          : typeof data.clock_ms === "number"
            ? data.clock_ms
            : 0;
      const dur =
        typeof data.playback_duration_ms === "number"
          ? data.playback_duration_ms
          : 0;
      const st =
        typeof data.playback_status === "string"
          ? data.playback_status
          : undefined;
      const isPaused = Boolean(data.paused);
      const finished = st === "finished";
      const isRunning =
        !finished &&
        st !== undefined &&
        st !== "idle" &&
        st !== "error";
      setFallback({
        status: st,
        running: isRunning,
        finished,
        paused: isPaused,
        playback_position_ms: pos,
        playback_duration_ms: dur,
      });
      setSeekMs(pos);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      /* ignore poll errors */
    } finally {
      pendingRef.current = false;
    }
  }, [mode, sessionId]);

  // Fallback poll only while the WebSocket is down (live/upload).
  useEffect(() => {
    if (mode !== "live" || useWs) {
      abortRef.current?.abort();
      abortRef.current = null;
      return;
    }
    void refreshFallback();
    const id = window.setInterval(() => void refreshFallback(), 1000);
    const onVis = () => {
      if (!document.hidden) void refreshFallback();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
      abortRef.current?.abort();
    };
  }, [mode, useWs, refreshFallback]);

  const run = async (label: string, fn: () => Promise<Response>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `${label} failed (${res.status})`);
      }
      if (mode === "live" && !useWs) await refreshFallback();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const base = sessionId ? sessionPath(sessionId, "") : "";

  const commitSeek = (ms: number) => {
    if (mode === "replay") {
      onSeek?.(ms);
      return;
    }
    if (!sessionId) return;
    void run("seek", () =>
      postJson(`${base}/incident/seek`, { ms })
    );
  };

  const setLiveSpeed = (s: ReplaySpeed) => {
    onSpeed(s);
    if (mode === "live" && sessionId) {
      void run("speed", () =>
        postJson(`${base}/incident/speed`, { speed: s })
      );
    }
  };

  return (
    <section
      className="sticky bottom-0 z-20 border-t border-line bg-base/95 backdrop-blur-xl"
      aria-label="Playback controls"
    >
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-2 px-4 py-3 sm:px-6">
        <button
          type="button"
          disabled={busy || (mode === "live" && !running)}
          className="pill-btn border border-line2 bg-raised/70 text-ink"
          onClick={onPauseToggle}
        >
          {paused ? "Play" : "Pause"}
        </button>

        <div
          className="flex shrink-0 items-center rounded-full border border-line bg-panel/60 p-0.5"
          role="group"
          aria-label="Playback speed"
        >
          {REPLAY_SPEEDS.map((s) => {
            const active = speed === s;
            return (
              <button
                key={s}
                type="button"
                onClick={() => setLiveSpeed(s)}
                className={cx(
                  "whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold",
                  active
                    ? "bg-accent/20 text-accentSoft"
                    : "text-inkMute hover:text-ink"
                )}
                aria-pressed={active}
              >
                {s}×
              </button>
            );
          })}
        </div>

        <SessionAudio
          src={audioSrc}
          sessionKey={sessionKey}
          getTargetMs={getTargetMs}
          paused={paused}
          playbackRate={speed}
          armed={audioArmed}
          onClockMs={onAudioClock}
        />

        <div className="flex min-w-[160px] flex-1 basis-[240px] flex-col gap-1 px-1">
          <div className="flex items-center justify-between gap-2 text-[11px] text-inkFaint">
            <span className="led shrink-0 text-inkMute">{fmt(position)}</span>
            <span className="truncate">{status}</span>
            <span className="led shrink-0 text-inkMute">{fmt(duration)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={Math.max(duration, 1)}
            step={500}
            value={Math.min(seekMs, Math.max(duration, 1))}
            disabled={busy || duration <= 0}
            className="w-full accent-[var(--accent)]"
            onChange={(e) => setSeekMs(Number(e.target.value))}
            onMouseUp={() => commitSeek(seekMs)}
            onTouchEnd={() => commitSeek(seekMs)}
          />
        </div>

        {mode === "live" && leaseId && (
          <button
            type="button"
            disabled={busy}
            className="pill-btn border border-accent/40 bg-accent/15 text-accentSoft hover:border-accent"
            onClick={() =>
              void run("start", () =>
                postJson(`${base}/incident/start`, {
                  file: DEMO_FILE,
                  lease_id: leaseId,
                })
              )
            }
          >
            {running ? "Running" : "Start demo"}
          </button>
        )}

        <button
          type="button"
          disabled={busy || (mode === "live" && !leaseId)}
          className="pill-btn whitespace-nowrap border border-line2 bg-raised/70 text-ink"
          onClick={() => {
            if (mode === "replay") {
              onRestart();
              return;
            }
            void run("restart", async () => {
              const res = await postJson(`${base}/incident/restart`, {
                lease_id: leaseId,
              });
              onRestart();
              return res;
            });
          }}
        >
          Restart
        </button>

        {mode === "live" && sessionId ? (
          <a
            className="pill-btn whitespace-nowrap border border-line2 bg-raised/70 text-ink hover:border-accent/40"
            href={`${apiBase()}${base}/export`}
            target="_blank"
            rel="noreferrer"
          >
            Export
          </a>
        ) : null}

        {mode === "replay" && onJumpReconciliation ? (
          <button
            type="button"
            onClick={onJumpReconciliation}
            className="pill-btn whitespace-nowrap border border-line2 bg-raised/70 text-ink hover:border-accent/50 hover:text-accentSoft"
            title="Skip to teardown speaker reconciliation"
          >
            Jump to recon
          </button>
        ) : null}
      </div>

      {error && (
        <p
          className="mx-auto max-w-[1500px] px-4 pb-2 text-[11px] text-danger sm:px-6"
          role="alert"
        >
          {error}
        </p>
      )}
    </section>
  );
}
