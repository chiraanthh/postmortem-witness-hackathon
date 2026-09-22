"""Concurrent live-pipeline seat leases + FIFO wait queue.

Replay / mock sessions are uncapped and never touch this. Live seats are
capped (default 2) and freed on explicit release, idle timeout without a
heartbeat, or when the manager is told the live pipeline completed.

When the pool is full, clients may join a FIFO wait queue. Expired waiters
(no heartbeat) are dropped. On every free seat the head of the queue is
promoted into a reserved lease the client claims via heartbeat.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Lease:
    lease_id: str
    last_beat: float


@dataclass
class _Waiter:
    ticket_id: str
    last_beat: float
    enqueued_at: float
    # Set when a seat is reserved for this waiter (not yet claimed into a session).
    reserved_lease_id: str | None = None


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
        self._queue: OrderedDict[str, _Waiter] = OrderedDict()
        # ticket_id -> lease_id after promotion, until claim or expire
        self._ready: dict[str, str] = {}

    def status(self) -> dict[str, int]:
        with self._lock:
            self._expire_unlocked()
            used = len(self._leases)
            return {
                "used": used,
                "cap": self.cap,
                "available": max(0, self.cap - used),
                "queue_depth": len(self._queue),
            }

    def acquire(self) -> str | None:
        """Return a new lease_id, or None when the pool is full."""
        with self._lock:
            self._expire_unlocked()
            if len(self._leases) >= self.cap:
                return None
            return self._mint_lease_unlocked()

    def claim_ready(self, ticket_id: str) -> str | None:
        """Take a reserved lease for a promoted waiter. None if not ready."""
        with self._lock:
            self._expire_unlocked()
            lease_id = self._ready.pop(ticket_id, None)
            if lease_id is None:
                return None
            # Lease must still be alive (heartbeat while waiting).
            if lease_id not in self._leases:
                return None
            self._queue.pop(ticket_id, None)
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
            gone = self._leases.pop(lease_id, None) is not None
            # Drop any ready mapping that pointed at this lease.
            for tid, lid in list(self._ready.items()):
                if lid == lease_id:
                    del self._ready[tid]
            if gone:
                self._promote_unlocked()
            return gone

    def release_all(self) -> int:
        """Drop every lease (e.g. tests). Returns count."""
        with self._lock:
            n = len(self._leases)
            self._leases.clear()
            self._ready.clear()
            self._promote_unlocked()
            return n

    def has(self, lease_id: str) -> bool:
        with self._lock:
            self._expire_unlocked()
            return lease_id in self._leases

    def enqueue(self) -> dict[str, object]:
        """Join the FIFO wait queue. Returns ticket + 1-based position."""
        with self._lock:
            self._expire_unlocked()
            # Fast path: seat free → mint immediately and mark ready.
            if len(self._leases) < self.cap:
                ticket_id = uuid.uuid4().hex
                lease_id = self._mint_lease_unlocked()
                self._ready[ticket_id] = lease_id
                now = self._clock()
                self._queue[ticket_id] = _Waiter(
                    ticket_id=ticket_id,
                    last_beat=now,
                    enqueued_at=now,
                    reserved_lease_id=lease_id,
                )
                return {
                    "ticket_id": ticket_id,
                    "position": 1,
                    "ready": True,
                    "lease_id": lease_id,
                    **self._status_unlocked(),
                }
            ticket_id = uuid.uuid4().hex
            now = self._clock()
            self._queue[ticket_id] = _Waiter(
                ticket_id=ticket_id,
                last_beat=now,
                enqueued_at=now,
            )
            return {
                "ticket_id": ticket_id,
                "position": self._position_unlocked(ticket_id),
                "ready": False,
                "lease_id": None,
                **self._status_unlocked(),
            }

    def queue_heartbeat(self, ticket_id: str) -> dict[str, object] | None:
        """Refresh a wait ticket. None if unknown/expired."""
        with self._lock:
            self._expire_unlocked()
            waiter = self._queue.get(ticket_id)
            if waiter is None and ticket_id not in self._ready:
                return None
            if waiter is not None:
                waiter.last_beat = self._clock()
            # Reserved lease also needs a beat so idle expiry does not steal it.
            lease_id = self._ready.get(ticket_id) or (
                waiter.reserved_lease_id if waiter else None
            )
            if lease_id and lease_id in self._leases:
                self._leases[lease_id].last_beat = self._clock()
            ready = ticket_id in self._ready
            return {
                "ticket_id": ticket_id,
                "position": self._position_unlocked(ticket_id) if ticket_id in self._queue else 0,
                "ready": ready,
                "lease_id": self._ready.get(ticket_id),
                **self._status_unlocked(),
            }

    def dequeue(self, ticket_id: str) -> bool:
        """Leave the wait queue / abandon a reserved lease."""
        with self._lock:
            waiter = self._queue.pop(ticket_id, None)
            lease_id = self._ready.pop(ticket_id, None)
            if lease_id is None and waiter is not None:
                lease_id = waiter.reserved_lease_id
            if lease_id is not None:
                self._leases.pop(lease_id, None)
                self._promote_unlocked()
            return waiter is not None or lease_id is not None

    def expired_lease_ids(self) -> list[str]:
        """Run expiry and return lease ids that were just dropped for idle."""
        with self._lock:
            return self._expire_unlocked()

    def _mint_lease_unlocked(self) -> str:
        lease_id = uuid.uuid4().hex
        now = self._clock()
        self._leases[lease_id] = _Lease(lease_id=lease_id, last_beat=now)
        return lease_id

    def _position_unlocked(self, ticket_id: str) -> int:
        for i, tid in enumerate(self._queue.keys(), start=1):
            if tid == ticket_id:
                return i
        return 0

    def _status_unlocked(self) -> dict[str, int]:
        used = len(self._leases)
        return {
            "used": used,
            "cap": self.cap,
            "available": max(0, self.cap - used),
            "queue_depth": len(self._queue),
        }

    def _promote_unlocked(self) -> None:
        """Reserve free seats for the head of the wait queue."""
        while len(self._leases) < self.cap:
            # Find next waiter without a reservation.
            nxt: _Waiter | None = None
            for w in self._queue.values():
                if w.ticket_id not in self._ready and w.reserved_lease_id is None:
                    nxt = w
                    break
            if nxt is None:
                break
            lease_id = self._mint_lease_unlocked()
            nxt.reserved_lease_id = lease_id
            self._ready[nxt.ticket_id] = lease_id

    def _expire_unlocked(self) -> list[str]:
        now = self._clock()
        dead_leases = [
            lid
            for lid, lease in self._leases.items()
            if (now - lease.last_beat) > self.idle_timeout_s
        ]
        for lid in dead_leases:
            del self._leases[lid]
            for tid, rid in list(self._ready.items()):
                if rid == lid:
                    del self._ready[tid]
                    self._queue.pop(tid, None)

        dead_tickets = [
            tid
            for tid, w in self._queue.items()
            if (now - w.last_beat) > self.idle_timeout_s
        ]
        for tid in dead_tickets:
            w = self._queue.pop(tid, None)
            self._ready.pop(tid, None)
            if w and w.reserved_lease_id:
                self._leases.pop(w.reserved_lease_id, None)

        if dead_leases or dead_tickets:
            self._promote_unlocked()
        return dead_leases
