"""OpenTTD admin-port client (TCP 3977): the control plane's only channel to the game.

Threaded and blocking by design (the server core is threaded): one reader thread
parses packets into (a) subscription mirrors (date, company info/economy/stats),
(b) GameScript replies matched by request id (one packet per batch item + `done`),
(c) pushes (no id) handed to a callback, (d) rcon output/END acks.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("openttd.admin")

# Admin -> server
ADMIN_JOIN, ADMIN_QUIT, ADMIN_UPDATE_FREQUENCY, ADMIN_POLL, ADMIN_CHAT, ADMIN_RCON, ADMIN_GAMESCRIPT, ADMIN_PING = (
    range(8)
)
# Server -> admin
SERVER_FULL, SERVER_BANNED, SERVER_ERROR, SERVER_PROTOCOL, SERVER_WELCOME, SERVER_NEWGAME, SERVER_SHUTDOWN = range(
    100, 107
)
SERVER_DATE = 107
SERVER_COMPANY_NEW, SERVER_COMPANY_INFO, SERVER_COMPANY_UPDATE, SERVER_COMPANY_REMOVE = 113, 114, 115, 116
SERVER_COMPANY_ECONOMY, SERVER_COMPANY_STATS, SERVER_CHAT, SERVER_RCON, SERVER_CONSOLE = 117, 118, 119, 120, 121
SERVER_GAMESCRIPT, SERVER_RCON_END, SERVER_PONG = 124, 125, 126

# Update types / frequencies (frequency is a bit set: 1 << enum)
UPDATE_DATE, UPDATE_CLIENT_INFO, UPDATE_COMPANY_INFO, UPDATE_COMPANY_ECONOMY, UPDATE_COMPANY_STATS = range(5)
UPDATE_CHAT, UPDATE_CONSOLE, UPDATE_CMD_NAMES, UPDATE_CMD_LOGGING, UPDATE_GAMESCRIPT = range(5, 10)
FREQ_POLL, FREQ_DAILY, FREQ_WEEKLY, FREQ_MONTHLY, FREQ_QUARTERLY, FREQ_ANNUALLY, FREQ_AUTOMATIC = (
    1 << i for i in range(7)
)

GS_JSON_LIMIT = 9000 - 1  # NETWORK_GAMESCRIPT_JSON_LENGTH includes the terminator


class AdminPortError(RuntimeError):
    pass


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.p = payload
        self.o = 0

    def u8(self) -> int:
        v = self.p[self.o]
        self.o += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.p, self.o)[0]
        self.o += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.p, self.o)[0]
        self.o += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.p, self.o)[0]
        self.o += 8
        return v

    def boolean(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        end = self.p.index(b"\0", self.o)
        s = self.p[self.o : end].decode("utf-8", errors="replace")
        self.o = end + 1
        return s


class GSRequest:
    """One admin->GS request awaiting its per-item replies and `done`."""

    def __init__(self, request_id: str, expected: int) -> None:
        self.id = request_id
        self.expected = expected
        self.items: dict[int, dict[str, Any]] = {}
        self.done: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.event = threading.Event()
        self.started = time.monotonic()


class AdminClient:
    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        on_push: Callable[[dict[str, Any]], None] | None = None,
        on_mirror: Callable[[str, dict[str, Any]], None] | None = None,
        name: str = "openttd-bench control plane",
    ) -> None:
        self.host, self.port, self.password, self.name = host, port, password, name
        self.on_push = on_push
        self.on_mirror = on_mirror
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._pending: dict[str, GSRequest] = {}
        self._pending_lock = threading.Lock()
        self._rcon_lock = threading.Lock()
        self._rcon_state: dict[str, Any] | None = None
        self._rcon_event = threading.Event()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self.last_error: str | None = None
        self.welcome: dict[str, Any] = {}
        self.date: int | None = None
        self.date_at: float = 0.0
        self.companies: dict[int, dict[str, Any]] = {}
        self.economy: dict[int, dict[str, Any]] = {}
        self.stats: dict[int, dict[str, Any]] = {}
        self.packets = 0

    # -- lifecycle ---------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="admin-port", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._ready.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._fail_pending(AdminPortError("admin client stopped"))

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    # -- requests ------------------------------------------------------------------

    def gs_send(self, payload: dict[str, Any], expected_items: int, timeout: float) -> GSRequest:
        """Send one JSON request to the GameScript and wait for its replies."""
        if not self.wait_ready(min(timeout, 10)):
            raise AdminPortError(f"admin port not ready: {self.last_error or 'connecting'}")
        request_id = str(payload["id"])
        req = GSRequest(request_id, expected_items)
        with self._pending_lock:
            self._pending[request_id] = req
        try:
            raw = json.dumps(payload, separators=(",", ":")).encode()
            if len(raw) > GS_JSON_LIMIT:
                raise AdminPortError(f"GameScript request too large ({len(raw)} B > {GS_JSON_LIMIT} B)")
            self._send(ADMIN_GAMESCRIPT, raw + b"\0")
            if not req.event.wait(timeout):
                raise AdminPortError(f"GameScript did not answer request {request_id} within {timeout:.0f}s")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if req.error is not None:
            raise req.error
        return req

    def next_id(self, prefix: str = "r") -> str:
        with self._pending_lock:
            self._seq += 1
            return f"{prefix}{self._seq}"

    def rcon(self, command: str, timeout: float = 15) -> list[str]:
        if not command or any(c in command for c in "\0\r\n"):
            raise ValueError("rcon command must be one non-empty line")
        with self._rcon_lock:
            if not self.wait_ready(min(timeout, 10)):
                raise AdminPortError(f"admin port not ready: {self.last_error or 'connecting'}")
            self._rcon_state = {"command": command, "output": []}
            self._rcon_event.clear()
            self._send(ADMIN_RCON, command.encode() + b"\0")
            if not self._rcon_event.wait(timeout):
                self._rcon_state = None
                raise AdminPortError(f"rcon {command!r} was not acknowledged within {timeout}s")
            state, self._rcon_state = self._rcon_state, None
            if state is None or state.get("error"):
                raise AdminPortError(f"rcon {command!r} failed: {state and state.get('error')}")
            return list(state["output"])

    def poll(self, update_type: int, target: int = 0xFFFFFFFF) -> None:
        self._send(ADMIN_POLL, struct.pack("<BI", update_type, target))

    def ping(self) -> None:
        self._send(ADMIN_PING, struct.pack("<I", int(time.time()) & 0xFFFFFFFF))

    # -- wire ----------------------------------------------------------------------

    def _send(self, packet_type: int, payload: bytes = b"") -> None:
        packet = struct.pack("<HB", len(payload) + 3, packet_type) + payload
        with self._send_lock:
            sock = self._sock
            if sock is None:
                raise AdminPortError("admin port disconnected")
            sock.sendall(packet)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_and_read()
            except (OSError, AdminPortError, ValueError, json.JSONDecodeError) as exc:
                self.last_error = str(exc)
                log.warning("admin port: %s", exc)
            finally:
                self._ready.clear()
                sock, self._sock = self._sock, None
                if sock is not None:
                    sock.close()
                self._fail_pending(AdminPortError("admin port disconnected"))
            self._stop.wait(0.5)

    def _connect_and_read(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.settimeout(None)
        self._sock = sock
        self._send(ADMIN_JOIN, self.password.encode() + b"\0" + self.name.encode() + b"\0" + b"3.0\0")
        buf = bytearray()
        while not self._stop.is_set():
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise AdminPortError("admin port closed the connection")
            buf += chunk
            while len(buf) >= 2:
                size = struct.unpack_from("<H", buf, 0)[0]
                if size < 3:
                    raise AdminPortError(f"invalid packet size {size}")
                if len(buf) < size:
                    break
                packet = bytes(buf[2:size])
                del buf[:size]
                self.packets += 1
                self._handle(packet[0], packet[1:])

    def _handle(self, ptype: int, payload: bytes) -> None:
        r = _Reader(payload)
        if ptype == SERVER_ERROR:
            raise AdminPortError(f"admin port rejected us (error {payload[0] if payload else '?'})")
        if ptype == SERVER_PROTOCOL:
            return
        if ptype == SERVER_WELCOME:
            self.welcome = {
                "server_name": r.string(),
                "revision": r.string(),
                "dedicated": r.boolean(),
                "map_name": r.string(),
                "seed": r.u32(),
                "landscape": r.u8(),
                "start_date": r.u32(),
                "map_x": r.u16(),
                "map_y": r.u16(),
            }
            for update, freq in (
                (UPDATE_DATE, FREQ_DAILY),
                (UPDATE_COMPANY_INFO, FREQ_AUTOMATIC),
                (UPDATE_COMPANY_ECONOMY, FREQ_WEEKLY),
                (UPDATE_COMPANY_STATS, FREQ_WEEKLY),
                (UPDATE_GAMESCRIPT, FREQ_AUTOMATIC),
            ):
                self._send(ADMIN_UPDATE_FREQUENCY, struct.pack("<HH", update, freq))
            self.poll(UPDATE_DATE, 0)
            self.poll(UPDATE_COMPANY_INFO)
            self.poll(UPDATE_COMPANY_ECONOMY)
            self.last_error = None
            self._ready.set()
            return
        if ptype in (SERVER_NEWGAME, SERVER_SHUTDOWN):
            log.warning("admin port: server %s", "new game" if ptype == SERVER_NEWGAME else "shutdown")
            return
        if ptype == SERVER_DATE:
            self.date = r.u32()
            self.date_at = time.monotonic()
            return
        if ptype == SERVER_COMPANY_NEW:
            company = r.u8()
            self.companies.setdefault(company, {"id": company})
            self.poll(UPDATE_COMPANY_INFO, company)
            self._mirror("company_new", {"id": company})
            return
        if ptype == SERVER_COMPANY_INFO:
            row = {
                "id": r.u8(),
                "name": r.string(),
                "president": r.string(),
                "colour": r.u8(),
                "passworded": r.boolean(),
                "inaugurated_year": r.u32(),
                "is_ai": r.boolean(),
                "quarters_of_bankruptcy": r.u8(),
                "removed": False,
            }
            self.companies[row["id"]] = row
            self._mirror("company_info", row)
            return
        if ptype == SERVER_COMPANY_UPDATE:
            company = r.u8()
            row = self.companies.setdefault(company, {"id": company})
            row.update(
                {
                    "name": r.string(),
                    "president": r.string(),
                    "colour": r.u8(),
                    "passworded": r.boolean(),
                    "quarters_of_bankruptcy": r.u8(),
                }
            )
            return
        if ptype == SERVER_COMPANY_REMOVE:
            company, reason = r.u8(), r.u8()
            row = self.companies.setdefault(company, {"id": company})
            row["removed"] = True
            row["remove_reason"] = reason
            self._mirror("company_remove", {"id": company, "reason": reason})
            return
        if ptype == SERVER_COMPANY_ECONOMY:
            row = {
                "id": r.u8(),
                "money": r.i64(),
                "loan": r.i64(),
                "income": r.i64(),
                "delivered_this_quarter": r.u16(),
                "history": [],
            }
            for _ in range(2):
                row["history"].append({"value": r.i64(), "performance": r.u16(), "delivered": r.u16()})
            row["at_date"] = self.date
            self.economy[row["id"]] = row
            return
        if ptype == SERVER_COMPANY_STATS:
            row = {"id": r.u8(), "vehicles": [r.u16() for _ in range(5)], "stations": [r.u16() for _ in range(5)]}
            self.stats[row["id"]] = row
            return
        if ptype == SERVER_RCON:
            r.u16()
            line = r.string()
            state = self._rcon_state
            if state is not None:
                state["output"].append(line)
            return
        if ptype == SERVER_RCON_END:
            command = r.string()
            state = self._rcon_state
            if state is not None:
                if command != state["command"]:
                    state["error"] = f"acknowledged {command!r}, expected {state['command']!r}"
                self._rcon_event.set()
            return
        if ptype == SERVER_GAMESCRIPT:
            text = r.string()
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                log.warning("unparseable GameScript packet: %.200s", text)
                return
            if not isinstance(message, dict):
                return
            request_id = message.get("id")
            if request_id is not None:
                with self._pending_lock:
                    req = self._pending.get(str(request_id))
                if req is not None:
                    if message.get("done"):
                        req.done = message
                        req.event.set()
                    elif "i" in message:
                        req.items[int(message["i"])] = message
                        if req.expected == 0:
                            req.event.set()
                    else:
                        # single-reply requests (configure, ping)
                        req.items[0] = message
                        req.done = message
                        req.event.set()
                    return
            if "push" in message and self.on_push is not None:
                try:
                    self.on_push(message)
                except Exception:  # noqa: BLE001
                    log.exception("push handler failed")
            return
        if ptype == SERVER_PONG:
            return
        # chat, console, client packets: ignored

    def _mirror(self, kind: str, row: dict[str, Any]) -> None:
        if self.on_mirror is not None:
            try:
                self.on_mirror(kind, row)
            except Exception:  # noqa: BLE001
                log.exception("mirror handler failed")

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for req in pending:
            req.error = error
            req.event.set()
        state = self._rcon_state
        if state is not None:
            state["error"] = str(error)
            self._rcon_event.set()


__all__ = ["AdminClient", "AdminPortError", "GSRequest", "GS_JSON_LIMIT"]
