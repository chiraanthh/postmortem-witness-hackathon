"""AssemblyAI streaming client, and the CLI that exercises it.

Run it against a recording:

    python -m backend.transcription.stream --file demo/audio/call.wav

Design notes that are not obvious from the API:

- A turn finalizes twice. The unformatted final is what we time; the formatted
  final is what we read. See "Known API behaviours" in CLAUDE.md.
- `turn_order` and word timestamps are both **per connection**, and both reset
  to zero on reconnect. Since turn_order is our join key for speaker revisions,
  a reconnect would silently alias turn 0 of the second connection onto turn 0
  of the first and reattribute someone's words. So every connection gets a
  monotonic `connection_epoch`, and the join key everywhere downstream is the
  pair `(connection_epoch, turn_order)` - see `TurnKey`. Word timestamps get an
  audio-time base instead, since those do need to be globally comparable.
- `client.stream()` returns as soon as bytes are queued and becomes a silent
  no-op once the socket is gone. A dropped connection therefore looks exactly
  like silence unless you watch for it, which is what `_alive` is for.
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from assemblyai.streaming.v3 import (
    BeginEvent,
    SpeakerRevisionEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    TerminationEvent,
    TurnEvent,
)

from backend import config
from backend.config import ConfigError
from backend.metrics import ASR, FORMAT, AudioClock, Metrics, RevisionLog, now_ms
from backend.transcription import audio
from backend.transcription.audio import AudioError, Chunk
from backend.state.models import TurnKey
from backend.transcription.buffer import (
    PAUSE_MS,
    Amendment,
    FinalTurn,
    Utterance,
    UtteranceBuffer,
)

UNKNOWN_SPEAKER = "?"


@dataclass
class StreamConfig:
    """Everything tunable about one spike run."""

    speaker_labels: bool = config.SPEAKER_LABELS
    max_speakers: int | None = config.MAX_SPEAKERS
    format_turns: bool = config.FORMAT_TURNS
    pause_ms: int = PAUSE_MS

    # Reconnect policy. Exponential with full jitter, so a server-side blip
    # does not turn into a synchronised retry storm.
    max_reconnects: int = 5
    backoff_base_s: float = 0.5
    backoff_max_s: float = 15.0

    # connect() returns as soon as the WebSocket handshake completes, but a
    # rejection (bad key, bad params) arrives moments later on the reader
    # thread. A session is only real once Begin has landed, so we wait for it.
    handshake_timeout_s: float = 10.0

    def to_params(self) -> StreamingParameters:
        return StreamingParameters(
            sample_rate=config.SAMPLE_RATE,
            encoding=config.ENCODING,
            format_turns=self.format_turns,
            speaker_labels=self.speaker_labels,
            max_speakers=self.max_speakers,
        )


@dataclass
class _Session:
    """Per-connection bookkeeping that must not leak across a reconnect.

    `epoch` is the half of the turn key that makes the server's turn numbering
    unambiguous. It only ever goes up.
    """

    epoch: int = 0
    audio_base_ms: int = 0
    seen_first_chunk: bool = False
    session_id: str | None = None


class TranscriptionStream:
    """Streams audio to AssemblyAI and emits speaker-labelled utterances.

    Callbacks fire on the SDK's reader thread, not the caller's. The buffer is
    mutated from both, so everything touching it takes `_lock`.
    """

    def __init__(
        self,
        chunks: Iterator[Chunk],
        stream_config: StreamConfig | None = None,
        *,
        api_key: str | None = None,
        on_utterance: Callable[[Utterance], None] | None = None,
        on_amendment: Callable[[Amendment, float], None] | None = None,
        on_turn: Callable[[FinalTurn, float | None], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.chunks = chunks
        self.config = stream_config or StreamConfig()
        self._api_key = api_key or config.assemblyai_api_key()

        self.on_utterance = on_utterance or (lambda u: None)
        self.on_amendment = on_amendment or (lambda a, d: None)
        self.on_turn = on_turn or (lambda t, l: None)
        self.on_status = on_status or (lambda m: None)

        self.clock = AudioClock()
        self.metrics = Metrics()
        self.revisions = RevisionLog()
        self.buffer = UtteranceBuffer(pause_ms=self.config.pause_ms)

        self._lock = threading.Lock()
        self._alive = threading.Event()
        self._begin = threading.Event()
        self._closing = False
        self._client: StreamingClient | None = None
        self._session = _Session()
        self._last_error: str | None = None

        self.reconnects = 0
        self.turns_seen = 0

    @property
    def failed(self) -> bool:
        """True when the stream ended in an error it never recovered from."""
        return self._last_error is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # --- session lifecycle -------------------------------------------------

    def _connect(self) -> None:
        client = StreamingClient(
            StreamingClientOptions(
                api_key=self._api_key,
                api_host="streaming.assemblyai.com",
            )
        )
        # Handlers must be registered before connect(): an HTTP-level rejection
        # such as a bad key is dispatched to the Error handler, not raised.
        client.on(StreamingEvents.Begin, self._handle_begin)
        client.on(StreamingEvents.Turn, self._handle_turn)
        client.on(StreamingEvents.SpeakerRevision, self._handle_revision)
        client.on(StreamingEvents.Termination, self._handle_termination)
        client.on(StreamingEvents.Error, self._handle_error)

        self._last_error = None
        self._alive.set()
        self._begin.clear()
        client.connect(self.config.to_params())
        self._client = client

        # Wait for Begin. Without this an invalid key looks like a healthy
        # session that simply never transcribes anything, and we cheerfully
        # stream a whole call into a socket that was refused.
        deadline = time.monotonic() + self.config.handshake_timeout_s
        while time.monotonic() < deadline:
            if self._begin.wait(0.05):
                return
            if not self._alive.is_set():
                raise StreamingError(self._last_error or "connection rejected")
        raise StreamingError(
            f"no Begin within {self.config.handshake_timeout_s:.0f}s "
            "- the session was never established"
        )

    def _session_dead(self) -> bool:
        if not self._alive.is_set():
            return True
        client = self._client
        # Defensive: some drops set the SDK's stop event without dispatching an
        # Error we can see. Reading a private attribute is worth it here —
        # the alternative is streaming into a socket that is already gone.
        stop = getattr(client, "_stop_event", None)
        return bool(stop is not None and stop.is_set())

    def _reconnect(self) -> bool:
        """Reopen the session, preserving global turn and audio numbering."""
        for attempt in range(1, self.config.max_reconnects + 1):
            delay = min(
                self.config.backoff_max_s,
                self.config.backoff_base_s * (2 ** (attempt - 1)),
            )
            delay = random.uniform(0, delay)  # full jitter
            self.on_status(
                f"connection lost ({self._last_error or 'no reason given'}); "
                f"reconnect attempt {attempt}/{self.config.max_reconnects} "
                f"in {delay:.1f}s"
            )
            time.sleep(delay)

            with self._lock:
                # A fresh connection restarts turn_order at 0 and word
                # timestamps at 0. Bump the epoch so turn keys stay unique,
                # and rebase the audio clock on the first chunk we send.
                self._session = _Session(
                    epoch=self._session.epoch + 1,
                    audio_base_ms=0,
                    seen_first_chunk=False,
                )
            try:
                self._connect()
            except (StreamingError, OSError) as exc:
                self._last_error = str(exc)
                continue

            self.reconnects += 1
            self.on_status(
                f"reconnected as epoch {self._session.epoch}; the server "
                f"restarts turn numbering at 0"
            )
            return True

        self.on_status(
            f"giving up after {self.config.max_reconnects} reconnect attempts"
        )
        return False

    def close(self) -> None:
        self._closing = True
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect(terminate=True)
            except (StreamingError, OSError, RuntimeError):
                pass

    # --- event handlers (SDK reader thread) --------------------------------

    def _handle_begin(self, _client: StreamingClient, event: BeginEvent) -> None:
        self._session.session_id = event.id
        self._last_error = None
        self._begin.set()
        self.on_status(f"session {event.id} open")

    def _handle_error(self, _client: StreamingClient, error: StreamingError) -> None:
        self._last_error = str(error)
        self._alive.clear()

    def _handle_termination(
        self, _client: StreamingClient, event: TerminationEvent
    ) -> None:
        self._alive.clear()
        if self._closing:
            self.on_status(
                f"session closed after {event.audio_duration_seconds}s of audio"
            )

    def _handle_turn(self, _client: StreamingClient, event: TurnEvent) -> None:
        if not event.end_of_turn:
            return  # partials are not this spike's business

        with self._lock:
            session = self._session
            key = TurnKey(session.epoch, event.turn_order)
            base = session.audio_base_ms

        speaker = event.speaker_label or self._infer_speaker(event) or UNKNOWN_SPEAKER
        start_ms, end_ms = self._turn_bounds(event, base)

        latency: float | None = None
        if not event.turn_is_formatted:
            # The unformatted final is the honest moment the words existed.
            latency = self.clock.latency_ms(end_ms)
            if latency is not None:
                self.metrics.record(ASR, latency)
            with self._lock:
                self.revisions.note_turn(key, speaker)
                self.turns_seen += 1
        else:
            first = self.revisions.first_seen(key)
            if first is not None:
                self.metrics.record(FORMAT, max(0.0, now_ms() - first))
            with self._lock:
                # Diarization can also settle between the two finals.
                self.revisions.note_turn(key, speaker)

        turn = FinalTurn(
            connection_epoch=key.connection_epoch,
            turn_order=key.turn_order,
            speaker_label=speaker,
            text=event.transcript,
            start_ms=start_ms,
            end_ms=end_ms,
            is_formatted=event.turn_is_formatted,
        )
        self.on_turn(turn, latency)

        with self._lock:
            completed = self.buffer.add(turn)
        for utterance in completed:
            self.on_utterance(utterance)

    def _handle_revision(
        self, _client: StreamingClient, event: SpeakerRevisionEvent
    ) -> None:
        for item in event.revisions:
            with self._lock:
                # A revision always refers to a turn on the connection that
                # sent it, so it takes the current epoch.
                key = TurnKey(self._session.epoch, item.turn_order)
                record = self.revisions.note_revision(key, item.speaker_label)
                amendment = self.buffer.apply_revision(key, item.speaker_label)
            if amendment is not None:
                record.text = amendment.utterance.text
                record.partial = amendment.partial
                self.on_amendment(amendment, record.delay_ms)

    @staticmethod
    def _infer_speaker(event: TurnEvent) -> str | None:
        """Fall back to word-level speakers when the turn carries no label."""
        for word in reversed(event.words or []):
            if getattr(word, "speaker", None):
                return str(word.speaker)
        return None

    @staticmethod
    def _turn_bounds(event: TurnEvent, base_ms: int) -> tuple[int, int]:
        words = event.words or []
        if not words:
            return base_ms, base_ms
        return base_ms + int(words[0].start), base_ms + int(words[-1].end)

    # --- main loop ---------------------------------------------------------

    def run(self) -> None:
        self._connect()
        try:
            self._pump()
        finally:
            self.close()
            with self._lock:
                trailing = self.buffer.flush()
            for utterance in trailing:
                self.on_utterance(utterance)

    def _pump(self) -> None:
        for chunk in self.chunks:
            if self._session_dead() and not self._closing:
                if not self._reconnect():
                    return

            with self._lock:
                session = self._session
                if not session.seen_first_chunk:
                    # Session-local audio time starts at this chunk's start.
                    session.audio_base_ms = chunk.end_ms - chunk.duration_ms
                    session.seen_first_chunk = True

            client = self._client
            if client is None:
                return
            client.stream(chunk.pcm)
            self.clock.record_chunk(chunk.end_ms)

            with self._lock:
                idle = self.buffer.tick(chunk.end_ms)
            for utterance in idle:
                self.on_utterance(utterance)


# --- CLI -------------------------------------------------------------------

DIM = "\033[2m"
BOLD = "\033[1m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _supports_colour() -> bool:
    return sys.stdout.isatty()


class _Printer:
    """Terminal output for the spike. Plain text when piped to a file."""

    def __init__(self, *, colour: bool, show_turns: bool) -> None:
        self.colour = colour
        self.show_turns = show_turns

    def _c(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self.colour else text

    @staticmethod
    def clock(ms: int) -> str:
        total = max(0, ms) / 1000.0
        return f"{int(total // 60):02d}:{total % 60:06.3f}"

    def utterance(self, u: Utterance) -> None:
        turns = (
            f"turn {u.turn_key}"
            if len(u.turn_keys) == 1
            else f"turns {u.turn_keys[0]}-{u.turn_keys[-1]}"
        )
        head = f"[{self.clock(u.start_ms)}] {u.display_speaker}:"
        flag = self._c("  (amended)", YELLOW) if u.amended else ""
        print(f"{self._c(head, BOLD)} {u.text}{flag}")
        print(self._c(f"           {turns} | {u.duration_ms} ms", DIM))

    def turn(self, t: FinalTurn, latency: float | None) -> None:
        if not self.show_turns:
            return
        phase = "formatted" if t.is_formatted else "raw"
        lat = f" | asr {latency:.0f} ms" if latency is not None else ""
        line = f"    · turn {t.key} {phase} [{t.speaker_label}]{lat}"
        print(self._c(line, DIM))

    def amendment(self, a: Amendment, delay_ms: float) -> None:
        if not a.changed:
            return
        arrow = f"{a.previous_label} -> {a.new_label}"
        note = "  PARTIAL: utterance spans several turns" if a.partial else ""
        print(
            self._c(
                f"  ~ speaker revised on turn {a.turn_key}: {arrow} "
                f"(+{delay_ms:.0f} ms after the turn){note}",
                YELLOW,
            )
        )
        print(self._c(f"    was quoted as: {a.utterance.text[:70]}", DIM))

    def status(self, message: str) -> None:
        print(self._c(f"  [{message}]", CYAN))


def _print_report(stream: TranscriptionStream, printer: _Printer) -> None:
    m = stream.metrics
    log = stream.revisions

    print()
    print(printer._c("=" * 68, DIM))
    print(printer._c("LATENCY", BOLD))
    print(f"  {m.summary(ASR)}")
    print(f"  {m.summary(FORMAT)}")
    print(
        printer._c(
            "  asr    = audio chunk sent -> unformatted final received\n"
            "  format = unformatted final -> formatted final, same turn",
            DIM,
        )
    )

    print()
    print(printer._c("SPEAKER REVISIONS", BOLD))
    print(f"  {log.summary()}")
    changed = log.changed
    if changed:
        print(printer._c("  each revision, and how late it arrived:", DIM))
        for rev in changed:
            partial = "  [partial]" if rev.partial else ""
            quote = f'  "{rev.text[:44]}"' if rev.text else ""
            print(
                f"    turn {str(rev.turn_key):>8}  "
                f"{rev.previous_label} -> {rev.new_label}  "
                f"+{rev.delay_ms:>7.0f} ms{partial}{quote}"
            )
        print()
        print(
            printer._c(
                "  Read this as: how long a wrong name was on screen before it\n"
                "  was corrected. That is the number that decides Sep 6.",
                DIM,
            )
        )
    elif log.total:
        print(printer._c("  revisions arrived but none changed a label.", DIM))
    else:
        print(
            printer._c(
                "  none received. Either diarization was confident throughout,\n"
                "  or the audio has too few distinct voices to revise.",
                DIM,
            )
        )

    print()
    print(printer._c("STREAM", BOLD))
    print(f"  turns: {stream.turns_seen}   utterances: {len(stream.buffer.emitted)}")
    print(f"  audio sent: {stream.clock.audio_sent_ms / 1000:.1f}s")
    print(f"  reconnects: {stream.reconnects}   "
          f"connection epochs used: {stream.reconnects + 1}")
    print(
        printer._c(
            f"  unformatted finals not extracted from: "
            f"{stream.buffer.dropped_unformatted}",
            DIM,
        )
    )
    print(printer._c("=" * 68, DIM))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.transcription.stream",
        description="Stream audio to AssemblyAI and print speaker-labelled "
                    "utterances, latency, and speaker revisions as they arrive.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="audio file, replayed in real time")
    src.add_argument("--mic", action="store_true", help="capture the microphone")

    p.add_argument("--device", default=":0", help="mic device for ffmpeg (default :0)")
    p.add_argument("--fast", action="store_true",
                   help="replay as fast as possible. Latency numbers become "
                        "meaningless; use only to check transcript quality.")
    p.add_argument("--max-speakers", type=int, default=None,
                   help="1-10. Helps when you know the headcount on the bridge.")
    p.add_argument("--no-diarization", action="store_true",
                   help="disable speaker labels")
    p.add_argument("--pause-ms", type=int, default=PAUSE_MS,
                   help=f"silence that ends an utterance (default {PAUSE_MS})")
    p.add_argument("--show-turns", action="store_true",
                   help="print every raw turn, not just assembled utterances")
    p.add_argument("--no-colour", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.max_speakers is not None and not 1 <= args.max_speakers <= 10:
        print("--max-speakers must be between 1 and 10", file=sys.stderr)
        return 2

    printer = _Printer(
        colour=_supports_colour() and not args.no_colour,
        show_turns=args.show_turns,
    )

    try:
        if args.mic:
            chunks = audio.mic_source(args.device)
            source_desc = f"microphone ({args.device})"
        else:
            chunks = audio.file_source(args.file, realtime=not args.fast)
            duration = audio.probe_duration_ms(args.file)
            length = f", {duration / 1000:.1f}s" if duration else ""
            pace = "as fast as possible" if args.fast else "real time"
            source_desc = f"{args.file.name}{length}, replayed at {pace}"

        stream_config = StreamConfig(
            speaker_labels=not args.no_diarization,
            max_speakers=args.max_speakers,
            pause_ms=args.pause_ms,
        )
        stream = TranscriptionStream(
            chunks,
            stream_config,
            on_utterance=printer.utterance,
            on_amendment=printer.amendment,
            on_turn=printer.turn,
            on_status=printer.status,
        )
    except (ConfigError, AudioError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    diar = "on" if stream_config.speaker_labels else "off"
    cap = f", max {args.max_speakers} speakers" if args.max_speakers else ""
    print(printer._c(f"source: {source_desc}", DIM))
    print(printer._c(f"diarization: {diar}{cap}", DIM))
    if args.fast:
        print(printer._c("latency numbers are not meaningful with --fast", YELLOW))
    print()

    interrupted = False
    try:
        stream.run()
    except KeyboardInterrupt:
        interrupted = True
        print(printer._c("\n  [interrupted]", CYAN))
    except (ConfigError, AudioError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    except StreamingError as exc:
        print(f"\nstreaming failed: {exc}\n", file=sys.stderr)
        _print_report(stream, printer)
        return 1

    _print_report(stream, printer)

    if stream.failed:
        print(f"\nstream ended in an error: {stream.last_error}\n", file=sys.stderr)
        return 1
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
