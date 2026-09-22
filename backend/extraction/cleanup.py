"""Optional utterance cleanup via AssemblyAI LLM Gateway + qwen3.5-4b-32k-fast.

Default OFF (CLEANUP_ENABLED). Fixes transcription artifacts only — never
changes meaning. Extraction runs on cleaned text; grounding checks raw AND
cleaned.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

import httpx2

from backend import config
from backend.metrics import Metrics, now_ms
from backend.transcription.buffer import Utterance

log = logging.getLogger("postmortem.extraction.cleanup")

CLEANUP = "cleanup"  # metrics stage name

SYSTEM = """\
You clean up live speech-to-text transcripts from an incident bridge call.

Fix ONLY transcription artifacts:
- obvious ASR mishearings of technical terms (e.g. "BNS" → "DNS", \
"De Broglie" → "the deploy" when that is clearly what was meant)
- broken casing / punctuation that makes the line harder to read
- duplicated stutter fragments ("the the deploy" → "the deploy")

NEVER change meaning. NEVER invent words that were not spoken. NEVER add \
or remove commitments, names, numbers, or hypothesis claims. If unsure, \
return the input unchanged.

Negative examples (WRONG — do not do this):
- Input: "I don't think it's the cache" → Output: "We ruled out the cache"
  (changed doubt into a ruling-out)
- Input: "Someone needs to check the rate limit" → Output: "I'll check the rate limit"
  (changed an unowned ask into a personal commitment)
- Input: "Could be the migration" → Output: "It was the migration"
  (changed a hypothesis into a confirmation)

Positive examples (OK):
- Input: "All 3, Priya, BNS, and the provider." → Output: "All 3, Priya, DNS, and the provider."
- Input: "I think it's the De Broglie." → Output: "I think it's the deploy."
- Input: "Hold on DNS." → Output: "Hold on, DNS."

Reply with ONLY the cleaned utterance text. No quotes, no preamble.
"""


def cleanup_utterance(
    utterance: Utterance,
    *,
    metrics: Metrics | None = None,
    http_client: httpx2.Client | None = None,
) -> Utterance:
    """Return a copy with cleaned_text set. On failure, return utterance as-is."""
    if utterance.cleaned_text:
        return utterance

    started = now_ms()
    try:
        text = _call_qwen(utterance.text, http_client=http_client)
    except Exception as exc:  # noqa: BLE001 — cleanup must never kill extraction
        log.warning("cleanup failed for turn %s: %s", utterance.turn_key, exc)
        text = utterance.text

    latency = now_ms() - started
    if metrics is not None:
        metrics.record(CLEANUP, latency)

    cleaned = (text or "").strip() or utterance.text
    if cleaned == utterance.text:
        return utterance
    return replace(utterance, cleaned_text=cleaned)


def _call_qwen(
    text: str, *, http_client: httpx2.Client | None = None
) -> str:
    region = config.LLM_GATEWAY_REGION
    base = (
        "https://llm-gateway.eu.assemblyai.com/v1"
        if region == "eu"
        else "https://llm-gateway.assemblyai.com/v1"
    )
    body = {
        "model": config.CLEANUP_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "max_tokens": 256,
        "temperature": 0,
    }
    client = http_client or httpx2.Client(timeout=config.EXTRACTION_TIMEOUT_S)
    owns = http_client is None
    last_err: Exception | None = None
    try:
        for attempt in range(1, 4):
            try:
                response = client.post(
                    f"{base}/chat/completions",
                    headers={
                        "authorization": config.assemblyai_api_key(),
                        "content-type": "application/json",
                    },
                    json=body,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.4 * attempt)
                continue
            if response.status_code == 429:
                last_err = RuntimeError(
                    f"cleanup HTTP 429: {response.text[:300]}"
                )
                time.sleep(3.0 * attempt)
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"cleanup HTTP {response.status_code}: {response.text[:300]}"
                )
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            return str(content).strip().strip('"').strip("'")
        raise last_err or RuntimeError("cleanup failed after retries")
    finally:
        if owns:
            client.close()
