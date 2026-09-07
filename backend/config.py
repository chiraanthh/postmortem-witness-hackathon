"""Configuration and audio constants.

Keys come from .env. Nothing here is ever hardcoded, and nothing here logs a
key — see `redacted_key` for the only representation safe to print.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Anchored to this file, not to the working directory, so the keys resolve the
# same whether the process was launched from the repo root, from backend/, or
# from anywhere else. backend/.env is accepted as a fallback because it is an
# easy place to put it by mistake; .env.example is deliberately NOT consulted -
# it is a tracked template and must never hold a real value.
_ENV_CANDIDATES = (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env")

for _candidate in _ENV_CANDIDATES:
    if _candidate.exists():
        load_dotenv(_candidate)


def env_file_status() -> dict[str, bool]:
    """Which env files exist, and whether each key resolved. Never values."""
    status = {str(c.relative_to(REPO_ROOT)): c.exists() for c in _ENV_CANDIDATES}
    for key in ("ASSEMBLYAI_API_KEY", "ANTHROPIC_API_KEY"):
        status[key] = bool(os.environ.get(key, "").strip())
    return status


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


# --- Audio -----------------------------------------------------------------
# The streaming API wants mono 16-bit PCM. Everything we send is normalized to
# this before it leaves the process; see transcription/audio.py.

SAMPLE_RATE = 16_000
ENCODING = "pcm_s16le"
CHANNELS = 1
BYTES_PER_SAMPLE = 2

# Audio is sent as binary frames of 50-1000 ms. We use the low end: smaller
# frames mean the latency measurement is attributed to a tighter slice of
# audio, which is the whole point of measuring it.
CHUNK_MS = 50

BYTES_PER_MS = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE // 1000
CHUNK_BYTES = CHUNK_MS * BYTES_PER_MS


# --- Streaming defaults ----------------------------------------------------

# Diarization is on. It is the whole reason this project can attribute an
# action to a person, and as of 2026 it is available on the streaming path.
SPEAKER_LABELS = True

# 1-10. None means "do not cap" — let the model decide how many voices it
# hears. Set it from the CLI when the number of people on the bridge is known,
# which measurably helps on overlapping speech.
MAX_SPEAKERS: int | None = None

# Formatted finals are what we extract from. See "Known API behaviours" in
# CLAUDE.md for why a turn finalizes twice.
FORMAT_TURNS = True


# --- Extraction ------------------------------------------------------------

# The model that turns an utterance into a structured event. Read from the
# environment so it can be swapped with one variable and no code change - the
# call site must never name a model literal.
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-haiku-4-5-20251001")

# The output is a handful of short fields. Anything larger is the model
# rambling, and truncation is cheaper to detect than to read.
EXTRACTION_MAX_TOKENS = int(os.environ.get("EXTRACTION_MAX_TOKENS", "512"))

# One retry, then noise. An incident call does not wait for us.
EXTRACTION_MAX_RETRIES = int(os.environ.get("EXTRACTION_MAX_RETRIES", "1"))

# Per-call ceiling. A slow extraction is worse than a missing one: the
# dashboard falling behind the call is the failure mode that kills the demo.
EXTRACTION_TIMEOUT_S = float(os.environ.get("EXTRACTION_TIMEOUT_S", "12"))

# Bounds on the running context. The full transcript is never resent; only
# the live hypothesis board and the open threads go back, and only this many.
EXTRACTION_MAX_HYPOTHESES = int(os.environ.get("EXTRACTION_MAX_HYPOTHESES", "12"))
EXTRACTION_MAX_THREADS = int(os.environ.get("EXTRACTION_MAX_THREADS", "8"))


def extraction_model() -> str:
    """Resolved at call time so tests and demos can override it in-process."""
    return os.environ.get("EXTRACTION_MODEL", EXTRACTION_MODEL)


def assemblyai_api_key() -> str:
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "ASSEMBLYAI_API_KEY is not set.\n"
            f"  cp {REPO_ROOT / '.env.example'} {REPO_ROOT / '.env'}\n"
            "  then paste your key into .env"
        )
    return key


def anthropic_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ConfigError("ANTHROPIC_API_KEY is not set. Add it to .env")
    return key


def redacted_key(key: str) -> str:
    """The only form of a key that is allowed to reach a log or a terminal."""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"
