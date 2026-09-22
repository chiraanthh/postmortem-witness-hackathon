"""Concurrent live-pipeline seat leases.

Replay / mock sessions are uncapped and never touch this. Live seats are
capped (default 2) and freed on explicit release, idle timeout without a
heartbeat, or when the manager is told the live pipeline completed.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Lease:
    lease_id: str
    last_beat: float


class LiveSlotManager:
    """Thread-safe lease pool for live pipeline sessions."""

    def __init__(
        self,
        *,
        cap: int = 2,
        idle_timeout_s: float = 45.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self.cap = int(cap)
        self.idle_timeout_s = float(idle_timeout_s)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._leases: dict[str, _Lease] = {}

    def status(self) -> dict[str, int]:
        with self._lock:
            self._expire_unlocked()
            used = len(self._leases)
            return {
                "used": used,
                "cap": self.cap,
                "available": max(0, self.cap - used),
            }

    def acquire(self) -> str | None:
        """Return a new lease_id, or None when the pool is full."""
        with self._lock:
            self._expire_unlocked()
            if len(self._leases) >= self.cap:
                return None
            lease_id = uuid.uuid4().hex
            now = self._clock()
            self._leases[lease_id] = _Lease(lease_id=lease_id, last_beat=now)
            return lease_id

    def heartbeat(self, lease_id: str) -> bool:
        """Refresh an existing lease. False if unknown/expired."""
        with self._lock:
            self._expire_unlocked()
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            lease.last_beat = self._clock()
            return True

    def release(self, lease_id: str) -> bool:
        """Drop a lease immediately. True if it was present."""
        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    def release_all(self) -> int:
        """Drop every lease (e.g. live pipeline completed). Returns count."""
        with self._lock:
            n = len(self._leases)
            self._leases.clear()
            return n

    def has(self, lease_id: str) -> bool:
        with self._lock:
            self._expire_unlocked()
            return lease_id in self._leases

    def _expire_unlocked(self) -> None:
        now = self._clock()
        dead = [
            lid
            for lid, lease in self._leases.items()
            if (now - lease.last_beat) > self.idle_timeout_s
        ]
        for lid in dead:
            del self._leases[lid]
