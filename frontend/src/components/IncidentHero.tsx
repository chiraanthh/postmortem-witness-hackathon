import type { IncidentStats } from "../state/selectors";
import { formatClock } from "../lib/time";
import { StatusPill } from "./StatusPill";
import { cx } from "../lib/cx";

function MetricCard({
  label,
  value,
  sub,
  danger,
  accent,
}: {
  label: string;
  value: number | string;
  sub?: string;
  danger?: boolean;
  accent?: boolean;
}) {
  return (
    <div
      className={cx(
        "panel-tight flex flex-col justify-between p-3.5",
        danger && "border-danger/40 bg-danger/5",
        accent && "border-accent/40"
      )}
    >
      <span className="kicker">{label}</span>
      <div className="mt-3 flex items-baseline gap-2">
        <span
          className={cx(
            "led text-3xl leading-none",
            danger ? "text-danger" : accent ? "text-accentSoft" : "text-ink"
          )}
        >
          {value}
        </span>
        {sub && <span className="text-[11px] font-medium text-inkMute">{sub}</span>}
      </div>
    </div>
  );
}

export function IncidentHero({
  incidentId,
  stats,
  clockMs,
  speakers,
  ingested,
  confirmedCause,
}: {
  incidentId: string;
  stats: IncidentStats;
  clockMs: number;
  speakers: number;
  ingested: number;
  confirmedCause: string | null;
}) {
  const headline = confirmedCause ?? "Root cause under investigation";

  return (
    <section id="top" className="grid grid-cols-1 gap-5 lg:grid-cols-12">
      {/* Headline block */}
      <div className="lg:col-span-8">
        <div className="flex items-center gap-3">
          <span className="kicker text-accentSoft">Live incident</span>
          <span className="h-px flex-1 bg-line" />
          <span className="text-[11px] tracking-wide text-inkFaint">
            listening to the bridge
          </span>
        </div>

        <h1 className="mt-4 font-display text-[40px] font-semibold leading-[1.04] tracking-tight text-ink sm:text-[52px]">
          {confirmedCause ? (
            <>
              <span className="text-inkMute">Confirmed cause — </span>
              <span className="text-accentSoft">{headline}</span>
            </>
          ) : (
            <>
              Root cause
              <br />
              <span className="text-inkMute">under investigation</span>
            </>
          )}
        </h1>

        <p className="mt-4 max-w-xl text-[14px] leading-relaxed text-inkMute">
          Nobody is typing into this dashboard. As four engineers talk through
          the outage, utterances are transcribed, structured, and assembled into
          a live incident record — hypotheses, open questions, and actions.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-2.5">
          <StatusPill status={stats.status} />
          <span className="chip">
            <span className="text-inkFaint">ID</span>
            <span className="text-ink">{incidentId}</span>
          </span>
          <span className="chip">
            <span className="text-inkFaint">Elapsed</span>
            <span className="led text-ink">{formatClock(clockMs)}</span>
          </span>
          <span className="chip">
            <span className="text-ink">{speakers}</span>
            <span className="text-inkFaint">on call</span>
          </span>
          <span className="chip">
            <span className="text-ink">{ingested}</span>
            <span className="text-inkFaint">turns processed</span>
          </span>
        </div>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-3 lg:col-span-4 lg:grid-cols-1">
        <MetricCard
          label="Hypotheses"
          value={stats.hypotheses}
          sub={`${stats.confirmed} confirmed · ${stats.open} open`}
          accent={stats.confirmed > 0}
        />
        <MetricCard
          label="Open threads"
          value={stats.openThreads}
          sub="unanswered"
        />
        <MetricCard
          label="Actions"
          value={stats.actions}
          sub={
            stats.unownedActions > 0
              ? `${stats.unownedActions} unowned`
              : "all owned"
          }
          danger={stats.unownedActions > 0}
        />
      </div>
    </section>
  );
}
