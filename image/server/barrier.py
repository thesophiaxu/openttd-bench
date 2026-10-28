"""The multi-seat clock barrier (plan section 9.5).

Time advances only while EVERY active seat is inside a wait. Bookkeeping plus one
manager thread; the game (lease, clock) is injected so it unit-tests without OpenTTD.

Waits are chunked: `block()` returns within `chunk_seconds` even when peers keep
thinking; a chunk-expired wait is *detached* but still gates the barrier for
`detach_grace` seconds so the client can re-post (`resume`) without the clock
collapsing between chunks. Issuing a command cancels the caller's detached wait.
`retire` removes a seat; the horizon satisfies every wait. ADMIN (-1) is the run-out
pseudo-seat.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

ADMIN = -1


@dataclass
class SeatWait:
    company: int
    kind: str  # "date" | "year" | "month_index"
    value: int
    attached: bool = True
    detach_deadline: float | None = None
    satisfied: bool = False
    registered_at: float = field(default_factory=time.monotonic)

    def reached(self, date: int | None, year: int | None, month: int | None) -> bool:
        if self.kind == "date":
            return date is not None and date >= self.value
        if self.kind == "year":
            return year is not None and year >= self.value
        if year is None:
            return False
        return year * 12 + (month or 0) >= self.value


class ClockBarrier:
    def __init__(
        self,
        seats: set[int],
        get_clock: Callable[[], tuple[int | None, int | None, int | None]],
        lease: Callable[[], AbstractContextManager],
        horizon_year: int,
        detach_grace: float = 180.0,
        poll_seconds: float = 0.25,
    ) -> None:
        self.cond = threading.Condition()
        self.active: set[int] = set(seats)
        self.waits: dict[int, SeatWait] = {}
        self._get_clock = get_clock
        self._lease = lease
        self.horizon_year = horizon_year
        self._detach_grace = detach_grace
        self._poll_seconds = poll_seconds
        self._stopped = False
        self._running = False
        self._thread: threading.Thread | None = None
        self.unpause_windows = 0

    # -- seat-facing ---------------------------------------------------------------

    def register(self, company: int, kind: str, value: int) -> SeatWait:
        with self.cond:
            if company not in self.active:
                raise ValueError(f"seat {company} is retired")
            wait = SeatWait(company=company, kind=kind, value=value)
            if self._horizon_reached:
                wait.satisfied = True  # the horizon satisfies every wait, even ones registered later
            self.waits[company] = wait
            self.cond.notify_all()
            return wait

    def block(self, wait: SeatWait, chunk_seconds: float) -> str:
        """'reached' | 'retired' | 'superseded' | 'blocked'."""
        deadline = time.monotonic() + chunk_seconds
        with self.cond:
            while True:
                if wait.satisfied:
                    if self.waits.get(wait.company) is wait:
                        del self.waits[wait.company]
                    self.cond.notify_all()
                    return "reached"
                if wait.company not in self.active:
                    if self.waits.get(wait.company) is wait:
                        del self.waits[wait.company]
                    self.cond.notify_all()
                    return "retired"
                if self.waits.get(wait.company) is not wait:
                    return "superseded"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    wait.attached = False
                    wait.detach_deadline = time.monotonic() + self._detach_grace
                    return "blocked"
                self.cond.wait(remaining)

    def resume(self, company: int, kind: str, value: int) -> SeatWait:
        """Re-attach a detached wait with the same target, or register anew."""
        with self.cond:
            wait = self.waits.get(company)
            if wait is not None and not wait.satisfied and wait.kind == kind and wait.value == value:
                wait.attached = True
                wait.detach_deadline = None
                self.cond.notify_all()
                return wait
        return self.register(company, kind, value)

    def cancel_detached(self, company: int) -> None:
        with self.cond:
            wait = self.waits.get(company)
            if wait is not None and not wait.attached and not wait.satisfied:
                del self.waits[company]
                self.cond.notify_all()

    def retire(self, company: int) -> bool:
        with self.cond:
            was_active = company in self.active
            self.active.discard(company)
            self.cond.notify_all()
            return was_active

    def add_seat(self, company: int) -> None:
        with self.cond:
            self.active.add(company)
            self.cond.notify_all()

    def status(self) -> dict:
        with self.cond:
            return {
                "mode": self.mode,
                "started": self._started_locked(),
                "horizon_reached": self._horizon_reached,
                "active": sorted(self.active),
                "waiting": sorted(c for c, w in self.waits.items() if not w.satisfied),
                "attached": sorted(c for c, w in self.waits.items() if w.attached and not w.satisfied),
                "running": self._running,
                "horizon_year": self.horizon_year,
                "unpause_windows": self.unpause_windows,
            }

    def is_waiting(self, company: int) -> bool:
        with self.cond:
            wait = self.waits.get(company)
            return wait is not None and not wait.satisfied

    # -- the manager ----------------------------------------------------------------

    def _expire_locked(self, now: float) -> None:
        for company, wait in list(self.waits.items()):
            if (
                not wait.attached
                and not wait.satisfied
                and wait.detach_deadline is not None
                and now >= wait.detach_deadline
            ):
                del self.waits[company]
                self.cond.notify_all()

    def _should_run_locked(self) -> bool:
        if not self.active:
            return False
        return all(c in self.waits and not self.waits[c].satisfied for c in self.active)

    def _started_locked(self) -> bool:
        """Paused mode has no start: the clock is 'started' whenever it is running."""
        return self._running

    def _mark_reached_locked(self, date: int | None, year: int | None, month: int | None) -> None:
        horizon = year is not None and year >= self.horizon_year
        if horizon and not self._horizon_reached:
            self._horizon_reached = True
            self.cond.notify_all()
        for wait in self.waits.values():
            if not wait.satisfied and (horizon or wait.reached(date, year, month)):
                wait.satisfied = True
                self.cond.notify_all()

    def run_manager(self) -> None:
        while True:
            with self.cond:
                while not self._stopped and not self._should_run_locked():
                    self.cond.wait(1.0)
                    self._expire_locked(time.monotonic())
                if self._stopped:
                    return
            # Targets already behind the clock resolve without unpausing.
            self._mark_from_clock()
            with self.cond:
                if self._stopped or not self._should_run_locked():
                    continue
            try:
                with self._lease():
                    with self.cond:
                        self._running = True
                        self.unpause_windows += 1
                    while True:
                        with self.cond:
                            self._expire_locked(time.monotonic())
                            if self._stopped or not self._should_run_locked():
                                break
                        self._mark_from_clock()
                        time.sleep(self._poll_seconds)
            except Exception:  # noqa: BLE001 - the lease can fail mid-shutdown; retry
                time.sleep(1.0)
            finally:
                with self.cond:
                    self._running = False
                    self.cond.notify_all()

    def _mark_from_clock(self) -> None:
        date, year, month = self._get_clock()
        with self.cond:
            self._mark_reached_locked(date, year, month)

    # -- mode hooks (the realtime subclass overrides these) ---------------------------------

    mode = "paused"

    def start_clock(self) -> bool:
        """Realtime only: let the clock run. Returns whether this call started it."""
        return False

    @property
    def horizon_reached(self) -> bool:
        with self.cond:
            return self._horizon_reached

    _horizon_reached = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_manager, name="clock-barrier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self.cond:
            self._stopped = True
            self.cond.notify_all()


class RealtimeClock(ClockBarrier):
    """The real-time variant: the same seat-facing surface, a different manager rule.

    The clock is stopped until `start_clock()` (the first seat action, or the admin), then
    holds the lease continuously — thinking, building and waiting all cost game time — until
    the horizon year arrives, when the lease is released and the game stays paused. Waits are
    plain sleeps until a game date; peers never gate them. Retiring a seat changes nothing.
    """

    mode = "realtime"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._started = False

    def start_clock(self) -> bool:
        with self.cond:
            if self._started:
                return False
            self._started = True
            self.cond.notify_all()
            return True

    def _started_locked(self) -> bool:
        return self._started

    def _should_run_locked(self) -> bool:
        return self._started and not self._horizon_reached


__all__ = ["ADMIN", "ClockBarrier", "RealtimeClock", "SeatWait"]
