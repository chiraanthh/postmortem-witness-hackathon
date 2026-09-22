"""Capture every WebSocket frame from a live IncidentHub run of a WAV.

Produces a timed fixture the portal replay emitter can play back with zero
API calls — handshake, snapshot, every diff (latency, board ops,
reconciliation, silence_summary), refusals, provenance.

    EXTRACTION_TEMPERATURE=0 python -m backend.tools.capture_ws_fixture \
        --file demo/audio/incident_01.wav \
        --out frontend/src/replay/incident_01.fixture.json

Wall-clock: ~audio duration + extraction drain. Requires ASSEMBLYAI_API_KEY
and ANTHROPIC_API_KEY (or the configured extraction provider).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from backend import config
from backend.main import IncidentHub, handshake_message, snapshot_message
from backend.metrics import now_ms


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.capture_ws_fixture")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--drain-s",
        type=float,
        default=30.0,
        help="seconds to wait after pipeline finished for late diffs",
    )
    args = p.parse_args(argv)

    path = args.file.expanduser()
    if not path.is_absolute():
        path = (config.REPO_ROOT / path).resolve()
    if not path.is_file():
        print(f"missing audio: {path}", file=sys.stderr)
        return 2

    # Pin temperature for a falsifiable capture.
    os.environ.setdefault("EXTRACTION_TEMPERATURE", "0")
    temp = float(os.environ.get("EXTRACTION_TEMPERATURE", "0"))
    if temp != 0.0:
        print(
            f"refusing capture at EXTRACTION_TEMPERATURE={temp} (must be 0)",
            file=sys.stderr,
        )
        return 2

    print(f"file={path}")
    print(f"extraction_model={config.extraction_model()}")
    print(f"extraction_provider={config.extraction_provider()}")
    print(f"temperature={config.EXTRACTION_TEMPERATURE}")

    return asyncio.run(_run(path, args.out, args.drain_s))


async def _run(path: Path, out: Path, drain_s: float) -> int:
    hub = IncidentHub()
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)

    frames: list[dict[str, Any]] = []
    t0 = now_ms()
    lock = asyncio.Lock()

    def record(msg: dict[str, Any]) -> None:
        frames.append(
            {
                "emit_at_ms": int(now_ms() - t0),
                "message": msg,
            }
        )

    # Replace broadcast — no live sockets; we are the sole recorder.
    hub._broadcast_raw = record  # type: ignore[method-assign]

    # Same openers a real /ws client receives.
    record(handshake_message())
    with hub._lock:
        state = hub.machine.snapshot()
        provenance = dict(hub._provenance)
        refusals = list(hub._refusal_journal)
    record(snapshot_message(state, provenance=provenance, refusals=refusals))

    print("starting pipeline…", flush=True)
    hub.start(path)

    # Poll until finished, then drain late extraction/teardown diffs.
    while True:
        st = hub.status
        if st.get("finished") or st.get("status") == "failed":
            break
        await asyncio.sleep(0.5)
        # Heartbeat progress every ~30s
        elapsed = int(now_ms() - t0)
        if elapsed > 0 and elapsed % 30000 < 600:
            print(
                f"  … {elapsed // 1000}s status={st.get('status')} "
                f"frames={len(frames)} pos={st.get('playback_position_ms')}",
                flush=True,
            )

    if hub.status.get("status") == "failed":
        print(f"pipeline failed: {hub.status.get('last_error')}", file=sys.stderr)
        return 1

    print(f"pipeline finished; draining {drain_s}s…", flush=True)
    await asyncio.sleep(drain_s)

    # Stop latency loop / pipeline threads cleanly.
    try:
        hub._stop_pipeline(join=True, suppress_teardown=True)
    except Exception:  # noqa: BLE001
        pass

    ops_seen: dict[str, int] = {}
    types_seen: dict[str, int] = {}
    for fr in frames:
        msg = fr["message"]
        types_seen[msg.get("type", "?")] = types_seen.get(msg.get("type", "?"), 0) + 1
        if msg.get("type") == "diff":
            for op in msg.get("ops") or []:
                kind = op.get("op", "?")
                ops_seen[kind] = ops_seen.get(kind, 0) + 1

    incident_id = "incident_01"
    try:
        snap = hub.machine.snapshot()
        incident_id = str(snap.get("incident_id") or incident_id)
    except Exception:  # noqa: BLE001
        pass

    payload = {
        "incident_id": incident_id,
        "source": "live IncidentHub capture of demo/audio/incident_01.wav",
        "contract_version": handshake_message()["contract_version"],
        "extraction_model": config.extraction_model(),
        "extraction_provider": config.extraction_provider(),
        "extraction_temperature": float(config.EXTRACTION_TEMPERATURE),
        "audio_file": "demo/audio/incident_01.wav",
        "captured_unix_ms": int(time.time() * 1000),
        "frame_count": len(frames),
        "duration_ms": frames[-1]["emit_at_ms"] if frames else 0,
        "type_counts": types_seen,
        "op_counts": ops_seen,
        "messages": frames,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} frames={len(frames)} duration_ms={payload['duration_ms']}")
    print(f"types={types_seen}")
    print(f"ops={ops_seen}")
    if "reconciliation" not in ops_seen:
        print("WARN: no reconciliation op in capture", file=sys.stderr)
    if "silence_summary" not in ops_seen:
        print("WARN: no silence_summary op in capture", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
