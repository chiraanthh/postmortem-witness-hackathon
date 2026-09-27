import { useCallback, useEffect, useRef, useState } from "react";
import { CONTRACT_VERSION } from "../contract";
import {
  BadKeyError,
  claimLiveQueue,
  createLiveSession,
  fetchHealth,
  heartbeatLiveQueue,
  joinLiveQueue,
  leaveLiveQueue,
  startLiveIncident,
  type ByokKeys,
  type Health,
  type LivePipelineStatus,
} from "../lib/api";

const NO_KEY_HINT =
  "Recorded run needs no key. Live pipeline runs on your own AssemblyAI and Anthropic keys.";

/**
 * Opening portal. Every visitor lands here. Live seats are capped;
 * recorded replay is always available, needs no key, and does not consume
 * a seat — it stays the primary action here.
 *
 * Live pipeline and upload are bring-your-own-key: the visitor's own
 * AssemblyAI + Anthropic credentials, held in this component's state only
 * (never localStorage/sessionStorage) and passed up so App can carry them
 * into the upload flow too. Validated server-side with one cheap call each
 * on submit; never sent back down after that.
 */
export function Portal({
  assemblyaiKey,
  anthropicKey,
  onAssemblyaiKeyChange,
  onAnthropicKeyChange,
  onEnterLive,
  onEnterReplay,
  onEnterUpload,
}: {
  assemblyaiKey: string;
  anthropicKey: string;
  onAssemblyaiKeyChange: (key: string) => void;
  onAnthropicKeyChange: (key: string) => void;
  onEnterLive: (sessionId: string, leaseId: string) => void;
  onEnterReplay: () => void;
  onEnterUpload: () => void;
}) {
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fullHint, setFullHint] = useState(false);
  const [ticketId, setTicketId] = useState<string | null>(null);
  const [queuePos, setQueuePos] = useState<number | null>(null);
  const [badKeyWhich, setBadKeyWhich] = useState<"assemblyai" | "anthropic" | null>(
    null
  );
  const claimingRef = useRef(false);

  const applyError = (err: unknown) => {
    setBadKeyWhich(err instanceof BadKeyError ? err.which : null);
    setError(err instanceof Error ? err.message : String(err));
  };

  const keys: ByokKeys = {
    assemblyaiKey: assemblyaiKey.trim(),
    anthropicKey: anthropicKey.trim(),
  };
  const hasKeys = Boolean(keys.assemblyaiKey && keys.anthropicKey);

  const keysRef = useRef(keys);
  keysRef.current = keys;

  const refresh = useCallback(async () => {
    const h = await fetchHealth();
    if (h) setHealth(h);
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(id);
  }, [refresh]);

  // Wait-queue heartbeat + auto-claim when a seat is reserved.
  useEffect(() => {
    if (!ticketId) return;
    let cancelled = false;
    const tick = async () => {
      const st = await heartbeatLiveQueue(ticketId);
      if (cancelled) return;
      if (st == null) {
        setTicketId(null);
        setQueuePos(null);
        setError("Wait-queue ticket expired — join again if seats are still full.");
        return;
      }
      setQueuePos(st.position > 0 ? st.position : 1);
      if (st.ready && !claimingRef.current) {
        claimingRef.current = true;
        try {
          const seat = await claimLiveQueue(ticketId, keysRef.current);
          if (cancelled) return;
          setTicketId(null);
          setQueuePos(null);
          const res = await startLiveIncident(seat.session_id, seat.lease_id);
          if (!res.ok) {
            const text = await res.text();
            throw new Error(text || `start failed (${res.status})`);
          }
          onEnterLive(seat.session_id, seat.lease_id);
        } catch (err) {
          applyError(err);
          setTicketId(null);
          setQueuePos(null);
        } finally {
          claimingRef.current = false;
        }
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ticketId, onEnterLive]);

  // Leave queue only on portal unmount, not when ticket clears after claim.
  const ticketRef = useRef<string | null>(null);
  ticketRef.current = ticketId;
  useEffect(() => {
    return () => {
      const tid = ticketRef.current;
      if (tid) void leaveLiveQueue(tid);
    };
  }, []);

  const slots: LivePipelineStatus = health?.live_pipeline ?? {
    used: 0,
    cap: 2,
    available: 2,
    queue_depth: 0,
  };
  const full = slots.available <= 0 || fullHint;
  const running = Boolean(
    health?.sessions?.some((s) => s.running) || health?.running
  );

  const runLive = async () => {
    if (!hasKeys) return;
    setBusy(true);
    setError(null);
    setBadKeyWhich(null);
    setFullHint(false);
    try {
      const seat = await createLiveSession(keys);
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
      applyError(err);
    } finally {
      setBusy(false);
    }
  };

  const joinQueue = async () => {
    if (!hasKeys) return;
    setBusy(true);
    setError(null);
    setBadKeyWhich(null);
    try {
      const q = await joinLiveQueue();
      setTicketId(q.ticket_id);
      setQueuePos(q.position);
      setHealth((h) =>
        h ? { ...h, live_pipeline: q.status } : { live_pipeline: q.status }
      );
      if (q.ready && q.lease_id) {
        // Effect will claim on next heartbeat tick; nudge immediately.
        claimingRef.current = false;
      }
    } catch (err) {
      applyError(err);
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

        <div className="flex flex-col gap-3 rounded-2xl border border-line bg-panel/60 p-4">
          <p className="text-[12px] font-medium text-ink">
            Bring your own key for the live pipeline
          </p>
          <p className="text-[12px] leading-relaxed text-inkFaint">
            Held in memory for this session only — never written to disk,
            never logged, and never sent back to your browser once
            submitted. Discarded the moment this session ends or its seat
            expires. Recorded run below needs none of this.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-[11px] text-inkMute">
              AssemblyAI API key
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={assemblyaiKey}
                onChange={(e) => onAssemblyaiKeyChange(e.target.value)}
                placeholder="paste your key"
                className={`rounded-lg border bg-raised/60 px-3 py-2 text-[13px] text-ink outline-none focus:border-accent/60 ${
                  badKeyWhich === "assemblyai" ? "border-danger/60" : "border-line2"
                }`}
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-inkMute">
              Anthropic API key
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={anthropicKey}
                onChange={(e) => onAnthropicKeyChange(e.target.value)}
                placeholder="paste your key"
                className={`rounded-lg border bg-raised/60 px-3 py-2 text-[13px] text-ink outline-none focus:border-accent/60 ${
                  badKeyWhich === "anthropic" ? "border-danger/60" : "border-line2"
                }`}
              />
            </label>
          </div>
        </div>

        {ticketId ? (
          <div className="flex flex-col gap-4 rounded-2xl border border-line bg-panel/80 p-5">
            <p className="text-[13px] font-medium text-ink">
              You are #{queuePos ?? "…"} in the live-seat wait queue
              {typeof slots.queue_depth === "number"
                ? ` (${slots.queue_depth} waiting)`
                : ""}
              .
            </p>
            <p className="text-[13px] text-inkMute">
              Keep this tab open. When a seat frees (run ends, leave, or 45s
              idle), you will be started automatically. Or watch the recorded
              run now — it does not use a live seat.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="pill-btn border border-accent/40 bg-accent/15 text-accentSoft hover:border-accent"
                onClick={onEnterReplay}
              >
                Recorded run of the live pipeline
              </button>
              <button
                type="button"
                disabled={busy}
                className="pill-btn border border-line2 bg-raised/70 text-ink"
                onClick={() => {
                  if (ticketId) void leaveLiveQueue(ticketId);
                  setTicketId(null);
                  setQueuePos(null);
                }}
              >
                Leave queue
              </button>
            </div>
          </div>
        ) : full ? (
          <div className="flex flex-col gap-4 rounded-2xl border border-line bg-panel/80 p-5">
            <p className="text-[13px] font-medium text-ink">
              Live pipeline seats are full ({slots.used} of {slots.cap} in
              use).
            </p>
            <p className="text-[13px] text-inkMute">
              Join the wait queue, try again in a few minutes, or watch the
              recorded run of the live pipeline now — same board, no live ASR
              seat required.
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
                disabled={busy || !hasKeys}
                title={hasKeys ? undefined : NO_KEY_HINT}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40 disabled:opacity-50"
                onClick={() => void joinQueue()}
              >
                Join wait queue
              </button>
              <button
                type="button"
                disabled={busy || !hasKeys}
                title={hasKeys ? undefined : NO_KEY_HINT}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40 disabled:opacity-50"
                onClick={onEnterUpload}
              >
                Upload your own call
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
            {!hasKeys && (
              <p className="text-[11px] text-inkFaint">{NO_KEY_HINT}</p>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-[12px] text-inkFaint">
              Live pipeline: {slots.used} of {slots.cap} in use
              {typeof slots.queue_depth === "number" && slots.queue_depth > 0
                ? ` · ${slots.queue_depth} waiting`
                : ""}
              {running ? " · a session is running (you will join as viewer)" : ""}
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
                disabled={busy || !hasKeys}
                title={hasKeys ? undefined : NO_KEY_HINT}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40 disabled:opacity-50"
                onClick={() => void runLive()}
              >
                Run the live pipeline
              </button>
              <button
                type="button"
                disabled={busy || !hasKeys}
                title={hasKeys ? undefined : NO_KEY_HINT}
                className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/40 disabled:opacity-50"
                onClick={onEnterUpload}
              >
                Upload your own call
              </button>
            </div>
            {!hasKeys && (
              <p className="text-[11px] text-inkFaint">{NO_KEY_HINT}</p>
            )}
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
