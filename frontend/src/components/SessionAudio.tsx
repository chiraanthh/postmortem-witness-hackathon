import { useCallback, useEffect, useRef, useState } from "react";

const DRIFT_MS = 250;

function muteKey(sessionKey: string): string {
  return `pw-mute:${sessionKey}`;
}

/**
 * Audible playback of the same WAV the pipeline consumes.
 * Reports currentTime as the single UI clock via onClockMs.
 * Server/replay target is used only to correct drift > 250ms.
 */
export function SessionAudio({
  src,
  sessionKey,
  getTargetMs,
  paused,
  playbackRate = 1,
  armed,
  onClockMs,
}: {
  src: string | null;
  sessionKey: string;
  getTargetMs: () => number | null;
  paused: boolean;
  playbackRate?: number;
  /** Set true on the click that starts the session — never on page load. */
  armed: boolean;
  onClockMs?: (ms: number) => void;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [muted, setMuted] = useState(() => {
    try {
      return sessionStorage.getItem(muteKey(sessionKey)) === "1";
    } catch {
      return false;
    }
  });
  const [ready, setReady] = useState(false);
  const onClockRef = useRef(onClockMs);
  onClockRef.current = onClockMs;

  useEffect(() => {
    try {
      sessionStorage.setItem(muteKey(sessionKey), muted ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [muted, sessionKey]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el || !src) return;
    el.src = src;
    el.load();
    setReady(false);
    const onCanPlay = () => setReady(true);
    el.addEventListener("canplay", onCanPlay);
    return () => el.removeEventListener("canplay", onCanPlay);
  }, [src]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    el.muted = muted;
    el.playbackRate = playbackRate > 0 ? playbackRate : 1;
  }, [muted, playbackRate]);

  // Arm play on user gesture path.
  useEffect(() => {
    const el = audioRef.current;
    if (!el || !armed || !src || !ready) return;
    if (paused) {
      el.pause();
      return;
    }
    const target = getTargetMs();
    if (target != null && Number.isFinite(target)) {
      const want = Math.max(0, target / 1000);
      if (Math.abs(el.currentTime - want) * 1000 > DRIFT_MS) {
        try {
          el.currentTime = want;
        } catch {
          /* ignore seek before metadata */
        }
      }
    }
    void el.play().catch(() => {
      /* autoplay policy — user must click mute/unmute or start again */
    });
  }, [armed, paused, ready, src, getTargetMs]);

  // Continuous drift correction + clock report while playing.
  useEffect(() => {
    if (!armed) return;
    const id = window.setInterval(() => {
      const el = audioRef.current;
      if (!el) return;
      const ms = Math.round(el.currentTime * 1000);
      onClockRef.current?.(ms);
      if (paused || el.paused) return;
      const target = getTargetMs();
      if (target == null || !Number.isFinite(target)) return;
      const want = Math.max(0, target / 1000);
      if (Math.abs(el.currentTime - want) * 1000 > DRIFT_MS) {
        try {
          el.currentTime = want;
        } catch {
          /* ignore */
        }
      }
    }, 200);
    return () => window.clearInterval(id);
  }, [armed, paused, getTargetMs]);

  const toggleMute = useCallback(() => {
    setMuted((m) => !m);
  }, []);

  if (!src) return null;

  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <audio ref={audioRef} preload="auto" playsInline />
      <button
        type="button"
        onClick={toggleMute}
        className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/50"
        title={muted ? "Unmute" : "Mute"}
        aria-label={muted ? "Unmute" : "Mute"}
      >
        {muted ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M11 5 6 9H2v6h4l5 4V5zM23 9l-6 6M17 9l6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M11 5 6 9H2v6h4l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M19 5a9 9 0 0 1 0 14"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
    </div>
  );
}
