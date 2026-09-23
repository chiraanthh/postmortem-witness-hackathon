import { memo } from "react";
import type { TimelineTurn } from "../state/selectors";
import { SpeakerBadge } from "./SpeakerBadge";
import { TypeTag } from "./TypeTag";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

const CORRECT_WINDOW = 4200;

export const TimelineTurnRow = memo(function TimelineTurnRow({
  turn,
  tick,
  reconBeatAt = null,
}: {
  turn: TimelineTurn;
  tick: number;
  reconBeatAt?: number | null;
}) {
  const justCorrected =
    turn.correctedAt != null &&
    tick - turn.correctedAt < CORRECT_WINDOW &&
    (reconBeatAt == null || Math.abs(turn.correctedAt - reconBeatAt) < 50);
  const wasCorrected = turn.amendedFrom != null;

  return (
    <li className="relative motion-safe:animate-rowIn pl-7">
      <span className="absolute left-[11px] top-1 bottom-0 w-px bg-line" />
      <span className="absolute left-2 top-[5px] h-2.5 w-2.5 rounded-full border-2 border-base bg-rose-400" />

      <div
        className={cx(
          "rounded-lg border px-3 py-2",
          justCorrected
            ? "motion-safe:animate-correctPulse border-accent/50"
            : "border-line bg-panel2/60"
        )}
      >
        <div className="flex items-center gap-2">
          <SpeakerBadge
            label={turn.speaker_label}
            name={turn.speaker_name}
            size="sm"
          />
          {wasCorrected && (
            <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-accentSoft">
              {turn.amendedFrom} → {turn.speaker_label}
            </span>
          )}
          <span className="led ml-auto text-[11px] text-inkFaint">
            {formatClock(turn.timestamp_ms)}
          </span>
        </div>

        <p className="mt-1.5 text-[13px] leading-snug text-ink">{turn.text}</p>

        {turn.events.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1 border-t border-line/60 pt-2">
            {turn.events.map((ev) => (
              <li
                key={ev.event_id}
                className="flex flex-wrap items-center gap-2 text-[12px] text-inkMute"
              >
                <TypeTag type={ev.type} />
                <span className="text-ink">{ev.summary}</span>
                {ev.confidence < 0.65 && (
                  <span className="text-[10px] text-inkFaint">
                    {(ev.confidence * 100).toFixed(0)}%
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
});
