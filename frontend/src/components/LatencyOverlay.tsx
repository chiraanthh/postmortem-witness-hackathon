import type { Latency } from "../contract";
import { ms } from "../lib/time";
import { cx } from "../lib/cx";

function Row({
  label,
  value,
  warn,
}: {
  label: string;
  value: number;
  warn?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-[10px] uppercase tracking-[0.16em] text-inkFaint">
        {label}
      </span>
      <span className="flex items-baseline gap-1">
        <span
          className={cx("led text-[15px]", warn ? "text-open" : "text-accentSoft")}
        >
          {value > 0 ? ms(value) : "—"}
        </span>
        <span className="text-[9px] text-inkFaint">ms</span>
      </span>
    </div>
  );
}

/**
 * Always-visible latency readout. Values come straight from the reducer's
 * latency state. A demo feature for the sponsor — small, legible, out of the
 * way, but never decorative.
 */
export function LatencyOverlay({ latency }: { latency: Latency }) {
  return (
    <div className="fixed bottom-4 right-4 z-40 w-[188px] select-none">
      <div className="rounded-xl border border-line2 bg-base/90 p-3 shadow-panel backdrop-blur-md">
        <div className="mb-2 flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 animate-blink rounded-full bg-accent" />
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-inkMute">
            Live latency · p50
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <Row label="ASR" value={latency.asr_ms} />
          <Row label="Extract" value={latency.extract_ms} warn />
          <Row label="E2E" value={latency.e2e_ms} />
        </div>
      </div>
    </div>
  );
}
