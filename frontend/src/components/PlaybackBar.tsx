import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, DEMO_FILE, postJson, sessionPath } from "../lib/api";

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
 * Demo playback controls. Cursor comes from the WebSocket `playback` frames
 * while connected. HTTP GET /incident is only a fallback when the socket is
 * down — at most one in-flight request, 1s cadence, paused when the tab is
 * hidden.
 */
export function PlaybackBar({
  onRestartTransport,
  sessionId,
  leaseId,
  playback,
  wsConnected,
  onPlaybackMeta,
  muteControl,
  speedControl,
}: {
  onRestartTransport: () => void;
  sessionId: string;
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
  onPlaybackMeta?: (meta: PlaybackMeta) => void;
  muteControl?: React.ReactNode;
  speedControl?: React.ReactNode;
}) {
  const [fallback, setFallback] = useState<PlaybackMeta>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seekMs, setSeekMs] = useState(0);
  const pendingRef = useRef(false);
  const onMetaRef = useRef(onPlaybackMeta);
  onMetaRef.current = onPlaybackMeta;

  const useWs = wsConnected;
  const position = useWs
    ? playback.position_ms
    : (fallback.playback_position_ms ?? 0);
  const duration = useWs
    ? playback.duration_ms
    : (fallback.playback_duration_ms ?? 0);
  const paused = useWs ? playback.paused : Boolean(fallback.paused);
  const running = useWs ? playback.running : Boolean(fallback.running);
  const status = useWs ? playback.status : (fallback.status ?? "idle");

  // Push WS cursor to audio sync without re-creating poll effects.
  useEffect(() => {
    if (!useWs) return;
    setSeekMs(position);
    onMetaRef.current?.({
      status,
      running,
      finished: playback.finished,
      paused,
      playback_position_ms: position,
      playback_duration_ms: duration,
    });
  }, [useWs, position, duration, paused, running, status, playback.finished]);

  const refreshFallback = useCallback(async () => {
    if (pendingRef.current) return;
    if (typeof document !== "undefined" && document.hidden) return;
    pendingRef.current = true;
    try {
      const res = await fetch(
        `${apiBase()}${sessionPath(sessionId, "/incident")}`
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
      const next: PlaybackMeta = {
        status: st,
        running: isRunning,
        finished,
        paused: isPaused,
        playback_position_ms: pos,
        playback_duration_ms: dur,
      };
      setFallback(next);
      setSeekMs(pos);
      onMetaRef.current?.(next);
    } catch {
      /* ignore poll errors */
    } finally {
      pendingRef.current = false;
    }
  }, [sessionId]);

  // Fallback poll only while the WebSocket is down.
  useEffect(() => {
    if (useWs) return;
    void refreshFallback();
    const id = window.setInterval(() => void refreshFallback(), 1000);
    const onVis = () => {
      if (!document.hidden) void refreshFallback();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [useWs, refreshFallback]);

  const run = async (label: string, fn: () => Promise<Response>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `${label} failed (${res.status})`);
      }
      if (!useWs) await refreshFallback();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const base = sessionPath(sessionId, "");

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !running}
          className="pill-btn border border-line2 bg-raised/70 text-ink"
          onClick={() =>
            void run(paused ? "resume" : "pause", () =>
              postJson(
                paused ? `${base}/incident/resume` : `${base}/incident/pause`
              )
            )
          }
        >
          {paused ? "Play" : "Pause"}
        </button>
        {speedControl}
        {muteControl}

        <div className="flex min-w-[160px] flex-1 basis-[220px] flex-col gap-1 px-1">
          <div className="flex items-center justify-between text-[11px] text-inkFaint">
            <span className="led text-inkMute">{fmt(position)}</span>
            <span className="truncate px-2">{status}</span>
            <span className="led text-inkMute">{fmt(duration)}</span>
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
            onMouseUp={() =>
              void run("seek", () =>
                postJson(`${base}/incident/seek`, { ms: seekMs })
              )
            }
            onTouchEnd={() =>
              void run("seek", () =>
                postJson(`${base}/incident/seek`, { ms: seekMs })
              )
            }
          />
        </div>

        <button
          type="button"
          disabled={busy || !leaseId}
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
        <button
          type="button"
          disabled={busy || !leaseId}
          className="pill-btn border border-line2 bg-raised/70 text-ink"
          onClick={() =>
            void run("restart", async () => {
              const res = await postJson(`${base}/incident/restart`, {
                lease_id: leaseId,
              });
              onRestartTransport();
              return res;
            })
          }
        >
          Restart
        </button>
        <a
          className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40"
          href={`${apiBase()}${base}/export`}
          target="_blank"
          rel="noreferrer"
        >
          Export
        </a>
      </div>

      {error && (
        <p className="max-w-sm text-[11px] text-danger sm:max-w-xs" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
