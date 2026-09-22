import { useCallback, useEffect, useState } from "react";
import { apiBase, sessionPath } from "../lib/api";

export type ExtractionStatus = {
  provider: string;
  model: string;
  cleanup_enabled: boolean;
  cleanup_model: string;
  gateway_region: string;
  temperature: number;
  defaults: {
    provider: string;
    model: string;
    cleanup_enabled: boolean;
  };
};

const PRESETS = [
  {
    id: "haiku",
    label: "Haiku",
    provider: "anthropic",
    model: "claude-haiku-4-5-20251001",
  },
  {
    id: "sonnet-gw",
    label: "Sonnet · Gateway",
    provider: "assemblyai_gateway",
    model: "claude-sonnet-4-6",
  },
] as const;

async function fetchExtraction(
  sessionId: string | null
): Promise<ExtractionStatus | null> {
  if (!sessionId) return null;
  try {
    const res = await fetch(
      `${apiBase()}${sessionPath(sessionId, "/incident/extraction")}`
    );
    if (!res.ok) return null;
    const data = (await res.json()) as ExtractionStatus & { ok?: boolean };
    return data;
  } catch {
    return null;
  }
}

async function postExtraction(
  sessionId: string,
  body: {
    provider?: string;
    model?: string;
    cleanup?: boolean;
  }
): Promise<ExtractionStatus | null> {
  const res = await fetch(
    `${apiBase()}${sessionPath(sessionId, "/incident/extraction")}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as ExtractionStatus;
}

function matchPreset(status: ExtractionStatus | null): string {
  if (!status) return "haiku";
  const hit = PRESETS.find(
    (p) => p.provider === status.provider && p.model === status.model
  );
  return hit?.id ?? "haiku";
}

/**
 * Runtime extraction model switch. Does not reset the board — only affects
 * subsequent utterances. Default selection is Haiku.
 */
export function ModelPicker({ sessionId }: { sessionId: string | null }) {
  const [status, setStatus] = useState<ExtractionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const selected = matchPreset(status);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      fetchExtraction(sessionId).then((s) => {
        if (!cancelled) setStatus(s);
      });
    };
    refresh();
    const id = window.setInterval(refresh, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [sessionId]);

  const apply = useCallback(async (presetId: string) => {
    if (!sessionId) return;
    const preset = PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    setBusy(true);
    setErr(null);
    try {
      const next = await postExtraction(sessionId, {
        provider: preset.provider,
        model: preset.model,
      });
      setStatus(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [sessionId]);

  const toggleCleanup = useCallback(async () => {
    if (!status || !sessionId) return;
    setBusy(true);
    setErr(null);
    try {
      const next = await postExtraction(sessionId, {
        cleanup: !status.cleanup_enabled,
      });
      setStatus(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [status, sessionId]);

  if (!sessionId || import.meta.env.VITE_MOCK === "true") {
    return null;
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1.5">
        <span className="hidden text-[10px] uppercase tracking-[0.14em] text-inkFaint sm:inline">
          Extract
        </span>
        <div className="flex rounded-md border border-line bg-panel/70 p-0.5">
          {PRESETS.map((p) => {
            const on = selected === p.id;
            return (
              <button
                key={p.id}
                type="button"
                disabled={busy}
                onClick={() => apply(p.id)}
                className={
                  on
                    ? "rounded px-2 py-0.5 text-[11px] font-medium text-accentSoft bg-accent/15"
                    : "rounded px-2 py-0.5 text-[11px] font-medium text-inkMute hover:text-ink"
                }
                title={`${p.provider} · ${p.model}`}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          disabled={busy || !status}
          onClick={toggleCleanup}
          className={
            status?.cleanup_enabled
              ? "rounded border border-accent/40 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accentSoft"
              : "rounded border border-line px-2 py-0.5 text-[10px] font-medium text-inkFaint hover:text-inkMute"
          }
          title="Optional qwen rewrite before extraction (default off)"
        >
          Cleanup {status?.cleanup_enabled ? "on" : "off"}
        </button>
      </div>
      {err && (
        <span className="max-w-[220px] truncate text-[10px] text-danger" title={err}>
          {err}
        </span>
      )}
    </div>
  );
}
