import type { HypothesisState } from "../contract";
import type { HypothesisNode } from "../state/types";
import { HypothesisColumn } from "./HypothesisColumn";
import { useFlip } from "../lib/useFlip";

const ORDER: HypothesisState[] = ["open", "ruled_out", "confirmed"];

export function HypothesisBoard({
  columns,
  tick,
}: {
  columns: Record<HypothesisState, HypothesisNode[]>;
  tick: number;
}) {
  // Signature changes whenever any card's column or the ordering changes, which
  // is exactly when the FLIP animation should run.
  const signature = ORDER.map(
    (s) => `${s}:${columns[s].map((h) => h.hypothesis_id).join(",")}`
  ).join("|");
  const boardRef = useFlip<HTMLDivElement>(signature);

  const total = ORDER.reduce((n, s) => n + columns[s].length, 0);

  return (
    <section id="board" className="panel p-5 sm:p-6">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-sm bg-accent" />
            <h2 className="font-display text-[19px] font-semibold tracking-tight text-ink">
              Hypothesis board
            </h2>
          </div>
          <p className="mt-1 text-[12.5px] text-inkMute">
            Every proposed cause, and where the room landed on it. Cards move
            live as theories are ruled out or confirmed.
          </p>
        </div>
        <span className="hidden shrink-0 items-center gap-1.5 rounded-full border border-line bg-panel2/70 px-3 py-1 text-[11px] text-inkMute sm:flex">
          <span className="led text-ink">{String(total).padStart(2, "0")}</span>
          theories tracked
        </span>
      </div>

      <div ref={boardRef} className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {ORDER.map((state) => (
          <HypothesisColumn
            key={state}
            state={state}
            items={columns[state]}
            tick={tick}
          />
        ))}
      </div>
    </section>
  );
}
