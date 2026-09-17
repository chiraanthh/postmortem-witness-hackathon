import type { IncidentStats } from "../state/selectors";
import { cx } from "../lib/cx";

const STYLE: Record<IncidentStats["status"], { dot: string; text: string; ring: string }> = {
  STANDBY: { dot: "bg-inkFaint", text: "text-inkMute", ring: "border-line2" },
  INVESTIGATING: {
    dot: "bg-open animate-blink",
    text: "text-open",
    ring: "border-open/40",
  },
  IDENTIFIED: {
    dot: "bg-accent animate-blink",
    text: "text-accentSoft",
    ring: "border-accent/50",
  },
};

export function StatusPill({ status }: { status: IncidentStats["status"] }) {
  const s = STYLE[status];
  return (
    <span
      className={cx(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em]",
        s.ring,
        s.text
      )}
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", s.dot)} />
      {status}
    </span>
  );
}
