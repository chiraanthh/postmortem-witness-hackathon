import type { EventType } from "../contract";
import { cx } from "../lib/cx";

const MAP: Record<string, { label: string; cls: string }> = {
  action: { label: "ACTION", cls: "text-teal-300 border-teal-400/30 bg-teal-400/5" },
  hypothesis: { label: "HYPOTHESIS", cls: "text-open border-open/30 bg-open/5" },
  status_change: { label: "STATUS", cls: "text-accentSoft border-accent/30 bg-accent/5" },
  thread: { label: "THREAD", cls: "text-rose-300 border-rose-400/30 bg-rose-400/5" },
  resolution: {
    label: "RESOLVED",
    cls: "text-teal-300 border-teal-400/30 bg-teal-400/5",
  },
  noise: { label: "NOISE", cls: "text-inkFaint border-line2 bg-raised/40" },
  speaker_amended: { label: "CORRECTION", cls: "text-accentSoft border-accent/40 bg-accent/10" },
};

export function TypeTag({ type }: { type: EventType }) {
  const t = MAP[type] ?? MAP.noise;
  return (
    <span
      className={cx(
        "rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em]",
        t.cls
      )}
    >
      {t.label}
    </span>
  );
}
