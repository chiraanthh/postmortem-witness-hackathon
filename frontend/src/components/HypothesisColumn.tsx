import type { HypothesisState } from "../contract";
import type { HypothesisNode } from "../state/types";
import { HypothesisCard } from "./HypothesisCard";
import { cx } from "../lib/cx";

const META: Record<
  HypothesisState,
  { title: string; dot: string; ring: string; head: string }
> = {
  open: {
    title: "Open",
    dot: "bg-open",
    ring: "border-open/25",
    head: "text-open",
  },
  ruled_out: {
    title: "Ruled out",
    dot: "bg-ruled",
    ring: "border-line2",
    head: "text-inkMute",
  },
  confirmed: {
    title: "Confirmed",
    dot: "bg-accent",
    ring: "border-accent/35",
    head: "text-accentSoft",
  },
};

export function HypothesisColumn({
  state,
  items,
  tick,
}: {
  state: HypothesisState;
  items: HypothesisNode[];
  tick: number;
}) {
  const m = META[state];
  return (
    <div
      className={cx(
        "flex min-h-[220px] flex-col rounded-xl border bg-base2/40 p-3",
        m.ring
      )}
    >
      <div className="mb-3 flex items-center justify-between px-0.5">
        <div className="flex items-center gap-2">
          <span className={cx("h-2 w-2 rounded-full", m.dot)} />
          <span
            className={cx(
              "text-[12px] font-semibold uppercase tracking-[0.14em]",
              m.head
            )}
          >
            {m.title}
          </span>
        </div>
        <span className="led text-[13px] text-inkFaint">
          {String(items.length).padStart(2, "0")}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-2.5">
        {items.length === 0 ? (
          <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-line/70 py-6 text-[11px] text-inkFaint">
            none yet
          </div>
        ) : (
          items.map((h) => (
            <HypothesisCard key={h.hypothesis_id} h={h} tick={tick} />
          ))
        )}
      </div>
    </div>
  );
}
