"""HTTP transport to the control plane (stdlib only; env-var or ~/.openttd_seat auth)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class OpenTTDError(RuntimeError):
    """The server rejected or could not complete a request."""

    def __init__(self, message: str, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def _seat_file_token() -> str:
    """The token from `~/.openttd_seat` (KEY=VALUE lines), for processes whose env lost it."""
    try:
        with open(os.path.expanduser("~/.openttd_seat")) as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    return values.get("OPENTTD_API_TOKEN", "").strip()


class Transport:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 120.0) -> None:
        self.url = (url or os.environ.get("OPENTTD_API_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.token = token if token is not None else (os.environ.get("OPENTTD_API_TOKEN") or _seat_file_token())
        self.timeout = timeout

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout if timeout is None else timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")
            try:
                detail = json.loads(text).get("detail", text)
            except ValueError:
                detail = text
            raise OpenTTDError(f"HTTP {exc.code} from {path}: {detail}", exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenTTDError(f"cannot reach the game server at {self.url}: {getattr(exc, 'reason', exc)}") from None
        if not isinstance(payload, dict):
            raise OpenTTDError(f"unexpected response from {path}: {payload!r}")
        return payload


__all__ = ["OpenTTDError", "Transport"]
