"""Audio sources: a WAV file replayed at real-time speed, or the microphone.

Both produce the same thing — 50 ms frames of 16 kHz mono 16-bit PCM, tagged
with their position in the audio. Everything downstream is written against
that and does not care where the bytes came from.

ffmpeg does the decoding and the microphone capture. That keeps arbitrary
input formats and live capture working without a native Python audio binding,
at the cost of one binary on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from backend.config import (
    BYTES_PER_MS,
    CHANNELS,
    CHUNK_BYTES,
    CHUNK_MS,
    SAMPLE_RATE,
)


class AudioError(RuntimeError):
    """Raised when audio cannot be read, decoded, or captured."""


@dataclass(frozen=True)
class Chunk:
    """One frame of PCM, and where it sits in the audio."""

    pcm: bytes
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return len(self.pcm) // BYTES_PER_MS


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _require_ffmpeg(reason: str) -> None:
    if not have_ffmpeg():
        raise AudioError(
            f"ffmpeg is required {reason} but was not found on PATH.\n"
            "  brew install ffmpeg"
        )


def _is_already_normalized(path: Path) -> bool:
    """True if the file is already exactly what the API wants.

    Lets a correctly-formatted WAV replay with no ffmpeg installed at all.
    """
    try:
        with wave.open(str(path), "rb") as w:
            return (
                w.getnchannels() == CHANNELS
                and w.getsampwidth() == 2
                and w.getframerate() == SAMPLE_RATE
                and w.getcomptype() == "NONE"
            )
    except (wave.Error, OSError, EOFError):
        return False


def probe_duration_ms(path: Path) -> int | None:
    """Duration in ms, best effort. Used only for progress reporting."""
    try:
        with wave.open(str(path), "rb") as w:
            return int(1000 * w.getnframes() / w.getframerate())
    except (wave.Error, OSError, EOFError, ZeroDivisionError):
        pass
    if not have_ffmpeg():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return int(float(out.stdout.strip()) * 1000)
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None


def _raw_pcm_from_file(path: Path) -> Iterator[bytes]:
    """Yield raw PCM blocks decoded from any audio file ffmpeg understands."""
    if _is_already_normalized(path):
        with wave.open(str(path), "rb") as w:
            while True:
                frames = w.readframes(CHUNK_BYTES // 2)
                if not frames:
                    return
                yield frames
        return

    _require_ffmpeg(f"to decode {path.name} (not already 16 kHz mono PCM)")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        while True:
            block = proc.stdout.read(CHUNK_BYTES)
            if not block:
                break
            yield block
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        rc = proc.wait()
        if rc not in (0, None) and stderr.strip():
            raise AudioError(f"ffmpeg failed decoding {path.name}: {stderr.strip()}")


def normalize_upload_to_wav(
    src: Path,
    dest: Path,
    *,
    max_duration_ms: int,
) -> int:
    """Transcode any ffmpeg-readable audio to 16 kHz mono 16-bit PCM WAV.

    Returns duration_ms of the written file. Raises AudioError on failure /
    over-duration. `dest` parent must exist.
    """
    if _is_already_normalized(src):
        duration = probe_duration_ms(src)
        if duration is None:
            raise AudioError("could not probe audio duration")
        if duration > max_duration_ms:
            raise AudioError(
                f"audio too long ({duration / 1000:.0f}s); "
                f"max is {max_duration_ms / 1000:.0f}s"
            )
        dest.write_bytes(src.read_bytes())
        return duration

    _require_ffmpeg("to normalise uploaded audio")
    # Probe first so we can reject long files before a huge transcode.
    probed = probe_duration_ms(src)
    if probed is not None and probed > max_duration_ms:
        raise AudioError(
            f"audio too long ({probed / 1000:.0f}s); "
            f"max is {max_duration_ms / 1000:.0f}s"
        )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg timed out normalising upload") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown ffmpeg error").strip()
        raise AudioError(f"not a usable audio file: {err[:240]}")
    if not dest.is_file() or dest.stat().st_size < 44:
        raise AudioError("normalisation produced an empty file")
    duration = probe_duration_ms(dest)
    if duration is None:
        raise AudioError("could not probe normalised duration")
    if duration > max_duration_ms:
        dest.unlink(missing_ok=True)
        raise AudioError(
            f"audio too long ({duration / 1000:.0f}s); "
            f"max is {max_duration_ms / 1000:.0f}s"
        )
    return duration


def _raw_pcm_from_mic(device: str) -> Iterator[bytes]:
    """Yield raw PCM blocks captured live from the system microphone."""
    _require_ffmpeg("to capture the microphone")
    if sys.platform == "darwin":
        capture = ["-f", "avfoundation", "-i", device]
    elif sys.platform.startswith("linux"):
        capture = ["-f", "pulse", "-i", device if device != ":0" else "default"]
    else:
        raise AudioError(f"Microphone capture is not wired up for {sys.platform}.")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        *capture,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        while True:
            block = proc.stdout.read(CHUNK_BYTES)
            if not block:
                stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                if stderr.strip():
                    raise AudioError(f"microphone capture failed: {stderr.strip()}")
                break
            yield block
    finally:
        proc.stdout.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _chunked(blocks: Iterator[bytes]) -> Iterator[Chunk]:
    """Re-slice arbitrary blocks into exact CHUNK_BYTES frames.

    A short final frame is emitted as-is rather than zero-padded: padding would
    invent audio, and the API is happy with a short last frame.
    """
    buf = bytearray()
    position = 0
    for block in blocks:
        buf.extend(block)
        while len(buf) >= CHUNK_BYTES:
            frame = bytes(buf[:CHUNK_BYTES])
            del buf[:CHUNK_BYTES]
            position += len(frame) // BYTES_PER_MS
            yield Chunk(pcm=frame, end_ms=position)
    if buf:
        frame = bytes(buf)
        position += max(1, len(frame) // BYTES_PER_MS)
        yield Chunk(pcm=frame, end_ms=position)


def _paced(chunks: Iterator[Chunk], realtime: bool) -> Iterator[Chunk]:
    """Release chunks no faster than the audio would actually play.

    Without this, a 10-minute WAV floods the socket in seconds and every
    latency number is fiction.
    """
    if not realtime:
        yield from chunks
        return
    started = time.monotonic()
    for chunk in chunks:
        due = started + (chunk.end_ms / 1000.0)
        drift = due - time.monotonic()
        if drift > 0:
            time.sleep(drift)
        yield chunk


def file_source(path: Path, *, realtime: bool = True) -> Iterator[Chunk]:
    """Replay an audio file. Real-time paced unless told otherwise."""
    if not path.exists():
        raise AudioError(f"No such audio file: {path}")
    return _paced(_chunked(_raw_pcm_from_file(path)), realtime)


class ControllableFileSource:
    """File replay with pause/resume, start offset, and a live position.

    Pause blocks the feeder without closing the ASR socket — extraction and
    WebSocket clients keep running. `stop()` unblocks and ends iteration so
    the pipeline thread can exit cleanly on seek/restart.
    """

    def __init__(
        self,
        path: Path,
        *,
        start_ms: int = 0,
        realtime: bool = True,
    ) -> None:
        if not path.exists():
            raise AudioError(f"No such audio file: {path}")
        self.path = path
        self.start_ms = max(0, int(start_ms))
        self.realtime = realtime
        self.position_ms = self.start_ms
        self._gate = threading.Event()
        self._gate.set()
        self._stop = threading.Event()
        # Set from another thread (e.g. ASR reconnect) so the next chunk is
        # due "now" instead of catching up wall time lost during a stall.
        self._reset_pacing = threading.Event()

    def pause(self) -> None:
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    @property
    def paused(self) -> bool:
        return not self._gate.is_set() and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        self._gate.set()

    def reset_pacing(self) -> None:
        """Restart the realtime wall clock from the current audio position.

        Safe to call from another thread. Without this, a stalled pump that
        later reconnects would release minutes of catch-up audio in a burst
        and flood the new WebSocket.
        """
        self._reset_pacing.set()

    def _reanchor_wall0(self, chunk: Chunk) -> float:
        """Wall clock such that `chunk` is due immediately."""
        return time.monotonic() - ((chunk.end_ms - self.start_ms) / 1000.0)

    def __iter__(self) -> Iterator[Chunk]:
        wall0 = time.monotonic()
        for chunk in _chunked(_raw_pcm_from_file(self.path)):
            if self._stop.is_set():
                return
            if chunk.end_ms <= self.start_ms:
                continue
            while not self._gate.wait(0.05):
                if self._stop.is_set():
                    return
            if self._stop.is_set():
                return
            if self._reset_pacing.is_set():
                self._reset_pacing.clear()
                wall0 = self._reanchor_wall0(chunk)
            if self.realtime:
                due = wall0 + (chunk.end_ms - self.start_ms) / 1000.0
                drift = due - time.monotonic()
                if drift > 0:
                    # Sleep in slices so pause/stop/reset_pacing can interrupt.
                    end = time.monotonic() + drift
                    while time.monotonic() < end:
                        if self._stop.is_set():
                            return
                        if self._reset_pacing.is_set():
                            self._reset_pacing.clear()
                            wall0 = self._reanchor_wall0(chunk)
                            break
                        if not self._gate.wait(0.05):
                            # Paused mid-wait: freeze pacing until resume.
                            while not self._gate.wait(0.05):
                                if self._stop.is_set():
                                    return
                            # After resume, don't try to catch up the pause.
                            wall0 = self._reanchor_wall0(chunk)
                            break
                        remaining = end - time.monotonic()
                        if remaining > 0:
                            time.sleep(min(0.05, remaining))
            self.position_ms = chunk.end_ms
            yield chunk


def mic_source(device: str = ":0") -> Iterator[Chunk]:
    """Capture the microphone. Already real-time by nature; no pacing."""
    return _chunked(_raw_pcm_from_mic(device))
