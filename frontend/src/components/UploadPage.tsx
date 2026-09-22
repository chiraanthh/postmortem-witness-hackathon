import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase } from "../lib/api";

/**
 * Portal → upload flow. Warns that real audio scores lower than the demo.
 * Consumes a live seat; never hangs a spinner on reject.
 */
export function UploadPage({
  onCancel,
  onStarted,
}: {
  onCancel: () => void;
  onStarted: (sessionId: string, leaseId: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    // Clear stale file input if remounted.
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  const onFile = useCallback(
    async (file: File | null) => {
      if (!file) return;
      setBusy(true);
      setError(null);
      setProgress("Uploading and normalising…");
      const ctrl = new AbortController();
      const kill = window.setTimeout(() => ctrl.abort(), 180_000);
      try {
        const body = new FormData();
        body.append("file", file);
        const res = await fetch(`${apiBase()}/sessions/upload`, {
          method: "POST",
          body,
          signal: ctrl.signal,
        });
        const data = (await res.json().catch(() => ({}))) as Record<
          string,
          unknown
        >;
        const detail = (data.detail ?? data) as Record<string, unknown> | string;
        if (res.status === 503) {
          const msg =
            typeof detail === "object" && detail && "message" in detail
              ? String(detail.message)
              : "Live seats are full";
          throw new Error(msg);
        }
        if (!res.ok) {
          const msg =
            typeof detail === "string"
              ? detail
              : typeof detail === "object" && detail && "message" in detail
                ? String(detail.message)
                : typeof detail === "object" && detail
                  ? JSON.stringify(detail)
                  : `upload failed (${res.status})`;
          throw new Error(msg);
        }
        if (
          typeof data.session_id !== "string" ||
          typeof data.lease_id !== "string"
        ) {
          throw new Error("server did not return a session");
        }
        setProgress(null);
        onStarted(data.session_id, data.lease_id);
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.name === "AbortError"
              ? "Upload timed out — try a shorter file"
              : err.message
            : String(err);
        setError(msg);
        setProgress(null);
      } finally {
        window.clearTimeout(kill);
        setBusy(false);
      }
    },
    [onStarted]
  );

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-base/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[720px] items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            onClick={onCancel}
            className="text-[13px] font-medium text-inkMute hover:text-ink"
          >
            ← Portal
          </button>
          <span className="text-[13px] font-semibold text-ink">
            Upload your own call
          </span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[720px] flex-1 flex-col justify-center gap-6 px-4 py-16 sm:px-6">
        <div className="flex flex-col gap-3">
          <p className="kicker">Bring your own bridge recording</p>
          <h1 className="text-[clamp(1.5rem,3.5vw,2rem)] font-semibold leading-tight tracking-tight text-ink">
            Run the live pipeline on a file you provide.
          </h1>
          <p className="max-w-xl text-[14px] leading-relaxed text-inkMute">
            Real audio will score lower than the scripted demo: unknown
            speaker count, real noise and crosstalk, and no ground-truth
            labels. The extractor was tuned on{" "}
            <span className="text-ink">incident_01</span>.
          </p>
          <ul className="max-w-xl list-disc space-y-1 pl-5 text-[13px] text-inkFaint">
            <li>Accepts common audio formats; ffmpeg normalises to 16 kHz mono PCM.</li>
            <li>Max upload 40 MiB · max duration 15 minutes.</li>
            <li>Consumes one live seat (same cap as the demo pipeline).</li>
            <li>File is deleted when the session ends or the seat expires.</li>
          </ul>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="pill-btn cursor-pointer border border-accent/40 bg-accent/15 text-accentSoft hover:border-accent">
            <input
              ref={inputRef}
              type="file"
              accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm,.flac"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                void onFile(f);
              }}
            />
            {busy ? "Working…" : "Choose audio file"}
          </label>
          <button
            type="button"
            disabled={busy}
            className="pill-btn border border-line2 bg-raised/70 text-ink"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>

        {progress && (
          <p className="text-[12px] text-inkMute" role="status">
            {progress}
          </p>
        )}
        {error && (
          <p className="text-[12px] text-danger" role="alert">
            {error}
          </p>
        )}
      </main>
    </div>
  );
}
