#!/usr/bin/env python3
"""Adversarial seat-isolation probe for a LIVE box (run as root inside it, mid-episode).

    prime sandbox upload <id> scripts/probe_seat_isolation.py /tmp/probe_seat_isolation.py
    prime sandbox run <id> -- python3 -I /tmp/probe_seat_isolation.py

For every seat user it re-runs itself demoted (`--inner`) and plays the rival: it dumps every
/proc/<pid>/cmdline it can read, tries to read the other seats' and root's secret files, and
asks the OpenTTD game port for its server info. The root side then checks that no secret the
box holds (seat tokens, admin token, admin/server passwords, inference keys from each seat's
`.seat_env`) is visible to any seat, that no cross-seat read succeeded, that the game port
demands a password, and — positive control — that each seat's own token still authenticates.
Prints one JSON report; exit 0 only when every check passes.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import socket
import struct
import subprocess
import sys

ROOT = "/opt/openttd"
RUN = "/run/openttd"
PROTECTED = [f"{ROOT}/seats.json", f"{ROOT}/world.json", f"{RUN}/openttd.cfg", f"{RUN}/world.json"]
PACKET_SERVER_GAME_INFO = 6
PACKET_CLIENT_GAME_INFO = 7


def game_info(port: int) -> dict:
    """OpenTTD 15.x TCP server-info query (src/network/core/network_game_info.cpp)."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(struct.pack("<HB", 3, PACKET_CLIENT_GAME_INFO))
        head = _recv(s, 2)
        size = struct.unpack("<H", head)[0]
        body = _recv(s, size - 2)
    if body[0] != PACKET_SERVER_GAME_INFO:
        return {"error": f"unexpected packet type {body[0]}"}
    r = _Reader(body[1:])
    version = r.u8()
    if version < 7:
        return {"error": f"unexpected game info version {version}"}
    r.take(8)  # ticks_playing
    names = r.u8() == 1  # NST_GRFID_MD5_NAME
    r.take(4)
    r.string()  # gamescript version + name
    for _ in range(r.u8()):
        r.take(4 + 16)
        if names:
            r.string()
    r.take(4 + 4 + 3)  # calendar date/start, companies max/on, clients max
    server_name, revision = r.string(), r.string()
    use_password = r.u8() != 0
    return {"server_name": server_name, "revision": revision, "use_password": use_password}


def _recv(s: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = s.recv(n - len(out))
        if not chunk:
            raise ConnectionError("game port closed the connection")
        out += chunk
    return out


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def take(self, n: int) -> bytes:
        out = self.data[self.at : self.at + n]
        self.at += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def string(self) -> str:
        end = self.data.index(b"\0", self.at)
        out = self.data[self.at : end].decode(errors="replace")
        self.at = end + 1
        return out


def inner(game_port: int) -> dict:
    """As a seat user: everything a rival could learn."""
    me = pwd.getpwuid(os.getuid()).pw_name
    cmdlines = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if raw:
            cmdlines.append(raw.replace(b"\0", b" ").decode(errors="replace"))
    readable = []
    others = [u.pw_dir for u in pwd.getpwall() if re.fullmatch(r"agent\d+", u.pw_name) and u.pw_name != me]
    for path in PROTECTED + [f"{h}/{n}" for h in others for n in (".openttd_seat", ".seat_env", "task-prompt.md")]:
        try:
            with open(path, "rb") as f:
                f.read(1)
            readable.append(path)
        except OSError:
            pass
    try:
        info = game_info(game_port)
    except Exception as exc:  # noqa: BLE001 - reported, judged by the root side
        info = {"error": f"{type(exc).__name__}: {exc}"}
    # Positive control: the seat's own token, found the way the client finds it.
    sys.path.insert(0, f"{ROOT}/client")
    try:
        from openttd_client.transport import Transport

        own = Transport().request("GET", "/api/state")
        own_ok = own.get("seat", {}).get("company") is not None
    except Exception as exc:  # noqa: BLE001
        own_ok = f"{type(exc).__name__}: {exc}"
    return {"user": me, "cmdlines": cmdlines, "readable": readable, "game_info": info, "own_token_ok": own_ok}


def secrets_in_box() -> dict[str, str]:
    found: dict[str, str] = {}
    seats = json.load(open(f"{ROOT}/seats.json"))
    found["admin token"] = seats["admin"]
    for token, company in seats["tokens"].items():
        found[f"seat token c{company}"] = token
    for line in open(f"{RUN}/openttd.cfg"):
        key, _, value = line.partition("=")
        if key.strip() in ("admin_password", "server_password") and value.strip():
            found[key.strip()] = value.strip()
    for user in pwd.getpwall():
        try:
            lines = open(f"{user.pw_dir}/.seat_env").read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.fullmatch(r'export (\w*(?:KEY|TOKEN|SECRET)\w*)="(.+)"', line)
            if m and len(m.group(2)) >= 8:
                found[f"{user.pw_name} {m.group(1)}"] = m.group(2)
    return found


def main() -> int:
    if "--inner" in sys.argv:  # demoted: world.json is root-only, so the port rides argv
        print(json.dumps(inner(int(sys.argv[sys.argv.index("--inner") + 1]))))
        return 0
    world = json.load(open(f"{ROOT}/world.json"))
    game_port = int(world["ports"]["game"])
    secrets = secrets_in_box()
    users = sorted(u.pw_name for u in pwd.getpwall() if re.fullmatch(r"agent\d+", u.pw_name))
    users = [u for u in users if int(u.removeprefix("agent")) <= int(world["llm_seats"])]
    seat_home = {u: pwd.getpwnam(u).pw_dir for u in users}
    failures: list[str] = []
    report: dict = {"secrets_checked": sorted(secrets), "seats": {}}
    for user in users:
        out = subprocess.run(
            ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups",
             "env", "-i", f"HOME={seat_home[user]}", "PATH=/usr/local/bin:/usr/bin:/bin",
             f"OPENTTD_API_URL=http://127.0.0.1:{world['ports']['api']}",
             sys.executable, "-I", os.path.abspath(__file__), "--inner", str(game_port)],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            failures.append(f"{user}: inner probe failed: {out.stderr[-500:]}")
            continue
        seen = json.loads(out.stdout)
        leaks = sorted(
            name for name, value in secrets.items()
            if any(value in line for line in seen["cmdlines"])
        )
        if leaks:
            failures.append(f"{user} sees secrets in /proc cmdlines: {leaks}")
        if seen["readable"]:
            failures.append(f"{user} can read {seen['readable']}")
        if seen["game_info"].get("use_password") is not True:
            failures.append(f"{user}: game port does not demand a password: {seen['game_info']}")
        if seen["own_token_ok"] is not True:
            failures.append(f"{user}: own token did not authenticate: {seen['own_token_ok']}")
        report["seats"][user] = {
            "processes_visible": len(seen["cmdlines"]),
            "secret_leaks": leaks,
            "readable": seen["readable"],
            "game_info": seen["game_info"],
            "own_token_ok": seen["own_token_ok"],
        }
    report["failures"] = failures
    report["ok"] = not failures
    print(json.dumps(report, indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
