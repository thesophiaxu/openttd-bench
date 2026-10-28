"""Fair GameScript dispatch: per-seat queues, round-robin, token buckets (plan 9.2).

The game provides no throttling, so this is the throttle. A batch is split into
admin-port packets (<= 9000 B each); packets from different seats interleave, one in
flight per seat, so one seat's 100-item batch cannot block a rival's single read.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any

from server.admin_client import GS_JSON_LIMIT, AdminClient, AdminPortError

log = logging.getLogger("openttd.dispatch")


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill = refill_per_second
        self.tokens = capacity
        self.at = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.refill)
        self.at = now

    def take(self, cost: float) -> bool:
        self._tick()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def level(self) -> float:
        self._tick()
        return self.tokens


class Packet:
    def __init__(self, seat: int, items: list[dict[str, Any]], cost: float) -> None:
        self.seat = seat
        self.items = items
        self.cost = cost
        self.result: dict[int, dict[str, Any]] = {}
        self.done: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.event = threading.Event()
        self.queued_at = time.monotonic()
        self.sent_at: float | None = None


class Dispatcher:
    def __init__(
        self,
        admin: AdminClient,
        max_inflight: int = 3,
        bucket_capacity: float = 400.0,
        bucket_refill: float = 120.0,
        timeout: float = 60.0,
    ) -> None:
        self.admin = admin
        self.max_inflight = max_inflight
        self.timeout = timeout
        self._cond = threading.Condition()
        self._queues: dict[int, deque[Packet]] = {}
        self._inflight: dict[int, Packet] = {}
        self._buckets: dict[int, TokenBucket] = {}
        self._bucket_capacity = bucket_capacity
        self._bucket_refill = bucket_refill
        self._order: deque[int] = deque()
        self._stopped = False
        self._thread: threading.Thread | None = None
        self.stats = {"packets": 0, "items": 0, "wait_ms_total": 0.0, "gs_ms_total": 0.0}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="gs-dispatch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def bucket(self, seat: int) -> TokenBucket:
        return self._buckets.setdefault(seat, TokenBucket(self._bucket_capacity, self._bucket_refill))

    def depth(self, seat: int) -> int:
        with self._cond:
            return len(self._queues.get(seat, ())) + (1 if seat in self._inflight else 0)

    def quota(self, seat: int) -> dict[str, Any]:
        b = self.bucket(seat)
        return {
            "tokens": round(b.level(), 1),
            "capacity": b.capacity,
            "refill_per_second": b.refill,
            "queue_depth": self.depth(seat),
        }

    # -- submission ----------------------------------------------------------------

    def execute(self, seat: int, items: list[dict[str, Any]], stop_on_error: bool) -> dict[int, dict[str, Any]]:
        """Run `items` (already coerced, each {i, cmd, args, validate, cost}) for `seat`;
        returns replies keyed by each item's `i` (its index in the caller's batch)."""
        results: dict[int, dict[str, Any]] = {}
        for packet_items in self._split(seat, items):
            packet = Packet(seat, packet_items, sum(float(i.get("cost", 1)) for i in packet_items))
            with self._cond:
                queue = self._queues.setdefault(seat, deque())
                queue.append(packet)
                if seat not in self._order:
                    self._order.append(seat)
                self._cond.notify_all()
            if not packet.event.wait(self.timeout + 30):
                raise AdminPortError("GameScript batch timed out")
            if packet.error is not None:
                raise packet.error
            failed = False
            for item in packet_items:
                reply = packet.result.get(item["i"])
                if reply is None:
                    reply = {"i": item["i"], "ok": False, "error": "no reply from the GameScript"}
                results[item["i"]] = reply
                if not reply.get("ok", False):
                    failed = True
            if failed and stop_on_error:
                break
        for item in items:
            results.setdefault(
                item["i"], {"i": item["i"], "ok": False, "error": "skipped (stop_on_error)", "skipped": True}
            )
        return results

    def _split(self, seat: int, items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        packets: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for item in items:
            candidate = current + [item]
            size = len(
                json.dumps(
                    {"id": "r9999999", "seat": seat, "items": [self._wire(i) for i in candidate]}, separators=(",", ":")
                ).encode()
            )
            if current and size > GS_JSON_LIMIT - 200:
                packets.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            packets.append(current)
        return packets

    @staticmethod
    def _wire(item: dict[str, Any]) -> dict[str, Any]:
        wire = {"i": item["i"], "cmd": item["cmd"], "args": item["args"]}
        if item.get("validate"):
            wire["validate"] = True
        return wire

    # -- the scheduler ---------------------------------------------------------------

    def _pick_locked(self) -> Packet | None:
        for _ in range(len(self._order)):
            seat = self._order[0]
            self._order.rotate(-1)
            queue = self._queues.get(seat)
            if not queue or seat in self._inflight:
                continue
            packet = queue[0]
            if not self.bucket(seat).take(packet.cost):
                continue
            queue.popleft()
            self._inflight[seat] = packet
            return packet
        return None

    def _loop(self) -> None:
        while True:
            with self._cond:
                packet = None
                while not self._stopped:
                    if len(self._inflight) < self.max_inflight:
                        packet = self._pick_locked()
                        if packet is not None:
                            break
                    self._cond.wait(0.05)
                if self._stopped:
                    return
            threading.Thread(target=self._send, args=(packet,), name="gs-send", daemon=True).start()

    def _send(self, packet: Packet) -> None:
        request_id = self.admin.next_id("b")
        payload = {"id": request_id, "seat": packet.seat, "items": [self._wire(i) for i in packet.items]}
        packet.sent_at = time.monotonic()
        try:
            req = self.admin.gs_send(payload, len(packet.items), self.timeout)
            packet.result = req.items
            packet.done = req.done
        except BaseException as exc:  # noqa: BLE001
            packet.error = exc
        finally:
            now = time.monotonic()
            self.stats["packets"] += 1
            self.stats["items"] += len(packet.items)
            self.stats["wait_ms_total"] += (packet.sent_at - packet.queued_at) * 1000
            self.stats["gs_ms_total"] += (now - packet.sent_at) * 1000
            with self._cond:
                self._inflight.pop(packet.seat, None)
                self._cond.notify_all()
            packet.event.set()


__all__ = ["Dispatcher", "Packet", "TokenBucket"]
