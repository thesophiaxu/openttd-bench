"""The control plane HTTP surface (plan section 9): FastAPI over a threaded core.

Auth: bearer token -> company (seats) or the admin token. Everything except /health
and /api/docs is token-gated. There is no single-command route: a call is a batch of one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from server import gamedate
from server.accumulator import Accumulator
from server.admin_client import AdminClient, AdminPortError
from server.barrier import ADMIN, ClockBarrier, RealtimeClock
from server.clock import Clock
from server.dispatcher import Dispatcher
from server.policy import NotVisible, event_audience
from server.policy import apply as apply_policy
from server.schema import Schema, SchemaError

log = logging.getLogger("openttd.app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

RUN_DIR = Path(os.environ.get("OPENTTD_RUN_DIR", "/run/openttd"))
WORLD = json.loads(Path(os.environ["OPENTTD_WORLD"]).read_text())
SEATS = json.loads(Path(os.environ["OPENTTD_SEATS"]).read_text())
SCHEMA = Schema.load(os.environ.get("OPENTTD_SCHEMA", "/opt/openttd/schema.json"))
ADMIN_PASSWORD = os.environ["OPENTTD_ADMIN_PASSWORD"]
ADMIN_PORT = int(os.environ.get("OPENTTD_ADMIN_PORT", WORLD["ports"]["admin"]))
WAIT_CHUNK_SECONDS = float(os.environ.get("OPENTTD_WAIT_CHUNK_SECONDS", "50"))
DETACH_GRACE = float(os.environ.get("OPENTTD_DETACH_GRACE", "180"))
MAX_BATCH = 100
BOOT_MARKER = RUN_DIR / "boot-complete"

SEAT_TOKENS: dict[str, int] = {str(t): int(c) for t, c in SEATS["tokens"].items()}
ADMIN_TOKEN: str = str(SEATS["admin"])
LLM_SEATS: list[int] = sorted(set(SEAT_TOKENS.values()))
ALLOWED_MODES: set[str] = set(WORLD["rules"]["allowed_modes"])
HORIZON_YEAR: int = int(WORLD["rules"]["horizon_year"])
CLOCK_MODE: str = str(WORLD["rules"].get("clock", "paused"))
START_YEAR: int = int(WORLD["map"]["start_year"])
CARGO_LABELS: dict[int, str] = {int(r["id"]): r["label"] for r in WORLD["cargo_table"]}
MONITORED: list[int] = [int(c) for c in WORLD["monitor"].get("cargos", [])]
SEAT_NAMES: dict[int, str] = {int(c["slot"]): c["name"] for c in WORLD["companies"]}


class Core:
    """Everything stateful, threaded; the FastAPI layer only translates HTTP."""

    def __init__(self) -> None:
        self.started = time.time()
        self.lock = threading.Lock()
        self.company_states: dict[int, dict[str, Any]] = {}
        self.events: dict[int, deque[dict[str, Any]]] = {c: deque(maxlen=500) for c in LLM_SEATS}
        self.admin_events: deque[dict[str, Any]] = deque(maxlen=2000)
        self.tickets: dict[int, dict[str, Any]] = {}
        self.ticket_cond = threading.Condition()
        self.idempotency: dict[int, OrderedDict[str, Any]] = {c: OrderedDict() for c in LLM_SEATS}
        self.gs_ready = False
        self.gs_alive = False
        self.configured: dict[str, Any] | None = None
        self.retired: set[int] = set()
        self.accumulator = Accumulator(CARGO_LABELS, MONITORED)
        self.admin = AdminClient("127.0.0.1", ADMIN_PORT, ADMIN_PASSWORD, on_push=self.on_push)
        self.dispatcher = Dispatcher(self.admin)
        self.clock = Clock(self.admin.rcon, self.status)
        barrier_class = RealtimeClock if CLOCK_MODE == "realtime" else ClockBarrier
        self.barrier = barrier_class(
            set(LLM_SEATS), self.clock_triple, self.clock.hold, HORIZON_YEAR, detach_grace=DETACH_GRACE
        )
        self.lease_refs = 0

    def start(self) -> None:
        self.admin.start()
        self.dispatcher.start()
        self.barrier.start()

    # -- game facts ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The GameScript's own view: paused flag, date, tick. Used to confirm transitions."""
        return self.admin_batch([{"cmd": "sys.status", "args": {}}])[0].get("result", {})

    def clock_triple(self) -> tuple[int | None, int | None, int | None]:
        date = self.admin.date
        if date is None:
            return None, None, None
        year, month, _ = gamedate.ymd_from_date(date)
        return date, year, month

    def date_view(self) -> dict[str, Any]:
        date = self.admin.date
        if date is None:
            return {"date": None, "year": None, "month": None, "day": None}
        year, month, day = gamedate.ymd_from_date(date)
        return {"date": date, "year": year, "month": month + 1, "day": day, "text": f"{year}-{month + 1:02d}-{day:02d}"}

    # -- GS access --------------------------------------------------------------------

    def admin_batch(self, items: list[dict[str, Any]], timeout: float = 60) -> list[dict[str, Any]]:
        payload = {
            "id": self.admin.next_id("a"),
            "seat": ADMIN,
            "items": [{"i": i, "cmd": it["cmd"], "args": it.get("args", {})} for i, it in enumerate(items)],
        }
        req = self.admin.gs_send(payload, len(items), timeout)
        return [req.items.get(i, {"i": i, "ok": False, "error": "no reply"}) for i in range(len(items))]

    def ping(self, timeout: float = 5.0) -> dict[str, Any]:
        payload = {"id": self.admin.next_id("p"), "ping": True}
        req = self.admin.gs_send(payload, 0, timeout)
        reply = req.items.get(0) or req.done or {}
        with self.lock:
            self.gs_alive = True
        return reply

    def configure(self) -> dict[str, Any]:
        config = {
            "llm_seats": WORLD["llm_seats"],
            "companies": [
                {"slot": c["slot"], "starting_cash": c["starting_cash"], "starting_loan": c["starting_loan"]}
                for c in WORLD["companies"]
            ],
            "monitor": WORLD["monitor"],
        }
        payload = {"id": self.admin.next_id("cfg"), "configure": config}
        req = self.admin.gs_send(payload, 0, 30)
        reply = req.items.get(0) or req.done or {}
        if not reply.get("ok"):
            raise AdminPortError(f"GameScript refused configure: {reply}")
        with self.lock:
            self.configured = reply
            self.gs_ready = True
            self.gs_alive = True
        labels = {int(r["id"]): r["label"] for r in reply.get("cargos", [])}
        self.accumulator.set_labels(labels or CARGO_LABELS, MONITORED)
        return reply

    # -- pushes from the GameScript -------------------------------------------------------

    def on_push(self, message: dict[str, Any]) -> None:
        kind = message.get("push")
        if kind == "ready":
            with self.lock:
                self.gs_ready = False
                self.gs_alive = True
            return
        if kind == "monitor":
            self.accumulator.add(
                int(message["company"]),
                int(message["cargo"]),
                str(message["kind"]),
                int(message["target"]),
                int(message["amount"]),
            )
            return
        if kind == "company":
            with self.lock:
                self.company_states[int(message["company"])] = message.get("state", {})
            return
        if kind == "ticket":
            ticket = int(message["ticket"])
            with self.ticket_cond:
                row = self.tickets.setdefault(ticket, {"ticket": ticket, "company": message.get("company")})
                row.update(
                    {
                        "status": "done" if message.get("ok") else "failed",
                        "ok": bool(message.get("ok")),
                        "error": message.get("error"),
                        "ticks": message.get("ticks"),
                        "orders_before": message.get("orders_before"),
                        "orders_after": message.get("orders_after"),
                        "resolved_at": time.time(),
                    }
                )
                self.ticket_cond.notify_all()
            self.record_event({"type": "order_ticket", "push": "ticket", **message})
            return
        if kind in ("event", "company_funded"):
            event = dict(message)
            event.setdefault("type", kind)
            self.record_event(event)

    def record_event(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.pop("push", None) if event.get("type") != "order_ticket" else None
        event["time"] = round(time.time() - self.started, 2)
        event["game_date"] = self.admin.date
        for seat in event_audience(event, LLM_SEATS):
            self.events[seat].append(event)
        self.admin_events.append(event)

    # -- batches --------------------------------------------------------------------------

    def run_batch(self, seat: int, items: list[dict[str, Any]], stop_on_error: bool) -> dict[str, Any]:
        if len(items) > MAX_BATCH:
            raise HTTPException(400, f"a batch holds at most {MAX_BATCH} items")
        if not self.gs_ready:
            raise HTTPException(503, "the game is still booting")
        self.seat_acts(seat)
        self.barrier.cancel_detached(seat)
        prepared: list[dict[str, Any]] = []
        results: list[dict[str, Any] | None] = [None] * len(items)
        local: dict[int, dict[str, Any]] = {}
        for index, item in enumerate(items):
            cmd = str(item.get("cmd", ""))
            try:
                spec = SCHEMA.get(cmd)
                if spec["admin_only"]:
                    raise SchemaError(f"{cmd} is admin-only")
                validate = bool(item.get("validate", False))
                if validate and not spec["validate"]:
                    raise SchemaError(f"{cmd} has no validate twin")
                if not spec["read_only"] and spec["mode"] and spec["mode"] not in ALLOWED_MODES:
                    raise NotVisible(
                        f"{cmd}: transport mode '{spec['mode']}' is not allowed in this task "
                        f"(allowed: {sorted(ALLOWED_MODES)})"
                    )
                args = SCHEMA.coerce(cmd, item.get("args"))
                if cmd == "vehicles.build":
                    args["allowed_types"] = sorted(ALLOWED_MODES)
                if spec["executor"] == "CP":
                    local[index] = {"i": index, "cmd": cmd, "ok": True, "result": self.local_command(seat, cmd, args)}
                    continue
                prepared.append({"i": index, "cmd": cmd, "args": args, "validate": validate, "cost": spec["cost"]})
            except (SchemaError, NotVisible, KeyError) as exc:
                local[index] = {"i": index, "cmd": cmd, "ok": False, "error": str(exc), "rejected": True}
                if stop_on_error:
                    break
        if prepared:
            try:
                replies = self.dispatcher.execute(seat, prepared, stop_on_error)
            except AdminPortError as exc:
                raise HTTPException(503, f"game unavailable: {exc}") from exc
            for item in prepared:
                reply = dict(
                    replies.get(item["i"]) or {"i": item["i"], "ok": False, "error": "no reply from the GameScript"}
                )
                reply["cmd"] = item["cmd"]
                if reply.get("ok") and "result" in reply:
                    try:
                        reply["result"] = apply_policy(item["cmd"], seat, reply["result"])
                    except NotVisible as exc:
                        reply = {"i": item["i"], "cmd": item["cmd"], "ok": False, "error": str(exc)}
                spec = SCHEMA.get(item["cmd"])
                if not spec["read_only"] and not item["validate"]:
                    receipt = {
                        "type": "receipt",
                        "cmd": item["cmd"],
                        "ok": bool(reply.get("ok")),
                        "company": seat,
                        "source": "receipt",
                    }
                    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
                    for key in ("cost", "error", "ticket", "vehicle", "station", "queued"):
                        if key in result:
                            receipt[key] = result[key]
                    if reply.get("error"):
                        receipt["error"] = reply["error"]
                    self.record_event(receipt)
                    if item["cmd"].startswith("orders.") and isinstance(result, dict) and "ticket" in result:
                        self.open_ticket(int(result["ticket"]), seat, item["cmd"])
                results[item["i"]] = reply
        for index, reply in local.items():
            results[index] = reply
        for index, item in enumerate(items):
            if results[index] is None:
                results[index] = {
                    "i": index,
                    "cmd": item.get("cmd"),
                    "ok": False,
                    "error": "skipped (stop_on_error)",
                    "skipped": True,
                }
        stopped_at = next((i for i, r in enumerate(results) if r and r.get("skipped")), None)
        return {"results": results, "count": len(results), "stopped_at": stopped_at, "date": self.date_view()}

    def local_command(self, seat: int, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        if cmd == "inspect.deliveries":
            cargo = int(args.get("cargo", -1))
            label = CARGO_LABELS.get(cargo) if cargo >= 0 else None
            return self.accumulator.view(seat, label)
        if cmd == "orders.ticket":
            return self.ticket_view(seat, int(args["ticket"]))
        raise SchemaError(f"{cmd} is not served locally")

    # -- tickets (the order-relay micro-lease) ----------------------------------------------

    def open_ticket(self, ticket: int, seat: int, cmd: str) -> None:
        with self.ticket_cond:
            # The GameScript may have resolved the ticket before this batch reply landed.
            row = self.tickets.setdefault(ticket, {"ticket": ticket, "status": "pending", "ok": None, "error": None})
            row.setdefault("opened_at", time.time())
            row["company"] = seat
            row["cmd"] = cmd
            pending = row.get("status") == "pending"
        if pending:
            threading.Thread(
                target=self._lease_until_resolved, args=(ticket,), name=f"ticket-{ticket}", daemon=True
            ).start()

    def _lease_until_resolved(self, ticket: int) -> None:
        """Hold the clock micro-lease while the ticket is outstanding (AIs cannot run paused)."""
        try:
            with self.clock.hold():
                with self.ticket_cond:
                    deadline = time.monotonic() + 30
                    while self.tickets.get(ticket, {}).get("status") == "pending" and time.monotonic() < deadline:
                        self.ticket_cond.wait(0.1)
        except Exception as exc:  # noqa: BLE001
            log.warning("ticket %s lease failed: %s", ticket, exc)

    def ticket_view(self, seat: int, ticket: int) -> dict[str, Any]:
        with self.ticket_cond:
            row = self.tickets.get(ticket)
            if row is None or row.get("company") != seat:
                raise SchemaError(f"unknown ticket {ticket}")
            out = dict(row)
        out["age_seconds"] = round(time.time() - out.get("opened_at", time.time()), 2)
        return out

    def wait_ticket(self, seat: int, ticket: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.ticket_cond:
            while True:
                row = self.tickets.get(ticket)
                if row is None or row.get("company") != seat:
                    raise SchemaError(f"unknown ticket {ticket}")
                if row.get("status") != "pending" or time.monotonic() >= deadline:
                    return self.ticket_view(seat, ticket)
                self.ticket_cond.wait(min(0.2, max(0.01, deadline - time.monotonic())))

    # -- waits ---------------------------------------------------------------------------

    def resolve_target(self, body: dict[str, Any]) -> tuple[str, int]:
        resume = body.get("resume")
        if isinstance(resume, dict) and "kind" in resume and "value" in resume:
            kind, value = str(resume["kind"]), int(resume["value"])
        else:
            date = self.admin.date
            if date is None:
                raise HTTPException(503, "the clock is not known yet")
            year, month, _ = gamedate.ymd_from_date(date)
            if body.get("until_date") is not None:
                kind, value = "date", int(body["until_date"])
            elif body.get("until_year") is not None:
                kind, value = "year", int(body["until_year"])
            elif body.get("years") is not None:
                kind, value = "year", year + int(body["years"])
            elif body.get("months") is not None:
                kind, value = "date", gamedate.add_months(date, int(body["months"]))
            elif body.get("days") is not None:
                kind, value = "date", date + int(body["days"])
            elif body.get("until_horizon"):
                kind, value = "year", HORIZON_YEAR
            else:
                raise HTTPException(
                    400, "wait needs one of: months, years, days, until_date, until_year, until_horizon"
                )
        # Clamp to the horizon: nothing can be earned past it.
        if kind == "year":
            value = min(value, HORIZON_YEAR)
        elif kind == "date":
            value = min(value, gamedate.date_from_ymd(HORIZON_YEAR, 0, 1))
        return kind, value

    def seat_acts(self, seat: int) -> None:
        """A seat's first action starts a real-time clock; nothing counts past the horizon."""
        if self.barrier.mode != "realtime":
            return
        if self.barrier.horizon_reached:
            raise HTTPException(
                409,
                f"the horizon (year {HORIZON_YEAR}) has passed: the game is over and "
                "scored; no further command counts. Stop playing.",
            )
        if self.barrier.start_clock():
            log.info("real-time clock started by seat %s", seat)
            self.record_event({"type": "clock_started", "company": seat, "source": "control_plane"})

    def wait(self, seat: int, body: dict[str, Any]) -> dict[str, Any]:
        kind, value = self.resolve_target(body)
        chunk = float(body.get("chunk_seconds") or WAIT_CHUNK_SECONDS)
        chunk = max(1.0, min(chunk, WAIT_CHUNK_SECONDS))
        if seat in self.retired or seat not in self.barrier.active:
            return {"status": "retired", "date": self.date_view()}
        self.seat_acts(seat)
        try:
            wait = (
                self.barrier.resume(seat, kind, value)
                if body.get("resume")
                else self.barrier.register(seat, kind, value)
            )
        except ValueError as exc:
            return {"status": "retired", "detail": str(exc), "date": self.date_view()}
        outcome = self.barrier.block(wait, chunk)
        response = {
            "status": outcome,
            "target": {"kind": kind, "value": value},
            "date": self.date_view(),
            "horizon_year": HORIZON_YEAR,
            "clock": self.barrier.status(),
        }
        if outcome == "blocked":
            response["resume"] = {"resume": {"kind": kind, "value": value}}
            if self.barrier.mode == "realtime":
                response["hint"] = (
                    "the target date is still ahead; the clock keeps running whether you wait or "
                    "work. Re-post this `resume` body to keep waiting, or do something useful"
                )
            else:
                response["hint"] = (
                    "peers are thinking; re-post this `resume` body to keep waiting "
                    f"(the wait stays registered for {int(DETACH_GRACE)}s)"
                )
        if outcome == "superseded":
            raise HTTPException(409, "a newer wait from this seat replaced this one")
        return response

    def retire(self, seat: int) -> dict[str, Any]:
        was_active = self.barrier.retire(seat)
        self.retired.add(seat)
        self.record_event({"type": "seat_retired", "company": seat, "source": "control_plane"})
        return {"retired": seat, "was_active": was_active, "clock": self.barrier.status()}

    def run_out(self, timeout: float) -> dict[str, Any]:
        """Admin: retire every seat and run the clock to the horizon."""
        for seat in list(self.barrier.active):
            self.barrier.retire(seat)
        if self.barrier.start_clock():
            log.info("real-time clock started by run-out (no seat ever acted)")
        self.barrier.add_seat(ADMIN)
        wait = self.barrier.register(ADMIN, "year", HORIZON_YEAR)
        deadline = time.monotonic() + timeout
        outcome = "blocked"
        while time.monotonic() < deadline:
            outcome = self.barrier.block(wait, 30)
            if outcome != "blocked":
                break
            wait = self.barrier.resume(ADMIN, "year", HORIZON_YEAR)
        self.barrier.retire(ADMIN)
        return {"status": outcome, "date": self.date_view(), "horizon_year": HORIZON_YEAR}

    # -- snapshots --------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Grading-time measurements per company: GS states + accumulator + economy mirror."""
        states = {}
        snapshot_error = None
        try:
            reply = self.admin_batch([{"cmd": "sys.company_states", "args": {}}], timeout=60)[0]
            for row in reply.get("result", {}).get("companies", []):
                states[int(row["id"])] = row
        except AdminPortError as exc:
            snapshot_error = str(exc)
            log.warning("snapshot fell back to pushed states: %s", exc)
            with self.lock:
                states = dict(self.company_states)
        companies: dict[str, Any] = {}
        for company, state in states.items():
            economy = self.admin.economy.get(company, {})
            info = self.admin.companies.get(company, {})
            measurements = {
                "operating_profit": state.get("operating_profit"),
                "accounting": state.get("accounting"),
                "company_value": state.get(
                    "company_value", economy.get("history", [{}])[0].get("value", 0) if economy.get("history") else 0
                ),
                "cash": state.get("cash", economy.get("money")),
                "loan": state.get("loan", economy.get("loan")),
                "max_loan": state.get("max_loan"),
                "performance": state.get("performance"),
                "name": state.get("name") or info.get("name"),
                "is_ai": state.get("is_ai", info.get("is_ai")),
                "bankrupt": bool(state.get("bankrupt") or info.get("removed", False)),
                "vehicles": state.get("vehicles"),
                "stations": state.get("stations"),
                "rail_pieces": state.get("rail_pieces"),
                "road_pieces": state.get("road_pieces"),
                **self.accumulator.measurements(company),
            }
            companies[str(company)] = measurements
        return {
            "date": self.date_view(),
            "horizon_year": HORIZON_YEAR,
            "accounting_snapshot": {"fresh": snapshot_error is None, "error": snapshot_error},
            "companies": companies,
            "clock": self.barrier.status(),
            "retired": sorted(self.retired),
            "dispatch": dict(self.dispatcher.stats),
            "deltas": self.accumulator.deltas,
        }

    def state(self, seat: int) -> dict[str, Any]:
        return {
            "date": self.date_view(),
            "start_year": START_YEAR,
            "horizon_year": HORIZON_YEAR,
            "paused": not self.clock.running,
            "clock_mode": self.barrier.mode,
            "game_over": self.barrier.mode == "realtime" and self.barrier.horizon_reached,
            "seat": {
                "company": seat,
                "name": SEAT_NAMES.get(seat),
                "retired": seat in self.retired,
                "waiting": self.barrier.is_waiting(seat),
                "quota": self.dispatcher.quota(seat),
            },
            "clock": self.barrier.status(),
            "tickets_pending": sum(
                1 for t in self.tickets.values() if t.get("company") == seat and t.get("status") == "pending"
            ),
            "allowed_modes": sorted(ALLOWED_MODES),
            "monitored_cargos": list(self.accumulator.monitored),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if (self.admin.ready and self.gs_ready) else "starting",
            "admin_ready": self.admin.ready,
            "gs_alive": self.gs_alive,
            "gs_configured": self.gs_ready,
            "boot_complete": BOOT_MARKER.exists(),
            "date": self.date_view(),
            "running": self.clock.running,
            "clock": self.barrier.status(),
            "dispatch": dict(self.dispatcher.stats),
            "packets": self.admin.packets,
            "companies": sorted(self.admin.companies),
            "last_error": self.admin.last_error,
            "uptime": round(time.time() - self.started, 1),
        }


core = Core()
app = FastAPI(title="openttd-bench control plane", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    core.start()


# -- auth --------------------------------------------------------------------------------------


def seat_auth(authorization: str | None = Header(default=None)) -> int:
    token = _bearer(authorization)
    if token in SEAT_TOKENS:
        return SEAT_TOKENS[token]
    raise HTTPException(401, "a seat bearer token is required (OPENTTD_API_TOKEN)")


def admin_auth(authorization: str | None = Header(default=None)) -> str:
    token = _bearer(authorization)
    if ADMIN_TOKEN and secrets.compare_digest(token, ADMIN_TOKEN):
        return "admin"
    raise HTTPException(401, "admin token required")


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


# -- models ----------------------------------------------------------------------------------------


class BatchItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cmd: str
    args: dict[str, Any] = Field(default_factory=dict)
    validate_: bool = Field(False, alias="validate")


class BatchBody(BaseModel):
    items: list[BatchItem]
    stop_on_error: bool = True
    idempotency_key: str | None = None


# -- routes -----------------------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, Any]:
    return core.health()


@app.get("/api/docs")
async def docs() -> dict[str, Any]:
    return {
        "schema": SCHEMA.data,
        "cargo_table": WORLD["cargo_table"],
        "rules": WORLD["rules"],
        "map": {"size_x": WORLD["map"]["size_x_bits"], "size_y": WORLD["map"]["size_y_bits"]},
    }


@app.get("/api/state")
async def state(seat: int = Depends(seat_auth)) -> dict[str, Any]:
    return core.state(seat)


@app.post("/api/batch")
async def batch(body: BatchBody, seat: int = Depends(seat_auth)) -> dict[str, Any]:
    if body.idempotency_key:
        cache = core.idempotency[seat]
        if body.idempotency_key in cache:
            cached = dict(cache[body.idempotency_key])
            cached["replayed"] = True
            return cached
    items = [item.model_dump(by_alias=True) for item in body.items]
    response = await asyncio.to_thread(core.run_batch, seat, items, body.stop_on_error)
    if body.idempotency_key:
        cache = core.idempotency[seat]
        cache[body.idempotency_key] = response
        while len(cache) > 200:
            cache.popitem(last=False)
    return response


@app.post("/api/wait")
async def wait(request: Request, seat: int = Depends(seat_auth)) -> dict[str, Any]:
    body = await request.json() if (await request.body()) else {}
    return await asyncio.to_thread(core.wait, seat, body)


@app.post("/api/retire")
async def retire(seat: int = Depends(seat_auth)) -> dict[str, Any]:
    return core.retire(seat)


@app.get("/api/events")
async def events(limit: int = 100, since: float = 0.0, seat: int = Depends(seat_auth)) -> dict[str, Any]:
    feed = [e for e in core.events[seat] if e.get("time", 0) > since]
    return {"events": feed[-max(1, min(limit, 500)) :], "now": round(time.time() - core.started, 2)}


@app.get("/api/ticket/{ticket}")
async def ticket(ticket: int, timeout: float = 0.0, seat: int = Depends(seat_auth)) -> dict[str, Any]:
    try:
        if timeout > 0:
            return await asyncio.to_thread(core.wait_ticket, seat, ticket, min(timeout, 60.0))
        return core.ticket_view(seat, ticket)
    except SchemaError as exc:
        raise HTTPException(404, str(exc)) from exc


# -- admin routes (init_game, the env) ----------------------------------------------------------------


@app.post("/api/admin/ping")
async def admin_ping(_: str = Depends(admin_auth)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(core.ping)
    except AdminPortError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/admin/configure")
async def admin_configure(_: str = Depends(admin_auth)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(core.configure)
    except AdminPortError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/admin/rcon")
async def admin_rcon(request: Request, _: str = Depends(admin_auth)) -> dict[str, Any]:
    body = await request.json()
    command = str(body.get("command", ""))
    try:
        return {"command": command, "output": await asyncio.to_thread(core.admin.rcon, command)}
    except (AdminPortError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/admin/batch")
async def admin_batch(request: Request, _: str = Depends(admin_auth)) -> dict[str, Any]:
    body = await request.json()
    items = body.get("items", [])
    try:
        return {"results": await asyncio.to_thread(core.admin_batch, items)}
    except AdminPortError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/admin/pause")
async def admin_pause(_: str = Depends(admin_auth)) -> dict[str, Any]:
    return await asyncio.to_thread(core.clock.pause)


@app.post("/api/admin/unpause")
async def admin_unpause(_: str = Depends(admin_auth)) -> dict[str, Any]:
    return await asyncio.to_thread(core.clock.unpause)


@app.post("/api/admin/start_clock")
async def admin_start_clock(_: str = Depends(admin_auth)) -> dict[str, Any]:
    """Realtime: start the clock now (seats start it themselves on their first action)."""
    started = core.barrier.start_clock()
    return {"started_now": started, "clock": core.barrier.status()}


@app.post("/api/admin/ready")
async def admin_ready(_: str = Depends(admin_auth)) -> dict[str, Any]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    BOOT_MARKER.write_text(str(time.time()))
    return {"boot_complete": True}


@app.post("/api/admin/retire")
async def admin_retire(request: Request, _: str = Depends(admin_auth)) -> dict[str, Any]:
    body = await request.json()
    return core.retire(int(body["company"]))


@app.post("/api/admin/run_out")
async def admin_run_out(request: Request, _: str = Depends(admin_auth)) -> dict[str, Any]:
    body = await request.json() if (await request.body()) else {}
    timeout = float(body.get("timeout_seconds", 10800))
    return await asyncio.to_thread(core.run_out, timeout)


@app.get("/api/admin/snapshot")
async def admin_snapshot(_: str = Depends(admin_auth)) -> dict[str, Any]:
    return await asyncio.to_thread(core.snapshot)


@app.get("/api/admin/events")
async def admin_events(limit: int = 500, _: str = Depends(admin_auth)) -> dict[str, Any]:
    return {"events": list(core.admin_events)[-limit:]}


@app.post("/api/admin/save")
async def admin_save(request: Request, _: str = Depends(admin_auth)) -> dict[str, Any]:
    body = await request.json() if (await request.body()) else {}
    name = "".join(ch for ch in str(body.get("name", "terminal")) if ch.isalnum() or ch in "-_") or "terminal"
    try:
        output = await asyncio.to_thread(core.admin.rcon, f"save {name}", 60)
    except AdminPortError as exc:
        raise HTTPException(503, str(exc)) from exc
    save_dir = Path(os.environ.get("OPENTTD_SAVE_DIR", str(Path.home() / ".local/share/openttd/save")))
    path = save_dir / f"{name}.sav"
    for _ in range(100):
        if path.exists() and path.stat().st_size > 0:
            break
        await asyncio.sleep(0.1)
    return {
        "name": name,
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "output": output,
    }


@app.exception_handler(SchemaError)
async def _schema_error(_: Request, exc: SchemaError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(NotVisible)
async def _not_visible(_: Request, exc: NotVisible) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})
