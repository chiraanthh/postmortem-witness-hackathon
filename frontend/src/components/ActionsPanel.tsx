import type { Action } from "../contract";
import { formatClock } from "../lib/time";
import { cx } from "../lib/cx";

function ownerInitials(owner: string): string {
  const parts = owner.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const second = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + second).toUpperCase();
}

function ActionRow({ a }: { a: Action }) {
  const unowned = a.unowned;
  return (
    <li
      className={cx(
        "animate-rowIn rounded-lg border p-3",
        unowned ? "border-danger/50 bg-danger/[0.06]" : "border-line bg-panel2/50"
      )}
    >
      <div className="flex items-start gap-3">
        {unowned ? (
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-danger/60 bg-danger/15 text-danger">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M12 9v4m0 4h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        ) : (
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-teal-400/40 bg-teal-400/10 text-[11px] font-bold text-teal-300">
            {ownerInitials(a.owner as string)}
          </span>
        )}

        <div className="min-w-0 flex-1">
          <p className="text-[13px] leading-snug text-ink">{a.text}</p>
          <div className="mt-1.5 flex items-center gap-2">
            {unowned ? (
              <span className="inline-flex items-center gap-1 rounded border border-danger/60 bg-danger/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.14em] text-danger">
                Unowned
              </span>
            ) : (
              <span className="text-[11px] text-inkMute">
                owner{" "}
                <span className="font-semibold text-ink">{a.owner}</span>
              </span>
            )}
            <span className="led ml-auto text-[11px] text-inkFaint">
              {formatClock(a.at_ms)}
            </span>
          </div>
        </div>
      </div>
    </li>
  );
}

export function ActionsPanel({ actions }: { actions: Action[] }) {
  const unowned = actions.filter((a) => a.unowned).length;
  return (
    <section id="actions" className="panel flex flex-col p-5">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-teal-400" />
          <h2 className="font-display text-[16px] font-semibold tracking-tight text-ink">
            Actions
          </h2>
        </div>
        {unowned > 0 ? (
          <span className="chip border-danger/40 text-danger">
            <span className="led text-danger">
              {String(unowned).padStart(2, "0")}
            </span>
            unowned
          </span>
        ) : (
          <span className="chip">
            <span className="led text-ink">
              {String(actions.length).padStart(2, "0")}
            </span>
            committed
          </span>
        )}
      </div>
      <p className="mb-3 text-[11.5px] text-inkFaint">
        Commitments made on the call. Unowned work is flagged, never hidden.
      </p>

      {actions.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-[12px] text-inkFaint">
          no actions committed yet
        </div>
      ) : (
        <ul className="scroll-slim flex max-h-[520px] flex-col gap-2 overflow-y-auto pr-1">
          {actions.map((a) => (
            <ActionRow key={a.action_id} a={a} />
          ))}
        </ul>
      )}
    </section>
  );
}
