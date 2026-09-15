"""Rewrites a script's start_ms from the measured synthesis durations.

    python -m backend.tools.retime_script \
        --script demo/script/incident_01.yaml \
        --groundtruth demo/audio/incident_01.groundtruth.json

The script's original timings were written by hand against a guessed speaking
rate. Bulbul came back faster than the guess, so 5 of the 7 lines flagged
`overlap: true` landed in silence and the intended crosstalk was simply not in
the file - while one *unflagged* line collided hard enough that the ASR merged
two speakers into a single turn.

Since ground truth records the real synthesised length of every line, the
timeline can be solved instead of guessed:

- a flagged line starts OVERLAP_MS before its predecessor's measured end, so
  the collision is a fact rather than a hope;
- an unflagged line starts after **everything** still playing, not merely
  after its predecessor - a flagged line can extend past the line it talks
  over, and checking only the predecessor would let a third line land on top
  of it;
- authored pauses are preserved where they were real. The original gap is
  kept when it was at least MIN_GAP, so the call keeps its rhythm instead of
  collapsing to a uniform stride.

Durations depend only on text, voice and pace - none of which this touches -
so retiming never invalidates the TTS cache, and regenerating is free.

The rewrite is textual, one `start_ms:` at a time. yaml.dump would round-trip
away every comment in the file, and those comments are the only record of
which events the script is deliberately embedding.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# How far before its predecessor's end a flagged line starts. The brief asks
# for 300-500 ms; one value in the middle keeps every overlap in range and
# makes the intended total exactly predictable.
OVERLAP_MS = 400

# Smallest silence left between two lines that are NOT meant to collide.
# Large enough to stay clear of the ASR's turn segmentation, small enough to
# still sound like people talking over a bridge.
MIN_GAP_MS = 500

# Deliberately [ \t] and not \s on the trailing group: \s would swallow the
# newline if a value ever ended a line, silently joining two YAML entries.
START_RE = re.compile(r"(start_ms:[ \t]*)(\d+)(,)([ \t]*)")


@dataclass
class Placed:
    index: int
    speaker: str
    overlap: bool
    text: str
    duration_ms: int
    old_start_ms: int
    new_start_ms: int
    # Overlap actually achieved against the immediately preceding line.
    achieved_overlap_ms: int = 0

    @property
    def new_end_ms(self) -> int:
        return self.new_start_ms + self.duration_ms


def solve(
    starts: list[int],
    durations: list[int],
    flags: list[bool],
    *,
    overlap_ms: int = OVERLAP_MS,
    min_gap_ms: int = MIN_GAP_MS,
) -> list[int]:
    """New start_ms for every line. Index 0 keeps its original start."""
    new = [starts[0]]
    for i in range(1, len(starts)):
        prev_end = new[i - 1] + durations[i - 1]
        still_playing = max(new[j] + durations[j] for j in range(i))

        if flags[i]:
            target = prev_end - overlap_ms
            # Never let a line start before the one it is talking over: the
            # script order has to stay the timeline order, or collision
            # detection downstream stops meaning anything.
            new.append(max(target, new[i - 1] + 1))
        else:
            authored_gap = starts[i] - (starts[i - 1] + durations[i - 1])
            new.append(still_playing + max(authored_gap, min_gap_ms))
    return new


def rewrite(text: str, new_starts: list[int]) -> str:
    """Replace the nth `start_ms:` with new_starts[n], keeping alignment.

    The script pads each value so the key after it lines up in a column. That
    padding is preserved by holding the width from the digits to the next key
    constant, rather than by reflowing the file.
    """
    counter = {"i": 0}

    def repl(match: re.Match[str]) -> str:
        i = counter["i"]
        counter["i"] += 1
        if i >= len(new_starts):
            return match.group(0)
        old_value, trailing = match.group(2), match.group(4)
        value = str(new_starts[i])
        field = len(old_value) + 1 + len(trailing)
        spaces = " " * max(1, field - len(value) - 1)
        return f"{match.group(1)}{value},{spaces}"

    out = START_RE.sub(repl, text)
    if counter["i"] != len(new_starts):
        raise SystemExit(
            f"found {counter['i']} start_ms entries but have "
            f"{len(new_starts)} lines - refusing to write a partial rewrite"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.retime_script")
    p.add_argument("--script", type=Path, required=True)
    p.add_argument("--groundtruth", type=Path, required=True)
    p.add_argument("--overlap-ms", type=int, default=OVERLAP_MS)
    p.add_argument("--min-gap-ms", type=int, default=MIN_GAP_MS)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    raw_text = args.script.read_text(encoding="utf-8")
    script = yaml.safe_load(raw_text)
    gt = json.loads(args.groundtruth.read_text(encoding="utf-8"))

    lines = script["lines"]
    gt_lines = gt["lines"]
    if len(lines) != len(gt_lines):
        raise SystemExit(
            f"script has {len(lines)} lines but ground truth has "
            f"{len(gt_lines)} - regenerate the audio first"
        )

    starts = [int(l["start_ms"]) for l in lines]
    flags = [bool(l.get("overlap", False)) for l in lines]
    durations = [int(g["duration_ms"]) for g in gt_lines]

    new_starts = solve(
        starts, durations, flags,
        overlap_ms=args.overlap_ms, min_gap_ms=args.min_gap_ms,
    )

    placed = [
        Placed(
            index=i,
            speaker=l["speaker"],
            overlap=flags[i],
            text=str(l["text"]),
            duration_ms=durations[i],
            old_start_ms=starts[i],
            new_start_ms=new_starts[i],
            achieved_overlap_ms=max(
                0, (new_starts[i - 1] + durations[i - 1]) - new_starts[i]
            ) if i else 0,
        )
        for i, l in enumerate(lines)
    ]

    print(f"{len(placed)} lines, overlap_ms={args.overlap_ms} "
          f"min_gap_ms={args.min_gap_ms}\n")
    print("flagged overlap:true - intended collisions:")
    intended = 0
    for pl in placed:
        if not pl.overlap:
            continue
        intended += pl.achieved_overlap_ms
        print(f"  line {pl.index:>2} {pl.speaker}  {pl.old_start_ms:>6} -> "
              f"{pl.new_start_ms:>6} ms   overlaps predecessor by "
              f"{pl.achieved_overlap_ms:>4} ms   {pl.text[:34]!r}")
    print(f"\n  intended overlap total: {intended} ms "
          f"({intended / 1000:.1f}s) across "
          f"{sum(1 for pl in placed if pl.overlap)} flagged lines")

    # Every pair that now collides, so unflagged collisions cannot slip past.
    bad = []
    for i, pl in enumerate(placed):
        for j in range(i - 1, -1, -1):
            other = placed[j]
            if other.new_end_ms <= pl.new_start_ms:
                continue
            if not pl.overlap:
                bad.append((pl, other, other.new_end_ms - pl.new_start_ms))
    if bad:
        print(f"\n  !! {len(bad)} unflagged collision(s) remain:")
        for pl, other, ms in bad:
            print(f"     line {pl.index} vs line {other.index} by {ms} ms")
    else:
        print("\n  every unflagged line is collision free")

    moved = [pl for pl in placed if pl.new_start_ms != pl.old_start_ms]
    total = max(pl.new_end_ms for pl in placed)
    print(f"\n  {len(moved)} of {len(placed)} lines moved")
    print(f"  timeline end: {max(starts[i] + durations[i] for i in range(len(placed)))} "
          f"-> {total} ms ({total / 1000:.1f}s)")

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    out = rewrite(raw_text, new_starts)
    # target_duration_ms is documentation; keep it honest.
    out = re.sub(
        r"(target_duration_ms:\s*)(\d+)",
        lambda m: f"{m.group(1)}{total}",
        out,
        count=1,
    )
    args.script.write_text(out, encoding="utf-8")

    reparsed = yaml.safe_load(args.script.read_text(encoding="utf-8"))
    got = [int(l["start_ms"]) for l in reparsed["lines"]]
    if got != new_starts:
        raise SystemExit("rewrite did not round-trip; the file may be damaged")
    print(f"\nwrote {args.script} ({len(moved)} start_ms values changed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
