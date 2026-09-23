import type { ReactNode } from "react";
import type { IncidentStats } from "../state/selectors";
import { CONTRACT_VERSION } from "../contract";
import { formatClock } from "../lib/time";
import { StatusPill } from "./StatusPill";
import { ModelPicker } from "./ModelPicker";

const NAV = [
  { id: "board", label: "Hypotheses" },
  { id: "timeline", label: "Timeline" },
  { id: "threads", label: "Threads" },
  { id: "actions", label: "Actions" },
];

export function TopBar({
  incidentId,
  status,
  clockMs,
  onReplay,
  onLeave,
  sessionLabel,
  sessionId,
  latencyChip,
}: {
  incidentId: string;
  status: IncidentStats["status"];
  clockMs: number;
  onReplay: () => void;
  onLeave?: () => void;
  sessionLabel?: string;
  sessionId?: string | null;
  latencyChip?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-base/80 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-3 px-4 sm:gap-4 sm:px-6">
        <button
          type="button"
          onClick={onLeave}
          disabled={!onLeave}
          className="flex min-w-0 max-w-[42%] shrink items-center gap-2.5 text-left disabled:cursor-default sm:max-w-[280px]"
          title={onLeave ? "Back to portal" : undefined}
        >
          <span className="relative flex h-7 w-7 shrink-0 items-center justify-center">
            <span className="absolute inset-0 rounded-full border border-accent/40" />
            <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-accent border-r-accent motion-safe:animate-spinSlow" />
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold leading-none tracking-tight text-ink">
              Postmortem Witness
            </span>
            <span className="mt-0.5 block truncate text-[10px] leading-none tracking-[0.14em] text-inkFaint">
              v{CONTRACT_VERSION}
              {sessionLabel ? ` · ${sessionLabel}` : ""}
            </span>
          </span>
        </button>

        <nav className="mx-auto hidden items-center gap-1 rounded-full border border-line bg-panel/60 px-1.5 py-1 md:flex">
          {NAV.map((n) => (
            <a
              key={n.id}
              href={`#${n.id}`}
              className="rounded-full px-3 py-1 text-[12.5px] font-medium text-inkMute transition-colors hover:bg-raised/70 hover:text-ink"
            >
              {n.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2 md:ml-0">
          {latencyChip}
          <ModelPicker sessionId={sessionId ?? null} />
          <StatusPill status={status} />
          <div className="hidden items-center gap-1.5 rounded-full border border-line bg-panel/60 px-3 py-1 sm:flex">
            <span className="text-[10px] uppercase tracking-[0.16em] text-inkFaint">
              Elapsed
            </span>
            <span className="led text-[13px] text-ink">{formatClock(clockMs)}</span>
          </div>
          <div className="hidden max-w-[140px] shrink items-center rounded-full border border-line bg-panel/60 px-3 py-1 lg:flex">
            <span className="truncate text-[11px] font-medium tracking-wide text-inkMute">
              {incidentId}
            </span>
          </div>
          {onLeave && (
            <button
              type="button"
              onClick={onLeave}
              className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/50 hover:text-accentSoft"
              title="Return to portal"
            >
              Portal
            </button>
          )}
          <button
            onClick={onReplay}
            className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/50 hover:text-accentSoft"
            title="Restart the incident replay"
          >
            Replay
          </button>
        </div>
      </div>
    </header>
  );
}
