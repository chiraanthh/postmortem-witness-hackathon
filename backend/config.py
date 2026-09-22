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

# Formatted finals are what we extract from. Docs describe a two-phase final;
# our config only ever receives the formatted pass — see CLAUDE.md.
FORMAT_TURNS = True


# --- Extraction ------------------------------------------------------------

# Which LLM backend runs extraction. Never silently fall over between them.
#   anthropic           — Anthropic Messages API (default)
#   assemblyai_gateway  — AssemblyAI LLM Gateway (OpenAI-compatible)
EXTRACTION_PROVIDER = os.environ.get("EXTRACTION_PROVIDER", "anthropic").strip()

# The model that turns an utterance into a structured event. Read from the
# environment so it can be swapped with one variable and no code change - the
# call site must never name a model literal.
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-haiku-4-5-20251001")

# LLM Gateway region: "us" (default) or "eu".
LLM_GATEWAY_REGION = os.environ.get("LLM_GATEWAY_REGION", "us").strip().lower()

# Optional pre-extraction rewrite via qwen3.5-4b-32k-fast. Default OFF.
CLEANUP_ENABLED = os.environ.get("CLEANUP_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CLEANUP_MODEL = os.environ.get("CLEANUP_MODEL", "qwen3.5-4b-32k-fast")

# The output is a handful of short fields. Anything larger is the model
# rambling, and truncation is cheaper to detect than to read.
EXTRACTION_MAX_TOKENS = int(os.environ.get("EXTRACTION_MAX_TOKENS", "512"))

# One retry, then noise. An incident call does not wait for us.
EXTRACTION_MAX_RETRIES = int(os.environ.get("EXTRACTION_MAX_RETRIES", "1"))

# Zero, because this is classification, not writing. Left unset the SDK
# samples at 1.0, and two extraction runs over the *same* recorded transcript
# disagreed on three of the ten events the demo script embeds - which makes
# every accuracy and recall number unfalsifiable, since any change can be
# explained away as sampling. A run has to be reproducible before it can be
# evidence.
EXTRACTION_TEMPERATURE = float(os.environ.get("EXTRACTION_TEMPERATURE", "0"))

# Per-call ceiling. A slow extraction is worse than a missing one: the
# dashboard falling behind the call is the failure mode that kills the demo.
EXTRACTION_TIMEOUT_S = float(os.environ.get("EXTRACTION_TIMEOUT_S", "12"))

# Bounds on the running context. The full transcript is never resent; only
# the live hypothesis board and the open threads go back, and only this many.
EXTRACTION_MAX_HYPOTHESES = int(os.environ.get("EXTRACTION_MAX_HYPOTHESES", "12"))
EXTRACTION_MAX_THREADS = int(os.environ.get("EXTRACTION_MAX_THREADS", "8"))

# Concurrent live-pipeline seats (AssemblyAI + extraction). Replay/mock is
# uncapped and never consumes a seat. Abandoned tabs free via idle timeout.
LIVE_PIPELINE_CAP = int(os.environ.get("LIVE_PIPELINE_CAP", "2"))
LIVE_SLOT_IDLE_TIMEOUT_S = float(os.environ.get("LIVE_SLOT_IDLE_TIMEOUT_S", "45"))

# Upload caps (raw upload bytes / duration after probe).
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", str(40 * 1024 * 1024)))  # 40 MiB
UPLOAD_MAX_DURATION_MS = int(os.environ.get("UPLOAD_MAX_DURATION_MS", str(15 * 60 * 1000)))  # 15 min
UPLOAD_DIR = REPO_ROOT / ".uploads"

# Runtime overrides from POST /incident/extraction (model picker). None means
# "use the env defaults". Never clears mid-incident board state.
_runtime_provider: str | None = None
_runtime_model: str | None = None
_runtime_cleanup: bool | None = None


def extraction_provider() -> str:
    if _runtime_provider is not None:
        return _runtime_provider
    return os.environ.get("EXTRACTION_PROVIDER", EXTRACTION_PROVIDER).strip()


def extraction_model() -> str:
    if _runtime_model is not None:
        return _runtime_model
    return os.environ.get("EXTRACTION_MODEL", EXTRACTION_MODEL)


def cleanup_enabled() -> bool:
    if _runtime_cleanup is not None:
        return _runtime_cleanup
    return os.environ.get("CLEANUP_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def set_extraction_runtime(
    *,
    provider: str | None = None,
    model: str | None = None,
    cleanup: bool | None = None,
) -> dict[str, object]:
    """Apply model-picker changes to subsequent utterances only."""
    global _runtime_provider, _runtime_model, _runtime_cleanup
    if provider is not None:
        _runtime_provider = provider.strip().lower()
    if model is not None:
        _runtime_model = model.strip()
    if cleanup is not None:
        _runtime_cleanup = bool(cleanup)
    return extraction_runtime_status()


def reset_extraction_runtime() -> dict[str, object]:
    """Drop picker overrides so the next visitor inherits env defaults (Haiku).

    Called on every fresh /incident/start and /incident/restart. Join-as-viewer
    must not call this — that would yank the running host's selection.
    """
    global _runtime_provider, _runtime_model, _runtime_cleanup
    _runtime_provider = None
    _runtime_model = None
    _runtime_cleanup = None
    return extraction_runtime_status()


def extraction_runtime_status() -> dict[str, object]:
    return {
        "provider": extraction_provider(),
        "model": extraction_model(),
        "cleanup_enabled": cleanup_enabled(),
        "cleanup_model": CLEANUP_MODEL,
        "gateway_region": LLM_GATEWAY_REGION,
        "temperature": EXTRACTION_TEMPERATURE,
        "defaults": {
            "provider": EXTRACTION_PROVIDER,
            "model": EXTRACTION_MODEL,
            "cleanup_enabled": False,
        },
    }


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


# Demo-audio synthesis only; the pipeline never calls Sarvam. SARWAM_API_KEY is
# accepted because that is how the name is actually spelled in the local .env,
# and silently failing over a transposed letter is worse than tolerating it.
_SARVAM_KEY_NAMES = ("SARVAM_API_KEY", "SARWAM_API_KEY")


def sarvam_api_key() -> str:
    for name in _SARVAM_KEY_NAMES:
        key = os.environ.get(name, "").strip()
        if key:
            return key
    raise ConfigError(
        f"No Sarvam key found. Set one of {' or '.join(_SARVAM_KEY_NAMES)} in .env"
    )


def redacted_key(key: str) -> str:
    """The only form of a key that is allowed to reach a log or a terminal."""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"
