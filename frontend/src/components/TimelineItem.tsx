import type { TimelineRow } from "../state/types";
import { SpeakerBadge } from "./SpeakerBadge";
import { TypeTag } from "./TypeTag";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

const CORRECT_WINDOW = 2800;

export function TimelineItem({ row, tick }: { row: TimelineRow; tick: number }) {
  const justCorrected =
    row.correctedAt != null && tick - row.correctedAt < CORRECT_WINDOW;
  const wasCorrected = row.amendedFrom != null;
  const lowConfidence = row.confidence < 0.65;

  return (
    <li className="relative animate-rowIn pl-6">
      {/* rail + node */}
      <span className="absolute left-[7px] top-1 bottom-0 w-px bg-line" />
      <span
        className={cx(
          "absolute left-1 top-[5px] h-2.5 w-2.5 rounded-full border-2 border-base",
          row.type === "status_change"
            ? "bg-accent"
            : row.type === "action"
            ? "bg-teal-400"
            : row.type === "hypothesis"
            ? "bg-open"
            : "bg-rose-400"
        )}
      />

      <div
        // Re-key on correctedAt so the one-shot highlight re-fires each amendment.
        key={row.correctedAt ?? "stable"}
        className={cx(
          "rounded-lg border px-3 py-2",
          justCorrected
            ? "animate-correctPulse border-accent/50"
            : "border-line bg-panel2/60"
        )}
      >
        <div className="flex items-center gap-2">
          <TypeTag type={row.type} />
          <SpeakerBadge label={row.speaker_label} name={row.speaker_name} size="sm" />
          {wasCorrected && (
            <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-accentSoft">
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {row.amendedFrom} → {row.speaker_label}
            </span>
          )}
          <span className="led ml-auto text-[11px] text-inkFaint">
            {formatClock(row.timestamp_ms)}
          </span>
        </div>

        <p className="mt-1.5 text-[13px] leading-snug text-ink">{row.summary}</p>

        {lowConfidence && (
          <span className="mt-1 inline-block text-[10px] text-inkFaint">
            low confidence · {(row.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
    </li>
  );
}
