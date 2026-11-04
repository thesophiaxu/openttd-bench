"""Separate trusted server code from the public, credential-free agent client."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

from openttd_bench.schema import schema_json
from openttd_bench.task_spec import TaskSpec

IMAGE_DIR = Path(__file__).resolve().parent / "_image"
if not IMAGE_DIR.is_dir():  # editable/source checkout
    IMAGE_DIR = Path(__file__).resolve().parent.parent / "image"
SERVER_FILES = ("init_game.py", "fetch_content.py", "patch_speed.py", "server", "game", "ai")


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, int(time.time())
    tar.addfile(info, io.BytesIO(data))


def build_server_payload(spec: TaskSpec, seats: dict[str, Any], world: dict[str, Any] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in SERVER_FILES:
            tar.add(IMAGE_DIR / name, arcname=name, filter=_clean)
        _add_bytes(tar, "schema.json", schema_json().encode())
        _add_bytes(tar, "world.json", json.dumps(world or spec.world()).encode(), 0o600)
        _add_bytes(tar, "seats.json", json.dumps(seats).encode(), 0o600)
    return buffer.getvalue()


def build_client_payload(spec: TaskSpec) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(IMAGE_DIR / "client", arcname="client", filter=_clean)
        _add_bytes(tar, "client/openttd_client/schema.json", schema_json().encode())
        _add_bytes(tar, "client/openttd_client/cargo_table.json", json.dumps(spec.cargo_table()).encode())
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
        tar.extractall(target, filter="data")


__all__ = ["IMAGE_DIR", "build_server_payload", "build_client_payload", "extract_payload"]
