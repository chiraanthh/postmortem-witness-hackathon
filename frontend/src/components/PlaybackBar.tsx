import { useCallback, useEffect, useState } from "react";
import { apiBase, DEMO_FILE, postJson, sessionPath } from "../lib/api";

type Health = {
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
 * Demo playback controls for a live-pipeline session (requires session + lease).
 */
export function PlaybackBar({
  onRestartTransport,
  sessionId,
  leaseId,
  onPlaybackMeta,
}: {
  onRestartTransport: () => void;
  sessionId: string;
  leaseId: string | null;
  onPlaybackMeta?: (meta: Health) => void;
}) {
  const [health, setHealth] = useState<Health>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seekMs, setSeekMs] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(
        `${apiBase()}${sessionPath(sessionId, "/incident")}`
      );
      if (!res.ok) return;
      const data = (await res.json()) as Record<string, unknown>;
      const position =
        typeof data.playback_position_ms === "number"
          ? data.playback_position_ms
          : typeof data.clock_ms === "number"
            ? data.clock_ms
            : 0;
      const duration =
        typeof data.playback_duration_ms === "number"
          ? data.playback_duration_ms
          : 0;
      const status =
        typeof data.playback_status === "string"
          ? data.playback_status
          : undefined;
      const paused = Boolean(data.paused);
      const finished = status === "finished";
      const running =
        !finished &&
        status !== undefined &&
        status !== "idle" &&
        status !== "error";
      const next: Health = {
        status,
        running,
        finished,
        paused,
        playback_position_ms: position,
        playback_duration_ms: duration,
      };
      setHealth(next);
      setSeekMs(position);
      onPlaybackMeta?.(next);
    } catch {
      /* ignore poll errors */
    }
  }, [sessionId, onPlaybackMeta]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<Response>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `${label} failed (${res.status})`);
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const duration = health.playback_duration_ms ?? 0;
  const position = health.playback_position_ms ?? 0;
  const running = Boolean(health.running);
  const paused = Boolean(health.paused);
  const base = sessionPath(sessionId, "");

  return (
    <section className="panel flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:gap-4">
      <div className="flex flex-wrap items-center gap-2">
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
          {running ? "Join / running" : "Start demo"}
        </button>
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
          {paused ? "Resume" : "Pause"}
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

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-center justify-between text-[11px] text-inkFaint">
          <span className="led text-inkMute">{fmt(position)}</span>
          <span>{health.status ?? "idle"}</span>
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

      {error && (
        <p className="max-w-sm text-[11px] text-danger sm:max-w-xs" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
