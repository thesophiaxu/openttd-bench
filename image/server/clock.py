"""The pause lease: the game runs only while someone holds it.

Reference-counted: the first holder unpauses, the last release pauses. Transitions
are confirmed against the GameScript's own pause state (`sys.status`), never
trusted from the rcon acknowledgement alone.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("openttd.clock")


class Clock:
    def __init__(self, rcon: Callable[[str], list[str]], status: Callable[[], dict[str, Any]]) -> None:
        self._rcon = rcon
        self._status = status
        self._lock = threading.RLock()
        self._holders = 0
        self.running = False
        self.transitions = 0

    def _confirm(self, paused: bool, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self._status()
            if bool(last.get("paused")) == paused:
                return last
            time.sleep(0.05)
        raise RuntimeError(f"game did not {'pause' if paused else 'unpause'} within {timeout}s: {last}")

    def pause(self) -> dict[str, Any]:
        with self._lock:
            self._rcon("pause")
            state = self._confirm(True)
            self.running = False
            self.transitions += 1
            return state

    def unpause(self) -> dict[str, Any]:
        with self._lock:
            self._rcon("unpause")
            state = self._confirm(False)
            self.running = True
            self.transitions += 1
            return state

    def acquire(self) -> None:
        with self._lock:
            self._holders += 1
            if self._holders == 1:
                try:
                    self.unpause()
                except Exception:
                    self._holders -= 1
                    raise

    def release(self) -> None:
        with self._lock:
            if self._holders <= 0:
                return
            self._holders -= 1
            if self._holders == 0:
                self.pause()

    @contextmanager
    def hold(self):
        self.acquire()
        try:
            yield
        finally:
            self.release()

    @property
    def holders(self) -> int:
        with self._lock:
            return self._holders


__all__ = ["Clock"]
