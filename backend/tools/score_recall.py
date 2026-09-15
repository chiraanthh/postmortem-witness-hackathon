"""Scores extraction recall against the events incident_01 deliberately embeds.

    python -m backend.tools.score_recall --run demo/runs/incident_01_v2

The script is written around ten specific events (see the header comment in
demo/script/incident_01.yaml). Type distribution alone cannot tell you whether
they were caught: a run can emit three actions and still have missed all five
real ones. This joins the extraction output back to the script.

**Matching is on text, not on time.** The obvious join is the timestamp, but
the ASR merges and splits turns, so one script line can arrive as half an
utterance or as a third of a merged one and the time window stops lining up.
Every known event is instead identified by a short distinctive phrase from its
line, matched against the normalised transcript.

That also separates the two failure modes, which have completely different
fixes:

- **not transcribed** - the phrase is nowhere in the transcript. The ASR lost
  it, usually to crosstalk. Extraction never had a chance.
- **not extracted** - the phrase is there and the utterance produced events,
  but none of the right type. That is a prompt or model problem.

A third outcome, **mistyped**, is called out separately: the event was seen
and classified as something plausible but wrong - an unowned action read as a
thread, say. It is a miss for recall, but a much cheaper one to fix than
silence.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

UNOWNED = "<unowned>"


@dataclass(frozen=True)
class Known:
    """One event the script puts in the call on purpose."""

    id: str
    kind: str
    line: int
    # A phrase from the line, distinctive enough to identify it and short
    # enough to survive the ASR. Matched after normalisation.
    needle: str
    owner: str | None = None
    note: str = ""


# The ten events named in demo/script/incident_01.yaml:
#   2 explicit rule-outs, 1 confirmed cause, 1 dropped thread,
#   5 actions (exactly one unowned), 1 resolution.
KNOWN: list[Known] = [
    Known("rule_out_dns", "status_change", 12, "dns is fine",
          note="explicit, with a spoken reason"),
    Known("rule_out_provider", "status_change", 17, "status page is green",
          note="explicit, with a spoken reason"),

    Known("confirmed_cause", "status_change", 43, "it was the deploy",
          note="explicit confirmation; shares its utterance with the revert"),

    Known("dropped_thread_tls", "thread", 31, "certificate on the gateway",
          note="nobody answers it, never revisited - the video cold open"),

    Known("action_rollback", "action", 34, "roll back the deploy now",
          owner="Disha", note="speaker commits to it herself"),
    Known("action_watch_error_rate", "action", 35, "watch the error rate",
          owner="Priya", note="speaker commits to it herself"),
    Known("action_rate_limit", "action", 36, "check the rate limit configuration",
          owner=UNOWNED, note="the one unowned action - 'someone needs to'"),
    Known("action_status_update", "action", 37, "draft a status update",
          owner="Rohan", note="assigned by name"),
    Known("action_revert", "action", 43, "revert the retry change",
          owner="Disha",
          note="second event in a status_change utterance; the one v1.2.0 ate"),

    Known("resolution", "resolution", 47, "declaring this resolved",
          note="explicit declaration, not just good news"),
]


@dataclass
class Result:
    known: Known
    status: str = "not transcribed"
    matched_text: str | None = None
    turn_key: str | None = None
    got_types: list[str] = field(default_factory=list)
    got_owner: str | None = None
    got_summary: str | None = None
    got_new_state: str | None = None


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Punctuation has to go because the needle is written against the script
    while the transcript is the ASR's own formatting - "I'll" against "Ill",
    "two-twenty" against "two twenty".
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def score(results_path: Path) -> list[Result]:
    rows = json.loads(results_path.read_text(encoding="utf-8"))
    for r in rows:
        r["_norm"] = normalise(r["text"])
        if "types" not in r:
            # A run recorded before contract v1.3.0, one flat event per
            # utterance. Lifted into the list shape so old runs stay
            # comparable - the whole point of the score is the trend.
            r["events"] = [{
                "event_id": None,
                "type": r["type"],
                "summary": r.get("summary"),
                "hypothesis_id": r.get("hypothesis_id"),
                "new_state": r.get("new_state"),
                "owner": r.get("owner"),
                "confidence": r.get("confidence"),
            }]
            r["types"] = [r["type"]]

    out: list[Result] = []
    for known in KNOWN:
        needle = normalise(known.needle)
        hits = [r for r in rows if needle in r["_norm"]]
        res = Result(known=known)

        if not hits:
            out.append(res)
            continue

        # If the phrase landed in several utterances, prefer one that actually
        # produced the right type - a merged turn can carry two known events.
        best = next(
            (r for r in hits if known.kind in r["types"]),
            hits[0],
        )
        res.matched_text = best["text"]
        res.turn_key = best["turn_key"]
        res.got_types = list(best["types"])

        match = next((e for e in best["events"] if e["type"] == known.kind), None)
        if match is not None:
            res.status = "caught"
            res.got_owner = match["owner"]
            res.got_summary = match["summary"]
            res.got_new_state = match["new_state"]
        elif any(t != "noise" for t in res.got_types):
            res.status = "mistyped"
        else:
            res.status = "not extracted"
        out.append(res)
    return out


def owner_verdict(res: Result) -> str:
    """Whether the owner came out right. Only meaningful once caught."""
    want = res.known.owner
    if want is None or res.status != "caught":
        return ""
    got = res.got_owner
    if want is UNOWNED or want == UNOWNED:
        return "owner ok (unowned)" if got is None else f"OWNER INVENTED: {got!r}"
    if got is None:
        return f"owner missing (wanted {want})"
    return "owner ok" if want.lower() in got.lower() else f"owner {got!r} != {want}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.score_recall")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    results = score(args.run / "extraction_events.json")

    width = 74
    print("=" * width)
    print(f"EXTRACTION RECALL  {args.run}")
    print("=" * width)

    by_status: dict[str, int] = {}
    for res in results:
        by_status[res.status] = by_status.get(res.status, 0) + 1
        mark = {
            "caught": "OK  ",
            "mistyped": "TYPE",
            "not extracted": "MISS",
            "not transcribed": "ASR ",
        }[res.status]
        print(f"\n{mark} {res.known.id:<24} want={res.known.kind}")
        print(f"     line {res.known.line}: {res.known.note}")
        if res.status == "not transcribed":
            print(f"     phrase {res.known.needle!r} is not in the transcript")
            continue
        print(f"     {res.turn_key}  got={res.got_types}")
        if res.status == "caught":
            bits = [f"summary={res.got_summary!r}"]
            if res.got_new_state:
                bits.append(f"new_state={res.got_new_state}")
            if res.known.owner is not None:
                bits.append(owner_verdict(res))
            print(f"     {'  '.join(b for b in bits if b)}")
        else:
            print(f"     said: {res.matched_text[:90]!r}")

    caught = by_status.get("caught", 0)
    n = len(results)
    print("\n" + "-" * width)
    print(f"recall {caught}/{n}  ({100 * caught / n:.0f}%)")
    for status in ("mistyped", "not extracted", "not transcribed"):
        if by_status.get(status):
            ids = [r.known.id for r in results if r.status == status]
            print(f"  {status:<17} {by_status[status]}  {', '.join(ids)}")

    owner_notes = [
        (r.known.id, owner_verdict(r)) for r in results
        if owner_verdict(r) and not owner_verdict(r).startswith("owner ok")
    ]
    if owner_notes:
        print("\nowner problems:")
        for eid, note in owner_notes:
            print(f"  {eid:<24} {note}")
    print("=" * width)

    out = args.out or (args.run / "recall.json")
    out.write_text(json.dumps([
        {
            "id": r.known.id,
            "want": r.known.kind,
            "line": r.known.line,
            "status": r.status,
            "turn_key": r.turn_key,
            "got_types": r.got_types,
            "got_summary": r.got_summary,
            "got_new_state": r.got_new_state,
            "got_owner": r.got_owner,
            "want_owner": r.known.owner,
            "owner_verdict": owner_verdict(r),
            "matched_text": r.matched_text,
        }
        for r in results
    ], indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
