import type { TimelineRow } from "../state/types";
import { SpeakerBadge } from "./SpeakerBadge";
import { TypeTag } from "./TypeTag";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

const CORRECT_WINDOW = 4200;
const STAGGER_MS = 320;

function shortModel(model: string): string {
  if (model.includes("haiku")) return "Haiku";
  if (model.includes("sonnet")) return "Sonnet";
  if (model.includes("qwen")) return "Qwen";
  const tail = model.split(/[-_]/).pop();
  return (tail && tail.length <= 12 ? tail : model.slice(0, 12)) || model;
}

export function TimelineItem({
  row,
  tick,
  reconBeatAt = null,
  staggerIndex = 0,
}: {
  row: TimelineRow;
  tick: number;
  reconBeatAt?: number | null;
  staggerIndex?: number;
}) {
  const inReconBeat =
    reconBeatAt != null &&
    row.correctedAt != null &&
    Math.abs((row.correctedAt ?? 0) - reconBeatAt) < 50;

  const revealAt = inReconBeat ? reconBeatAt! + staggerIndex * STAGGER_MS : null;
  const labelRevealed = revealAt == null || tick >= revealAt;

  const justCorrected =
    row.correctedAt != null &&
    tick - row.correctedAt < CORRECT_WINDOW &&
    labelRevealed;
  const wasCorrected = row.amendedFrom != null;
  const lowConfidence = row.confidence < 0.65;

  // During the recon stagger, show the previous label until this row's beat.
  const displayLabel =
    inReconBeat && !labelRevealed && row.amendedFrom
      ? row.amendedFrom
      : row.speaker_label;

  return (
    <li className="relative animate-rowIn pl-6">
      <span className="absolute left-[7px] top-1 bottom-0 w-px bg-line" />
      <span
        className={cx(
          "absolute left-1 top-[5px] h-2.5 w-2.5 rounded-full border-2 border-base",
          row.type === "status_change"
            ? "bg-accent"
            : row.type === "action"
            ? "bg-teal-400"
            : row.type === "hypothesis"
            ? "bg-open"
            : "bg-rose-400"
        )}
      />

      <div
        key={`${row.correctedAt ?? "stable"}-${labelRevealed ? "new" : "old"}`}
        className={cx(
          "rounded-lg border px-3 py-2",
          justCorrected
            ? "animate-correctPulse border-accent/50"
            : "border-line bg-panel2/60"
        )}
      >
        <div className="flex items-center gap-2">
          <TypeTag type={row.type} />
          <SpeakerBadge label={displayLabel} name={row.speaker_name} size="sm" />
          {wasCorrected && labelRevealed && (
            <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-accentSoft">
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {row.amendedFrom} → {row.speaker_label}
            </span>
          )}
          {row.extractionModel && (
            <span
              className="rounded border border-line px-1.5 py-0.5 text-[9px] font-medium tracking-wide text-inkFaint"
              title={
                row.extractionProvider
                  ? `${row.extractionProvider} · ${row.extractionModel}`
                  : row.extractionModel
              }
            >
              {shortModel(row.extractionModel)}
            </span>
          )}
          <span className="led ml-auto text-[11px] text-inkFaint">
            {formatClock(row.timestamp_ms)}
          </span>
        </div>

        <p className="mt-1.5 text-[13px] leading-snug text-ink">{row.summary}</p>

        {lowConfidence && (
          <span className="mt-1 inline-block text-[10px] text-inkFaint">
            low confidence · {(row.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
    </li>
  );
}
