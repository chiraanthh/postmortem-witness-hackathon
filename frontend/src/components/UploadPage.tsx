import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase } from "../lib/api";

/**
 * Portal → upload flow. Warns that real audio scores lower than the demo.
 * Consumes a live seat; never hangs a spinner on reject.
 * Shows upload % then "Normalising audio…" while ffmpeg runs server-side.
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
  const [phase, setPhase] = useState<"idle" | "upload" | "normalise">("idle");
  const [pct, setPct] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);

  useEffect(() => {
    if (inputRef.current) inputRef.current.value = "";
    return () => {
      xhrRef.current?.abort();
    };
  }, []);

  const onFile = useCallback(
    (file: File | null) => {
      if (!file) return;
      setBusy(true);
      setError(null);
      setPhase("upload");
      setPct(0);

      const body = new FormData();
      body.append("file", file);

      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      xhr.open("POST", `${apiBase()}/sessions/upload`);
      xhr.timeout = 180_000;

      xhr.upload.onprogress = (ev) => {
        if (!ev.lengthComputable) return;
        setPct(Math.min(99, Math.round((ev.loaded / ev.total) * 100)));
      };
      xhr.upload.onload = () => {
        setPct(100);
        setPhase("normalise");
      };

      xhr.onload = () => {
        xhrRef.current = null;
        let data: Record<string, unknown> = {};
        try {
          data = JSON.parse(xhr.responseText || "{}") as Record<string, unknown>;
        } catch {
          /* ignore */
        }
        const detail = (data.detail ?? data) as Record<string, unknown> | string;
        if (xhr.status === 503) {
          const msg =
            typeof detail === "object" && detail && "message" in detail
              ? String(detail.message)
              : "Live seats are full";
          setError(msg);
          setBusy(false);
          setPhase("idle");
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          const msg =
            typeof detail === "string"
              ? detail
              : typeof detail === "object" && detail && "message" in detail
                ? String(detail.message)
                : typeof detail === "object" && detail
                  ? JSON.stringify(detail)
                  : `upload failed (${xhr.status})`;
          setError(msg);
          setBusy(false);
          setPhase("idle");
          return;
        }
        if (
          typeof data.session_id !== "string" ||
          typeof data.lease_id !== "string"
        ) {
          setError("server did not return a session");
          setBusy(false);
          setPhase("idle");
          return;
        }
        setPhase("idle");
        setBusy(false);
        onStarted(data.session_id, data.lease_id);
      };

      xhr.onerror = () => {
        xhrRef.current = null;
        setError("Network error during upload");
        setBusy(false);
        setPhase("idle");
      };
      xhr.ontimeout = () => {
        xhrRef.current = null;
        setError("Upload timed out — try a shorter file");
        setBusy(false);
        setPhase("idle");
      };
      xhr.onabort = () => {
        xhrRef.current = null;
        setBusy(false);
        setPhase("idle");
      };

      xhr.send(body);
    },
    [onStarted]
  );

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-base/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[720px] items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            onClick={() => {
              xhrRef.current?.abort();
              onCancel();
            }}
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
                onFile(f);
              }}
            />
            {busy ? "Working…" : "Choose audio file"}
          </label>
          <button
            type="button"
            disabled={busy}
            className="pill-btn border border-line2 bg-raised/70 text-ink"
            onClick={() => {
              xhrRef.current?.abort();
              onCancel();
            }}
          >
            Cancel
          </button>
        </div>

        {phase !== "idle" && (
          <div className="flex flex-col gap-2" role="status">
            <p className="text-[12px] text-inkMute">
              {phase === "upload"
                ? `Uploading… ${pct}%`
                : "Normalising audio…"}
            </p>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel2">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-200"
                style={{
                  width: phase === "normalise" ? "100%" : `${pct}%`,
                  opacity: phase === "normalise" ? 0.55 : 1,
                }}
              />
            </div>
          </div>
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
