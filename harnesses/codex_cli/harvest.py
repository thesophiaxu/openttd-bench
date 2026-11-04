"""Bounded native Codex capture. Never export auth/config or modify VM evidence."""

from __future__ import annotations

import asyncio
import re
import tarfile
import tempfile
import uuid
from io import BytesIO
from pathlib import Path, PurePosixPath

MAX_FILES = 2048
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_FILES * 4096 + 10240
EXEC_LOG = "codex-exec.jsonl"

# Bounds apply in the VM before transferring anything, as well as at extraction.
# Only an incomplete final JSONL record is removed; complete native bytes are retained.
CAPTURE = r'''
import io, json, os, resource, signal, stat, sys, tarfile
from pathlib import Path
home, target = Path(sys.argv[1]), sys.argv[2]
max_files, max_file, max_total, max_archive = map(int, sys.argv[3:])
signal.alarm(20)
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
resource.setrlimit(resource.RLIMIT_FSIZE, (max_archive, max_archive))
files = []
visited = 0
for relative in (".codex/sessions", ".codex/archived_sessions"):
    root = home / relative
    if (home / ".codex").is_symlink() or root.is_symlink():
        raise ValueError("linked store")
    for directory, dirs, names in os.walk(root, followlinks=False):
        visited += len(dirs) + len(names) + 1
        if visited > max_files * 4:
            raise ValueError("store entry limit")
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        files.extend(Path(directory) / n for n in sorted(names) if n.endswith(".jsonl"))
        if len(files) > max_files:
            raise ValueError("file count limit")
log = home / "codex-exec.jsonl"
if log.exists() or log.is_symlink():
    files.append(log)
if len(files) > max_files:
    raise ValueError("file count limit")
total = 0
with open(target, "xb") as archive, tarfile.open(fileobj=archive, mode="w") as tar:
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("non-regular source")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as src:
            info = os.fstat(src.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_file:
                raise ValueError("source file limit")
            data = src.read(min(max_file, max_total - total) + 1)
        total += len(data)
        if len(data) > max_file or total > max_total:
            raise ValueError("source byte limit")
        if data and not data.endswith(b"\n"):
            end = data.rfind(b"\n") + 1
            try:
                json.loads(data[end:])
            except (ValueError, UnicodeError):
                data = data[:end]
        member = tarfile.TarInfo(path.relative_to(home).as_posix())
        member.size, member.mode, member.mtime = len(data), 0o600, info.st_mtime
        tar.addfile(member, io.BytesIO(data))
'''


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and str(path) == name
        and (name == EXEC_LOG or (
            len(path.parts) >= 3
            and path.parts[:2] in ((".codex", "sessions"), (".codex", "archived_sessions"))
            and path.suffix == ".jsonl"
        ))
    )


def publish(data: bytes, base: Path, trace_id: str) -> str:
    """Validate every tar member and atomically publish a complete trace directory."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", trace_id):
        raise ValueError("invalid trace identity")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive byte limit")
    base.mkdir(parents=True, exist_ok=True)
    dest = base / trace_id
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("trace capture already exists")
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=base) as tmp:
        staging = Path(tmp) / "native"
        staging.mkdir()
        seen: set[str] = set()
        total = 0
        with tarfile.open(fileobj=BytesIO(data), mode="r:") as tar:
            for member in tar:
                if (not member.isfile() or not _allowed(member.name) or member.name in seen
                        or member.size < 0 or member.size > MAX_FILE_BYTES):
                    raise ValueError("unsafe archive member")
                seen.add(member.name)
                total += member.size
                if len(seen) > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise ValueError("archive content limit")
                path = staging / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, path.open("xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("incomplete archive member")
                        output.write(chunk)
                        remaining -= len(chunk)
                path.chmod(0o600)
        staging.rename(dest)
    return str(dest)


async def harvest(runtime, home: str, trace_id: str, base: Path) -> str:
    # Trace identity never becomes a VM shell argument or a remote pathname.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", trace_id):
        raise ValueError("invalid trace identity")
    target = f"/tmp/codex-capture-{uuid.uuid4().hex}.tar"
    try:
        result = await runtime.run([
            "python3", "-c", CAPTURE, home, target, str(MAX_FILES), str(MAX_FILE_BYTES),
            str(MAX_TOTAL_BYTES), str(MAX_ARCHIVE_BYTES),
        ], {})
        if result.exit_code != 0:
            raise RuntimeError("native capture failed")
        data = await runtime.read(target, max_bytes=MAX_ARCHIVE_BYTES)
        return publish(data, base, trace_id)
    finally:
        # Independently bounded even if the surrounding capture timeout has fired.
        try:
            await asyncio.wait_for(runtime.run(["rm", "-f", target], {}), 2)
        except Exception:
            pass
