import type { SilenceSummary, Thread } from "../contract";
import { formatClock } from "../lib/time";

/** Live age while open; frozen unanswered_age_ms after resolution. */
function threadAgeMs(t: Thread, clockMs: number): number {
  if (t.unanswered_age_ms != null) return t.unanswered_age_ms;
  if (t.answered) return Math.max(0, (t.answered_at_ms ?? clockMs) - t.asked_at_ms);
  return Math.max(0, clockMs - t.asked_at_ms);
}

function ThreadRow({ t, clockMs }: { t: Thread; clockMs: number }) {
  const ageMs = threadAgeMs(t, clockMs);
  return (
    <li className="animate-rowIn rounded-lg border border-line bg-panel2/50 p-3">
      <div className="flex items-start gap-2.5">
        <span className="mt-1 h-full w-0.5 shrink-0 self-stretch rounded bg-rose-400/60" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] leading-snug text-ink">{t.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10.5px]">
            <span className="inline-flex items-center gap-1 rounded border border-rose-400/40 bg-rose-400/10 px-1.5 py-0.5 font-semibold uppercase tracking-wide text-rose-300">
              unanswered
            </span>
            <span className="text-inkFaint">
              {t.addressee
                ? `asked ${t.addressee}`
                : t.owner
                  ? `owner ${t.owner}`
                  : "no owner"}
            </span>
            <span className="led ml-auto text-rose-300/90" title="open for">
              open {formatClock(ageMs)}
            </span>
            <span className="led text-inkFaint">{formatClock(t.asked_at_ms)}</span>
          </div>
        </div>
      </div>
    </li>
  );
}

export function OpenThreads({
  threads,
  clockMs,
  silence,
  resolved,
}: {
  threads: Thread[];
  clockMs: number;
  silence: SilenceSummary | null;
  resolved: boolean;
}) {
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
          open
        </span>
      </div>
      <p className="mb-3 text-[11.5px] text-inkFaint">
        Questions still open on the call. Ages tick until someone answers or
        the incident resolves.
      </p>

      {threads.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-[12px] text-inkFaint">
          no loose ends yet
        </div>
      ) : (
        <ul className="scroll-slim flex max-h-[520px] flex-col gap-2 overflow-y-auto pr-1">
          {threads.map((t) => (
            <ThreadRow key={t.thread_id} t={t} clockMs={clockMs} />
          ))}
        </ul>
      )}

      {resolved && silence && (
        <div className="mt-4 rounded-lg border border-line/80 bg-panel2/40 px-3 py-2.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-inkMute">
            At resolution
          </p>
          <p className="mt-1 text-[12.5px] leading-snug text-ink">
            {silence.questions_unanswered} of {silence.questions_asked}{" "}
            question{silence.questions_asked === 1 ? "" : "s"} still open
            {silence.longest_unanswered_ms > 0
              ? ` · longest ${formatClock(silence.longest_unanswered_ms)}`
              : ""}
            .
          </p>
          {silence.unanswered_addressees.length > 0 && (
            <p className="mt-1 text-[11.5px] text-inkFaint">
              Never answered: {silence.unanswered_addressees.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
