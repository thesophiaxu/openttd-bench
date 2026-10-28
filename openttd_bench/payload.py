"""The sandbox payload: the `image/` tree plus generated artifacts, as one tarball.

Uploaded per episode (it is small), so a code change never needs an image rebuild;
prebuilt images merely cache the binaries. Contents: init_game.py, fetch_content.py,
patch_speed.py, server/, client/, game/, ai/, schema.json (also inside the client
package), the client's cargo_table.json, world.json and seats.json.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

from openttd_bench.schema import schema_json
from openttd_bench.task_spec import TaskSpec

IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"
INCLUDE = ("init_game.py", "fetch_content.py", "patch_speed.py", "server", "client", "game", "ai")


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def build_payload(spec: TaskSpec, seats: dict[str, Any], world: dict[str, Any] | None = None) -> bytes:
    world = world or spec.world()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in INCLUDE:
            path = IMAGE_DIR / name
            tar.add(path, arcname=name, filter=_clean)
        schema = schema_json().encode()
        _add_bytes(tar, "schema.json", schema)
        _add_bytes(tar, "client/openttd_client/schema.json", schema)
        _add_bytes(tar, "client/openttd_client/cargo_table.json", json.dumps(spec.cargo_table(), indent=1).encode())
        _add_bytes(tar, "world.json", json.dumps(world, indent=1).encode(), 0o600)
        _add_bytes(tar, "seats.json", json.dumps(seats).encode(), 0o600)
    return buffer.getvalue()


def _clean(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in info.name or info.name.endswith(".pyc"):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def extract_payload(payload: bytes, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        tar.extractall(target)


__all__ = ["IMAGE_DIR", "build_payload", "extract_payload"]
