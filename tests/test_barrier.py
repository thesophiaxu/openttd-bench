"""ClockBarrier unit tests with an injected fake clock (no OpenTTD)."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "image"))

from server.barrier import ADMIN, ClockBarrier  # noqa: E402


class FakeGame:
    """A clock that advances only while the lease is held."""

    def __init__(self, start_date: int = 712223, days_per_second: float = 400.0) -> None:
        self.date = start_date
        self.running = False
        self.days_per_second = days_per_second
        self.leases = 0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _advance(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self.running:
                self.date += int((now - self._last) * self.days_per_second)
            self._last = now

    def clock(self):
        self._advance()
        from server import gamedate

        year, month, _ = gamedate.ymd_from_date(self.date)
        return self.date, year, month

    @contextmanager
    def lease(self):
        self._advance()
        with self._lock:
            self.running = True
            self.leases += 1
        try:
            yield
        finally:
            self._advance()
            with self._lock:
                self.running = False


def make(seats, horizon=1952, grace=0.5):
    game = FakeGame()
    barrier = ClockBarrier(set(seats), game.clock, game.lease, horizon, detach_grace=grace, poll_seconds=0.02)
    barrier.start()
    return game, barrier


def test_single_seat_wait_runs_the_clock_and_pauses_after():
    game, barrier = make({0})
    target = game.date + 30
    wait = barrier.register(0, "date", target)
    assert barrier.block(wait, 10) == "reached"
    assert game.date >= target
    time.sleep(0.1)
    assert not game.running and game.leases == 1
    barrier.stop()


def test_clock_only_runs_while_every_seat_waits():
    game, barrier = make({0, 1})
    wait0 = barrier.register(0, "date", game.date + 20)
    assert barrier.block(wait0, 0.3) == "blocked"  # seat 1 is thinking
    assert not game.running and game.leases == 0
    wait1 = barrier.register(1, "date", game.date + 20)
    results = {}

    def run(seat, wait):
        results[seat] = barrier.block(wait, 10)

    t0 = threading.Thread(target=run, args=(0, barrier.resume(0, "date", wait0.value)))
    t1 = threading.Thread(target=run, args=(1, wait1))
    t0.start()
    t1.start()
    t0.join(5)
    t1.join(5)
    assert results == {0: "reached", 1: "reached"}
    barrier.stop()


def test_command_cancels_a_detached_wait_and_retire_unblocks_peers():
    game, barrier = make({0, 1})
    wait0 = barrier.register(0, "date", game.date + 20)
    assert barrier.block(wait0, 0.2) == "blocked"
    barrier.cancel_detached(0)
    assert not barrier.is_waiting(0)
    wait1 = barrier.register(1, "date", game.date + 20)
    assert barrier.block(wait1, 0.3) == "blocked"
    barrier.retire(0)
    assert barrier.block(barrier.resume(1, "date", wait1.value), 10) == "reached"
    barrier.stop()


def test_detached_wait_expires_after_grace():
    game, barrier = make({0, 1}, grace=0.3)
    wait0 = barrier.register(0, "date", game.date + 20)
    assert barrier.block(wait0, 0.1) == "blocked"
    time.sleep(1.5)
    assert not barrier.is_waiting(0)
    barrier.stop()


def test_horizon_satisfies_every_wait_and_run_out_uses_admin_seat():
    game, barrier = make({0}, horizon=1951)
    wait = barrier.register(0, "year", 1960)
    assert barrier.block(wait, 10) == "reached"
    from server import gamedate

    assert gamedate.ymd_from_date(game.date)[0] >= 1951
    barrier.retire(0)
    barrier.add_seat(ADMIN)
    admin_wait = barrier.register(ADMIN, "year", 1951)
    assert barrier.block(admin_wait, 5) == "reached"
    barrier.stop()


def test_superseded_wait():
    game, barrier = make({0, 1})
    first = barrier.register(0, "date", game.date + 5)
    barrier.register(0, "date", game.date + 50)
    assert barrier.block(first, 0.2) == "superseded"
    barrier.stop()
