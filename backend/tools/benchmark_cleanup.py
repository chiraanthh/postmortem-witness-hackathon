"""Recall with CLEANUP on vs off. Reports added cleanup latency.

    python -m backend.tools.benchmark_cleanup

Does not change defaults. Haiku-direct only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from backend import config
from backend.extraction.providers.factory import make_provider
from backend.extraction.worker import ExtractionWorker
from backend.metrics import CLEANUP, EXTRACT
from backend.tools.benchmark_providers import _labelled_report, _recall_report


def _run(
    label: str,
    cleanup: bool,
    run: Path,
    *,
    labelled: bool,
    throttle_s: float,
) -> dict:
    status = config.set_extraction_runtime(cleanup=cleanup)
    print(
        f"\n>>> {label}: cleanup={status['cleanup_enabled']} "
        f"model={status['model']} throttle={throttle_s}s",
        flush=True,
    )
    provider = make_provider(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
    )

    labelled_report: dict = {}
    if labelled:
        worker = ExtractionWorker(provider=provider, on_log=lambda m: None)
        labelled_report = _labelled_report(worker)

    worker2 = ExtractionWorker(provider=provider, on_log=lambda m: None)
    if cleanup and throttle_s > 0:
        original = worker2.extract

        def throttled(utt, ctx=None):
            out = original(utt, ctx)
            time.sleep(throttle_s)
            return out

        worker2.extract = throttled  # type: ignore[method-assign]

    recall = _recall_report(worker2, run)
    return {
        "label": label,
        "cleanup_enabled": cleanup,
        "labelled": (
            {k: v for k, v in labelled_report.items() if k != "rows"}
            if labelled_report
            else None
        ),
        "labelled_rows": labelled_report.get("rows") if labelled_report else None,
        "recall": {k: v for k, v in recall.items() if k != "results"},
        "recall_results": recall["results"],
        "cleanup_p50_ms": round(worker2.metrics.p50(CLEANUP), 1),
        "cleanup_p95_ms": round(worker2.metrics.p95(CLEANUP), 1),
        "cleanup_n": worker2.metrics.count(CLEANUP),
        "extract_p50_ms": round(worker2.metrics.p50(EXTRACT), 1),
        "extract_p95_ms": round(worker2.metrics.p95(EXTRACT), 1),
        "throttle_s": throttle_s,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--run",
        type=Path,
        default=config.REPO_ROOT / "demo" / "runs" / "incident_01_keyterms",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--throttle",
        type=float,
        default=1.25,
        help="Seconds to wait after each cleanup-enabled utterance",
    )
    p.add_argument(
        "--only",
        choices=("both", "off", "on"),
        default="both",
    )
    args = p.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY required", file=sys.stderr)
        return 1
    if args.only in ("both", "on") and not os.environ.get("ASSEMBLYAI_API_KEY"):
        print("ASSEMBLYAI_API_KEY required for cleanup_on", file=sys.stderr)
        return 1
    if not args.run.is_dir():
        print(f"missing run dir {args.run}", file=sys.stderr)
        return 1

    results = []
    if args.only in ("both", "off"):
        results.append(
            _run("cleanup_off", False, args.run, labelled=True, throttle_s=0.0)
        )
    if args.only in ("both", "on"):
        if results or args.only == "on":
            print("\n…cooling Gateway rate limit for 25s…", flush=True)
            time.sleep(25)
        results.append(
            _run(
                "cleanup_on",
                True,
                args.run,
                labelled=False,  # labelled texts are already clean English
                throttle_s=args.throttle,
            )
        )

    config._runtime_cleanup = None  # noqa: SLF001 — restore env default

    print("\n" + "=" * 72)
    print("CLEANUP BENCHMARK (Haiku direct, temp 0)")
    print("=" * 72)
    for r in results:
        rec = r["recall"]
        lab = r.get("labelled") or {}
        lab_s = (
            f"strict {lab['strict_accuracy']}  FP {lab['state_change_fp']}  "
            if lab
            else ""
        )
        print(
            f"{r['label']:<14} "
            f"{lab_s}"
            f"recall {rec['recall']}  "
            f"extract p50/p95 {r['extract_p50_ms']}/{r['extract_p95_ms']}  "
            f"cleanup p50/p95 {r['cleanup_p50_ms']}/{r['cleanup_p95_ms']} "
            f"(n={r['cleanup_n']})"
        )

    out = args.out or (args.run / "cleanup_benchmark.json")
    if args.only == "on" and out.is_file():
        try:
            prior = json.loads(out.read_text())
            off_prior = [r for r in prior if r.get("label") == "cleanup_off"]
            results = off_prior + results
        except Exception:  # noqa: BLE001
            pass

    if len([r for r in results if r.get("label") in ("cleanup_off", "cleanup_on")]) >= 2:
        off = next(r for r in results if r["label"] == "cleanup_off")
        on = next(r for r in results if r["label"] == "cleanup_on")
        off_map = {x["id"]: x for x in off["recall_results"]}
        on_map = {x["id"]: x for x in on["recall_results"]}
        recovered = [
            eid
            for eid, row in on_map.items()
            if row.get("status") == "caught"
            and off_map.get(eid, {}).get("status") != "caught"
        ]
        newly_missed = [
            eid
            for eid, row in off_map.items()
            if row.get("status") == "caught"
            and on_map.get(eid, {}).get("status") != "caught"
        ]
        print("-" * 72)
        print(f"recovered by cleanup: {recovered or 'none'}")
        print(f"newly missed with cleanup: {newly_missed or 'none'}")
    print("Default unchanged: CLEANUP_ENABLED=false")
    print("=" * 72)

    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
