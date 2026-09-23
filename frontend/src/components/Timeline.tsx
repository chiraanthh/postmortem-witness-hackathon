import { memo, useEffect, useRef } from "react";
import type { TimelineTurn } from "../state/selectors";
import { TimelineTurnRow } from "./TimelineTurnRow";

export const Timeline = memo(function Timeline({
  turns,
  tick,
  reconBeatAt,
  partialCaption,
}: {
  turns: TimelineTurn[];
  tick: number;
  reconBeatAt?: number | null;
  partialCaption?: {
    text: string;
    speaker_label: string;
  } | null;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const count = turns.length;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [count, partialCaption?.text]);

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
          turns
        </span>
      </div>

      <div
        ref={scrollRef}
        className="scroll-slim -mr-2 max-h-[520px] flex-1 overflow-x-clip overflow-y-auto pr-2 pl-1"
      >
        {count === 0 && !partialCaption ? (
          <div className="flex h-40 items-center justify-center text-[12px] text-inkFaint">
            waiting for the first meaningful utterance…
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {turns.map((turn) => (
              <TimelineTurnRow
                key={turn.key}
                turn={turn}
                tick={tick}
                reconBeatAt={reconBeatAt ?? null}
              />
            ))}
            {partialCaption && partialCaption.text.trim() && (
              <li className="relative pl-7 opacity-70">
                <span className="absolute left-[11px] top-1 bottom-0 w-px bg-line" />
                <span className="absolute left-2 top-[5px] h-2.5 w-2.5 rounded-full border-2 border-base bg-inkFaint motion-safe:animate-blink" />
                <div className="rounded-lg border border-dashed border-line px-3 py-2">
                  <p className="text-[11px] uppercase tracking-wide text-inkFaint">
                    Live · speaker {partialCaption.speaker_label}
                  </p>
                  <p className="mt-1 text-[13px] leading-snug text-inkMute">
                    {partialCaption.text}
                  </p>
                </div>
              </li>
            )}
          </ul>
        )}
      </div>
    </section>
  );
});
