import { useCallback, useEffect, useState } from "react";
import { CONTRACT_VERSION } from "../contract";
import {
  createLiveSession,
  fetchHealth,
  type Health,
  type LivePipelineStatus,
  startLiveIncident,
} from "../lib/api";

/**
 * Opening portal. Every visitor lands here. Live seats are capped;
 * recorded replay is always available and does not consume a seat.
 */
export function Portal({
  onEnterLive,
  onEnterReplay,
}: {
  onEnterLive: (sessionId: string, leaseId: string) => void;
  onEnterReplay: () => void;
}) {
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fullHint, setFullHint] = useState(false);

  const refresh = useCallback(async () => {
    const h = await fetchHealth();
    if (h) setHealth(h);
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const slots: LivePipelineStatus = health?.live_pipeline ?? {
    used: 0,
    cap: 2,
    available: 2,
  };
  const full = slots.available <= 0 || fullHint;
  const running = Boolean(
    health?.sessions?.some((s) => s.running) || health?.running
  );

  const runLive = async () => {
    setBusy(true);
    setError(null);
    setFullHint(false);
    try {
      const seat = await createLiveSession();
      if (!seat.ok) {
        setFullHint(true);
        setHealth((h) =>
          h
            ? { ...h, live_pipeline: seat.status }
            : { live_pipeline: seat.status }
        );
        return;
      }
      const res = await startLiveIncident(seat.session_id, seat.lease_id);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `start failed (${res.status})`);
      }
      onEnterLive(seat.session_id, seat.lease_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-base/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[720px] items-center gap-3 px-4 sm:px-6">
          <span className="relative flex h-7 w-7 items-center justify-center">
            <span className="absolute inset-0 rounded-full border border-accent/40" />
            <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-accent border-r-accent animate-spinSlow" />
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          </span>
          <span>
            <span className="block text-[13px] font-semibold leading-none tracking-tight text-ink">
              Postmortem Witness
            </span>
            <span className="block text-[10px] leading-none tracking-[0.2em] text-inkFaint">
              LISTENER · v{CONTRACT_VERSION}
            </span>
          </span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[720px] flex-1 flex-col justify-center gap-8 px-4 py-16 sm:px-6">
        <div className="flex flex-col gap-4">
          <p className="kicker">Incident bridge listener</p>
          <h1 className="text-[clamp(1.75rem,4vw,2.35rem)] font-semibold leading-[1.15] tracking-tight text-ink">
            Tracks which theories the room ruled out, and which questions
            nobody answered, while the call is still happening.
          </h1>
          <p className="max-w-xl text-[15px] leading-relaxed text-inkMute">
            Listener-only. Never speaks. Live seats are limited so judging
            does not burn the host; a recorded run of the live pipeline is
            always open (zero API seats).
          </p>
        </div>

        {full ? (
          <div className="flex flex-col gap-4 rounded-2xl border border-line bg-panel/80 p-5">
            <p className="text-[13px] font-medium text-ink">
              Live pipeline seats are full ({slots.used} of {slots.cap} in
              use).
            </p>
            <p className="text-[13px] text-inkMute">
              Try again in a few minutes, or watch the recorded run of the
              live pipeline now — same board, no live ASR seat required.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy}
                className="pill-btn border border-accent/40 bg-accent/15 text-accentSoft hover:border-accent"
                onClick={onEnterReplay}
              >
                Recorded run of the live pipeline
              </button>
              <button
                type="button"
                disabled={busy}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40"
                onClick={() => {
                  setFullHint(false);
                  void refresh();
                }}
              >
                Check again
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-[12px] text-inkFaint">
              Live pipeline: {slots.used} of {slots.cap} in use
              {running ? " · a session is running (you will join as viewer)" : ""}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy}
                className="pill-btn border border-accent/40 bg-accent/15 text-accentSoft hover:border-accent"
                onClick={() => void runLive()}
              >
                Run the live pipeline
              </button>
              <button
                type="button"
                disabled={busy}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40"
                onClick={onEnterReplay}
              >
                Recorded run of the live pipeline
              </button>
            </div>
          </div>
        )}

        {error && (
          <p className="text-[12px] text-danger" role="alert">
            {error}
          </p>
        )}

        <p className="text-[11px] text-inkFaint">
          Scripted demo · ~4.7 min · four synthesised speakers · contract v
          {CONTRACT_VERSION}
        </p>
      </main>
    </div>
  );
}
