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
 * Always-visible latency readout. In live mode these are the running
 * pipeline p50s. In replay they are the values captured in the fixture —
 * never presented as live.
 */
export function LatencyOverlay({
  latency,
  source,
}: {
  latency: Latency;
  /** live = current pipeline; recorded = fixture capture values */
  source: "live" | "recorded";
}) {
  return (
    <div className="fixed bottom-4 right-4 z-40 w-[188px] select-none">
      <div className="rounded-xl border border-line2 bg-base/90 p-3 shadow-panel backdrop-blur-md">
        <div className="mb-2 flex items-center gap-1.5">
          <span
            className={cx(
              "h-1.5 w-1.5 rounded-full",
              source === "live" ? "animate-blink bg-accent" : "bg-inkFaint"
            )}
          />
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-inkMute">
            {source === "recorded"
              ? "Recorded latency · p50"
              : "Live latency · p50"}
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
