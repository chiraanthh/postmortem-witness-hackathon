"""Configuration and audio constants.

Keys come from .env. Nothing here is ever hardcoded, and nothing here logs a
key — see `redacted_key` for the only representation safe to print.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


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
