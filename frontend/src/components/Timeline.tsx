import { useEffect, useRef } from "react";
import type { TimelineRow } from "../state/types";
import { TimelineItem } from "./TimelineItem";

export function Timeline({
  rows,
  tick,
}: {
  rows: TimelineRow[];
  tick: number;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const count = rows.length;

  // Auto-scroll toward the newest item at the bottom as the call unfolds.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [count]);

  return (
    <section id="timeline" className="panel flex min-h-0 flex-col p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-rose-400" />
          <h2 className="font-display text-[16px] font-semibold tracking-tight text-ink">
            Timeline
          </h2>
        </div>
        <span className="chip">
          <span className="led text-ink">{String(count).padStart(2, "0")}</span>
          utterances
        </span>
      </div>

      <div
        ref={scrollRef}
        className="scroll-slim -mr-2 max-h-[520px] flex-1 overflow-y-auto pr-2"
      >
        {count === 0 ? (
          <div className="flex h-40 items-center justify-center text-[12px] text-inkFaint">
            waiting for the first meaningful utterance…
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((row) => (
              <TimelineItem key={row.turn_order} row={row} tick={tick} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
