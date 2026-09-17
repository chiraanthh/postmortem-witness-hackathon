import type { IncidentStats } from "../state/selectors";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

function LedStat({
  value,
  label,
  tone = "ink",
}: {
  value: string;
  label: string;
  tone?: "ink" | "accent" | "muted" | "danger";
}) {
  const color =
    tone === "accent"
      ? "text-accentSoft"
      : tone === "muted"
      ? "text-inkMute"
      : tone === "danger"
      ? "text-danger"
      : "text-ink";
  return (
    <div className="flex min-w-0 flex-col">
      <span className={cx("led text-4xl leading-none sm:text-5xl", color)}>
        {value}
      </span>
      <span className="mt-3 h-px w-full bg-line2" />
      <span className="mt-2 flex items-center gap-2 text-[11px] font-medium tracking-wide text-inkMute">
        <span
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            tone === "danger" ? "bg-danger" : "bg-accent"
          )}
        />
        {label}
      </span>
    </div>
  );
}

export function StatStrip({
  stats,
  clockMs,
}: {
  stats: IncidentStats;
  clockMs: number;
}) {
  return (
    <section className="panel relative overflow-hidden p-6 sm:p-8">
      {/* warm corner glow, matching the reference panel */}
      <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-accent/10 blur-3xl" />

      <div className="relative grid grid-cols-1 gap-8 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <div className="flex items-center gap-2 text-inkFaint">
            <span className="h-2 w-2 rounded-full bg-accent" />
            <span className="text-[11px] uppercase tracking-[0.2em]">
              Incident signal
            </span>
          </div>
          <p className="mt-4 font-display text-[22px] leading-snug tracking-tight text-ink sm:text-[26px]">
            Postmortem Witness is{" "}
            <span className="text-accentSoft">listening to the bridge</span> and
            assembling the record{" "}
            <span className="text-inkMute">as the outage unfolds.</span>
          </p>
        </div>

        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4 lg:col-span-7">
          <LedStat
            value={String(stats.confirmed).padStart(2, "0")}
            label="Confirmed"
            tone="accent"
          />
          <LedStat
            value={String(stats.ruledOut).padStart(2, "0")}
            label="Ruled out"
            tone="muted"
          />
          <LedStat
            value={String(stats.open).padStart(2, "0")}
            label="Still open"
          />
          <LedStat value={formatClock(clockMs)} label="Incident clock" />
        </div>
      </div>
    </section>
  );
}
