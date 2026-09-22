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
  audio,
}: {
  incidentId: string;
  status: IncidentStats["status"];
  clockMs: number;
  onReplay: () => void;
  onLeave?: () => void;
  sessionLabel?: string;
  sessionId?: string | null;
  audio?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-base/80 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-4 px-4 sm:px-6">
        {/* Wordmark — returns to portal when leave is available */}
        <button
          type="button"
          onClick={onLeave}
          disabled={!onLeave}
          className="flex items-center gap-2.5 text-left disabled:cursor-default"
          title={onLeave ? "Back to portal" : undefined}
        >
          <span className="relative flex h-7 w-7 items-center justify-center">
            <span className="absolute inset-0 rounded-full border border-accent/40" />
            <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-accent border-r-accent animate-spinSlow" />
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          </span>
          <span className="hidden sm:block">
            <span className="block text-[13px] font-semibold leading-none tracking-tight text-ink">
              Postmortem Witness
            </span>
            <span className="block text-[10px] leading-none tracking-[0.2em] text-inkFaint">
              LISTENER · v{CONTRACT_VERSION}
              {sessionLabel ? ` · ${sessionLabel}` : ""}
            </span>
          </span>
        </button>

        {/* Center nav — anchors to the four functional areas */}
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

        {/* Right cluster */}
        <div className="ml-auto flex items-center gap-2.5 md:ml-0">
          {audio}
          <ModelPicker sessionId={sessionId ?? null} />
          <StatusPill status={status} />
          <div className="hidden items-center gap-1.5 rounded-full border border-line bg-panel/60 px-3 py-1 sm:flex">
            <span className="text-[10px] uppercase tracking-[0.16em] text-inkFaint">
              Elapsed
            </span>
            <span className="led text-[13px] text-ink">{formatClock(clockMs)}</span>
          </div>
          <div className="hidden shrink-0 items-center rounded-full border border-line bg-panel/60 px-3 py-1 lg:flex">
            <span className="whitespace-nowrap text-[11px] font-medium tracking-wide text-inkMute">
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
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Replay
          </button>
        </div>
      </div>
    </header>
  );
}
