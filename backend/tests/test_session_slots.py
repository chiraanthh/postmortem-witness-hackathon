"""Offline tests for live-pipeline seat leases and wait queue."""

from __future__ import annotations

import unittest

from backend.session_slots import LiveSlotManager


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestLiveSlotManager(unittest.TestCase):
    def test_cap_two_third_rejected(self) -> None:
        m = LiveSlotManager(cap=2, idle_timeout_s=60)
        a = m.acquire()
        b = m.acquire()
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNone(m.acquire())
        self.assertEqual(
            m.status(),
            {"used": 2, "cap": 2, "available": 0, "queue_depth": 0},
        )

    def test_release_frees_seat(self) -> None:
        m = LiveSlotManager(cap=2, idle_timeout_s=60)
        a = m.acquire()
        m.acquire()
        self.assertTrue(m.release(a or ""))
        c = m.acquire()
        self.assertIsNotNone(c)
        self.assertEqual(m.status()["used"], 2)

    def test_abandoned_tab_idle_timeout_frees_slot(self) -> None:
        """No heartbeat after acquire → seat expires so it cannot be held forever."""
        clock = FakeClock()
        m = LiveSlotManager(cap=2, idle_timeout_s=45.0, clock=clock)
        abandoned = m.acquire()
        kept = m.acquire()
        self.assertIsNotNone(abandoned)
        self.assertIsNotNone(kept)
        self.assertIsNone(m.acquire())

        # Abandoned tab: no heartbeat. Kept tab keeps beating.
        clock.advance(20)
        self.assertTrue(m.heartbeat(kept or ""))
        clock.advance(30)  # total 50s since abandoned last beat (>45)

        self.assertFalse(m.has(abandoned or ""))
        self.assertTrue(m.has(kept or ""))
        self.assertEqual(m.status()["used"], 1)

        replacement = m.acquire()
        self.assertIsNotNone(replacement)
        self.assertEqual(m.status()["used"], 2)

    def test_heartbeat_keeps_lease_alive(self) -> None:
        clock = FakeClock()
        m = LiveSlotManager(cap=1, idle_timeout_s=45.0, clock=clock)
        lease = m.acquire()
        for _ in range(5):
            clock.advance(40)
            self.assertTrue(m.heartbeat(lease or ""))
        self.assertTrue(m.has(lease or ""))
        self.assertIsNone(m.acquire())

    def test_release_all_on_completion(self) -> None:
        m = LiveSlotManager(cap=2, idle_timeout_s=60)
        m.acquire()
        m.acquire()
        self.assertEqual(m.release_all(), 2)
        self.assertEqual(m.status()["used"], 0)
        self.assertIsNotNone(m.acquire())

    def test_wait_queue_promotes_on_release(self) -> None:
        m = LiveSlotManager(cap=1, idle_timeout_s=60)
        held = m.acquire()
        self.assertIsNotNone(held)
        ticket = m.enqueue()
        self.assertFalse(ticket["ready"])
        self.assertEqual(ticket["position"], 1)
        self.assertEqual(m.status()["queue_depth"], 1)

        self.assertTrue(m.release(held or ""))
        beat = m.queue_heartbeat(str(ticket["ticket_id"]))
        assert beat is not None
        self.assertTrue(beat["ready"])
        self.assertIsNotNone(beat["lease_id"])

        claimed = m.claim_ready(str(ticket["ticket_id"]))
        self.assertEqual(claimed, beat["lease_id"])
        self.assertTrue(m.has(claimed or ""))
        self.assertEqual(m.status()["queue_depth"], 0)

    def test_waiter_idle_timeout_drops_ticket(self) -> None:
        clock = FakeClock()
        m = LiveSlotManager(cap=1, idle_timeout_s=45.0, clock=clock)
        m.acquire()
        ticket = m.enqueue()
        tid = str(ticket["ticket_id"])
        clock.advance(50)
        self.assertIsNone(m.queue_heartbeat(tid))
        self.assertEqual(m.status()["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()
