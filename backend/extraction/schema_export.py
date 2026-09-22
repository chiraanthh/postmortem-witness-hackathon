"""Shared JSON Schema for extraction — both providers derive from ExtractedEvents.

Do not hand-write a second schema. AnthropicDirect passes the pydantic model
to messages.parse(); AssemblyAIGateway embeds model_json_schema() in
response_format.json_schema.schema. Same source of truth.
"""

from __future__ import annotations

from typing import Any

from backend.state.models import ExtractedEvents

SCHEMA_NAME = "extracted_events"


def extracted_events_json_schema() -> dict[str, Any]:
    """JSON Schema object for ExtractedEvents (the `schema` field value)."""
    return ExtractedEvents.model_json_schema()


def gateway_response_format() -> dict[str, Any]:
    """AssemblyAI LLM Gateway structured-output parameter.

    Confirmed against https://www.assemblyai.com/docs/llm-gateway/structured-outputs
    (fetched 2026-09-15):

        response_format: {
          type: "json_schema",
          json_schema: { name, schema, strict? }
        }
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": SCHEMA_NAME,
            "schema": extracted_events_json_schema(),
            "strict": True,
        },
    }


# Anthropic messages.parse output_format — the class itself, not a dict.
ANTHROPIC_OUTPUT_FORMAT = ExtractedEvents
