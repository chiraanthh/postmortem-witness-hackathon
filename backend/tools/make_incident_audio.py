"""Synthesises a scripted incident bridge call into one mixed-down WAV.

    python -m backend.tools.make_incident_audio --script demo/script/incident_01.yaml

What this is for: the AMI recording proved the pipeline runs, but it is a
meeting about remote controls, so it exercises nothing the extractor cares
about. This builds a call with known content and, crucially, known speaker
boundaries, so diarization and extraction can both be scored instead of
eyeballed.

Three things here are deliberate and easy to get wrong:

- **Mixed down, never concatenated.** Every line is placed on a single
  timeline at its `start_ms` and summed. Concatenating would silently delete
  the crosstalk, which is the hardest thing for diarization and therefore the
  most interesting thing in the file.
- **Collisions are reported, never repaired.** A line flagged `overlap: true`
  is meant to collide. Any other collision is a bug in the script's timing,
  and the fix belongs in the script - so it is warned about loudly and left
  exactly where it is. Auto-shifting would make `start_ms` a lie and the
  ground truth worthless.
- **`end_ms` is measured, not predicted.** TTS duration is not knowable ahead
  of time, so ground truth records the real synthesised length.

Sarvam Bulbul v3 API details verified against the live docs on 2026-09-15
(https://docs.sarvam.ai/api-reference/text-to-speech/convert):

- `POST https://api.sarvam.ai/text-to-speech`, auth in the
  `api-subscription-key` header - NOT `Authorization: Bearer`.
- The request field is **`language_code`**, not `target_language_code`. The
  latter is the older name and now survives only as the Python SDK's
  WebSocket `configure()` argument.
- Response is JSON with base64-encoded WAV strings in `audios`, one per input
  text. It is not raw audio bytes.
- `pace` is 0.5-2.0 on v3. `pitch`, `loudness` and `enable_preprocessing` are
  v2-only and return 400 on v3, so they are never sent.
- `speech_sample_rate` accepts 16000, which is exactly what the streaming ASR
  wants, so nothing needs resampling.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.signal import butter, sosfilt

from backend import config
from backend.config import ConfigError

ENDPOINT = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v3"

# Every speaker the current docs list as bulbul:v3-compatible. The v2 voices
# (anushka, abhilash, arya, ...) are rejected by v3, and the resulting 400 is
# much clearer caught here against a named voice than read off an HTTP body.
V3_SPEAKERS = {
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait",
    "anand", "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti",
    "suhani", "mohit", "kavitha", "rehan", "soham", "rupali",
}

PACE_MIN, PACE_MAX = 0.5, 2.0
MAX_CHARS = 2500

# Call-audio colouring. Deliberately gentle: the point is to stop the file
# sounding like a studio booth, not to simulate a bad line. A narrow or steep
# band-pass would degrade ASR and we would be measuring the filter.
BAND_LOW_HZ = 250.0
BAND_HIGH_HZ = 3600.0
BAND_ORDER = 2
NOISE_DBFS = -50.0
PEAK_DBFS = -3.0

TAIL_MS = 1500  # trailing silence, so the last turn gets its end-of-turn


class SynthError(RuntimeError):
    """Raised when synthesis cannot produce usable audio."""


@dataclass
class Line:
    index: int
    speaker: str
    speaker_name: str
    voice: str
    pace: float
    start_ms: int
    text: str
    overlap: bool
    pcm: np.ndarray | None = None       # int16, mono, at the script's rate
    duration_ms: int = 0
    cached: bool = False

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


@dataclass
class Collision:
    """Two lines whose audio overlaps in time."""

    later: int
    earlier: int
    overlap_ms: int
    expected: bool
    adjacent: bool  # earlier is the immediately preceding line
    detail: str = ""


@dataclass
class Script:
    meta: dict[str, Any]
    speakers: dict[str, dict[str, Any]]
    lines: list[Line] = field(default_factory=list)

    @property
    def sample_rate(self) -> int:
        return int(self.meta.get("sample_rate", config.SAMPLE_RATE))

    @property
    def language_code(self) -> str:
        return str(self.meta.get("language", "en-IN"))


# --- script loading --------------------------------------------------------

def load_script(path: Path) -> Script:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        raise SynthError(f"{path} is empty")
    for key in ("speakers", "lines"):
        if key not in raw:
            raise SynthError(f"{path} has no '{key}' section")

    speakers = raw["speakers"]
    lines: list[Line] = []
    for i, entry in enumerate(raw["lines"]):
        label = entry["speaker"]
        if label not in speakers:
            raise SynthError(f"line {i}: speaker {label!r} is not in 'speakers'")
        spk = speakers[label]
        voice = str(spk["voice"])
        if voice not in V3_SPEAKERS:
            raise SynthError(
                f"line {i}: voice {voice!r} for speaker {label} is not a "
                f"{MODEL} voice. The v2 voices are not accepted by v3."
            )
        pace = float(entry.get("pace", spk.get("pace", 1.0)))
        if not PACE_MIN <= pace <= PACE_MAX:
            raise SynthError(
                f"line {i}: pace {pace} is outside the {MODEL} range "
                f"{PACE_MIN}-{PACE_MAX}"
            )
        text = str(entry["text"]).strip()
        if not text:
            raise SynthError(f"line {i}: empty text")
        if len(text) > MAX_CHARS:
            raise SynthError(f"line {i}: {len(text)} chars exceeds the {MAX_CHARS} limit")

        lines.append(Line(
            index=i,
            speaker=label,
            speaker_name=str(spk.get("name", label)),
            voice=voice,
            pace=pace,
            start_ms=int(entry["start_ms"]),
            text=text,
            overlap=bool(entry.get("overlap", False)),
        ))

    out_of_order = [
        l.index for l, nxt in zip(lines, lines[1:]) if nxt.start_ms < l.start_ms
    ]
    if out_of_order:
        raise SynthError(
            "lines are not in ascending start_ms order; offending indices: "
            f"{out_of_order}. Collision detection assumes script order is "
            "timeline order."
        )
    return Script(meta=raw.get("meta", {}) or {}, speakers=speakers, lines=lines)


# --- TTS -------------------------------------------------------------------

class BulbulTTS:
    """Bulbul v3 over REST, with an on-disk cache so re-runs do not re-bill."""

    def __init__(
        self,
        api_key: str,
        *,
        language_code: str,
        sample_rate: int,
        cache_dir: Path,
        timeout_s: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self.language_code = language_code
        self.sample_rate = sample_rate
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.requests = 0
        self.cache_hits = 0

    def cache_key(self, text: str, voice: str, pace: float) -> str:
        """Hash of everything that changes the audio.

        text + voice + pace as asked, plus model, language and sample rate:
        those also change the bytes, and a stale hit after switching model
        would be indistinguishable from a fresh synthesis.
        """
        payload = json.dumps(
            {
                "text": text,
                "voice": voice,
                "pace": round(pace, 4),
                "model": MODEL,
                "language_code": self.language_code,
                "sample_rate": self.sample_rate,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def synthesize(self, text: str, voice: str, pace: float) -> tuple[np.ndarray, bool]:
        key = self.cache_key(text, voice, pace)
        wav_path = self.cache_dir / f"{key}.wav"
        if wav_path.exists():
            self.cache_hits += 1
            return self._decode_wav(wav_path.read_bytes()), True

        wav_bytes = self._request(text, voice, pace)
        pcm = self._decode_wav(wav_bytes)  # validate before caching
        wav_path.write_bytes(wav_bytes)
        (self.cache_dir / f"{key}.json").write_text(json.dumps({
            "text": text,
            "voice": voice,
            "pace": pace,
            "model": MODEL,
            "language_code": self.language_code,
            "sample_rate": self.sample_rate,
            "samples": int(pcm.size),
        }, indent=2, ensure_ascii=False))
        return pcm, False

    def _request(self, text: str, voice: str, pace: float) -> bytes:
        body = json.dumps({
            "text": text,
            "language_code": self.language_code,
            "model": MODEL,
            "speaker": voice,
            "pace": pace,
            "speech_sample_rate": self.sample_rate,
        }).encode("utf-8")

        last: str | None = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(
                ENDPOINT,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    # Header auth, not Bearer. The key is never logged.
                    "api-subscription-key": self._api_key,
                },
            )
            try:
                self.requests += 1
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                audios = payload.get("audios") or []
                if not audios:
                    raise SynthError(f"response carried no audio: {payload!r}")
                return base64.b64decode(audios[0])
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last = f"HTTP {exc.code}: {detail}"
                # 4xx other than 429 will fail identically on retry.
                if exc.code != 429 and 400 <= exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            except json.JSONDecodeError as exc:
                last = f"response was not JSON: {exc}"

            if attempt < self.max_retries:
                time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))

        raise SynthError(f"synthesis failed for voice {voice!r}: {last}")

    def _decode_wav(self, wav_bytes: bytes) -> np.ndarray:
        """Base64-decoded WAV -> int16 mono at self.sample_rate."""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as w:
                channels = w.getnchannels()
                width = w.getsampwidth()
                rate = w.getframerate()
                frames = w.readframes(w.getnframes())
        except (wave.Error, EOFError) as exc:
            raise SynthError(f"returned audio is not a readable WAV: {exc}") from exc

        if width != 2:
            raise SynthError(f"expected 16-bit audio, got {width * 8}-bit")
        pcm = np.frombuffer(frames, dtype="<i2")
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if rate != self.sample_rate:
            # Should not happen - we ask for the rate we want - but a silent
            # rate mismatch would shift every timestamp in the ground truth.
            raise SynthError(
                f"asked for {self.sample_rate} Hz, got {rate} Hz. "
                "Ground-truth timings would be wrong."
            )
        return np.ascontiguousarray(pcm)


# --- timeline --------------------------------------------------------------

def find_collisions(lines: list[Line]) -> list[Collision]:
    """Every pair of lines whose synthesised audio overlaps.

    Checks each line against the immediately preceding one (the case the
    script's `overlap` flag is about) and also against any earlier line still
    playing, which a flag on the adjacent pair would not describe.
    """
    collisions: list[Collision] = []
    for i, line in enumerate(lines):
        for j in range(i - 1, -1, -1):
            earlier = lines[j]
            if earlier.end_ms <= line.start_ms:
                continue
            adjacent = j == i - 1
            collisions.append(Collision(
                later=line.index,
                earlier=earlier.index,
                overlap_ms=earlier.end_ms - line.start_ms,
                # The flag is a property of the line - "this one is meant to
                # talk over someone" - not of a particular pair. A flagged
                # line that lands on top of an older line still playing is
                # doing what it was asked to.
                expected=line.overlap,
                adjacent=adjacent,
            ))
    return collisions


def find_missed_overlaps(lines: list[Line], collisions: list[Collision]) -> list[Line]:
    """Lines flagged `overlap: true` that ended up colliding with nothing.

    The script's start_ms assumes a speaking rate; when the synthesised line
    comes back shorter than that, the intended crosstalk silently evaporates
    and the file is easier than the script describes. Not an error, but it has
    to be visible or the test is weaker than it looks.
    """
    collided = {c.later for c in collisions}
    return [l for l in lines if l.overlap and l.index not in collided]


def report_collisions(collisions: list[Collision], lines: list[Line]) -> int:
    """Print the collision report. Returns the number of unexpected ones."""
    by_index = {l.index: l for l in lines}
    expected = [c for c in collisions if c.expected]
    unexpected = [c for c in collisions if not c.expected]

    print(f"\ncollisions: {len(collisions)} total, "
          f"{len(expected)} flagged overlap:true, {len(unexpected)} NOT flagged")
    for c in expected:
        later = by_index[c.later]
        print(f"  ok   line {c.later:>2} ({later.speaker}) overlaps line "
              f"{c.earlier} by {c.overlap_ms} ms  [flagged]")

    missed = find_missed_overlaps(lines, collisions)
    if missed:
        print(f"\n  note: {len(missed)} line(s) flagged overlap:true did NOT "
              f"collide - the synthesised audio was shorter than start_ms assumed.")
        print("  The intended crosstalk is not in the file. Nothing shifted.")
        for l in missed:
            prev = by_index.get(l.index - 1)
            gap = l.start_ms - prev.end_ms if prev else 0
            print(f"    line {l.index:>2} ({l.speaker} @ {l.start_ms} ms) starts "
                  f"{gap} ms AFTER line {l.index - 1} ends  {l.text[:40]!r}")

    if not unexpected:
        return 0

    print()
    print("!" * 72)
    print(f"!! {len(unexpected)} UNFLAGGED COLLISION(S). The synthesised audio is")
    print("!! longer than the script's timing assumed. Nothing has been shifted:")
    print("!! fix start_ms in the script, or add `overlap: true` if it is wanted.")
    print("!" * 72)
    for c in unexpected:
        later, earlier = by_index[c.later], by_index[c.earlier]
        kind = "previous line" if c.adjacent else f"line {c.earlier}, still playing"
        print(f"  !! line {c.later:>2} ({later.speaker} @ {later.start_ms} ms) collides "
              f"with {kind} by {c.overlap_ms} ms")
        print(f"       earlier: {earlier.speaker} [{earlier.start_ms}-{earlier.end_ms}] "
              f"{earlier.text[:58]!r}")
        print(f"       later:   {later.speaker} [{later.start_ms}-{later.end_ms}] "
              f"{later.text[:58]!r}")
    print("!" * 72)
    return len(unexpected)


def mix_down(lines: list[Line], sample_rate: int) -> np.ndarray:
    """Place every line at its start_ms on one timeline and sum."""
    total_ms = max((l.end_ms for l in lines), default=0) + TAIL_MS
    n = int(total_ms * sample_rate / 1000)
    mix = np.zeros(n, dtype=np.float32)
    for line in lines:
        if line.pcm is None or line.pcm.size == 0:
            continue
        start = int(line.start_ms * sample_rate / 1000)
        seg = line.pcm.astype(np.float32) / 32768.0
        end = min(start + seg.size, n)
        mix[start:end] += seg[: end - start]
    return mix


def apply_call_colour(
    mix: np.ndarray, sample_rate: int, *, noise_dbfs: float = NOISE_DBFS
) -> np.ndarray:
    """Light band-pass plus a low noise bed, to suggest a phone bridge."""
    nyquist = sample_rate / 2.0
    sos = butter(
        BAND_ORDER,
        [BAND_LOW_HZ / nyquist, min(BAND_HIGH_HZ / nyquist, 0.99)],
        btype="bandpass",
        output="sos",
    )
    voiced = sosfilt(sos, mix).astype(np.float32)

    peak = float(np.max(np.abs(voiced))) or 1.0
    voiced *= (10.0 ** (PEAK_DBFS / 20.0)) / peak

    # Noise bed through the same band, so it sits behind the voices instead of
    # adding hiss on top of them.
    rng = np.random.default_rng(20260915)
    noise = rng.standard_normal(voiced.size).astype(np.float32)
    noise = sosfilt(sos, noise).astype(np.float32)
    rms = float(np.sqrt(np.mean(noise ** 2))) or 1.0
    noise *= (10.0 ** (noise_dbfs / 20.0)) / rms

    return np.clip(voiced + noise, -1.0, 1.0)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.round(samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


# --- CLI -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.tools.make_incident_audio",
        description="Synthesise a scripted incident call into one mixed WAV "
                    "plus a ground-truth JSON.",
    )
    p.add_argument("--script", type=Path, default=Path("demo/script/incident_01.yaml"))
    p.add_argument("--out", type=Path, default=Path("demo/audio/incident_01.wav"))
    p.add_argument("--groundtruth", type=Path, default=None,
                   help="defaults to <out> with a .groundtruth.json suffix")
    p.add_argument("--cache-dir", type=Path,
                   default=Path("demo/audio/.tts_cache"))
    p.add_argument("--dry-run", action="store_true",
                   help="parse and price the run without calling the API")
    p.add_argument("--no-colour", action="store_true",
                   help="skip the band-pass and noise bed")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        script = load_script(args.script)
    except (SynthError, KeyError, ValueError, yaml.YAMLError) as exc:
        print(f"script error: {exc}", file=sys.stderr)
        return 1

    rate = script.sample_rate
    print(f"script: {args.script}  ({len(script.lines)} lines, "
          f"{len(script.speakers)} speakers, {script.language_code}, {rate} Hz)")
    for label, spk in script.speakers.items():
        n = sum(1 for l in script.lines if l.speaker == label)
        print(f"  {label} {spk.get('name', label):<8} voice={spk['voice']:<8} "
              f"pace={spk.get('pace', 1.0)}  {n} lines")

    if args.dry_run:
        print(f"\ndry run: {len(script.lines)} requests would be made "
              f"(minus cache hits)")
        return 0

    try:
        tts = BulbulTTS(
            config.sarvam_api_key(),
            language_code=script.language_code,
            sample_rate=rate,
            cache_dir=args.cache_dir,
        )
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"\nsynthesising with {MODEL} (cache: {args.cache_dir})")
    for line in script.lines:
        try:
            pcm, cached = tts.synthesize(line.text, line.voice, line.pace)
        except SynthError as exc:
            print(f"\nline {line.index}: {exc}\n", file=sys.stderr)
            return 1
        line.pcm = pcm
        line.duration_ms = int(round(1000 * pcm.size / rate))
        line.cached = cached
        tag = "cache" if cached else "  api"
        print(f"  [{tag}] line {line.index:>2} {line.speaker} {line.voice:<8} "
              f"{line.start_ms:>6}-{line.end_ms:<6} ms  ({line.duration_ms:>5} ms)  "
              f"{line.text[:44]!r}")

    print(f"\n{tts.requests} API request(s), {tts.cache_hits} cache hit(s)")

    collisions = find_collisions(script.lines)
    unexpected = report_collisions(collisions, script.lines)

    mix = mix_down(script.lines, rate)
    if not args.no_colour:
        mix = apply_call_colour(mix, rate)
    write_wav(args.out, mix, rate)
    duration_ms = int(round(1000 * mix.size / rate))

    gt_path = args.groundtruth or args.out.with_suffix(".groundtruth.json")
    gt = {
        "incident_id": script.meta.get("incident_id"),
        "audio_file": str(args.out),
        "script_file": str(args.script),
        "sample_rate": rate,
        "channels": 1,
        "duration_ms": duration_ms,
        "synthesis": {
            "model": MODEL,
            "language_code": script.language_code,
            "band_pass_hz": None if args.no_colour else [BAND_LOW_HZ, BAND_HIGH_HZ],
            "noise_bed_dbfs": None if args.no_colour else NOISE_DBFS,
        },
        "speakers": {
            label: {
                "speaker_name": spk.get("name", label),
                "role": spk.get("role"),
                "voice": spk.get("voice"),
                "pace": spk.get("pace", 1.0),
            }
            for label, spk in script.speakers.items()
        },
        "collisions": [
            {
                "later_line": c.later,
                "earlier_line": c.earlier,
                "overlap_ms": c.overlap_ms,
                "flagged": c.expected,
                "adjacent": c.adjacent,
            }
            for c in collisions
        ],
        "flagged_but_no_collision": [
            l.index for l in find_missed_overlaps(script.lines, collisions)
        ],
        "lines": [
            {
                "index": l.index,
                "speaker": l.speaker,
                "speaker_name": l.speaker_name,
                "start_ms": l.start_ms,
                "end_ms": l.end_ms,
                "duration_ms": l.duration_ms,
                "overlap": l.overlap,
                "voice": l.voice,
                "pace": l.pace,
                "text": l.text,
            }
            for l in script.lines
        ],
    }
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps(gt, indent=2, ensure_ascii=False))

    speech_ms = sum(l.duration_ms for l in script.lines)
    print(f"\nwrote {args.out}  ({duration_ms / 1000:.1f}s, {rate} Hz mono 16-bit)")
    print(f"wrote {gt_path}  ({len(script.lines)} lines)")
    print(f"speech {speech_ms / 1000:.1f}s over {duration_ms / 1000:.1f}s "
          f"({100 * speech_ms / max(1, duration_ms):.0f}% density)")
    target = script.meta.get("target_duration_ms")
    if target:
        print(f"target_duration_ms {target} -> actual {duration_ms} "
              f"({duration_ms - int(target):+d} ms)")

    # Unflagged collisions are a script bug, so the exit code says so. The
    # audio is still written - it is needed to see how bad the damage is.
    return 3 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
