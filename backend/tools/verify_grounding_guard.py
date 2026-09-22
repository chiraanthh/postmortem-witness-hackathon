"""Live end-to-end check: grounding guard fires on the cache hedge line.

Requires a running server and API keys (ASR + Haiku). Run after recharging:

    # terminal 1
    uvicorn backend.main:app --port 8000

    # terminal 2 — temperature 0 is the server default (EXTRACTION_TEMPERATURE=0)
    python -m backend.tools.verify_grounding_guard \\
        --file demo/audio/incident_01.wav

Reports whether a grounding_refusal landed for the unevidenced cache line,
and whether the 10 known demo events still recall at 9/10 (or better).
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

from backend.tools.score_recall import KNOWN, normalise


CACHE_NEEDLE = "probably not the cache"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m backend.tools.verify_grounding_guard"
    )
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--file", default="demo/audio/incident_01.wav")
    p.add_argument("--timeout-s", type=float, default=600.0)
    args = p.parse_args(argv)

    base = args.base.rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    frames: list[dict] = []
    done = threading.Event()
    error: list[str] = []

    def reader() -> None:
        try:
            with connect(ws_url, open_timeout=10, close_timeout=5) as ws:
                msg = json.loads(ws.recv(timeout=10))
                frames.append(msg)
                if msg.get("type") != "handshake":
                    error.append(f"expected handshake, got {msg.get('type')}")
                    done.set()
                    return
                msg = json.loads(ws.recv(timeout=10))
                frames.append(msg)
                if msg.get("type") != "snapshot":
                    error.append(f"expected snapshot, got {msg.get('type')}")
                    done.set()
                    return

                deadline = time.monotonic() + args.timeout_s
                while time.monotonic() < deadline:
                    try:
                        raw = ws.recv(timeout=5)
                    except TimeoutError:
                        try:
                            st = httpx.get(f"{base}/health", timeout=5).json()
                        except Exception:  # noqa: BLE001
                            continue
                        if st.get("status") == "failed":
                            error.append(f"pipeline failed: {st.get('last_error')}")
                            break
                        if st.get("finished") and not st.get("running"):
                            try:
                                raw = ws.recv(timeout=3)
                            except TimeoutError:
                                break
                            else:
                                frames.append(json.loads(raw))
                                continue
                        continue
                    frames.append(json.loads(raw))
                done.set()
        except Exception as exc:  # noqa: BLE001
            error.append(str(exc))
            done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.4)

    path = Path(args.file)
    r = httpx.post(
        f"{base}/incident/start",
        json={"file": str(path)},
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"start failed: {r.status_code} {r.text}", file=sys.stderr)
        return 1
    print(f"started {path}")

    done.wait(timeout=args.timeout_s + 30)
    if error:
        print("ERROR:", "; ".join(error), file=sys.stderr)
        return 1

    refusals: list[dict] = []
    timeline: list[dict] = []
    for fr in frames:
        if fr.get("type") == "diff":
            refusals.extend(fr.get("refusals") or [])
            for op in fr.get("ops") or []:
                if op.get("op") == "upsert_event":
                    timeline.append(op.get("value") or {})
        elif fr.get("type") == "snapshot":
            refusals.extend(fr.get("refusals") or [])
            timeline = list((fr.get("state") or {}).get("timeline") or timeline)

    cache_refusals = [
        r for r in refusals
        if r.get("kind") == "grounding_refusal"
        and CACHE_NEEDLE in normalise(r.get("utterance_text") or "")
    ]
    # Also accept any refusal that claimed cache.
    if not cache_refusals:
        cache_refusals = [
            r for r in refusals
            if r.get("kind") == "grounding_refusal"
            and (r.get("claimed_hypothesis_id") or "").startswith("cache")
        ]

    print("\n=== GROUNDING GUARD ===")
    print(f"refusals total: {len(refusals)}")
    if cache_refusals:
        r0 = cache_refusals[0]
        print("GUARD FIRED on cache hedge:")
        print(f"  claimed: {r0.get('claimed_hypothesis_id')} -> {r0.get('claimed_new_state')}")
        print(f"  quote:   {r0.get('claimed_quote')!r}")
        print(f"  reason:  {r0.get('reason')}")
        print(f"  recorded={r0.get('recorded')}")
    else:
        print("GUARD DID NOT FIRE on the cache hedge line.")
        print("  (Haiku may have classified it as noise — check transcript.)")

    # Board must not show cache as ruled_out from that hedge.
    cache_ruled = [
        e for e in timeline
        if e.get("type") == "status_change"
        and (e.get("hypothesis_id") or "").startswith("cache")
        and e.get("new_state") == "ruled_out"
        and CACHE_NEEDLE in normalise(e.get("text") or "")
    ]
    print(f"board cache rule-outs from hedge utterance: {len(cache_ruled)} (want 0)")

    # Lightweight recall against timeline text (not full extract_run).
    print("\n=== RECALL (timeline needles) ===")
    caught = 0
    for known in KNOWN:
        needle = normalise(known.needle)
        hit = next(
            (
                e for e in timeline
                if e.get("type") == known.kind
                and needle in normalise(e.get("text") or "")
            ),
            None,
        )
        if hit is None:
            # Some events match summary rather than text.
            hit = next(
                (
                    e for e in timeline
                    if e.get("type") == known.kind
                    and needle in normalise(e.get("summary") or "")
                ),
                None,
            )
        ok = hit is not None
        caught += int(ok)
        print(f"  {'OK' if ok else 'MISS'}  {known.id:<28} want={known.kind}")

    print(f"\nrecall {caught}/{len(KNOWN)}")
    print(f"guard_fired={bool(cache_refusals)}  board_clean={len(cache_ruled) == 0}")

    ok = bool(cache_refusals) and len(cache_ruled) == 0 and caught >= 9
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
