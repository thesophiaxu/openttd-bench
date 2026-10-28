"""`ottd docs`: the generated reference from the schema + cargo table + gameplay notes."""

from __future__ import annotations

from typing import Any

from openttd_client.game import load_cargo_table, load_schema

NOTES = """\
GAMEPLAY NOTES
- Coordinates: x grows towards the south-west on screen, y towards the south-east; tile
  (x, y) is a map cell. `inspect region` renders rows of y, columns of x.
- rail.track lays a straight run: FROM = tile just before the run (direction; the
  platform/track tile you leave), VIA = first tile built, TO = tile just AFTER the last
  tile built (TO is untouched: point it at the platform tile you want to reach).
  FROM=(117,125) VIA=(118,125) TO=(139,125) builds 118..138 and connects to a platform
  at 139. A curve is a one-tile run: FROM=(x,y-1) VIA=(x,y) TO=(x+1,y).
- Station platforms lie along `direction` (NE_SW = along x, NW_SE = along y) and only
  connect to track entering along that axis at either end.
- A depot's `front` tile must carry track (or road) that leads into the line; a depot
  beside a line with no connecting piece is unreachable.
- vehicles.build needs the depot tile and an engine id; trains need wagons
  (vehicles.add_wagon while stopped in the depot) for the cargo; road vehicles carry
  cargo directly (buses: passengers; trucks: freight).
- Orders are applied asynchronously by your company AI: each orders.* call returns a
  ticket; `Ticket.wait()` (or `ottd ticket <id>`) confirms it. Then vehicles.start.
- Flags: full_load on the loading end only; non_stop_intermediate to skip stations on
  the way; unload/transfer only with a real feeder plan. orders.unshare clears orders.
- Construction commands return {ok, possible, executed, queued, cost}. Queued
  construction lands within a game tick; re-query tiles to confirm.
- inspect.deliveries is the server-accumulated delivery count for the task's monitored
  cargos; everything else is a raw lookup of what a human player could see.
- The clock has two modes; `ottd state` reports `clock_mode`. `paused` (a barrier): nothing
  moves until you `wait`; in multi-company games a wait returns status=blocked after ~4
  minutes if a rival is still thinking — not an error, keep building and wait again later.
  `realtime`: the clock starts at the first company's first action and runs continuously,
  while you think too; `wait` just sleeps until the target date. Waits are clamped to the
  horizon; in realtime games `game_over: true` means the horizon passed and commands are
  refused.
"""


def render(target: str | None = None) -> str:
    schema = load_schema()
    cargo = load_cargo_table()
    commands = [c for c in schema["commands"] if not c.get("admin_only")]
    if target:
        wanted = [c for c in commands if c["name"] == target or c["name"].startswith(target + ".")]
        if not wanted:
            return f"no command or namespace named {target!r}; namespaces: {sorted({c['name'].split('.')[0] for c in commands})}"
        return "\n\n".join(_render_command(c, schema) for c in wanted)
    out = [
        "OPENTTD BENCH — COMMAND REFERENCE",
        "",
        "Every command is `ottd <namespace> <command> --arg value ...` or `g.<namespace>.<command>(arg=value)`.",
        "Construction commands accept --validate / g.<ns>.validate.<command>(...) to test without building.",
        "Root: ottd state | ottd wait --months N | ottd events | ottd ticket ID | ottd batch FILE | ottd docs [ns[.cmd]]",
        "",
    ]
    if cargo:
        out.append("CARGO TABLE (id: label = name)")
        for row in cargo:
            out.append(f"  {row['id']:>2}: {row['label']} = {row['name']}")
        out.append("")
    out.append("ORDER FLAGS: " + ", ".join(sorted(schema["order_flags"])))
    out.append("")
    current = None
    for c in commands:
        ns = c["name"].split(".", 1)[0]
        if ns != current:
            current = ns
            out.append(f"== {ns} ==")
        out.append(_render_command(c, schema, brief=True))
    out.append("")
    out.append(NOTES)
    return "\n".join(out)


def _render_command(c: dict[str, Any], schema: dict[str, Any], brief: bool = False) -> str:
    args = []
    for a in c["args"]:
        part = f"{a['name']}:{a['type']}"
        if not a["required"]:
            part += f"={a['default']!r}"
        if a.get("choices") and a["name"] != "flags":
            part += "{" + "|".join(a["choices"]) + "}"
        args.append(part)
    head = f"{c['name']}({', '.join(args)})"
    tags = []
    if not c["read_only"]:
        tags.append("write")
    if c["validate"]:
        tags.append("validate")
    if c.get("mode"):
        tags.append(c["mode"])
    if c.get("visibility") == "own":
        tags.append("own-only")
    line = f"  {head}" + (f"  [{' '.join(tags)}]" if tags else "")
    if brief:
        return line + f"\n      {c['doc']}"
    return line + f"\n      {c['doc']}\n      returns: {c['returns']}"


__all__ = ["render"]
