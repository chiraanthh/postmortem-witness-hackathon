import type { Thread } from "../contract";
import { formatClock } from "../lib/time";

function ThreadRow({ t }: { t: Thread }) {
  return (
    <li className="animate-rowIn rounded-lg border border-line bg-panel2/50 p-3">
      <div className="flex items-start gap-2.5">
        <span className="mt-1 h-full w-0.5 shrink-0 self-stretch rounded bg-rose-400/60" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] leading-snug text-ink">{t.text}</p>
          <div className="mt-2 flex items-center gap-2 text-[10.5px]">
            <span className="inline-flex items-center gap-1 rounded border border-rose-400/40 bg-rose-400/10 px-1.5 py-0.5 font-semibold uppercase tracking-wide text-rose-300">
              unanswered
            </span>
            <span className="text-inkFaint">
              {t.owner ? `owner ${t.owner}` : "no owner"}
            </span>
            <span className="led ml-auto text-inkFaint">
              {formatClock(t.opened_at_ms)}
            </span>
          </div>
        </div>
      </div>
    </li>
  );
}

export function OpenThreads({ threads }: { threads: Thread[] }) {
  return (
    <section id="threads" className="panel flex flex-col p-5">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-rose-400" />
          <h2 className="font-display text-[16px] font-semibold tracking-tight text-ink">
            Open threads
          </h2>
        </div>
        <span className="chip border-rose-400/30 text-rose-300">
          <span className="led text-rose-300">
            {String(threads.length).padStart(2, "0")}
          </span>
          dropped
        </span>
      </div>
      <p className="mb-3 text-[11.5px] text-inkFaint">
        Raised on the call. Nobody closed them.
      </p>

      {threads.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-[12px] text-inkFaint">
          no loose ends yet
        </div>
      ) : (
        <ul className="scroll-slim flex max-h-[520px] flex-col gap-2 overflow-y-auto pr-1">
          {threads.map((t) => (
            <ThreadRow key={t.thread_id} t={t} />
          ))}
        </ul>
      )}
    </section>
  );
}
