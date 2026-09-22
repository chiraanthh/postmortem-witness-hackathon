"""Compare extraction providers on the labelled set + incident_01 recall.

    python -m backend.tools.benchmark_providers

Defaults are never changed by this tool — it reports numbers only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from backend import config
from backend.extraction.providers.factory import make_provider
from backend.extraction.worker import ExtractionWorker, RunningContext
from backend.metrics import EXTRACT
from backend.state.models import Event, EventType
from backend.tests.test_extraction import LABELLED, TRAPS, utt
from backend.tools.score_recall import KNOWN, normalise
from backend.transcription.buffer import Utterance
from backend.state.models import TurnKey


def _labelled_report(worker: ExtractionWorker) -> dict:
    context = RunningContext(
        hypotheses=(
            ("dns", "DNS resolution failing", "open"),
            ("cache-eviction", "Cache evicting hot keys", "open"),
            ("bad-deploy", "This morning's deploy broke it", "open"),
            ("replica-lag", "Read replicas lagging", "open"),
        ),
        threads=(("t1", "Nobody has checked the CDN yet"),),
    )
    strict = tolerant = false_pos = missed = 0
    rows = []
    for c in LABELLED:
        out = worker.extract(utt(c.text), context)
        types = [e.type for e in out.events]
        ok_strict = c.expected in types
        ok_tolerant = bool(c.acceptable & set(types))
        strict += ok_strict
        tolerant += ok_tolerant
        if c.trap and "status_change" in types:
            false_pos += 1
        if c.expected == "status_change" and "status_change" not in types:
            missed += 1
        rows.append({
            "text": c.text,
            "want": c.expected,
            "got": types,
            "ok_strict": ok_strict,
            "trap_fp": c.trap and "status_change" in types,
            "latency_ms": out.latency_ms,
        })
    n = len(LABELLED)
    return {
        "strict_accuracy": f"{strict}/{n}",
        "strict_pct": round(100 * strict / n, 1),
        "tolerant_accuracy": f"{tolerant}/{n}",
        "tolerant_pct": round(100 * tolerant / n, 1),
        "state_change_fp": f"{false_pos}/{len(TRAPS)}",
        "state_change_fp_pct": round(100 * false_pos / max(1, len(TRAPS)), 1),
        "state_changes_missed": missed,
        "extract_p50_ms": round(worker.metrics.p50(EXTRACT), 1),
        "extract_p95_ms": round(worker.metrics.p95(EXTRACT), 1),
        "rows": rows,
        "report": worker.report(),
    }


def _load_utterances(run: Path) -> list[Utterance]:
    path = run / "stream.jsonl"
    out: list[Utterance] = []
    for line in path.read_text().splitlines():
        o = json.loads(line)
        if o.get("kind") != "utterance":
            continue
        keys = [TurnKey(*k) for k in o["turn_keys"]]
        out.append(Utterance(
            turn_key=keys[0],
            turn_keys=keys,
            speaker_label=o["speaker_label"],
            start_ms=o["start_ms"],
            end_ms=o["end_ms"],
            text=o["text"],
            amended=o.get("amended", False),
            previous_speaker_label=o.get("previous_speaker_label"),
        ))
    return out


def _recall_report(worker: ExtractionWorker, run: Path) -> dict:
    utterances = _load_utterances(run)
    # Replay with a growing context like extract_run.
    from backend.state.machine import IncidentMachine

    machine = IncidentMachine(incident_id=run.name)
    events_by_turn: dict[str, list[Event]] = {}
    for u in utterances:
        hyps = tuple(
            (h.hypothesis_id, h.text, h.state)
            for h in machine.hypotheses.values()
        )
        threads = tuple(
            (t.thread_id, t.text)
            for t in machine.threads.values()
            if not t.closed
        )
        ctx = RunningContext(hypotheses=hyps, threads=threads)
        outcome = worker.extract(u, ctx)
        machine.apply_many(outcome.events)
        key = str(u.turn_key)
        events_by_turn.setdefault(key, []).extend(outcome.events)

    # score_recall-compatible matching on needles
    results = []
    for known in KNOWN:
        needle = normalise(known.needle)
        matched_utt = None
        for u in utterances:
            if needle in normalise(u.text):
                matched_utt = u
                break
        if matched_utt is None:
            results.append({
                "id": known.id,
                "want": known.kind,
                "status": "not transcribed",
            })
            continue
        evs = events_by_turn.get(str(matched_utt.turn_key), [])
        types = [e.type for e in evs]
        if known.kind in types:
            hit = next(e for e in evs if e.type == known.kind)
            results.append({
                "id": known.id,
                "want": known.kind,
                "status": "caught",
                "got_types": types,
                "got_summary": hit.summary,
            })
        elif types and types != ["noise"]:
            results.append({
                "id": known.id,
                "want": known.kind,
                "status": "mistyped",
                "got_types": types,
            })
        else:
            results.append({
                "id": known.id,
                "want": known.kind,
                "status": "not extracted",
                "got_types": types,
            })

    caught = sum(1 for r in results if r["status"] == "caught")
    return {
        "recall": f"{caught}/{len(KNOWN)}",
        "recall_pct": round(100 * caught / len(KNOWN), 1),
        "results": results,
        "extract_p50_ms": round(worker.metrics.p50(EXTRACT), 1),
        "extract_p95_ms": round(worker.metrics.p95(EXTRACT), 1),
        "report": worker.report(),
    }


def _run_one(label: str, provider: str, model: str, run: Path) -> dict:
    print(f"\n>>> {label}: provider={provider} model={model}", flush=True)
    worker = ExtractionWorker(
        provider=make_provider(provider=provider, model=model),
        on_log=lambda m: None,
    )
    labelled = _labelled_report(worker)
    # Fresh worker metrics for recall (or continue — user wants latency of
    # each suite; report combined p50 from a dedicated recall worker).
    worker2 = ExtractionWorker(
        provider=make_provider(provider=provider, model=model),
        on_log=lambda m: None,
    )
    recall = _recall_report(worker2, run)
    return {
        "label": label,
        "provider": provider,
        "model": model,
        "labelled": {
            k: v for k, v in labelled.items() if k != "rows"
        },
        "labelled_rows": labelled["rows"],
        "recall": {k: v for k, v in recall.items() if k != "results"},
        "recall_results": recall["results"],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--run",
        type=Path,
        default=config.REPO_ROOT / "demo" / "runs" / "incident_01_keyterms",
    )
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    if not args.run.is_dir():
        print(f"missing run dir {args.run}", file=sys.stderr)
        return 1

    suites = [
        ("haiku_direct", "anthropic", "claude-haiku-4-5-20251001"),
        ("sonnet_gateway", "assemblyai_gateway", "claude-sonnet-4-6"),
    ]

    results = []
    for label, provider, model in suites:
        if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            print("skip haiku: no ANTHROPIC_API_KEY", file=sys.stderr)
            continue
        if provider == "assemblyai_gateway" and not os.environ.get("ASSEMBLYAI_API_KEY"):
            print("skip gateway: no ASSEMBLYAI_API_KEY", file=sys.stderr)
            continue
        results.append(_run_one(label, provider, model, args.run))

    print("\n" + "=" * 72)
    print("PROVIDER BENCHMARK SUMMARY")
    print("=" * 72)
    for r in results:
        lab = r["labelled"]
        rec = r["recall"]
        print(
            f"{r['label']:<18} "
            f"strict {lab['strict_accuracy']} ({lab['strict_pct']}%)  "
            f"FP {lab['state_change_fp']}  "
            f"recall {rec['recall']}  "
            f"labelled p50/p95 {lab['extract_p50_ms']}/{lab['extract_p95_ms']} ms  "
            f"recall p50/p95 {rec['extract_p50_ms']}/{rec['extract_p95_ms']} ms"
        )
    print("=" * 72)
    print("Default unchanged: anthropic + claude-haiku-4-5-20251001")

    out = args.out or (args.run / "provider_benchmark.json")
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
