import { useEffect, useState } from "react";
import type { ReconciliationSummary } from "../contract";

const BEAT_MS = 4200;
const STAGGER_MS = 280;

/**
 * Visible reconciliation moment. Fired every time a reconciliation DiffOp
 * lands — including after seek — keyed by beatId so it can be re-watched.
 */
export function ReconciliationBeat({
  summary,
  beatAt,
  beatId,
  tick,
}: {
  summary: ReconciliationSummary | null;
  beatAt: number | null;
  beatId: number;
  tick: number;
}) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (beatId <= 0 || beatAt == null) return;
    setActive(true);
    const t = window.setTimeout(() => setActive(false), BEAT_MS);
    return () => window.clearTimeout(t);
  }, [beatId, beatAt]);

  if (!summary || beatAt == null || beatId <= 0) return null;

  const n = summary.speakers.length;
  const elapsed = tick - beatAt;
  const showing = active || elapsed < BEAT_MS;

  if (!showing) return null;

  return (
    <div
      key={beatId}
      className="pointer-events-none fixed inset-x-0 top-14 z-40 flex justify-center px-4"
      aria-live="polite"
    >
      <div className="mt-3 max-w-lg animate-rowIn rounded-xl border border-accent/35 bg-base/90 px-4 py-3 shadow-glow backdrop-blur-md">
        <p className="text-center text-[12px] font-semibold tracking-wide text-accentSoft">
          Speaker attributions reconciled
        </p>
        <p className="mt-1 text-center text-[13px] text-ink">
          {n === 0
            ? "No speaker labels moved."
            : `${n} attribution${n === 1 ? "" : "s"} corrected across the timeline.`}
        </p>
        {n > 0 && (
          <ul className="mt-2 flex flex-wrap justify-center gap-1.5">
            {summary.speakers.slice(0, 8).map((sc, i) => {
              const revealAt = i * STAGGER_MS;
              const visible = elapsed >= revealAt;
              return (
                <li
                  key={`${sc.event_id}-${i}`}
                  className="rounded border border-line bg-panel/80 px-2 py-0.5 text-[11px] text-inkMute transition-opacity duration-300"
                  style={{ opacity: visible ? 1 : 0.15 }}
                >
                  <span className="text-inkFaint">{sc.previous_speaker_label}</span>
                  <span className="mx-1 text-accentSoft">→</span>
                  <span className="text-ink">{sc.speaker_label}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Desaturate the board briefly while reconciliation plays. */
export function useReconciliationDim(
  beatAt: number | null,
  beatId: number,
  tick: number
): boolean {
  if (beatId <= 0 || beatAt == null) return false;
  return tick - beatAt < BEAT_MS;
}
