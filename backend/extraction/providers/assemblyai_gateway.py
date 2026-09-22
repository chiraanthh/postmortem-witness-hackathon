"""AssemblyAI LLM Gateway — OpenAI-compatible chat completions + json_schema.

Docs (fetched 2026-09-15):
  https://www.assemblyai.com/docs/llm-gateway
  https://www.assemblyai.com/docs/llm-gateway/structured-outputs

Auth: header `authorization: <ASSEMBLYAI_API_KEY>` with no Bearer prefix.
Structured output:

  response_format: {
    type: "json_schema",
    json_schema: { name, schema, strict: true }
  }

Optional: post_processing_steps: [{type: "json-repair"}]
Every response carries request_id — persist it with model, region, timestamp.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx2

from backend import config
from backend.extraction.providers import ProviderError, ProviderResult
from backend.extraction.schema_export import gateway_response_format
from backend.state.models import ExtractedEvents

log = logging.getLogger("postmortem.extraction.gateway")

GATEWAY_CALL_LOG = config.REPO_ROOT / "demo" / "runs" / "gateway_calls.jsonl"


class AssemblyAIGateway:
    name = "assemblyai_gateway"

    def __init__(
        self,
        *,
        model: str | None = None,
        region: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        http_client: httpx2.Client | None = None,
        call_log: Path | None = None,
    ) -> None:
        self.model = model or config.extraction_model()
        self.region = (region or config.LLM_GATEWAY_REGION).lower()
        self._api_key = api_key
        self._timeout_s = (
            config.EXTRACTION_TIMEOUT_S if timeout_s is None else timeout_s
        )
        self._http = http_client
        self._call_log = call_log or GATEWAY_CALL_LOG

    @property
    def base_url(self) -> str:
        if self.region == "eu":
            return "https://llm-gateway.eu.assemblyai.com/v1"
        return "https://llm-gateway.assemblyai.com/v1"

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        return config.assemblyai_api_key()

    def _client(self) -> httpx2.Client:
        if self._http is None:
            self._http = httpx2.Client(timeout=self._timeout_s)
        return self._http

    def complete(self, *, system: str, user_message: str) -> ProviderResult:
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": config.EXTRACTION_MAX_TOKENS,
            "temperature": config.EXTRACTION_TEMPERATURE,
            "response_format": gateway_response_format(),
            "post_processing_steps": [{"type": "json-repair"}],
        }
        sent_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        try:
            response = self._client().post(
                url,
                headers={
                    "authorization": self._key(),
                    "content-type": "application/json",
                },
                json=body,
            )
        except Exception as exc:  # noqa: BLE001
            self._persist_call(
                request_id=None,
                sent_at=sent_at,
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            raise ProviderError(
                f"gateway transport error: {type(exc).__name__}: {exc}",
                provider=self.name,
                model=self.model,
            ) from exc

        latency_ms = (time.monotonic() - t0) * 1000
        request_id: str | None = None
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:2000]}

        if isinstance(payload, dict):
            request_id = payload.get("request_id") or payload.get("id")

        if response.status_code >= 400:
            self._persist_call(
                request_id=request_id,
                sent_at=sent_at,
                status=response.status_code,
                error=payload,
                latency_ms=latency_ms,
            )
            log.error(
                "LLM Gateway error status=%s request_id=%s model=%s region=%s body=%s",
                response.status_code,
                request_id,
                self.model,
                self.region,
                payload,
            )
            raise ProviderError(
                f"gateway HTTP {response.status_code}: {payload}",
                provider=self.name,
                model=self.model,
            )

        self._persist_call(
            request_id=request_id,
            sent_at=sent_at,
            status=response.status_code,
            error=None,
            latency_ms=latency_ms,
        )

        try:
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                # Some gateways return content parts; join text.
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            data = json.loads(content) if isinstance(content, str) else content
            parsed = ExtractedEvents.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "LLM Gateway parse failure request_id=%s model=%s: %s",
                request_id,
                self.model,
                exc,
            )
            raise ProviderError(
                f"gateway response not schema-valid: {type(exc).__name__}: {exc}",
                provider=self.name,
                model=self.model,
            ) from exc

        return ProviderResult(
            parsed=parsed,
            request_id=request_id,
            region=self.region,
            model=self.model,
        )

    def _persist_call(
        self,
        *,
        request_id: str | None,
        sent_at: str,
        status: int | None,
        error: Any,
        latency_ms: float,
    ) -> None:
        record = {
            "request_id": request_id,
            "model": self.model,
            "region": self.region,
            "base_url": self.base_url,
            "timestamp": sent_at,
            "status": status,
            "latency_ms": round(latency_ms, 1),
            "error": error,
        }
        try:
            self._call_log.parent.mkdir(parents=True, exist_ok=True)
            with self._call_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning("failed to persist gateway call log: %s", exc)
        log.info(
            "gateway call request_id=%s model=%s region=%s ts=%s status=%s",
            request_id,
            self.model,
            self.region,
            sent_at,
            status,
        )
