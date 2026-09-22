import { REPLAY_SPEEDS, type ReplaySpeed } from "../replay/emitter";

/**
 * Replay-only chrome: realtime by default, optional 2x/4x, and a jump to
 * the teardown reconciliation beat so judges skip the long middle.
 */
export function ReplayControls({
  speed,
  onSpeed,
  onJumpReconciliation,
}: {
  speed: number;
  onSpeed: (speed: ReplaySpeed) => void;
  onJumpReconciliation: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div
        className="flex items-center rounded-full border border-line bg-panel/60 p-0.5"
        role="group"
        aria-label="Replay speed"
      >
        {REPLAY_SPEEDS.map((s) => {
          const active = speed === s;
          return (
            <button
              key={s}
              type="button"
              onClick={() => onSpeed(s)}
              className={
                active
                  ? "rounded-full bg-accent/20 px-2.5 py-1 text-[11px] font-semibold text-accentSoft"
                  : "rounded-full px-2.5 py-1 text-[11px] font-medium text-inkMute hover:text-ink"
              }
              aria-pressed={active}
            >
              {s}×
            </button>
          );
        })}
      </div>
      <button
        type="button"
        onClick={onJumpReconciliation}
        className="pill-btn border border-line2 bg-raised/70 text-ink hover:border-accent/50 hover:text-accentSoft"
        title="Skip to the teardown speaker reconciliation"
      >
        Jump to reconciliation
      </button>
    </div>
  );
}
