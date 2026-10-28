#!/usr/bin/env python3
"""init_game: readable, sequential boot of one episode's world (plan section 11.2).

    python3 init_game.py --root /opt/openttd --world /opt/openttd/world.json \
                         --seats /opt/openttd/seats.json

Root-only in production; `--dev-home DIR` runs everything as the current user from
DIR (no user accounts, no system paths) for local development against a host binary.
The process stays alive as the supervisor of the game and the control plane.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

START_AI_PATTERN = re.compile(r"^start_ai [A-Za-z0-9_.-]+( [A-Za-z0-9_=,.-]+)?$")


def log(msg: str) -> None:
    print(f"[init_game {time.strftime('%H:%M:%S')}] {msg}", flush=True)


@dataclass
class Paths:
    root: Path
    cache: Path
    install: Path
    run: Path
    home: Path
    dev: bool

    @classmethod
    def make(cls, root: Path, dev_home: Path | None) -> Paths:
        if dev_home is not None:
            dev_home = dev_home.resolve()
            return cls(
                root=root.resolve(),
                cache=dev_home / "cache",
                install=dev_home / "install",
                run=dev_home / "run",
                home=dev_home / "home",
                dev=True,
            )
        return cls(
            root=root,
            cache=root / "cache",
            install=root / "install",
            run=Path("/run/openttd"),
            home=Path("/root"),
            dev=False,
        )


class Admin:
    """The control plane's admin API, the only lifecycle channel (it owns the admin port)."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    def call(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 60) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    def health(self) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.url}/health", timeout=10) as resp:
            return json.load(resp)

    def batch(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.call("POST", "/api/admin/batch", {"items": items})["results"]

    def rcon(self, command: str) -> list[str]:
        return self.call("POST", "/api/admin/rcon", {"command": command})["output"]


# --- steps ------------------------------------------------------------------------------


def acquire_binaries(paths: Paths) -> Path:
    sys.path.insert(0, str(paths.root))
    import fetch_content  # noqa: E402  (part of the payload)

    binary = paths.install / "openttd"
    if binary.exists() and (paths.install / "baseset").exists() and (paths.install / "ai" / "RailwAI-30").exists():
        log(f"binaries present at {paths.install}")
        return binary
    log("acquiring pinned OpenTTD 15.3 + basesets + RailwAI (bootstrap path)")
    return fetch_content.install(paths.cache, paths.install)


def patch_speed(paths: Paths, percent: int) -> Path:
    target = paths.install / f"openttd-speed-{percent}"
    if target.exists():
        return target
    import patch_speed as patcher  # noqa: E402

    patched = patcher.patch((paths.install / "openttd").read_bytes(), percent)
    target.write_bytes(patched)
    target.chmod(0o755)
    log(f"speed patch: {percent}% -> {target.name}")
    return target


def write_cfg(paths: Paths, world: dict[str, Any], admin_password: str) -> Path:
    m = world["map"]
    ports = world["ports"]
    companies = len(world["companies"])
    # Enforce the declared transport modes for native AIs as well as LLMs.
    modes = set(world["rules"]["allowed_modes"])
    vehicle_limits = "\n".join(
        f"{setting} = 0" for mode, setting in
        (("rail", "max_trains"), ("road", "max_roadveh"), ("air", "max_aircraft"), ("water", "max_ships"))
        if mode not in modes
    )
    cfg = f"""\
[vehicle]
{vehicle_limits}

[misc]
language = english_US.lng
sounddriver = null
musicdriver = null

[game_creation]
town_name = english
landscape = {m["landscape"]}
starting_year = {m["start_year"]}
ending_year = 5000000
land_generator = 1
tgen_smoothness = 1
variety = 0
tree_placer = 0
map_x = {m["size_x_bits"]}
map_y = {m["size_y_bits"]}
water_borders = 0
custom_town_number = {m.get("custom_town_number", 1)}
amount_of_rivers = 0

[construction]
command_pause_level = 3

[difficulty]
max_no_competitors = 0
number_towns = {m["town_density"]}
industry_density = {m["industry_density"]}
max_loan = {world["rules"].get("max_loan", 500000)}
initial_interest = 2
vehicle_costs = 1
vehicle_breakdowns = 0
construction_cost = 1
terrain_type = {m["terrain_type"]}
quantity_sea_lakes = {m["sea_level"]}
economy = false
disasters = false
town_council_tolerance = 0

[economy]
inflation = false
infrastructure_maintenance = false
allow_town_roads = true

[company]
engine_renew = false

[ai]
ai_in_multiplayer = true

[locale]
currency = USD
units = metric

[gui]
autosave_interval = 0

[network]
server_game_type = local
server_name = openttd-bench
server_port = {ports["game"]}
server_admin_port = {ports["admin"]}
admin_password = {admin_password}
allow_insecure_admin_login = true
pause_on_join = false
min_active_clients = 0
max_clients = 2
max_companies = {companies}
autoclean_companies = false

[script]
script_max_opcode_till_suspend = 250000
script_max_memory_megabytes = 2048

[game_scripts]
LLMEval = log_level=1
"""
    paths.run.mkdir(parents=True, exist_ok=True)
    path = paths.run / "openttd.cfg"
    path.write_text(cfg)
    if not paths.dev:
        path.chmod(0o600)
    return path


def stage_scripts(paths: Paths) -> None:
    for kind, name in (("game", "LLMEval"), ("ai", "LLMCompany")):
        src = paths.root / kind / name
        dst = paths.install / kind / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    log("staged LLMEval + LLMCompany into the installation tree")


def free_port(port: int) -> bool:
    # SO_REUSEADDR like the server itself: lingering TIME_WAIT peers must not block a boot.
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def launch_game(paths: Paths, binary: Path, world: dict[str, Any], cfg: Path) -> subprocess.Popen:
    ports = world["ports"]
    for port in (ports["game"], ports["admin"]):
        if not free_port(port):
            raise SystemExit(f"port {port} is already in use")
    log_file = (paths.run / "openttd.log").open("ab")
    env = dict(os.environ)
    env["HOME"] = str(paths.home)
    (paths.home / ".local/share/openttd/save").mkdir(parents=True, exist_ok=True)
    argv = [
        str(binary),
        "-D",
        f"127.0.0.1:{ports['game']}",
        "-g",
        "-G",
        str(world["map"]["seed"]),
        "-c",
        str(cfg),
        "-x",
        "-d",
        "script=3",
    ]
    log("launching " + " ".join(argv))
    proc = subprocess.Popen(
        argv, cwd=str(paths.install), stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT, env=env
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"openttd exited during boot (code {proc.returncode}); see {paths.run / 'openttd.log'}")
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", ports["admin"]))
                return proc
            except OSError:
                time.sleep(0.2)
    raise SystemExit("openttd admin port never opened")


def start_control_plane(paths: Paths, world: dict[str, Any], seats_path: Path, admin_password: str) -> subprocess.Popen:
    ports = world["ports"]
    env = dict(os.environ)
    env.update(
        {
            "OPENTTD_WORLD": str(paths.run / "world.json"),
            "OPENTTD_SEATS": str(seats_path),
            "OPENTTD_SCHEMA": str(paths.root / "schema.json"),
            "OPENTTD_ADMIN_PASSWORD": admin_password,
            "OPENTTD_ADMIN_PORT": str(ports["admin"]),
            "OPENTTD_RUN_DIR": str(paths.run),
            # The dedicated server saves next to its config file (`-c` directory), not under $HOME.
            "OPENTTD_SAVE_DIR": str(paths.run / "save"),
            "PYTHONPATH": str(paths.root),
            "PYTHONUNBUFFERED": "1",
        }
    )
    log_file = (paths.run / "api.log").open("ab")
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(ports["api"]),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(argv, cwd=str(paths.root), stdout=log_file, stderr=subprocess.STDOUT, env=env)
    return proc


def wait_health(admin: Admin, key: str, timeout: float, paths: Paths | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        try:
            last = admin.health()
            if last.get(key):
                return last
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(0.5)
    detail = ""
    if paths is not None:
        detail = "\n" + script_log_tail(paths)
    raise SystemExit(f"control plane never reported {key}: {last}{detail}")


def script_log_tail(paths: Paths, lines: int = 25) -> str:
    log_path = paths.run / "openttd.log"
    if not log_path.exists():
        return ""
    interesting = [
        l for l in log_path.read_text(errors="replace").splitlines() if "script" in l or "squirrel" in l.lower()
    ]
    return "openttd.log (script lines):\n" + "\n".join(interesting[-lines:])


def wait_gamescript(admin: Admin, paths: Paths, timeout: float) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            reply = admin.call("POST", "/api/admin/ping", {}, timeout=15)
            if reply.get("pong"):
                return
            last = str(reply)
        except urllib.error.HTTPError as exc:
            last = exc.read().decode(errors="replace")[:200]
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(1)
    raise SystemExit(f"GameScript never answered a ping: {last}\n{script_log_tail(paths)}")


def configure_gamescript(admin: Admin, timeout: float = 120) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            reply = admin.call("POST", "/api/admin/configure", {}, timeout=40)
            if reply.get("ok"):
                return reply
            last = str(reply)
        except urllib.error.HTTPError as exc:
            last = exc.read().decode(errors="replace")[:300]
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(2)
    raise SystemExit(f"GameScript never accepted configure: {last}")


def verify_cargo_table(world: dict[str, Any], reply: dict[str, Any]) -> None:
    runtime = {int(r["id"]): r["label"] for r in reply.get("cargos", [])}
    generated = {int(r["id"]): r["label"] for r in world["cargo_table"]}
    if runtime != generated:
        raise SystemExit(f"cargo table mismatch: generated {generated} != runtime {runtime}")
    log(f"cargo table verified ({len(runtime)} cargos)")


def found_towns(admin: Admin, world: dict[str, Any]) -> None:
    towns = world["map"].get("founded_towns") or []
    if not towns:
        return
    items = [{"cmd": "sys.found_town", "args": {k: v for k, v in t.items() if v is not None}} for t in towns]
    for town, reply in zip(towns, admin.batch(items)):
        result = reply.get("result", {})
        if not reply.get("ok") or not result.get("ok"):
            raise SystemExit(f"could not found town at ({town['x']},{town['y']}): {reply}")
        log(f"founded town {result.get('town')} at ({town['x']},{town['y']}) distance {result.get('distance')}")


def company_ids(admin: Admin) -> list[int]:
    reply = admin.batch([{"cmd": "sys.status", "args": {}}])[0]
    return [int(c) for c in reply.get("result", {}).get("companies", [])]


def seat_companies(admin: Admin, world: dict[str, Any]) -> None:
    for company in world["companies"]:
        line = (
            "start_ai LLMCompany"
            if company["seat"] == "llm"
            else (
                f"start_ai {company['ai_name']}"
                + (
                    ""
                    if not company["ai_config"]
                    else " " + ",".join(f"{k}={v}" for k, v in sorted(company["ai_config"].items()))
                )
            )
        )
        if not START_AI_PATTERN.fullmatch(line):
            raise SystemExit(f"refusing console line {line!r}")
        slot = int(company["slot"])
        if slot in company_ids(admin):
            log(f"company slot {slot} already exists")
            continue
        admin.rcon(line)
        deadline = time.time() + 60
        while time.time() < deadline:
            ids = company_ids(admin)
            if slot in ids:
                break
            time.sleep(0.25)
        else:
            raise SystemExit(f"company slot {slot} never appeared after {line!r}")
        log(f"seated slot {slot}: {line}")


def wait_funded(admin: Admin, world: dict[str, Any], timeout: float = 60) -> None:
    wanted = {int(c["slot"]): int(c["starting_cash"]) for c in world["companies"]}
    deadline = time.time() + timeout
    while time.time() < deadline:
        reply = admin.batch([{"cmd": "sys.company_states", "args": {}}])[0]
        rows = {int(r["id"]): r for r in reply.get("result", {}).get("companies", [])}
        ok = all(slot in rows and abs(int(rows[slot]["cash"]) - cash) <= 1000 for slot, cash in wanted.items())
        if ok:
            log("every company funded: " + ", ".join(f"{s}=${int(rows[s]['cash']):,}" for s in wanted))
            return
        time.sleep(0.5)
    raise SystemExit("companies were not funded in time")


def ensure_users(paths: Paths, world: dict[str, Any]) -> None:
    if paths.dev:
        return
    for slot in range(int(world["llm_seats"])):
        user = f"agent{slot + 1}"
        try:
            pwd.getpwnam(user)
        except KeyError:
            subprocess.run(["useradd", "--create-home", "--shell", "/bin/bash", user], check=True)
            log(f"created user {user}")


def write_seat_env(paths: Paths, world: dict[str, Any], seats: dict[str, Any]) -> None:
    by_company = {int(c): t for t, c in seats["tokens"].items()}
    url = f"http://127.0.0.1:{world['ports']['api']}"
    for slot in range(int(world["llm_seats"])):
        token = by_company.get(slot)
        if token is None:
            continue
        if paths.dev:
            target = paths.home / f"agent{slot + 1}.env"
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            user = pwd.getpwnam(f"agent{slot + 1}")
            target = Path(user.pw_dir) / ".openttd_seat"
        target.write_text(f"OPENTTD_API_URL={url}\nOPENTTD_API_TOKEN={token}\nOPENTTD_SEAT={slot}\n")
        target.chmod(0o600)
        if not paths.dev:
            os.chown(target, user.pw_uid, user.pw_gid)


def install_client(paths: Paths) -> None:
    """World-readable client + `ottd` on PATH + a .pth so `import openttd_client` works for every user."""
    if paths.dev:
        return
    # The root dir stays traversable (755): agents need client/ and schema.json; everything
    # sensitive below it is 700.
    os.chmod(paths.root, 0o755)
    subprocess.run(["chmod", "-R", "a+rX", str(paths.root / "client"), str(paths.root / "schema.json")], check=True)
    shutil.copy(paths.root / "client" / "ottd", "/usr/local/bin/ottd")
    os.chmod("/usr/local/bin/ottd", 0o755)
    site = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    Path(site, "openttd_client.pth").write_text(str(paths.root / "client") + "\n")
    for name in (
        "server",
        "game",
        "ai",
        "init_game.py",
        "patch_speed.py",
        "fetch_content.py",
        "world.json",
        "seats.json",
    ):
        target = paths.root / name
        if target.exists():
            subprocess.run(["chmod", "-R", "go-rwx", str(target)], check=True)
    for name in ("run", "install"):
        subprocess.run(["chmod", "-R", "go-rwx", str(paths.run if name == "run" else paths.install)], check=True)


def ensure_python_deps() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        log("installing control plane dependencies (bootstrap path)")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir", "fastapi", "uvicorn", "pydantic"],
            check=True,
        )


def ensure_system_deps(paths: Paths) -> None:
    if paths.dev:
        return
    missing = [lib for lib in ("libgomp.so.1", "libglib-2.0.so.0") if not _lib_present(lib)]
    if missing or shutil.which("setpriv") is None:
        log(f"installing system libraries (bootstrap path): {missing}")
        subprocess.run(["apt-get", "update", "-qq"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            [
                "apt-get",
                "install",
                "-y",
                "-qq",
                "--no-install-recommends",
                "libgomp1",
                "libglib2.0-0",
                "util-linux",
                "procps",
                "ca-certificates",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def _lib_present(name: str) -> bool:
    for base in ("/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu", "/usr/lib", "/lib"):
        if Path(base, name).exists():
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("/opt/openttd"))
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument(
        "--seats",
        type=Path,
        help="seats.json ({tokens: {token: company}, admin: token}); generated in dev mode when absent",
    )
    parser.add_argument("--dev-home", type=Path, help="run as the current user from this directory (development)")
    parser.add_argument("--no-supervise", action="store_true", help="exit after boot instead of supervising")
    args = parser.parse_args()

    paths = Paths.make(args.root, args.dev_home)
    world = json.loads(args.world.read_text())
    paths.run.mkdir(parents=True, exist_ok=True)
    (paths.run / "world.json").write_text(json.dumps(world))
    for stale in ("boot-complete",):
        (paths.run / stale).unlink(missing_ok=True)

    if args.seats and args.seats.exists():
        seats = json.loads(args.seats.read_text())
        seats_path = args.seats
    else:
        if not paths.dev:
            raise SystemExit("--seats is required outside dev mode")
        seats = {
            "tokens": {secrets.token_hex(16): slot for slot in range(int(world["llm_seats"]))},
            "admin": secrets.token_hex(16),
        }
        seats_path = paths.run / "seats.json"
        seats_path.write_text(json.dumps(seats))
        seats_path.chmod(0o600)
    admin_password = secrets.token_hex(12)
    percent = int(world["rules"]["game_speed_percent"])
    ports = world["ports"]

    ensure_system_deps(paths)
    ensure_python_deps()
    acquire_binaries(paths)
    if any(c.get("ai_name") == "AAAHogEx" for c in world["companies"]):
        import fetch_content

        no_loans = world["rules"].get("max_loan") == 0
        fetch_content.install_hogex(paths.cache, paths.install, no_loans=no_loans)
        log("installed pinned AAAHogEx v115" + (" with zero-loan inflation guard" if no_loans else ""))
    patched = patch_speed(paths, percent)
    cfg = write_cfg(paths, world, admin_password)
    stage_scripts(paths)
    ensure_users(paths, world)
    game = launch_game(paths, patched, world, cfg)
    api = start_control_plane(paths, world, seats_path, admin_password)
    admin = Admin(f"http://127.0.0.1:{ports['api']}", seats["admin"])
    try:
        wait_health(admin, "admin_ready", 120, paths)
        wait_gamescript(admin, paths, 90)
        log("admin link up and GameScript alive; pausing before anything else acts")
        admin.call("POST", "/api/admin/pause", {})
        reply = configure_gamescript(admin)
        log(f"GameScript configured: {len(reply.get('registry', []))} commands")
        verify_cargo_table(world, reply)
        found_towns(admin, world)
        seat_companies(admin, world)
        wait_funded(admin, world)
        for hook in world.get("lifecycle", []):
            log(f"lifecycle hook {hook!r} requested but none are registered this iteration")
        state = admin.call("POST", "/api/admin/pause", {})
        write_seat_env(paths, world, seats)
        install_client(paths)
        admin.call("POST", "/api/admin/ready", {})
        log(f"boot complete: paused at date {state.get('date')} year {state.get('year')}; api :{ports['api']}")
    except BaseException:
        game.terminate()
        api.terminate()
        raise
    if args.no_supervise:
        return

    def shutdown(*_: Any) -> None:
        for proc in (api, game):
            if proc.poll() is None:
                proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while True:
        if game.poll() is not None:
            log(f"openttd exited with {game.returncode}")
            api.terminate()
            sys.exit(game.returncode or 0)
        if api.poll() is not None:
            log(f"control plane exited with {api.returncode}")
            game.terminate()
            sys.exit(api.returncode or 1)
        time.sleep(1)


if __name__ == "__main__":
    main()
