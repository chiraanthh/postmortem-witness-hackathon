import { memo } from "react";
import type { HypothesisState } from "../contract";
import type { HypothesisNode } from "../state/types";
import { SpeakerBadge } from "./SpeakerBadge";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

const STATE_LABEL: Record<HypothesisState, string> = {
  open: "OPEN",
  ruled_out: "RULED OUT",
  confirmed: "CONFIRMED",
};

const MOVE_WINDOW = 1600;
const CORRECT_WINDOW = 2800;

export const HypothesisCard = memo(function HypothesisCard({
  h,
  tick,
}: {
  h: HypothesisNode;
  tick: number;
}) {
  const justMoved = h.movedAt != null && tick - h.movedAt < MOVE_WINDOW;
  const justCorrected =
    h.correctedAt != null && tick - h.correctedAt < CORRECT_WINDOW;

  const ruled = h.state === "ruled_out";
  const confirmed = h.state === "confirmed";

  return (
    <article
      data-flip-id={h.hypothesis_id}
      className={cx(
        "group relative rounded-xl border bg-panel2/90 p-3.5",
        confirmed && "border-accent/55 shadow-glow",
        ruled && "border-ruled/40 opacity-95",
        !confirmed && !ruled && "border-open/35",
        justMoved && "motion-safe:animate-moveGlow"
      )}
    >
      {justMoved && h.prevState && h.prevState !== h.state && (
        <div className="absolute -top-2.5 left-3 rounded-full border border-accent/50 bg-base px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-accentSoft">
          {STATE_LABEL[h.prevState]} → {STATE_LABEL[h.state]}
        </div>
      )}

      <p
        className={cx(
          "text-[13.5px] font-medium leading-snug",
          ruled ? "text-inkMute line-through decoration-line2" : "text-ink"
        )}
      >
        {h.text}
      </p>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-inkFaint">
            raised by
          </span>
          <SpeakerBadge label={h.raised_by_label} name={null} size="sm" />
          {h.implicit && (
            <span className="text-[9px] font-bold uppercase tracking-wide text-inkFaint">
              implicit
            </span>
          )}
          {justCorrected && (
            <span className="motion-safe:animate-blink text-[9px] font-bold uppercase tracking-wide text-accentSoft">
              reattributed
            </span>
          )}
        </div>
        <span className="led text-[11px] text-inkFaint">
          {formatClock(h.raised_at_ms)}
        </span>
      </div>
    </article>
  );
});
