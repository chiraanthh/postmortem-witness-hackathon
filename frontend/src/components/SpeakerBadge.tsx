import { cx } from "../lib/cx";

/**
 * Renders the speaker as an intentional, designed token. Contract rule: show
 * the name when we have one, otherwise the diarization label (A–D). Never
 * "Unknown", null, or a blank — `speaker_name` is null for now by design.
 */

const LABEL_TINT: Record<string, string> = {
  A: "border-accent/50 text-accentSoft bg-accent/10",
  B: "border-amber-400/40 text-amber-300 bg-amber-400/10",
  C: "border-rose-400/40 text-rose-300 bg-rose-400/10",
  D: "border-teal-400/40 text-teal-300 bg-teal-400/10",
  // Provisional diarization before a label lands — not a person on call.
  PENDING: "border-line2 text-inkFaint bg-raised/60",
  "?": "border-line2 text-inkFaint bg-raised/60",
};

function tint(label: string): string {
  return LABEL_TINT[label] ?? "border-line2 text-inkMute bg-raised/70";
}

export function SpeakerBadge({
  label,
  name,
  size = "md",
}: {
  label: string;
  name: string | null;
  size?: "sm" | "md";
}) {
  const display = name ?? label;
  const isLabel = !name;
  return (
    <span
      className={cx(
        "inline-flex items-center justify-center rounded-md border font-semibold tabular-nums",
        size === "sm" ? "h-5 min-w-5 px-1 text-[11px]" : "h-6 min-w-6 px-1.5 text-xs",
        tint(label)
      )}
      title={isLabel ? `Speaker ${label} (name not yet assigned)` : name ?? ""}
    >
      {display}
    </span>
  );
}
