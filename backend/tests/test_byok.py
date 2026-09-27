"""Bring-your-own-key: validators, session wiring, and never-echoed keys.

No real network calls — AssemblyAI's REST check is mocked at httpx.get and
Anthropic's client is mocked at the SDK boundary the same way the rest of
this suite fakes providers (see test_extraction_runtime.py).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import anthropic
import httpx2

from backend.byok import (
    ApiKeys,
    KeyValidationError,
    validate_anthropic_key,
    validate_assemblyai_key,
)


def _resp(status: int) -> httpx2.Response:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx2.Response(status, request=req)


class TestValidateAssemblyaiKey(unittest.TestCase):
    def test_401_rejected(self) -> None:
        fake = MagicMock(status_code=401)
        with patch("backend.byok.httpx.get", return_value=fake):
            with self.assertRaises(KeyValidationError) as ctx:
                validate_assemblyai_key("bad-key")
        self.assertEqual(ctx.exception.which, "assemblyai")
        self.assertNotIn("bad-key", str(ctx.exception))

    def test_200_accepted(self) -> None:
        fake = MagicMock(status_code=200)
        with patch("backend.byok.httpx.get", return_value=fake):
            validate_assemblyai_key("real-key")  # must not raise

    def test_429_rate_limited_still_accepted(self) -> None:
        """A real key throttled by the validation endpoint is still a real key."""
        fake = MagicMock(status_code=429)
        with patch("backend.byok.httpx.get", return_value=fake):
            validate_assemblyai_key("real-key")  # must not raise

    def test_connection_failure_rejected_without_leaking_key(self) -> None:
        import httpx

        with patch(
            "backend.byok.httpx.get", side_effect=httpx.ConnectError("dns fail")
        ):
            with self.assertRaises(KeyValidationError) as ctx:
                validate_assemblyai_key("super-secret-key")
        self.assertNotIn("super-secret-key", str(ctx.exception))


class TestValidateAnthropicKey(unittest.TestCase):
    def test_authentication_error_rejected(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = anthropic.AuthenticationError(
            "invalid x-api-key", response=_resp(401), body=None
        )
        with patch("backend.byok.anthropic.Anthropic", return_value=fake_client):
            with self.assertRaises(KeyValidationError) as ctx:
                validate_anthropic_key("sk-ant-bad")
        self.assertEqual(ctx.exception.which, "anthropic")
        self.assertNotIn("sk-ant-bad", str(ctx.exception))

    def test_usage_limit_400_still_accepted(self) -> None:
        """The exact error this project's own key hit — a valid key, just
        out of quota. Must not be classified as a bad key."""
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = anthropic.BadRequestError(
            "You have reached your specified API usage limits.",
            response=_resp(400),
            body=None,
        )
        with patch("backend.byok.anthropic.Anthropic", return_value=fake_client):
            validate_anthropic_key("sk-ant-real-but-capped")  # must not raise

    def test_rate_limit_429_still_accepted(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = anthropic.RateLimitError(
            "rate limited", response=_resp(429), body=None
        )
        with patch("backend.byok.anthropic.Anthropic", return_value=fake_client):
            validate_anthropic_key("sk-ant-real")  # must not raise

    def test_connection_error_rejected_without_leaking_key(self) -> None:
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = anthropic.APIConnectionError(
            request=req
        )
        with patch("backend.byok.anthropic.Anthropic", return_value=fake_client):
            with self.assertRaises(KeyValidationError) as ctx:
                validate_anthropic_key("sk-ant-super-secret")
        self.assertNotIn("sk-ant-super-secret", str(ctx.exception))

    def test_success_accepted(self) -> None:
        fake_client = MagicMock()
        fake_client.messages.create.return_value = object()
        with patch("backend.byok.anthropic.Anthropic", return_value=fake_client):
            validate_anthropic_key("sk-ant-real")  # must not raise


class TestSessionKeyWiring(unittest.TestCase):
    """The hub-level plumbing: a keyed session's worker actually uses that
    key, and nothing about the key ever appears in a snapshot."""

    def test_hub_with_api_keys_builds_worker_on_session_key(self) -> None:
        from backend.main import IncidentHub

        keys = ApiKeys(assemblyai="asr-key-123", anthropic="ant-key-456")
        hub = IncidentHub(api_keys=keys)
        provider = hub.worker.provider
        self.assertEqual(provider.name, "anthropic")
        self.assertEqual(provider.client.api_key, "ant-key-456")

    def test_hub_without_api_keys_unaffected(self) -> None:
        from backend.main import IncidentHub

        hub = IncidentHub()
        self.assertIsNone(hub._api_keys)

    def test_snapshot_never_carries_keys(self) -> None:
        from backend.main import IncidentHub

        keys = ApiKeys(assemblyai="asr-key-123", anthropic="ant-key-456")
        hub = IncidentHub(api_keys=keys)
        snap = hub.snapshot()
        blob = str(snap)
        self.assertNotIn("asr-key-123", blob)
        self.assertNotIn("ant-key-456", blob)

    def test_dropping_session_removes_the_only_reference(self) -> None:
        from backend.session_slots import LiveSlotManager
        from backend.sessions import SessionRegistry

        slots = LiveSlotManager(cap=2, idle_timeout_s=60)
        registry = SessionRegistry(slots)
        keys = ApiKeys(assemblyai="asr-key-123", anthropic="ant-key-456")
        session = registry.create_live(api_keys=keys)
        assert session is not None
        self.assertIs(session.hub._api_keys, keys)
        registry.drop(session.session_id)
        # The registry held the only reference to this hub; once dropped,
        # nothing in the process can reach these keys through it again.
        self.assertIsNone(registry.get(session.session_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
