import { useState } from "react";
import type { Latency } from "../contract";
import { ms } from "../lib/time";
import { cx } from "../lib/cx";

/**
 * Compact latency chip for the top bar. Expanded panel is opt-in so it never
 * covers Actions/Threads.
 */
export function LatencyOverlay({
  latency,
  source,
}: {
  latency: Latency;
  source: "live" | "recorded";
}) {
  const [open, setOpen] = useState(false);
  const e2e = latency.e2e_ms > 0 ? ms(latency.e2e_ms) : "—";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="chip gap-1.5"
        aria-expanded={open}
        title={source === "recorded" ? "Recorded latency p50" : "Live latency p50"}
      >
        <span
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            source === "live"
              ? "bg-accent motion-safe:animate-blink"
              : "bg-inkFaint"
          )}
        />
        <span className="text-[10px] uppercase tracking-[0.12em] text-inkFaint">
          {source === "recorded" ? "Rec" : "Live"}
        </span>
        <span className="led text-[12px] text-accentSoft">{e2e}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-50 w-[188px] rounded-xl border border-line2 bg-base p-3 shadow-panel">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-inkMute">
              {source === "recorded" ? "Recorded · p50" : "Live · p50"}
            </span>
            <button
              type="button"
              className="text-[10px] text-inkFaint hover:text-ink"
              onClick={() => setOpen(false)}
            >
              close
            </button>
          </div>
          <div className="flex flex-col gap-1.5 text-[12px]">
            <Row label="ASR" value={latency.asr_ms} />
            <Row label="Extract" value={latency.extract_ms} warn />
            <Row label="E2E" value={latency.e2e_ms} />
          </div>
        </div>
      )}
    </div>
  );
}

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
      <span className={cx("led text-[14px]", warn ? "text-open" : "text-accentSoft")}>
        {value > 0 ? ms(value) : "—"}
      </span>
    </div>
  );
}
