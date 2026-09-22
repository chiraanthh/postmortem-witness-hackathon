"""Multi-session registry: each live visitor gets an IncidentHub at /s/<id>.

Live sessions consume a LiveSlotManager seat (cap default 2). Replay stays
client-side (recorded live-pipeline WS fixture, zero API) and never enters this registry.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.main import IncidentHub
    from backend.session_slots import LiveSlotManager

log = logging.getLogger("postmortem.sessions")


@dataclass
class Session:
    session_id: str
    hub: "IncidentHub"
    kind: str  # "live"
    lease_id: str


class SessionRegistry:
    def __init__(self, slots: "LiveSlotManager") -> None:
        self._slots = slots
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def create_live(self) -> Session | None:
        """Acquire a seat and create a dedicated hub. None if seats full."""
        from backend.main import IncidentHub

        lease_id = self._slots.acquire()
        if lease_id is None:
            return None
        session_id = uuid.uuid4().hex[:12]
        hub = IncidentHub()
        if self._loop is not None:
            hub.bind_loop(self._loop)
        session = Session(
            session_id=session_id,
            hub=hub,
            kind="live",
            lease_id=lease_id,
        )
        with self._lock:
            self._sessions[session_id] = session
        log.info("live session created id=%s lease=%s…", session_id, lease_id[:8])
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def drop(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        try:
            session.hub._stop_pipeline(join=False, suppress_teardown=True)
        except Exception:  # noqa: BLE001
            log.exception("stop pipeline on session drop")
        self._slots.release(session.lease_id)
        log.info("live session dropped id=%s", session_id)
        return True

    def release_lease_for_hub(self, hub: "IncidentHub") -> None:
        """Called when a hub's live pipeline completes — free its seat."""
        with self._lock:
            match = next(
                (s for s in self._sessions.values() if s.hub is hub),
                None,
            )
        if match is None:
            return
        self._slots.release(match.lease_id)

    def status(self) -> dict:
        with self._lock:
            sessions = [
                {
                    "session_id": s.session_id,
                    "kind": s.kind,
                    "running": s.hub.status.get("running"),
                    "finished": s.hub.status.get("finished"),
                }
                for s in self._sessions.values()
            ]
        return {
            "live_pipeline": self._slots.status(),
            "sessions": sessions,
        }
