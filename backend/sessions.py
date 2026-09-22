"""Multi-session registry: each live visitor gets an IncidentHub at /s/<id>.

Live sessions consume a LiveSlotManager seat (cap default 2). Replay stays
client-side (recorded live-pipeline WS fixture, zero API) and never enters
this registry. Uploaded WAVs are session-scoped and deleted on drop.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.main import IncidentHub
    from backend.session_slots import LiveSlotManager

log = logging.getLogger("postmortem.sessions")


@dataclass
class Session:
    session_id: str
    hub: "IncidentHub"
    kind: str  # "live" | "upload"
    lease_id: str
    upload_dir: Path | None = None
    audio_path: Path | None = None
    # True after the live pipeline finished and intentionally freed the seat
    # so other judges can run. Finished boards stay viewable until drop/leave.
    seat_released: bool = False
    queued_at: float | None = field(default=None, repr=False)


class SessionRegistry:
    def __init__(self, slots: "LiveSlotManager") -> None:
        self._slots = slots
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def create_live(
        self,
        *,
        kind: str = "live",
        lease_id: str | None = None,
    ) -> Session | None:
        """Acquire a seat and create a dedicated hub. None if seats full.

        If `lease_id` is provided (promoted wait-queue ticket), bind that
        reservation instead of minting a new one.
        """
        from backend.main import IncidentHub

        if lease_id is None:
            lease_id = self._slots.acquire()
            if lease_id is None:
                return None
        elif not self._slots.has(lease_id):
            return None

        session_id = uuid.uuid4().hex[:12]
        hub = IncidentHub()
        if self._loop is not None:
            hub.bind_loop(self._loop)
        session = Session(
            session_id=session_id,
            hub=hub,
            kind=kind,
            lease_id=lease_id,
        )
        with self._lock:
            self._sessions[session_id] = session
        log.info(
            "session created id=%s kind=%s lease=%s…",
            session_id,
            kind,
            lease_id[:8],
        )
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
        if not session.seat_released:
            self._slots.release(session.lease_id)
        if session.upload_dir is not None:
            try:
                shutil.rmtree(session.upload_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                log.exception("upload cleanup failed for %s", session_id)
        log.info("session dropped id=%s", session_id)
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
            if match.seat_released:
                return
            match.seat_released = True
            lease_id = match.lease_id
        self._slots.release(lease_id)

    def sweep_expired(self) -> list[str]:
        """Drop sessions whose lease idle-expired mid-run (uploads included).

        Finished sessions that intentionally released their seat are kept so
        the board/export remain until the client leaves.
        """
        self._slots.expired_lease_ids()  # run idle clock
        dead: list[str] = []
        with self._lock:
            for sid, session in self._sessions.items():
                if session.seat_released:
                    continue
                if not self._slots.has(session.lease_id):
                    dead.append(sid)
        for sid in dead:
            self.drop(sid)
        return dead

    def status(self) -> dict:
        self.sweep_expired()
        with self._lock:
            sessions = [
                {
                    "session_id": s.session_id,
                    "kind": s.kind,
                    "running": s.hub.status.get("running"),
                    "finished": s.hub.status.get("finished"),
                    "seat_released": s.seat_released,
                }
                for s in self._sessions.values()
            ]
        return {
            "live_pipeline": self._slots.status(),
            "sessions": sessions,
        }
