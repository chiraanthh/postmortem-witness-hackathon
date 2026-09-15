"""Scores the ASR's speaker labels against the synthesised ground truth.

    python -m backend.tools.score_diarization \
        --run demo/runs/incident_01 \
        --groundtruth demo/audio/incident_01.groundtruth.json

Diarization labels are arbitrary names for clusters. `A` in the transcript has
no reason to be `A` in the script, so raw label equality measures nothing. The
scorer therefore builds a frame-level co-occurrence matrix between reference
speakers and hypothesis labels, and solves the optimal one-to-one assignment
(Hungarian, maximising agreement) before anything is called right or wrong.
Any other approach - greedy matching, or matching by first appearance -
rewards or punishes label ordering, which is not a property of the system.

Two details that decide whether the number means anything:

- **Overlapped speech is scored separately.** Where the script has two people
  talking at once, a single label cannot be right, and folding those frames
  into one accuracy figure hides the only hard part of the file. Clean
  single-speaker frames are the headline; overlap frames get their own line.
- **Both label generations are scored.** The live labels are what the
  dashboard showed during the call; the post-revision labels are what the API
  settled on after the fact. Only reporting the second one would describe a
  product nobody experienced.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

FRAME_MS = 10
NO_SPEAKER = ""      # silence, or no label
OVERLAP = "<multi>"  # more than one reference speaker active


@dataclass
class Hypothesis:
    """One set of ASR labels over time: (start_ms, end_ms, label) spans."""

    name: str
    spans: list[tuple[int, int, str]]


def load_groundtruth(path: Path) -> tuple[list[tuple[int, int, str]], dict, int]:
    gt = json.loads(path.read_text(encoding="utf-8"))
    spans = [(l["start_ms"], l["end_ms"], l["speaker"]) for l in gt["lines"]]
    return spans, gt.get("speakers", {}), int(gt["duration_ms"])


def load_hypotheses(run: Path) -> list[Hypothesis]:
    """Live labels and post-revision labels, from a recorded spike run."""
    utterances: list[dict] = []
    revisions: list[dict] = []
    for line in (run / "stream.jsonl").open(encoding="utf-8"):
        d = json.loads(line)
        if d["kind"] == "utterance":
            utterances.append(d)
        elif d["kind"] == "revision":
            revisions.append(d)

    live = [(u["start_ms"], u["end_ms"], u["speaker_label"]) for u in utterances]

    # A revision names one turn, which may sit inside a merged utterance, so
    # the join goes through the full turn-key membership. Bare turn_order is
    # never used - it restarts at 0 on reconnect.
    turn_to_utterance: dict[tuple[int, int], int] = {}
    for i, u in enumerate(utterances):
        for key in u["turn_keys"]:
            turn_to_utterance[tuple(key)] = i

    revised = list(live)
    for rev in revisions:
        i = turn_to_utterance.get(tuple(rev["turn_key"]))
        if i is None or rev["new_label"] is None:
            continue
        start, end, _ = revised[i]
        revised[i] = (start, end, rev["new_label"])

    return [
        Hypothesis("live (as shown during the call)", live),
        Hypothesis("post-revision (final API answer)", revised),
    ]


def frame_labels(
    spans: list[tuple[int, int, str]], n_frames: int, *, mark_overlap: bool
) -> np.ndarray:
    """Rasterise spans onto a frame grid.

    When `mark_overlap`, a frame claimed by two different speakers becomes
    OVERLAP rather than silently going to whichever span was written last.
    """
    out = np.full(n_frames, NO_SPEAKER, dtype=object)
    for start_ms, end_ms, label in spans:
        lo = max(0, start_ms // FRAME_MS)
        hi = min(n_frames, -(-end_ms // FRAME_MS))
        if hi <= lo:
            continue
        if not mark_overlap:
            out[lo:hi] = label
            continue
        window = out[lo:hi]
        empty = window == NO_SPEAKER
        clash = ~empty & (window != label)
        window[empty] = label
        window[clash] = OVERLAP
    return out


def solve_assignment(
    ref: np.ndarray, hyp: np.ndarray, ref_speakers: list[str], hyp_labels: list[str]
) -> tuple[dict[str, str], np.ndarray]:
    """Hungarian assignment of hypothesis labels to reference speakers.

    linear_sum_assignment minimises, so the agreement counts are negated.
    The matrix is padded to square so an unequal number of clusters still has
    a solution; unmatched labels simply get no reference.
    """
    counts = np.zeros((len(ref_speakers), len(hyp_labels)), dtype=np.int64)
    ref_ix = {s: i for i, s in enumerate(ref_speakers)}
    hyp_ix = {s: i for i, s in enumerate(hyp_labels)}
    for r, h in zip(ref, hyp):
        if r in ref_ix and h in hyp_ix:
            counts[ref_ix[r], hyp_ix[h]] += 1

    size = max(counts.shape)
    padded = np.zeros((size, size), dtype=np.int64)
    padded[: counts.shape[0], : counts.shape[1]] = counts
    rows, cols = linear_sum_assignment(-padded)

    mapping: dict[str, str] = {}
    for r, c in zip(rows, cols):
        if r < len(ref_speakers) and c < len(hyp_labels) and padded[r, c] > 0:
            mapping[hyp_labels[c]] = ref_speakers[r]
    return mapping, counts


def score(
    ref: np.ndarray,
    hyp: np.ndarray,
    ref_speakers: list[str],
    hyp_labels: list[str],
) -> dict:
    mapping, counts = solve_assignment(ref, hyp, ref_speakers, hyp_labels)

    ref_speech = ref != NO_SPEAKER
    clean = ref_speech & (ref != OVERLAP)
    overlap = ref == OVERLAP
    hyp_speech = hyp != NO_SPEAKER

    mapped = np.array([mapping.get(h, h) if h != NO_SPEAKER else NO_SPEAKER
                       for h in hyp], dtype=object)

    scored = clean & hyp_speech
    correct = scored & (mapped == ref)

    # On an overlap frame any one of the active speakers is defensible, so
    # credit is given for naming either. The reference does not record which,
    # so this is the generous reading, stated as such.
    overlap_scored = overlap & hyp_speech
    overlap_correct = int(np.sum(overlap_scored & np.isin(
        mapped.astype(str), np.array(ref_speakers, dtype=str)
    )))

    per_speaker: dict[str, dict] = {}
    for spk in ref_speakers:
        sel = clean & (ref == spk)
        n = int(np.sum(sel))
        got = int(np.sum(sel & hyp_speech & (mapped == spk)))
        missed = int(np.sum(sel & ~hyp_speech))
        confused = defaultdict(int)
        for h in mapped[sel & hyp_speech & (mapped != spk)]:
            confused[str(h)] += 1
        per_speaker[spk] = {
            "frames": n,
            "seconds": n * FRAME_MS / 1000,
            "correct": got,
            "accuracy": got / n if n else 0.0,
            "missed_no_label_frames": missed,
            "confused_with": dict(sorted(confused.items(), key=lambda kv: -kv[1])),
        }

    # Confusion matrix over clean frames: rows = reference speaker,
    # cols = mapped hypothesis label, plus a column for unlabelled frames.
    cols = list(ref_speakers) + sorted(
        {str(mapping.get(h, h)) for h in hyp_labels} - set(ref_speakers)
    ) + ["<none>"]
    matrix = {spk: {c: 0 for c in cols} for spk in ref_speakers}
    for r, h, ok in zip(ref, mapped, clean):
        if not ok:
            continue
        matrix[str(r)]["<none>" if h == NO_SPEAKER else str(h)] += 1

    return {
        "mapping": mapping,
        "unmatched_labels": [h for h in hyp_labels if h not in mapping],
        "counts": counts.tolist(),
        "ref_speakers": ref_speakers,
        "hyp_labels": hyp_labels,
        "clean": {
            "frames": int(np.sum(clean)),
            "seconds": float(np.sum(clean) * FRAME_MS / 1000),
            "labelled_frames": int(np.sum(scored)),
            "correct": int(np.sum(correct)),
            "accuracy": float(np.sum(correct) / max(1, np.sum(scored))),
            "accuracy_over_all_speech": float(np.sum(correct) / max(1, np.sum(clean))),
            "missed_speech_frames": int(np.sum(clean & ~hyp_speech)),
        },
        "overlap": {
            "frames": int(np.sum(overlap)),
            "seconds": float(np.sum(overlap) * FRAME_MS / 1000),
            "labelled_frames": int(np.sum(overlap_scored)),
            "named_an_active_speaker": overlap_correct,
        },
        "false_alarm_frames": int(np.sum(~ref_speech & hyp_speech)),
        "per_speaker": per_speaker,
        "confusion_matrix": matrix,
        "confusion_columns": cols,
    }


def print_report(hyp_name: str, result: dict, names: dict) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{hyp_name}\n{bar}")

    print("optimal label assignment (Hungarian):")
    for label, spk in sorted(result["mapping"].items(), key=lambda kv: str(kv[1])):
        who = names.get(spk, {}).get("speaker_name", spk)
        print(f"  ASR {label:<8} -> {spk} ({who})")
    if result["unmatched_labels"]:
        print(f"  unmatched ASR labels: {result['unmatched_labels']}")

    c = result["clean"]
    print(f"\nclean (single-speaker) speech: {c['seconds']:.1f}s in "
          f"{c['frames']} frames of {FRAME_MS} ms")
    print(f"  accuracy on labelled frames : {100 * c['accuracy']:.1f}%  "
          f"({c['correct']}/{c['labelled_frames']})")
    print(f"  accuracy over all speech    : {100 * c['accuracy_over_all_speech']:.1f}%  "
          f"(counts missed speech as wrong)")
    print(f"  reference speech with no ASR label: "
          f"{c['missed_speech_frames'] * FRAME_MS / 1000:.1f}s")

    o = result["overlap"]
    if o["frames"]:
        pct = 100 * o["named_an_active_speaker"] / max(1, o["labelled_frames"])
        print(f"\noverlapped speech: {o['seconds']:.1f}s "
              f"({o['frames']} frames), scored separately")
        print(f"  named one of the active speakers: {pct:.1f}%  "
              f"({o['named_an_active_speaker']}/{o['labelled_frames']})")
    else:
        print("\noverlapped speech: none in the reference")

    print(f"\nASR labelled {result['false_alarm_frames'] * FRAME_MS / 1000:.1f}s "
          f"of reference silence (false alarm)")

    print("\nper-speaker accuracy on clean frames:")
    for spk, s in result["per_speaker"].items():
        who = names.get(spk, {}).get("speaker_name", spk)
        worst = ", ".join(
            f"{k} {v * FRAME_MS / 1000:.1f}s" for k, v in
            list(s["confused_with"].items())[:3]
        ) or "-"
        print(f"  {spk} {who:<8} {s['seconds']:>6.1f}s  "
              f"{100 * s['accuracy']:>5.1f}%  lost to: {worst}")

    cols = result["confusion_columns"]
    print("\nconfusion matrix (clean frames, seconds; rows=truth, cols=ASR):")
    print("        " + "".join(f"{c:>9}" for c in cols))
    for spk, row in result["confusion_matrix"].items():
        cells = "".join(f"{row[c] * FRAME_MS / 1000:>9.1f}" for c in cols)
        print(f"  {spk:<6}{cells}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.tools.score_diarization")
    p.add_argument("--run", type=Path, required=True,
                   help="recorded spike run directory")
    p.add_argument("--groundtruth", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None,
                   help="defaults to <run>/diarization.json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    ref_spans, names, duration_ms = load_groundtruth(args.groundtruth)
    hypotheses = load_hypotheses(args.run)

    n_frames = -(-duration_ms // FRAME_MS)
    ref = frame_labels(ref_spans, n_frames, mark_overlap=True)
    ref_speakers = sorted({s for _, _, s in ref_spans})

    print(f"reference: {args.groundtruth.name}  "
          f"{len(ref_spans)} lines, {len(ref_speakers)} speakers, "
          f"{duration_ms / 1000:.1f}s")

    results = {}
    for h in hypotheses:
        hyp = frame_labels(h.spans, n_frames, mark_overlap=False)
        hyp_labels = sorted({s for _, _, s in h.spans})
        result = score(ref, hyp, ref_speakers, hyp_labels)
        results[h.name] = result
        print_report(h.name, result, names)

    out = args.out or (args.run / "diarization.json")
    out.write_text(json.dumps({
        "groundtruth": str(args.groundtruth),
        "run": str(args.run),
        "frame_ms": FRAME_MS,
        "results": results,
    }, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
