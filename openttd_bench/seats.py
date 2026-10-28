"""Seats: N concurrent agent rollouts inside ONE sandbox.

verifiers 0.3.1 serializes every rollout that borrows the same `Runtime` object:
`Rollout.open()` takes `runtime.borrow_lock` for the rollout's whole life because it
swaps the runtime-wide `runtime.env` (the per-task process environment) in and out.
Two seats sharing one box would therefore play one after the other, and a naive
"drop the lock" patch would let seat A's processes inherit seat B's environment —
which is where the seat's bearer token lives.

`SeatRuntime` is the patch that keeps both properties: a per-seat *view* of the box
with its own lock and its own env, delegating every operation to the underlying
runtime. Concurrency needs no framework change and the stock wheel keeps working.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from verifiers.v1 import ProgramResult, Runtime, RuntimeProcess


class SeatRuntime(Runtime):
    """One seat's view of a shared box: private `env` + `borrow_lock`, shared world."""

    def __init__(self, box: Runtime, seat: str) -> None:
        super().__init__(name=f"{box.name}/{seat}")
        self.box = box
        self.seat = seat
        self.config = box.config
        self.info = box.info.model_copy(update={"borrowed": True})

    # -- identity the framework reads -------------------------------------------

    @property
    def is_local(self) -> bool:  # type: ignore[override]
        return self.box.is_local

    @property
    def stopped(self) -> bool:  # type: ignore[override]
        return self.box.stopped

    @stopped.setter
    def stopped(self, value: bool) -> None:
        # `Runtime.__init__` and `stop()` assign this; a view never owns teardown.
        pass

    @property
    def supports_live_processes(self) -> bool:
        return self.box.supports_live_processes

    @property
    def published_port(self) -> int | None:
        return self.box.published_port

    def host_url(self, url: str) -> str:
        return self.box.host_url(url)

    # -- lifecycle: a view never starts or stops the box -------------------------

    async def start(self) -> None:
        raise RuntimeError("a SeatRuntime views a live box; start the box instead")

    async def teardown(self) -> None:
        return None

    def cleanup(self) -> None:
        return None

    async def prepare_setup(self) -> None:
        await self.box.prepare_setup()

    async def prepare_execution(self, routes: list[str] | None) -> None:
        await self.box.prepare_execution(routes)

    async def expose(self, port: int) -> str | None:
        return await self.box.expose(port)

    # -- execution: the seat's env rides every process ---------------------------

    async def run(self, argv: list[str], env: dict[str, str]) -> ProgramResult:
        return await self.box.run(argv, self.process_env(env))

    async def run_program(self, argv: list[str], env: dict[str, str]) -> ProgramResult:
        return await self.box.run_program(argv, self.process_env(env))

    async def open_process(self, argv: list[str], env: dict[str, str]) -> RuntimeProcess:
        return await self.box.open_process(argv, self.process_env(env))

    async def run_background(self, argv: list[str], env: dict[str, str], log: str) -> None:
        await self.box.run_background(argv, self.process_env(env), log)

    async def alive(self) -> bool:
        return await self.box.alive()

    async def _read(self, path: str) -> bytes:
        return await self.box._read(path)

    async def write(self, path: str, data: bytes) -> None:
        await self.box.write(path, data)


@asynccontextmanager
async def seat_views(box: Runtime, seats: list[str]) -> AsyncIterator[dict[str, SeatRuntime]]:
    """Per-seat views of `box` for the duration of the context."""
    yield {seat: SeatRuntime(box, seat) for seat in seats}


__all__: list[str] = ["SeatRuntime", "seat_views"]


def _unused(*_: Any) -> None:  # keep static checkers quiet about unused imports
    pass
