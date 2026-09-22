"""End-to-end check against a running server.

    # terminal 1
    uvicorn backend.main:app --port 8000

    # terminal 2
    python -m backend.tools.verify_demo --file demo/audio/incident_01.wav

Connects a WebSocket client first (so it sees the empty snapshot), starts
the incident, then watches for diffs, a reconciliation op at teardown, and
an export that matches the final board.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.verify_demo")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--file", default="demo/audio/incident_01.wav")
    p.add_argument("--timeout-s", type=float, default=600.0)
    args = p.parse_args(argv)

    base = args.base.rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    events: list[dict] = []
    done = threading.Event()
    error: list[str] = []

    def reader() -> None:
        try:
            with connect(ws_url, open_timeout=10, close_timeout=5) as ws:
                # Handshake
                msg = json.loads(ws.recv(timeout=10))
                events.append(msg)
                if msg.get("type") != "handshake":
                    error.append(f"expected handshake, got {msg.get('type')}")
                    done.set()
                    return
                print(f"handshake contract_version={msg.get('contract_version')}")

                # Snapshot
                msg = json.loads(ws.recv(timeout=10))
                events.append(msg)
                if msg.get("type") != "snapshot":
                    error.append(f"expected snapshot, got {msg.get('type')}")
                    done.set()
                    return
                print(
                    f"snapshot: resolved={msg['state'].get('resolved')} "
                    f"timeline={len(msg['state'].get('timeline') or [])}"
                )

                deadline = time.monotonic() + args.timeout_s
                while time.monotonic() < deadline:
                    try:
                        raw = ws.recv(timeout=5)
                    except TimeoutError:
                        # Still alive; check HTTP status.
                        try:
                            st = httpx.get(f"{base}/health", timeout=5).json()
                            if st.get("status") == "failed":
                                error.append(
                                    f"pipeline failed: {st.get('last_error')}"
                                )
                                break
                            if st.get("finished") and not st.get("running"):
                                # Drain a moment longer for final diffs.
                                try:
                                    raw = ws.recv(timeout=2)
                                except TimeoutError:
                                    break
                            else:
                                continue
                        except Exception:  # noqa: BLE001
                            continue
                    msg = json.loads(raw)
                    events.append(msg)
                    if msg.get("type") == "diff":
                        ops = msg.get("ops") or []
                        kinds = [o.get("op") for o in ops]
                        print(f"diff ({len(ops)}): {kinds}")
                        if "reconciliation" in kinds:
                            print("reconciliation received")
                done.set()
        except Exception as exc:  # noqa: BLE001
            error.append(f"{type(exc).__name__}: {exc}")
            done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.5)  # let the socket land before starting

    print(f"POST /incident/start file={args.file}")
    r = httpx.post(
        f"{base}/incident/start",
        json={"file": args.file},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"start failed: {r.status_code} {r.text}", file=sys.stderr)
        return 1
    print(r.json())

    done.wait(timeout=args.timeout_s + 30)
    t.join(timeout=5)

    if error:
        print("ERROR:", error, file=sys.stderr)
        return 1

    diffs = [e for e in events if e.get("type") == "diff"]
    recon = [
        o
        for e in diffs
        for o in (e.get("ops") or [])
        if o.get("op") == "reconciliation"
    ]
    print(f"\n{len(events)} messages, {len(diffs)} diffs, "
          f"{len(recon)} reconciliation op(s)")

    export = httpx.get(f"{base}/export", timeout=30)
    export.raise_for_status()
    md = export.text
    print(f"export: {len(md)} chars")
    print("--- export head ---")
    print("\n".join(md.splitlines()[:40]))
    print("---")

    snap = httpx.get(f"{base}/incident", timeout=30).json()
    ok = True
    if not diffs:
        print("FAIL: no diffs arrived", file=sys.stderr)
        ok = False
    if not recon:
        print("FAIL: no reconciliation op at teardown", file=sys.stderr)
        ok = False
    if not snap.get("resolved"):
        print("WARN: board not marked resolved "
              "(extractor may have missed the resolution line)")
    if "Postmortem:" not in md:
        print("FAIL: export missing title", file=sys.stderr)
        ok = False
    # Export should reflect snapshot hypotheses.
    for h in snap.get("hypotheses") or []:
        if h["hypothesis_id"] not in md:
            print(f"FAIL: export missing hypothesis {h['hypothesis_id']}",
                  file=sys.stderr)
            ok = False

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
