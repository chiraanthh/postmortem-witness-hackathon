"""Records an ASR spike run to JSONL so the extraction pass can replay it.

Same code path as `python -m backend.transcription.stream --file ...` — it
builds the identical StreamConfig and TranscriptionStream — but it also writes
every turn, revision and utterance to disk with wall-clock arrival offsets, so
a later extraction run can reconstruct the real-time schedule without paying
for the audio replay twice.

    python -m backend.tools.spike_run --file demo/audio/x.wav --out demo/runs/x
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from backend.metrics import ASR, FORMAT, now_ms
from backend.transcription import audio
from backend.transcription.buffer import Amendment, FinalTurn, Utterance
from backend.transcription.stream import (
    StreamConfig,
    TranscriptionStream,
    _Printer,
    _print_report,
)


class Recorder:
    """Appends one JSON object per line, thread-safely."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, kind: str, **payload) -> None:
        with self._lock:
            self._fh.write(json.dumps({"kind": kind, **payload}) + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m backend.tools.spike_run")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="output directory")
    p.add_argument("--max-speakers", type=int, default=None)
    p.add_argument("--fast", action="store_true")
    args = p.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(args.out / "stream.jsonl")

    printer = _Printer(colour=False, show_turns=True)
    chunks = audio.file_source(args.file, realtime=not args.fast)
    duration_ms = audio.probe_duration_ms(args.file)

    stream_config = StreamConfig(max_speakers=args.max_speakers)
    print(f"keyterms: {len(stream_config.keyterms)} from demo/script/keyterms.txt")

    t0 = now_ms()

    def on_turn(turn: FinalTurn, latency: float | None) -> None:
        printer.turn(turn, latency)
        rec.write(
            "turn",
            at_ms=now_ms() - t0,
            epoch=turn.connection_epoch,
            turn_order=turn.turn_order,
            speaker_label=turn.speaker_label,
            text=turn.text,
            start_ms=turn.start_ms,
            end_ms=turn.end_ms,
            is_formatted=turn.is_formatted,
            asr_latency_ms=latency,
        )

    def on_utterance(u: Utterance) -> None:
        printer.utterance(u)
        rec.write(
            "utterance",
            at_ms=now_ms() - t0,
            epoch=u.connection_epoch,
            turn_order=u.turn_order,
            turn_keys=[list(k) for k in u.turn_keys],
            speaker_label=u.speaker_label,
            start_ms=u.start_ms,
            end_ms=u.end_ms,
            text=u.text,
            amended=u.amended,
            previous_speaker_label=u.previous_speaker_label,
        )

    def on_amendment(a: Amendment, delay_ms: float) -> None:
        printer.amendment(a, delay_ms)
        rec.write(
            "amendment",
            at_ms=now_ms() - t0,
            turn_key=list(a.turn_key),
            previous_label=a.previous_label,
            new_label=a.new_label,
            delay_ms=delay_ms,
            partial=a.partial,
            changed=a.changed,
            utterance_turn_key=list(a.utterance.turn_key),
            text=a.utterance.text,
        )

    def on_status(message: str) -> None:
        printer.status(message)
        rec.write("status", at_ms=now_ms() - t0, message=message)

    stream = TranscriptionStream(
        chunks,
        stream_config,
        on_utterance=on_utterance,
        on_amendment=on_amendment,
        on_turn=on_turn,
        on_status=on_status,
    )

    # --- raw protocol taps -------------------------------------------------
    # _connect() reads self._handle_turn / self._handle_revision off the
    # instance, so wrapping them here intercepts every message the server
    # sends, including partials the pipeline drops and revision messages
    # whose item count the RevisionLog flattens away.
    shapes: dict[str, int] = {}
    revision_messages: list[int] = []
    inner_turn = stream._handle_turn
    inner_revision = stream._handle_revision

    def tapped_turn(client, event):
        shape = f"end_of_turn={event.end_of_turn} formatted={event.turn_is_formatted}"
        shapes[shape] = shapes.get(shape, 0) + 1
        if event.end_of_turn:
            # Audio-clock latency measured independently of which final this
            # is, so a missing unformatted pass does not erase the number.
            words = event.words or []
            end = int(words[-1].end) if words else None
            lat = stream.clock.latency_ms(end) if end is not None else None
            rec.write(
                "raw_final",
                at_ms=now_ms() - t0,
                turn_order=event.turn_order,
                formatted=event.turn_is_formatted,
                speaker_label=event.speaker_label,
                audio_end_ms=end,
                harness_latency_ms=lat,
                n_words=len(words),
            )
        return inner_turn(client, event)

    def tapped_revision(client, event):
        revision_messages.append(len(event.revisions))
        rec.write(
            "revision_message",
            at_ms=now_ms() - t0,
            n_items=len(event.revisions),
            items=[
                {"turn_order": i.turn_order, "speaker_label": i.speaker_label}
                for i in event.revisions
            ],
        )
        return inner_revision(client, event)

    stream._handle_turn = tapped_turn
    stream._handle_revision = tapped_revision

    print(f"source: {args.file} ({(duration_ms or 0) / 1000:.1f}s)")
    print(f"diarization: on, max_speakers={args.max_speakers}")
    print()

    rc = 0
    try:
        stream.run()
    except KeyboardInterrupt:
        rc = 130
    except Exception as exc:  # noqa: BLE001 - spike harness
        print(f"stream failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        rc = 1

    _print_report(stream, printer)

    # Every revision the server sent, including ones that changed nothing and
    # ones that named a turn with no formatted text (so produced no amendment).
    for r in stream.revisions.revisions:
        rec.write(
            "revision",
            turn_key=list(r.turn_key),
            previous_label=r.previous_label,
            new_label=r.new_label,
            delay_ms=r.delay_ms,
            partial=r.partial,
            changed=r.changed,
            text=r.text,
        )

    summary = {
        "audio_file": str(args.file),
        "audio_duration_ms": duration_ms,
        "audio_sent_ms": stream.clock.audio_sent_ms,
        "wall_ms": now_ms() - t0,
        "turns_seen": stream.turns_seen,
        "utterances": len(stream.buffer.emitted),
        "dropped_unformatted": stream.buffer.dropped_unformatted,
        "reconnects": stream.reconnects,
        "epochs_used": stream.reconnects + 1,
        "failed": stream.failed,
        "last_error": stream.last_error,
        "asr": {
            "n": stream.metrics.count(ASR),
            "p50": stream.metrics.p50(ASR),
            "p95": stream.metrics.p95(ASR),
            "samples": stream.metrics.samples.get(ASR, []),
        },
        "format": {
            "n": stream.metrics.count(FORMAT),
            "p50": stream.metrics.p50(FORMAT),
            "p95": stream.metrics.p95(FORMAT),
        },
        "revisions_total": stream.revisions.total,
        "revisions_changed": len(stream.revisions.changed),
        "turn_event_shapes": shapes,
        "revision_messages": len(revision_messages),
        "revision_items_per_message": revision_messages,
        "keyterms": stream_config.keyterms,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    rec.close()
    print(f"\nrecorded to {args.out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
