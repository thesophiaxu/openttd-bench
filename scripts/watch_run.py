#!/usr/bin/env python3
"""Watch a run directory and post progress to ntfy: a start notice, a heartbeat with the
latest in-game progress line (the env writes `outputs/progress/<spec>-<idx>.jsonl` every
10 minutes), each completed episode's per-seat scores, and a finish notice.

    python3 scripts/watch_run.py outputs/prime-ffa-x4 --topic small-pineapple-good-morning --pid <eval pid> \
        --label "free_for_all x4" --episodes 4 --start-note "4 seeds sequential on Prime ..." \
        --progress outputs/progress --heartbeat 30
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path


def post(topic: str, title: str, body: str) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode(), method="POST", headers={"Title": title}
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:  # noqa: BLE001
        print("ntfy failed:", exc, flush=True)


def episode_summary(ep: dict, seat_labels: list[str] | None = None) -> str:
    task = ((ep.get("task") or {}).get("data") or {}).get("name")
    lines = [f"{task}: episode {'ok' if ep.get('ok') else 'FAILED'}"]
    for t in sorted(ep.get("traces") or [], key=lambda t: (t.get("info") or {}).get("openttd_company", 0)):
        info = t.get("info") or {}
        out = info.get("openttd_outcome") or {}
        final = info.get("openttd_final") or {}
        c = info.get("openttd_company")
        mine = (final.get("companies") or {}).get(str(c), {}) if c is not None else {}
        agent = (t.get("timing") or {}).get("agent") or {}
        span = (agent.get("end") or 0) - (agent.get("start") or 0)
        label = seat_labels[c] if seat_labels and isinstance(c, int) and c < len(seat_labels) else f"{info.get('openttd_role')}/c{c}"
        lines.append(
            f"  {label}: score {out.get('score', 0):.3f} place {int(out.get('values', {}).get('rank:place', 0))} "
            f"profit ${int(mine.get('operating_profit') or 0):,} vehicles {sum((mine.get('vehicles') or {}).values()) if isinstance(mine.get('vehicles'), dict) else '?'} "
            f"stations {mine.get('stations')} stop={t.get('stop_condition')} {span:.0f}s"
        )
    # Native companies have no LLM trace, but must appear in mixed-game results.
    traces = ep.get("traces") or []
    represented = {(t.get("info") or {}).get("openttd_company") for t in traces}
    final = next(((t.get("info") or {}).get("openttd_final") for t in traces
                  if (t.get("info") or {}).get("openttd_final")), {})
    for cid, company in sorted((final.get("companies") or {}).items()):
        if int(cid) not in represented:
            lines.append(f"  {company.get('name', 'native AI')}/c{cid}: "
                         f"profit ${int(company.get('operating_profit') or 0):,} "
                         f"vehicles {sum((company.get('vehicles') or {}).values())} "
                         f"loan ${int(company.get('loan') or 0):,}")
    if ep.get("errors"):
        lines.append("  Episode failed; inspect saved local logs for details.")
    return "\n".join(lines)


def latest_progress(progress_dir: Path, since: float, seat_labels: list[str] | None = None) -> str | None:
    """The newest progress line written after `since`, rendered for a phone screen."""
    newest: tuple[float, dict] | None = None
    for path in progress_dir.glob("*.jsonl"):
        if path.stat().st_mtime < since:
            continue
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        if not lines:
            continue
        try:
            row = json.loads(lines[-1])
        except json.JSONDecodeError:  # writer may be midway through an append
            continue
        if newest is None or row.get("at", 0) > newest[0]:
            newest = (row.get("at", 0), row)
    if newest is None:
        return None
    row = newest[1]
    clock = row.get("clock") or {}
    head = f"{row.get('spec')}: game date {row.get('date')} after {row.get('elapsed', 0) // 60} min"
    if clock.get("mode") == "realtime":
        head += f" (realtime, started={clock.get('started')}, horizon_reached={clock.get('horizon_reached')}, active={clock.get('active')})"
    else:
        head += f" (active seats {clock.get('active')}, waiting {clock.get('waiting')})"
    body = [head]
    for c, m in sorted((row.get("companies") or {}).items()):
        vehicles = m.get("vehicles")
        count = sum(vehicles.values()) if isinstance(vehicles, dict) else vehicles
        name = f"c{c}"
        if seat_labels and int(c) < len(seat_labels):
            name = seat_labels[int(c)]
        elif str(row.get("spec", "")).startswith("mixed_hogex"):
            name = {"0": "Astra", "1": "Opus 5.5", "2": "HogEx 1", "3": "HogEx 2"}.get(c, name)
        body.append(
            f"  {name}: profit ${int(m.get('operating_profit') or 0):,} vehicles {count} stations {m.get('stations')} cash ${int(m.get('cash') or 0):,}"
        )
        if "loan" in m:
            body[-1] += f" loan ${int(m.get('loan') or 0):,} (cap ${int(m.get('max_loan') or 0):,})"
    return "\n".join(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--label", default="free_for_all x4")
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--seat-labels", nargs="+", help="Names in company/seat order")
    parser.add_argument("--start-note", default="")
    parser.add_argument("--progress", type=Path, default=Path("outputs/progress"))
    parser.add_argument("--heartbeat", type=float, default=0, help="minutes between progress heartbeats (0 = none)")
    args = parser.parse_args()
    seen = 0
    traces_path: Path | None = None
    started = time.time()
    last_heartbeat = started
    post(
        args.topic,
        f"openttd-bench: {args.label} started",
        args.start_note or f"{args.episodes} episode(s); results under {args.outdir}.",
    )
    while True:
        alive = os.path.exists(f"/proc/{args.pid}")
        if traces_path is None:
            found = list(args.outdir.glob("*/traces.jsonl"))
            traces_path = found[0] if found else None
        if traces_path and traces_path.exists():
            lines = [l for l in traces_path.read_text().splitlines() if l.strip()]
            while seen < len(lines):
                try:
                    ep = json.loads(lines[seen])
                except json.JSONDecodeError:
                    break
                seen += 1
                post(
                    args.topic, f"openttd-bench: {args.label} episode {seen}/{args.episodes} done", episode_summary(ep, args.seat_labels)
                )
                print(episode_summary(ep, args.seat_labels), flush=True)
        if args.heartbeat and time.time() - last_heartbeat >= args.heartbeat * 60:
            last_heartbeat = time.time()
            progress = latest_progress(args.progress, started, args.seat_labels) if args.progress.exists() else None
            elapsed = (time.time() - started) / 60
            post(
                args.topic,
                f"openttd-bench: {args.label} running ({elapsed:.0f} min)",
                progress or f"no in-game progress line yet ({seen}/{args.episodes} episodes done).",
            )
            print("heartbeat:", progress or "no progress yet", flush=True)
        if not alive:
            manifest = args.outdir / "manifest.json"
            status = "finished"
            if manifest.exists():
                try:
                    status = json.loads(manifest.read_text()).get("status", "unknown")
                except json.JSONDecodeError:
                    status = "unknown"
                if status == "complete_with_caveat":
                    status = "finished WITH CAVEAT (early/unverified agent retirement)"
                elif status != "complete":
                    status = "FAILED / incomplete"
            body = f"{seen} episode(s) recorded after {(time.time() - started) / 60:.0f} min."
            for result_path in (args.outdir / "saves").glob("*/results.json"):
                results = json.loads(result_path.read_text())
                for row in sorted(results, key=lambda r: r.get("operating_profit", 0), reverse=True):
                    seat = row.get("seat")
                    if args.seat_labels and isinstance(seat, int) and 1 <= seat <= len(args.seat_labels):
                        name = args.seat_labels[seat - 1]
                    else:
                        name = {1: "Astra", 2: "Opus 5.5", 3: "HogEx 1", 4: "HogEx 2"}.get(seat, row.get("name"))
                    body += f"\n{name}: profit ${int(row.get('operating_profit', 0)):,}, score {row.get('objective_score', 0):.3f}"
            post(args.topic, f"openttd-bench: {args.label} {status}", body)
            return 0 if status in {"finished", "complete", "finished WITH CAVEAT (early/unverified agent retirement)"} else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
