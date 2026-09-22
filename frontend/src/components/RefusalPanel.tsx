import type { GroundingRefusal } from "../state/types";
import { SpeakerBadge } from "./SpeakerBadge";
import { formatClock } from "../lib/time";

/**
 * Visible decline from the grounding guard. Reads as the system choosing
 * not to conclude — not as an error or a board mutation.
 */
export function RefusalPanel({ refusals }: { refusals: GroundingRefusal[] }) {
  if (refusals.length === 0) return null;

  return (
    <section
      id="refusals"
      aria-label="Declined conclusions"
      className="panel p-4"
    >
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="kicker">Declined to conclude</h2>
        <span className="text-[11px] text-inkFaint">
          {refusals.length} claim{refusals.length === 1 ? "" : "s"} not recorded
        </span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {refusals.map((r, i) => (
          <li
            key={`${r.timestamp_ms}-${r.claimed_hypothesis_id ?? "x"}-${i}`}
            className="animate-rowIn rounded-lg border border-line/80 bg-base2/40 px-3 py-2.5"
          >
            <div className="flex items-center gap-2">
              <SpeakerBadge label={r.speaker_label} name={null} size="sm" />
              <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-inkFaint">
                not recorded
              </span>
              <span className="led ml-auto text-[11px] text-inkFaint">
                {formatClock(r.timestamp_ms)}
              </span>
            </div>
            <p className="mt-1.5 text-[13px] leading-snug text-inkMute">
              Would have marked{" "}
              <span className="text-ink">
                {r.claimed_hypothesis_id ?? "a hypothesis"}
              </span>{" "}
              as{" "}
              <span className="text-ink">
                {(r.claimed_new_state || "changed").replace(/_/g, " ")}
              </span>
              — no quotable evidence in what was said.
            </p>
            {r.claimed_quote && (
              <p className="mt-1 text-[12px] italic text-inkFaint">
                Claimed quote: “{r.claimed_quote}”
              </p>
            )}
            <p className="mt-1 text-[11px] text-inkFaint/90 line-clamp-2">
              “{r.utterance_text}”
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
