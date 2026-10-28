"""OpenTTDEnv: one Env for all tasks — seats p1..p4 play one shared sandbox (plan 13).

Per attempt: provision one box (retries get a fresh world), upload the payload, boot
`init_game`, launch every LLM seat concurrently through per-seat runtime views, retire
seats as they finish, run the clock out to the horizon, capture the terminal save and
the grading snapshot, then score every seat in `finalize`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
import time
from pathlib import Path
from typing import Any

import verifiers.v1 as vf

from openttd_bench import grader
from openttd_bench.activity import has_model_activity
from openttd_bench.payload import build_payload
from openttd_bench.seats import SeatRuntime
from openttd_bench.task_spec import TaskSpec
from openttd_bench.taskset import OpenTTDSeatTask, OpenTTDTask, agent_budget_seconds

log = logging.getLogger(__name__)

ROLES = ("p1", "p2", "p3", "p4")
ROOT = "/opt/openttd"
BOOT_LOG = f"{ROOT}/boot.log"
BOOT_MARKER = "/run/openttd/boot-complete"

_API_SCRIPT = r"""
import json, sys, urllib.request, urllib.error
method, url, token, body, timeout = sys.argv[1:6]
data = body.encode() if body else None
req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        sys.stdout.write(resp.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write(json.dumps({"_http_error": e.code, "detail": e.read().decode(errors="replace")[:2000]}))
    sys.exit(3)
"""


async def box_api(
    runtime: vf.Runtime, method: str, url: str, body: dict[str, Any] | None, token: str, timeout: float = 60
) -> dict[str, Any]:
    """Call the in-box control plane from the host through `runtime.run` (loopback-only server)."""
    result = await runtime.run(
        ["python3", "-c", _API_SCRIPT, method, url, token, json.dumps(body) if body is not None else "", str(timeout)],
        {},
    )
    text = (result.stdout or "").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise vf.SandboxError(
            f"{method} {url}: unparseable reply (exit {result.exit_code}): {text[-500:]} {result.stderr[-500:]}"
        ) from exc
    if result.exit_code != 0 or "_http_error" in payload:
        raise vf.SandboxError(f"{method} {url} failed (exit {result.exit_code}): {payload or result.stderr[-500:]}")
    return payload


class GameBox:
    """The booted world inside one box: payload upload, init_game, admin calls."""

    def __init__(
        self, box: vf.Runtime, spec: TaskSpec, world: dict[str, Any], seats: dict[str, Any], boot_seconds: int
    ) -> None:
        self.box = box
        self.spec = spec
        self.world = world
        self.seats = seats
        self.boot_seconds = boot_seconds
        self.api_url = f"http://127.0.0.1:{world['ports']['api']}"

    async def admin(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 60
    ) -> dict[str, Any]:
        return await box_api(self.box, method, f"{self.api_url}{path}", body, self.seats["admin"], timeout)

    async def boot(self) -> dict[str, Any]:
        payload = build_payload(self.spec, self.seats, self.world)
        await self.box.write(f"{ROOT}/payload.tgz", payload)
        unpack = await self.box.run(
            [
                "sh",
                "-c",
                f"mkdir -p {ROOT} /run/openttd && rm -f {BOOT_MARKER} && tar -xzf {ROOT}/payload.tgz -C {ROOT} "
                f"&& rm -f {ROOT}/payload.tgz && chmod 755 {ROOT} && chmod 700 {ROOT}/server {ROOT}/game {ROOT}/ai "
                f"{ROOT}/world.json {ROOT}/seats.json {ROOT}/init_game.py",
            ],
            {},
        )
        if unpack.exit_code != 0:
            raise vf.SandboxError(f"payload unpack failed: {unpack.stderr[-800:]}")
        await self.box.run_background(
            [
                "python3",
                f"{ROOT}/init_game.py",
                "--root",
                ROOT,
                "--world",
                f"{ROOT}/world.json",
                "--seats",
                f"{ROOT}/seats.json",
            ],
            {"PYTHONUNBUFFERED": "1"},
            BOOT_LOG,
        )
        started = time.monotonic()
        while time.monotonic() - started < self.boot_seconds:
            probe = await self.box.run(
                ["sh", "-c", f"if test -f {BOOT_MARKER}; then echo READY; fi; tail -c 1500 {BOOT_LOG} 2>/dev/null"], {}
            )
            out = probe.stdout or ""
            if out.startswith("READY"):
                log.info("openttd world booted in %.0fs", time.monotonic() - started)
                return await self.admin("GET", "/health")
            if "Traceback" in out or "SystemExit" in out or "init_game" in out and ("never" in out or "exited" in out):
                raise vf.SandboxError(f"init_game failed: {out[-1200:]}")
            await asyncio.sleep(3)
        tail = await self.box.run(["tail", "-c", "2000", BOOT_LOG], {})
        raise vf.SandboxError(f"world did not boot within {self.boot_seconds}s: {tail.stdout[-1500:]}")

    async def retire(self, company: int) -> None:
        try:
            await self.admin("POST", "/api/admin/retire", {"company": company})
        except vf.SandboxError as exc:
            log.warning("retire %s: %s", company, exc)

    async def run_out(self, timeout: float) -> dict[str, Any]:
        return await self.admin("POST", "/api/admin/run_out", {"timeout_seconds": timeout}, timeout=timeout + 120)

    async def snapshot(self) -> dict[str, Any]:
        return await self.admin("GET", "/api/admin/snapshot", None, timeout=120)

    async def save(self, name: str) -> dict[str, Any]:
        return await self.admin("POST", "/api/admin/save", {"name": name}, timeout=120)

    async def read(self, path: str) -> bytes:
        return await self.box.read(path)


def make_seats(count: int) -> dict[str, Any]:
    return {"tokens": {secrets.token_hex(16): slot for slot in range(count)}, "admin": secrets.token_hex(16)}


def pick_ports(idx: int) -> dict[str, int]:
    """Per-episode loopback ports (docker host networking shares the host loopback)."""
    base = 20000 + (random.randrange(0, 400) * 100) + (idx % 10) * 10
    return {"game": base + 1, "admin": base + 2, "api": base + 3}


class OpenTTDEnvConfig(vf.EnvConfig):
    p1: vf.AgentConfig = vf.AgentConfig()
    p2: vf.AgentConfig = vf.AgentConfig()
    p3: vf.AgentConfig = vf.AgentConfig()
    p4: vf.AgentConfig = vf.AgentConfig()


class OpenTTDEnv(vf.Env[OpenTTDEnvConfig]):
    async def run(self, task: vf.Task, agents: vf.Agents) -> None:
        if not isinstance(task, OpenTTDTask):
            raise TypeError(f"OpenTTDEnv runs OpenTTD tasks; got {type(task).__name__}")
        spec = task.spec
        seats = spec.llm_seats
        limit = self.config.max_concurrent_agents
        if limit is not None and limit < seats:
            raise ValueError(
                f"{spec.name} has {seats} concurrent seats: set --env.max-concurrent-agents >= {seats} (got {limit})"
            )
        config = self.taskset.config
        rotation = (task.data.idx or 0) % seats
        role_of = {company: ROLES[(company + rotation) % seats] for company in range(seats)}
        tokens = make_seats(seats)
        token_of = {company: token for token, company in tokens["tokens"].items()}
        world = spec.world()
        world["ports"] = pick_ports(task.data.idx or 0)
        agent_seconds = config.agent_seconds or agent_budget_seconds(spec)

        async with getattr(agents, ROLES[0]).provision(task) as box:
            game = GameBox(box, spec, world, tokens, config.boot_seconds)
            health = await game.boot()
            log.info("world %s ready: %s", spec.name, health.get("date"))
            audit_dir = Path(config.save_dir) / f"{spec.name}-{task.data.idx or 0}"
            audit_dir.mkdir(parents=True, exist_ok=True)
            initial = await game.snapshot()
            from openttd_bench.accounting import validate_accounting
            (audit_dir / "initial-snapshot.json").write_text(json.dumps(initial, indent=2) + "\n")
            validate_accounting(initial, range(len(spec.companies)))
            for slot, company in enumerate(spec.companies):
                state = initial["companies"][str(slot)]
                if state["cash"] != company.starting_cash or state["loan"] != company.starting_loan:
                    raise vf.SandboxError(f"incorrect initial funding for company {slot}: {state}")
                if spec.rules.max_loan == 0 and state.get("max_loan") != 0:
                    raise vf.SandboxError(f"borrowing was not disabled for company {slot}: {state}")
            for filename, value in (("spec.json", spec.model_dump(mode="json")),
                                    ("world.json", world), ("initial-snapshot.json", initial)):
                (audit_dir / filename).write_text(json.dumps(value, indent=2) + "\n")
            initial_save = await game.save(f"initial-{spec.name}")
            if initial_save.get("exists"):
                (audit_dir / "initial.sav").write_bytes(await game.read(initial_save["path"]))
            if config.launch_gate:
                log.warning("world %s booted paused; waiting for launch gate %s", spec.name, config.launch_gate)
                while not Path(config.launch_gate).exists():
                    await asyncio.sleep(2)
            views = {company: SeatRuntime(box, role_of[company]) for company in range(seats)}
            traces: list[vf.Trace] = []

            async def play(company: int) -> None:
                role = role_of[company]
                seat_task = OpenTTDSeatTask(
                    task.data.model_copy(
                        update={
                            "name": f"{spec.name}-c{company}",
                            "prompt": spec.companies[company].prompt,
                            "timeout": vf.TaskTimeout(setup=600, agent=agent_seconds, finalize=120, scoring=120),
                        }
                    ),
                    spec=spec,
                    company=company,
                    token=token_of[company],
                    api_url=game.api_url,
                    user=f"agent{company + 1}",
                )
                try:
                    trace = await getattr(agents, role).run(seat_task, runtime=views[company])
                    trace.info["openttd_company"] = company
                    trace.info["openttd_role"] = role
                    trace.info["openttd_spec"] = spec.name
                    traces.append(trace)
                    if config.require_model_activity and not has_model_activity(trace.calls, trace.info):
                        raise vf.SandboxError(
                            f"{role} exited without model activity: "
                            f"{trace.info.get('harness_output_tail', '')[-1000:]}"
                        )
                finally:
                    await game.retire(company)

            progress = asyncio.create_task(self.report_progress(game, task, config))
            try:
                await asyncio.gather(*(play(company) for company in range(seats)))
            finally:
                progress.cancel()
            run_out = await game.run_out(config.run_out_seconds)
            log.info("run-out: %s", run_out)
            snapshot = await game.snapshot()
            (audit_dir / "final-snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
            try:
                validate_accounting(snapshot, range(len(spec.companies)))
            except ValueError:
                # Preserve diagnostic evidence even when we must refuse to score.
                try:
                    failed_save = await game.save(f"invalid-accounting-{spec.name}")
                    if failed_save.get("exists"):
                        (audit_dir / "invalid-accounting.sav").write_bytes(await game.read(failed_save["path"]))
                except Exception as exc:
                    (audit_dir / "invalid-accounting-save-error.txt").write_text(str(exc))
                raise
            if spec.rules.max_loan == 0:
                if any(c.get("loan") != 0 or c.get("max_loan") != 0 for c in snapshot["companies"].values()):
                    raise vf.SandboxError("nonzero loan/cap in no-borrowing final snapshot")
            all_results = [
                {"seat": slot + 1, "kind": company.seat, "name": company.name,
                 **grader.metrics(spec, snapshot, slot, grader.score(spec, snapshot, slot))}
                for slot, company in enumerate(spec.companies)
            ]
            (audit_dir / "results.json").write_text(json.dumps(all_results, indent=2) + "\n")
            save_info: dict[str, Any] = {}
            try:
                save_info = await game.save(f"terminal-{spec.name}")
                if save_info.get("exists"):
                    data = await game.read(save_info["path"])
                    target = Path(config.save_dir) / f"{spec.name}-{int(time.time())}.sav"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    save_info["host_path"] = str(target)
            except Exception as exc:  # noqa: BLE001 - the save is an audit artifact, never the score
                save_info = {"error": str(exc)}
            for trace in traces:
                trace.info["openttd_final"] = snapshot
                trace.info["openttd_save"] = save_info
                trace.info["openttd_run_out"] = run_out
                trace.info["openttd_clock"] = spec.rules.clock

    async def report_progress(self, game: GameBox, task: OpenTTDTask, config: Any) -> None:
        """While the seats play, append a snapshot line every `progress_seconds` to
        `<progress_dir>/<spec>-<idx>.jsonl` (date, clock, per-company profit/vehicles/stations)
        so a watcher on the host can report a long episode's progress; never affects scoring."""
        if not config.progress_seconds:
            return
        path = Path(config.progress_dir) / f"{task.spec.name}-{task.data.idx or 0}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        while True:
            await asyncio.sleep(config.progress_seconds)
            try:
                snapshot = await game.snapshot()
            except Exception as exc:  # noqa: BLE001 - progress is best effort
                log.warning("progress snapshot failed: %s", exc)
                continue
            line = {
                "at": time.time(),
                "elapsed": round(time.time() - started),
                "spec": task.spec.name,
                "date": (snapshot.get("date") or {}).get("text"),
                "clock": snapshot.get("clock"),
                "accounting_snapshot": snapshot.get("accounting_snapshot"),
                "companies": {
                    c: {k: m.get(k) for k in ("operating_profit", "accounting", "vehicles", "stations", "cash", "loan", "max_loan")}
                    for c, m in (snapshot.get("companies") or {}).items()
                },
            }
            with path.open("a") as fh:
                fh.write(json.dumps(line) + "\n")

    async def finalize(self, task: vf.Task, episode: vf.Episode) -> None:
        assert isinstance(task, OpenTTDTask)
        for trace in episode.traces:
            company = trace.info.get("openttd_company")
            snapshot = trace.info.get("openttd_final")
            if company is None or not isinstance(snapshot, dict):
                continue
            outcome = grader.score(task.spec, snapshot, int(company))
            trace.info["openttd_outcome"] = outcome.model_dump()
            trace.record_reward("objective", outcome.score)
            trace.record_metrics(grader.metrics(task.spec, snapshot, int(company), outcome))


__all__ = ["GameBox", "OpenTTDEnv", "OpenTTDEnvConfig", "box_api", "make_seats", "pick_ports"]
