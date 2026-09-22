"""Replays a recorded spike through the extraction worker and reports lag.

The recorded run carries, for every utterance, the wall-clock offset at which
it became available. That is enough to reconstruct the real-time schedule
without replaying the audio: a single serial consumer starts each utterance at
max(available_at, previous_finish), so queueing is modelled rather than
assumed away. Only the extraction calls are real.

    python -m backend.tools.extract_run --run demo/runs/full
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from backend.extraction.worker import ExtractionWorker, RunningContext
from backend.metrics import EXTRACT, percentile
from backend.state.models import TurnKey
from backend.transcription.buffer import Utterance


def load_utterances(run: Path) -> list[tuple[Utterance, float]]:
    """(utterance, available_at_ms) in the order the stream emitted them."""
    out: list[tuple[Utterance, float]] = []
    for line in (run / "stream.jsonl").open(encoding="utf-8"):
        d = json.loads(line)
        if d["kind"] != "utterance":
            continue
        u = Utterance(
            turn_key=TurnKey(d["epoch"], d["turn_order"]),
            turn_keys=[TurnKey(*k) for k in d["turn_keys"]],
            speaker_label=d["speaker_label"],
            start_ms=d["start_ms"],
            end_ms=d["end_ms"],
            text=d["text"],
            amended=d["amended"],
            previous_speaker_label=d["previous_speaker_label"],
        )
        out.append((u, float(d["at_ms"])))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.extract_run")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)

    pairs = load_utterances(args.run)
    if args.limit:
        pairs = pairs[: args.limit]
    print(f"{len(pairs)} utterances from {args.run}")

    refusals: list[dict] = []
    worker = ExtractionWorker(on_log=lambda m: None)

    # The state machine does not exist yet, so the running context is
    # accumulated here: hypotheses as they are proposed, threads as they open.
    hypotheses: dict[str, list[str]] = {}   # id -> [text, state]
    threads: list[tuple[str, str]] = []

    results: list[dict] = []
    prev_finish = 0.0

    for i, (u, available_at) in enumerate(pairs):
        context = RunningContext(
            hypotheses=tuple((h, v[0], v[1]) for h, v in hypotheses.items()),
            threads=tuple(threads),
        )
        start_at = max(available_at, prev_finish)
        outcome = worker.extract(u, context)
        finish_at = start_at + outcome.latency_ms
        prev_finish = finish_at

        # One utterance can now yield several events (contract v1.3.0). The
        # timing is a property of the utterance, so it is recorded once and
        # repeated on each event rather than divided between them.
        results.append({
            "i": i,
            "turn_key": str(u.turn_key),
            "speaker": u.speaker_label,
            "audio_end_ms": u.end_ms,
            "available_at_ms": available_at,
            "start_at_ms": start_at,
            "finish_at_ms": finish_at,
            "queue_wait_ms": start_at - available_at,
            "pipeline_lag_ms": finish_at - available_at,
            "realtime_lag_ms": finish_at - u.end_ms,
            "extract_ms": outcome.latency_ms,
            "n_events": len(outcome.events),
            "types": [e.type for e in outcome.events],
            "events": [
                {
                    "event_id": e.event_id,
                    "type": e.type,
                    "summary": e.summary,
                    "hypothesis_id": e.hypothesis_id,
                    "new_state": e.new_state,
                    "owner": e.owner,
                    "confidence": e.confidence,
                }
                for e in outcome.events
            ],
            "attempts": outcome.attempts,
            "degraded": outcome.degraded,
            "rejected": [r.reason for r in outcome.rejections],
            "text": u.text,
        })

        for rejection in outcome.rejections:
            refusals.append({
                "turn_key": str(u.turn_key),
                "speaker": u.speaker_label,
                "reason": rejection.reason,
                "claimed_hypothesis_id": rejection.claimed_hypothesis_id,
                "claimed_new_state": rejection.claimed_new_state,
                "claimed_quote": rejection.claimed_quote,
                "utterance": u.text,
            })

        for ev in outcome.events:
            if ev.type == "hypothesis" and ev.hypothesis_id:
                hypotheses.setdefault(ev.hypothesis_id, [ev.summary, "open"])
            elif ev.type == "status_change" and ev.hypothesis_id:
                entry = hypotheses.setdefault(ev.hypothesis_id, [ev.summary, "open"])
                entry[1] = ev.new_state or entry[1]
            elif ev.type == "thread":
                # thread_id is the event id — answers_thread_id must match exactly.
                threads.append((ev.event_id, ev.summary or ev.text))
            if ev.answers_thread_id:
                threads = [
                    t for t in threads if t[0] != ev.answers_thread_id
                ]

        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(pairs)}  {worker.report()}", flush=True)

    dist = Counter(t for r in results for t in r["types"])
    lags = [r["realtime_lag_ms"] for r in results]
    waits = [r["queue_wait_ms"] for r in results]

    summary = {
        "utterances": len(results),
        "events": sum(r["n_events"] for r in results),
        "events_per_utterance": dict(
            Counter(r["n_events"] for r in results)
        ),
        "type_distribution": dict(dist),
        "extract": {
            "n": worker.metrics.count(EXTRACT),
            "p50": worker.metrics.p50(EXTRACT),
            "p95": worker.metrics.p95(EXTRACT),
            "max": max((r["extract_ms"] for r in results), default=0.0),
            "total_ms": sum(r["extract_ms"] for r in results),
        },
        "lag": {
            "final_realtime_lag_ms": lags[-1] if lags else 0.0,
            "max_realtime_lag_ms": max(lags, default=0.0),
            "p50_realtime_lag_ms": percentile(lags, 50),
            "p95_realtime_lag_ms": percentile(lags, 95),
            "final_queue_wait_ms": waits[-1] if waits else 0.0,
            "max_queue_wait_ms": max(waits, default=0.0),
        },
        "worker": {
            "calls": worker.calls,
            "events_emitted": worker.events_emitted,
            "multi_event_utterances": worker.multi_event_utterances,
            "retries": worker.retries,
            "degraded": worker.degraded,
            "rejected_status_changes": worker.rejected_status_changes,
            "blank_summaries": worker.blank_summaries,
            "unknown_hypothesis_refs": worker.unknown_hypothesis_refs,
        },
        "refusals": refusals,
        "hypotheses": {h: v for h, v in hypotheses.items()},
    }

    (args.run / "extraction.json").write_text(json.dumps(summary, indent=2))
    (args.run / "extraction_events.json").write_text(json.dumps(results, indent=2))

    print()
    print(json.dumps({k: v for k, v in summary.items() if k != "refusals"}, indent=2))
    print(f"\n{len(refusals)} refused status_changes")
    for r in refusals:
        print(f"  {r['turn_key']} {r['reason']}\n     quote={r['claimed_quote']!r}\n     said={r['utterance'][:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
