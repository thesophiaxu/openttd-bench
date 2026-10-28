"""RealtimeClock unit tests with the barrier tests' injected fake game (no OpenTTD)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "image"))

from server import gamedate  # noqa: E402
from server.barrier import ADMIN, RealtimeClock  # noqa: E402
from test_barrier import FakeGame  # noqa: E402


def make(seats, horizon=1951, days_per_second=400.0):
    game = FakeGame(days_per_second=days_per_second)
    clock = RealtimeClock(set(seats), game.clock, game.lease, horizon, detach_grace=0.5, poll_seconds=0.02)
    clock.start()
    return game, clock


def test_clock_is_stopped_until_started_then_runs_without_waits():
    game, clock = make({0, 1})
    assert clock.mode == "realtime" and clock.status()["started"] is False
    time.sleep(0.2)
    assert not game.running and game.leases == 0
    assert clock.start_clock() is True and clock.start_clock() is False
    time.sleep(0.3)
    assert game.running and game.leases == 1  # nobody is waiting; the clock runs anyway
    clock.stop()


def test_wait_is_a_sleep_until_the_date_and_ignores_peers():
    game, clock = make({0, 1})
    clock.start_clock()
    time.sleep(0.05)
    target = game.date + 40
    wait = clock.register(0, "date", target)
    assert clock.block(wait, 10) == "reached"  # seat 1 never waited
    assert game.date >= target
    assert clock.block(clock.register(1, "date", game.date + 2000), 0.2) == "blocked"  # target still ahead
    clock.stop()


def test_horizon_releases_the_lease_and_stays_paused_and_run_out_is_instant():
    game, clock = make({0}, horizon=1951)
    assert clock.horizon_reached is False
    clock.start_clock()
    wait = clock.register(0, "year", 1960)
    assert clock.block(wait, 10) == "reached"
    assert gamedate.ymd_from_date(game.date)[0] >= 1951
    time.sleep(0.2)
    assert clock.horizon_reached and not game.running and game.leases == 1
    clock.retire(0)
    clock.add_seat(ADMIN)
    assert clock.block(clock.register(ADMIN, "year", 1951), 5) == "reached"
    time.sleep(0.1)
    assert not game.running and game.leases == 1  # the horizon never re-opens the lease
    clock.stop()


def test_retiring_seats_does_not_stop_the_clock():
    game, clock = make({0, 1})
    clock.start_clock()
    clock.retire(0)
    clock.retire(1)
    time.sleep(0.2)
    assert game.running
    clock.stop()
