"""The Game object: schema-generated namespaces, batching, tickets, waits."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openttd_client.transport import OpenTTDError, Transport

SCHEMA_PATH = Path(__file__).with_name("schema.json")
CARGO_PATH = Path(__file__).with_name("cargo_table.json")


def load_schema() -> dict[str, Any]:
    """The schema shipped next to the package (payload); from a repo checkout, generate it."""
    if SCHEMA_PATH.exists():
        return json.loads(SCHEMA_PATH.read_text())
    from openttd_bench.schema import schema_dict  # host checkout only

    return schema_dict()


def load_cargo_table() -> list[dict[str, Any]]:
    if CARGO_PATH.exists():
        return json.loads(CARGO_PATH.read_text())
    return []


class Record(dict):
    """A dict with attribute access: `tile.slope`, `town.name`."""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError:
            raise AttributeError(name) from None
        return _wrap(value)

    def __repr__(self) -> str:
        return "Record(" + dict.__repr__(self) + ")"


def _wrap(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, Record):
        return Record(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class Result(Record):
    """One command's result. `ok` says whether the server executed it; a failed
    command raises unless the call was made inside a batch with `raise_errors=False`."""

    @property
    def value(self) -> "Result":
        """Direct calls already hold their value; `.value` exists so code written for
        batch results (`Pending.value`) also works on direct results."""
        return self

    @property
    def ok(self) -> bool:
        """The game's own verdict for mutators (`ok` in the envelope); True for plain reads."""
        return bool(dict.get(self, "ok", True))

    @property
    def error(self) -> str | None:
        return dict.get(self, "error")


class Pending:
    """A batched call's placeholder: `.value` after the batch runs (a Result or list)."""

    def __init__(self, cmd: str, args: dict[str, Any], validate: bool) -> None:
        self.cmd = cmd
        self.args = args
        self.validate = validate
        self._reply: dict[str, Any] | None = None

    def _resolve(self, reply: dict[str, Any]) -> None:
        self._reply = reply

    @property
    def done(self) -> bool:
        return self._reply is not None

    @property
    def ok(self) -> bool:
        """Transport success AND, for mutators, the game's own `ok` in the result."""
        if not self._reply or not self._reply.get("ok"):
            return False
        result = self._reply.get("result")
        return not (isinstance(result, dict) and result.get("ok") is False)

    @property
    def error(self) -> str | None:
        if self._reply is None:
            return None
        if self._reply.get("error"):
            return str(self._reply["error"])
        result = self._reply.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            return str(result.get("error") or "failed")
        return None

    @property
    def value(self) -> Any:
        if self._reply is None:
            raise OpenTTDError(f"{self.cmd}: the batch has not run yet (leave the `with g.batch()` block first)")
        return _materialize(self.cmd, self._reply)

    def __repr__(self) -> str:
        state = "pending" if self._reply is None else ("ok" if self.ok else f"error: {self.error}")
        return f"Pending({self.cmd}, {state})"


def _materialize(cmd: str, reply: dict[str, Any]) -> Any:
    result = reply.get("result")
    if not reply.get("ok"):
        raise OpenTTDError(f"{cmd}: {reply.get('error', 'failed')}", detail=reply)
    if isinstance(result, dict):
        out = Result(result)
        return out
    return _wrap(result)


class Ticket:
    """An asynchronous order edit; `wait()` polls until the company AI applied it."""

    def __init__(self, game: Game, ticket: int, cmd: str) -> None:
        self.game = game
        self.id = ticket
        self.cmd = cmd
        self.state: dict[str, Any] = {"status": "pending"}

    def refresh(self) -> dict[str, Any]:
        self.state = self.game.transport.request("GET", f"/api/ticket/{self.id}")
        return self.state

    def wait(self, timeout: float = 30.0) -> Record:
        self.state = self.game.transport.request(
            "GET", f"/api/ticket/{self.id}?timeout={min(timeout, 60)}", timeout=timeout + 10
        )
        if self.state.get("status") == "pending":
            raise OpenTTDError(f"{self.cmd}: ticket {self.id} still pending after {timeout}s")
        if not self.state.get("ok"):
            raise OpenTTDError(
                f"{self.cmd}: {self.state.get('error') or 'the company AI rejected the order'}", detail=self.state
            )
        return Record(self.state)

    @property
    def ok(self) -> bool:
        return bool(self.state.get("ok"))

    def __repr__(self) -> str:
        return f"Ticket({self.id}, {self.cmd}, {self.state.get('status')})"


class _Namespace:
    """Methods generated from the schema for one namespace (`g.rail.station(...)`)."""

    def __init__(self, game: Game, namespace: str, validate: bool = False) -> None:
        self._game = game
        self._namespace = namespace
        self._validate = validate
        self._commands = {
            c["name"].split(".", 1)[1]: c
            for c in game.schema["commands"]
            if c["name"].startswith(namespace + ".") and not c.get("admin_only")
        }
        if not validate and any(c["validate"] for c in self._commands.values()):
            self.validate = _Namespace(game, namespace, validate=True)

    def __dir__(self) -> list[str]:
        return sorted(self._commands)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        spec = self._commands.get(name)
        if spec is None:
            raise AttributeError(f"{self._namespace} has no command {name!r}; see `ottd docs {self._namespace}`")
        if self._validate and not spec["validate"]:
            raise AttributeError(f"{spec['name']} has no validate twin")

        def call(*args: Any, **kwargs: Any) -> Any:
            if args:
                names = [a["name"] for a in spec["args"]]
                if len(args) > len(names):
                    raise TypeError(f"{spec['name']} takes at most {len(names)} positional arguments")
                kwargs = {**dict(zip(names, args)), **kwargs}
            # Records (a cargo, station, vehicle, town row) stand in for their ids.
            types = {a["name"]: a["type"] for a in spec["args"]}
            for key, value in list(kwargs.items()):
                if types.get(key) == "int" and isinstance(value, dict) and "id" in value:
                    kwargs[key] = value["id"]
            return self._game._call(spec["name"], kwargs, self._validate)

        call.__name__ = name
        call.__doc__ = _doc(spec)
        return call


def _doc(spec: dict[str, Any]) -> str:
    args = ", ".join(
        f"{a['name']}: {a['type']}" + ("" if a["required"] else f" = {a['default']!r}") for a in spec["args"]
    )
    return f"{spec['name']}({args})\n{spec['doc']}\nReturns: {spec['returns']}"


class Cargo:
    """The generated cargo table: `g.cargo.PASS.id`, `g.cargo.by_label('COAL')`."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = [Record(r) for r in rows]
        self._by_label = {r["label"]: r for r in self.rows}
        self._by_id = {int(r["id"]): r for r in self.rows}

    def __getattr__(self, label: str) -> Record:
        if label.startswith("_"):
            raise AttributeError(label)
        row = self._by_label.get(label) or self._by_label.get(label + "_")
        if row is None:
            raise AttributeError(f"no cargo labelled {label!r}; labels: {sorted(self._by_label)}")
        return row

    def by_label(self, label: str) -> Record:
        return getattr(self, label)

    def by_id(self, cargo_id: int) -> Record:
        return self._by_id[int(cargo_id)]

    def __iter__(self):
        return iter(self.rows)

    def __repr__(self) -> str:
        return "Cargo(" + ", ".join(f"{r['label']}={r['id']}" for r in self.rows) + ")"


class Batch:
    """Collects calls; runs them as one POST /api/batch on exit."""

    def __init__(self, game: Game, stop_on_error: bool, idempotency_key: str | None) -> None:
        self.game = game
        self.stop_on_error = stop_on_error
        self.idempotency_key = idempotency_key
        self.pending: list[Pending] = []
        self.response: dict[str, Any] | None = None
        self.schema = game.schema
        for ns in game.namespaces:
            setattr(self, ns, _Namespace(self, ns))  # type: ignore[arg-type]

    def _call(self, cmd: str, args: dict[str, Any], validate: bool) -> Pending:
        item = Pending(cmd, args, validate)
        self.pending.append(item)
        return item

    def run(self) -> dict[str, Any]:
        if not self.pending:
            self.response = {"results": []}
            return self.response
        body = {
            "items": [{"cmd": p.cmd, "args": p.args, "validate": p.validate} for p in self.pending],
            "stop_on_error": self.stop_on_error,
        }
        if self.idempotency_key:
            body["idempotency_key"] = self.idempotency_key
        self.response = self.game.transport.request("POST", "/api/batch", body)
        for item, reply in zip(self.pending, self.response.get("results", [])):
            item._resolve(reply)
        return self.response

    def __enter__(self) -> Batch:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.run()


class Game:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 120.0) -> None:
        self.transport = Transport(url, token, timeout)
        self.schema = load_schema()
        self.namespaces = list(
            dict.fromkeys(c["name"].split(".", 1)[0] for c in self.schema["commands"] if not c.get("admin_only"))
        )
        for ns in self.namespaces:
            setattr(self, ns, _Namespace(self, ns))
        self.cargo = Cargo(load_cargo_table())
        self.order_flags = dict(self.schema["order_flags"])

    # -- direct calls (a batch of one) -----------------------------------------------------

    def _call(self, cmd: str, args: dict[str, Any], validate: bool) -> Any:
        body = {"items": [{"cmd": cmd, "args": args, "validate": validate}], "stop_on_error": True}
        response = self.transport.request("POST", "/api/batch", body)
        reply = response["results"][0]
        value = _materialize(cmd, reply)
        if cmd.startswith("orders.") and cmd != "orders.ticket" and isinstance(value, dict) and "ticket" in value:
            return Ticket(self, int(value["ticket"]), cmd)
        return value

    def batch(self, stop_on_error: bool = True, idempotency_key: str | None = None) -> Batch:
        return Batch(self, stop_on_error, idempotency_key)

    def call(self, cmd: str, **args: Any) -> Any:
        """Call any command by name (`g.call("inspect.tile", x=1, y=2)`)."""
        return self._call(cmd, args, False)

    # -- root-level --------------------------------------------------------------------------

    def state(self) -> Record:
        return Record(self.transport.request("GET", "/api/state"))

    def events(self, limit: int = 100, since: float = 0.0) -> list[Record]:
        payload = self.transport.request("GET", f"/api/events?limit={limit}&since={since}")
        return [Record(e) for e in payload.get("events", [])]

    def ticket(self, ticket: int) -> Ticket:
        t = Ticket(self, int(ticket), "orders")
        t.refresh()
        return t

    def wait(
        self,
        *,
        months: int | None = None,
        years: int | None = None,
        days: int | None = None,
        until_year: int | None = None,
        until_date: int | None = None,
        timeout: float = 240.0,
    ) -> Record:
        """Advance the clock (barrier-aware). Loops transparently on chunked waits for up to
        `timeout` wall seconds; if rivals are still thinking by then it returns a Record with
        status "blocked" instead of burning your session: keep working (your seat's wait stays
        registered for a grace period) and wait again later."""
        body: dict[str, Any] = {
            k: v
            for k, v in {
                "months": months,
                "years": years,
                "days": days,
                "until_year": until_year,
                "until_date": until_date,
            }.items()
            if v is not None
        }
        if not body:
            raise OpenTTDError("wait needs months=, years=, days=, until_year= or until_date=")
        deadline = time.monotonic() + timeout
        while True:
            response = self.transport.request("POST", "/api/wait", body, timeout=90)
            if response.get("status") != "blocked":
                return Record(response)
            if time.monotonic() >= deadline:
                if (response.get("clock") or {}).get("mode") == "realtime":
                    response["hint"] = (
                        "blocked: the target date is still ahead and this call gave up waiting; the "
                        "clock keeps running regardless, so do useful work now and wait again later"
                    )
                else:
                    response["hint"] = (
                        "blocked: a rival company is still thinking or building, so the clock did not "
                        "run; do useful work now (your target stays registered for a while) and wait again"
                    )
                return Record(response)
            body = dict(response["resume"])

    def retire(self) -> Record:
        return Record(self.transport.request("POST", "/api/retire", {}))

    def docs(self) -> Record:
        return Record(self.transport.request("GET", "/api/docs"))

    def __repr__(self) -> str:
        return f"Game({self.transport.url}, namespaces={self.namespaces})"


__all__ = ["Batch", "Cargo", "Game", "OpenTTDError", "Pending", "Record", "Result", "Ticket"]
