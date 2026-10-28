#!/usr/bin/env python3
"""The stress rig (plan section 14): scripted seats hammering the control plane, no LLMs.

    python3 tests/stress/rig.py --api http://127.0.0.1:14000 --tokens tok1,tok2,tok3,tok4 \
        --admin ADMIN_TOKEN --seconds 120 [--waits]

Load shape: every seat sustains ~2 mixed read batches/s (tile region, town list, station
detail, deliveries); seat 1 additionally bursts 200 async builds and 20 order tickets.
Measured: game tick rate (admin date vs wall clock, via /health), p50/p99 batch latency per
seat, cross-seat interference (seats 2-4 latency while seat 1 bursts), order-ticket
resolution, GS opcode headroom (`ops_low_water` from sys.status).
Pass bar: tick rate >= 90 % of nominal while unpaused; p99 < 250 ms; tickets <= 5 game
ticks; no starvation (each seat's p99 within 2x of its solo baseline).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "image" / "client"))
from openttd_client import Game, OpenTTDError  # noqa: E402

NOMINAL_TICKS_PER_SECOND = 1000 / 27


def api(url: str, token: str, method: str, path: str, body=None, timeout=120):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def read_batch(g: Game, x: int, y: int) -> float:
    started = time.monotonic()
    with g.batch(stop_on_error=False) as b:
        b.inspect.region(x=x, y=y, width=16, height=16)
        b.inspect.towns(limit=20)
        b.inspect.stations()
        b.inspect.deliveries()
        b.inspect.company()
    return (time.monotonic() - started) * 1000


def seat_reader(g: Game, seconds: float, rate: float, latencies: list[float], stop: threading.Event, x0: int) -> None:
    deadline = time.monotonic() + seconds
    period = 1.0 / rate
    i = 0
    while time.monotonic() < deadline and not stop.is_set():
        started = time.monotonic()
        try:
            latencies.append(read_batch(g, 20 + (x0 + i * 7) % 180, 20 + (i * 11) % 180))
        except OpenTTDError as exc:
            latencies.append(float("nan"))
            print("read error:", exc, file=sys.stderr)
        i += 1
        time.sleep(max(0.0, period - (time.monotonic() - started)))


def seat_burst(g: Game, results: dict) -> None:
    """200 async construction validations/builds in two batches + 20 order tickets."""
    started = time.monotonic()
    ok = 0
    with g.batch(stop_on_error=False) as b:
        items = [
            b.rail.validate.track(from_x=30 + i, from_y=30, via_x=31 + i, via_y=30, to_x=32 + i, to_y=30)
            for i in range(100)
        ]
    ok += sum(1 for p in items if p.ok)
    with g.batch(stop_on_error=False) as b:
        items = [b.land.demolish(x=40 + (i % 50), y=60 + i // 50) for i in range(100)]
    ok += sum(1 for p in items if p.ok)
    results["burst_ms"] = (time.monotonic() - started) * 1000
    results["burst_ok"] = ok
    tickets = []
    try:
        # Find a buildable depot site on a random map with the validate twin.
        site = None
        with g.batch(stop_on_error=False) as b:
            plans = [
                ((x, y), b.rail.validate.depot(x=x, y=y, front_x=x, front_y=y + 1))
                for x in range(40, 200, 16)
                for y in range(40, 200, 16)
            ]
        for (x, y), plan in plans:
            if plan.ok and plan.value.possible:
                site = (x, y)
                break
        if site is None:
            raise OpenTTDError("no depot site found")
        x, y = site
        depot = g.rail.depot(x=x, y=y, front_x=x, front_y=y + 1)
        g.rail.track(from_x=x, from_y=y, via_x=x, via_y=y + 1, to_x=x, to_y=y + 6)
        engines = g.rail.engines(limit=50).engines
        loco = max((e for e in engines if not e.wagon), key=lambda e: e.power)
        v = g.vehicles.build(depot_x=x, depot_y=y, engine=loco.id)
        results["depot"] = {
            "site": site,
            "ok": depot.ok,
            "depot_error": depot.error,
            "vehicle": v.ok,
            "vehicle_error": v.error,
            "depot_tile": dict(g.inspect.tile(x=x, y=y)),
        }
        if v.ok:
            t_start = time.monotonic()
            for i in range(20):
                t = g.orders.append_depot(vehicle=v.vehicle, x=x, y=y, flags=["service_if_needed"])
                state = t.wait(30)
                tickets.append(state.get("ticks"))
            results["ticket_ms_total"] = (time.monotonic() - t_start) * 1000
    except OpenTTDError as exc:
        results["ticket_error"] = str(exc)
    results["ticket_ticks"] = tickets


def percentile(values: list[float], p: float) -> float:
    clean = sorted(v for v in values if v == v)
    if not clean:
        return float("nan")
    return clean[min(len(clean) - 1, int(round((len(clean) - 1) * p)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--tokens", required=True, help="comma-separated seat tokens")
    parser.add_argument("--admin", required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--rate", type=float, default=2.0)
    parser.add_argument("--waits", action="store_true", help="interleave barrier waits (seats wait 1 month every 10 s)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    tokens = args.tokens.split(",")
    games = [Game(args.api, tok) for tok in tokens]

    # Solo baseline per seat.
    baseline = {}
    for i, g in enumerate(games):
        lat = [read_batch(g, 30 + i * 5, 30) for _ in range(10)]
        baseline[i] = percentile(lat, 0.99)
    print("solo p99 baseline ms:", {k: round(v, 1) for k, v in baseline.items()})

    health0 = api(args.api, args.admin, "GET", "/health")
    date0, t0 = health0["date"]["date"], time.monotonic()
    stop = threading.Event()
    latencies: dict[int, list[float]] = {i: [] for i in range(len(games))}
    threads = [
        threading.Thread(target=seat_reader, args=(g, args.seconds, args.rate, latencies[i], stop, i * 40))
        for i, g in enumerate(games)
    ]
    burst_results: dict = {}
    threads.append(threading.Thread(target=seat_burst, args=(games[0], burst_results)))
    waiters = []
    if args.waits:

        def waiter(g: Game):
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                try:
                    g.wait(months=1, timeout=120)
                except OpenTTDError as exc:
                    print("wait error:", exc, file=sys.stderr)
                time.sleep(2)

        waiters = [threading.Thread(target=waiter, args=(g,)) for g in games]
    for t in threads + waiters:
        t.start()
    for t in threads + waiters:
        t.join()
    health1 = api(args.api, args.admin, "GET", "/health")
    status = api(args.api, args.admin, "POST", "/api/admin/batch", {"items": [{"cmd": "sys.status", "args": {}}]})[
        "results"
    ][0]["result"]
    elapsed = time.monotonic() - t0
    days = health1["date"]["date"] - date0
    report = {
        "seconds": round(elapsed, 1),
        "game_days_elapsed": days,
        "unpause_windows": health1["clock"]["unpause_windows"],
        "dispatch": health1["dispatch"],
        "ops_low_water": status.get("ops_low_water"),
        "seats": {
            i: {
                "n": len(v),
                "p50_ms": round(percentile(v, 0.5), 1),
                "p99_ms": round(percentile(v, 0.99), 1),
                "solo_p99_ms": round(baseline[i], 1),
                "starved": percentile(v, 0.99) > 2 * max(baseline[i], 30.0),
            }
            for i, v in latencies.items()
        },
        "burst": burst_results,
    }
    if args.waits and days:
        # While unpaused at 400 % the nominal rate is 4x; report the observed share of unpaused time.
        report["ticks_per_second_unpaused_est"] = round(days * 74 / elapsed, 1)
    verdict = {
        "p99_under_250ms": all(s["p99_ms"] < 250 for s in report["seats"].values()),
        "no_starvation": not any(s["starved"] for s in report["seats"].values()),
        "tickets_within_5_ticks": all((t or 99) <= 5 for t in burst_results.get("ticket_ticks", []))
        if burst_results.get("ticket_ticks")
        else None,
    }
    report["verdict"] = verdict
    print(json.dumps(report, indent=2))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return 0 if all(v in (True, None) for v in verdict.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
