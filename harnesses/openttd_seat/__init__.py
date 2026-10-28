"""The shared seat layer: what every OpenTTD harness needs besides its own program.

Not a harness id (verifiers loads a harness by top-level module name); harnesses live next
to this package under `harnesses/` and subclass `SeatHarness`.
"""

from openttd_seat.seat import SeatConfig, SeatHarness, SeatPaths, demoted, seat_paths

__all__ = ["SeatConfig", "SeatHarness", "SeatPaths", "demoted", "seat_paths"]
