"""`ottd`: a thin argparse shell over the library. JSON to stdout, errors to stderr + exit 1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openttd_client.docs import render
from openttd_client.game import Game, OpenTTDError, Ticket, load_schema


def _scalar(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str), flush=True)


def build_parser(schema: dict[str, Any]) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ottd", description="OpenTTD bench CLI (see `ottd docs`).")
    sub = root.add_subparsers(dest="ns", required=True)
    sub.add_parser("state", help="clock, seat status, quota")
    docs = sub.add_parser("docs", help="the generated reference")
    docs.add_argument("target", nargs="?")
    ev = sub.add_parser("events", help="your event feed (receipts + public events)")
    ev.add_argument("--limit", type=int, default=100)
    ev.add_argument("--since", type=float, default=0.0)
    wait = sub.add_parser("wait", help="advance the clock")
    wait.add_argument("--months", type=int)
    wait.add_argument("--years", type=int)
    wait.add_argument("--days", type=int)
    wait.add_argument("--until-year", type=int)
    wait.add_argument("--until-date", type=int)
    wait.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="wall seconds to keep waiting on thinking rivals before returning status=blocked",
    )
    tk = sub.add_parser("ticket", help="poll an order ticket")
    tk.add_argument("ticket", type=int)
    tk.add_argument("--wait", type=float, default=0.0)
    batch = sub.add_parser(
        "batch", help='run a JSON file: [{"cmd": "inspect.tile", "args": {...}, "validate": false}, ...]'
    )
    batch.add_argument("file", type=Path)
    batch.add_argument("--continue-on-error", action="store_true")
    batch.add_argument("--idempotency-key")
    sub.add_parser("retire", help="leave the clock barrier (your company keeps operating)")
    namespaces: dict[str, Any] = {}
    for c in schema["commands"]:
        if c.get("admin_only"):
            continue
        ns, name = c["name"].split(".", 1)
        if ns not in namespaces:
            p = sub.add_parser(ns, help=f"{ns} commands")
            namespaces[ns] = p.add_subparsers(dest="cmd", required=True)
        cp = namespaces[ns].add_parser(name, help=c["doc"][:80])
        positional = [a for a in c["args"] if a["required"]]
        # Common shapes get positionals: `ottd inspect tile 52 88`, `ottd inspect town 3`.
        simple = all(a["type"] == "int" for a in positional) and len(positional) <= 2
        for a in c["args"]:
            flag = "--" + a["name"].replace("_", "-")
            kwargs: dict[str, Any] = {"dest": a["name"], "help": a["doc"] or a["name"]}
            if a["type"] == "bool":
                cp.add_argument(flag, action="store_true", default=None, **kwargs)
                continue
            if a["type"] in ("str_list", "int_list"):
                kwargs["nargs"] = "*"
            if a.get("choices") and a["name"] != "flags":
                kwargs["choices"] = a["choices"]
            if a["required"] and simple:
                cp.add_argument(a["name"], nargs="?", type=_scalar, **{k: v for k, v in kwargs.items() if k != "dest"})
                cp.add_argument(flag, dest=a["name"] + "__flag", type=_scalar, help=argparse.SUPPRESS)
            else:
                cp.add_argument(flag, type=_scalar if a["type"] != "str" else str, **kwargs)
        if c["validate"]:
            cp.add_argument("--validate", action="store_true", help="test without building")
    return root


def main(argv: list[str] | None = None) -> int:
    schema = load_schema()
    parser = build_parser(schema)
    args = parser.parse_args(argv)
    try:
        if args.ns == "docs":
            print(render(args.target))
            return 0
        game = Game()
        if args.ns == "state":
            _emit(game.state())
        elif args.ns == "events":
            _emit(game.events(limit=args.limit, since=args.since))
        elif args.ns == "wait":
            _emit(
                game.wait(
                    months=args.months,
                    years=args.years,
                    days=args.days,
                    until_year=args.until_year,
                    until_date=args.until_date,
                    timeout=args.timeout,
                )
            )
        elif args.ns == "ticket":
            t = game.ticket(args.ticket)
            _emit(t.wait(args.wait) if args.wait > 0 else t.state)
        elif args.ns == "retire":
            _emit(game.retire())
        elif args.ns == "batch":
            items = json.loads(args.file.read_text())
            if not isinstance(items, list):
                raise OpenTTDError("the batch file must hold a JSON array of {cmd, args, validate}")
            body = {"items": items, "stop_on_error": not args.continue_on_error}
            if args.idempotency_key:
                body["idempotency_key"] = args.idempotency_key
            _emit(game.transport.request("POST", "/api/batch", body))
        else:
            spec = next(c for c in schema["commands"] if c["name"] == f"{args.ns}.{args.cmd}")
            call_args: dict[str, Any] = {}
            for a in spec["args"]:
                value = getattr(args, a["name"], None)
                flag_value = getattr(args, a["name"] + "__flag", None)
                if value is None and flag_value is not None:
                    value = flag_value
                if value is None:
                    continue
                if a["type"] == "int_list" and isinstance(value, list):
                    value = [int(v) for v in value]
                call_args[a["name"]] = value
            validate = bool(getattr(args, "validate", False))
            result = game._call(spec["name"], call_args, validate)
            if isinstance(result, Ticket):
                _emit({"ticket": result.id, "cmd": result.cmd, "hint": f"ottd ticket {result.id} --wait 10"})
            else:
                _emit(result)
        return 0
    except OpenTTDError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.detail and not isinstance(exc.detail, str):
            print(json.dumps(exc.detail, indent=2, default=str), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
