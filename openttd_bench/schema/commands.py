"""Every primitive the game exposes, declared once.

A `Command` is goal-free: it names a lookup or a build action, its arguments, where it
executes (`GS` = the GameScript batch executor, `CP` = served by the control plane),
its transport mode for HTTP-layer rule enforcement, and its visibility class for the
per-caller policy. Mutators with `validate=True` have a `--validate` twin (GSTestMode).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Order flags by name -> the engine's ScriptOrderFlags bits (script_order.hpp).
ORDER_FLAGS: dict[str, int] = {
    "none": 0,
    "non_stop_intermediate": 1 << 0,
    "non_stop_destination": 1 << 1,
    "unload": 1 << 2,
    "transfer": 1 << 3,
    "no_unload": 1 << 4,
    "full_load": 2 << 5,
    "full_load_any": 3 << 5,
    "no_load": 1 << 7,
    # depot orders only
    "service_if_needed": 1 << 2,
    "stop_in_depot": 1 << 3,
    "goto_nearest_depot": 1 << 8,
}

RAIL_DIRECTIONS = ("NE_SW", "NW_SE")
RAIL_TRACKS = ("NE_SW", "NW_SE", "NW_NE", "SW_SE", "NW_SW", "NE_SE")
SIGNAL_TYPES = (
    "normal",
    "entry",
    "exit",
    "combo",
    "pbs",
    "pbs_oneway",
    "normal_twoway",
    "entry_twoway",
    "exit_twoway",
    "combo_twoway",
)
SLOPES = ("W", "S", "E", "N", "NW", "SW", "SE", "NE", "EW", "NS")


@dataclass(frozen=True)
class Arg:
    name: str
    type: str = "int"  # int | str | bool | int_list | str_list
    required: bool = True
    default: Any = None
    doc: str = ""
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Command:
    name: str  # namespace.command
    doc: str
    args: tuple[Arg, ...] = ()
    read_only: bool = True
    executor: str = "GS"  # GS | CP
    mode: str | None = None  # rail | road | air | water | None (mode-neutral)
    validate: bool = False  # has a GSTestMode twin
    visibility: str = "public"  # public | own | company_filtered
    returns: str = ""
    cost: int = 1  # fairness weight (bigger = more game-thread work)
    admin_only: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def namespace(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def short(self) -> str:
        return self.name.split(".", 1)[1]


def _xy(prefix: str = "", doc: str = "") -> tuple[Arg, ...]:
    p = f"{prefix}_" if prefix else ""
    return (Arg(f"{p}x", doc=doc or f"{prefix or 'tile'} x"), Arg(f"{p}y", doc=doc or f"{prefix or 'tile'} y"))


ENVELOPE = (
    "{ok, possible, executed, cost, error} — `possible`/`cost` come from the engine's "
    "own validation; `executed` is false for --validate calls; construction runs "
    "asynchronously (confirm by re-querying), vehicle builds return their id synchronously"
)

_C: list[Command] = []


def add(*commands: Command) -> None:
    _C.extend(commands)


# --- sys: admin/deity only (init_game and the control plane) --------------------------
add(
    Command(
        "sys.status",
        "Clock, pause state, tick, company ids, map size.",
        executor="GS",
        admin_only=True,
        returns="{date, year, month, day, paused, tick, companies, map_x, map_y, ops_left}",
    ),
    Command(
        "sys.found_town",
        "Deity: found a town at a tile (task (a) map mechanism).",
        (
            Arg("x"),
            Arg("y"),
            Arg("size", "str", False, "medium", choices=("small", "medium", "large")),
            Arg("city", "bool", False, False),
            Arg("name", "str", False, None),
        ),
        read_only=False,
        admin_only=True,
        returns="{ok, town, error}",
    ),
    Command(
        "sys.company_states",
        "Deity: every company's measured state (grading snapshot).",
        admin_only=True,
        returns="{companies: [state...]}",
    ),
    Command(
        "sys.registry", "The GameScript's registered command names.", admin_only=True, returns="{commands: [name...]}"
    ),
    Command(
        "sys.tickets",
        "Diagnostic: outstanding order tickets and mailbox signs as the GameScript sees them.",
        admin_only=True,
        returns="{pending: [...], signs: [...], tick}",
    ),
    Command(
        "sys.probe",
        "Diagnostic: how this Squirrel build captures outer variables.",
        admin_only=True,
        returns="{local_capture, param_capture, param_passed, table_capture, nested_call}",
    ),
)

# --- inspect: goal-free lookups --------------------------------------------------------
add(
    Command(
        "inspect.tile",
        "Everything about one tile: slope, heights, buildability, water, owner, "
        "rail/road/station/depot/bridge/tunnel flags, industry, closest town.",
        _xy(),
        returns="{x, y, tile, slope, min_height, max_height, buildable, water, coast, tree, "
        "house, owner, rail, rail_type, rail_tracks, rail_depot, depot_front, rail_station, station, "
        "station_direction, level_crossing, road, road_depot, road_station, drive_through, bridge, "
        "bridge_end, tunnel, tunnel_end, industry, industry_type, closest_town, signals: [...]}",
    ),
    Command(
        "inspect.region",
        "ASCII map of a rectangle (<= 32x32): terrain, water, rail, road, stations, depots, industries, houses, trees.",
        (Arg("x"), Arg("y"), Arg("width", "int", False, 16), Arg("height", "int", False, 16)),
        returns="{x, y, width, height, rows: [str...], legend}",
        cost=4,
    ),
    Command(
        "inspect.acceptance",
        "Cargo acceptance and production sums inside a station coverage "
        "area anchored at (x,y) with the given footprint and catchment radius; a station "
        "accepts a cargo only when the sum is >= 8.",
        (
            Arg("x"),
            Arg("y"),
            Arg("width", "int", False, 1),
            Arg("height", "int", False, 1),
            Arg("radius", "int", False, 3),
        ),
        returns="{accepts: {LABEL: sum}, produces: {LABEL: sum}}",
        cost=2,
    ),
    Command(
        "inspect.towns",
        "Towns, paginated (limit <= 50).",
        (Arg("offset", "int", False, 0), Arg("limit", "int", False, 50)),
        returns="{towns: [{id, name, x, y, population, houses, city, growth_rate, "
        "exclusive_rights_company, exclusive_rights_months}], total, next_offset}",
        cost=2,
    ),
    Command(
        "inspect.town",
        "One town in detail, including last-month cargo production and your company's rating there.",
        (Arg("town"),),
        returns="{id, name, x, y, population, houses, city, growth_rate, rating, "
        "production: {LABEL: {produced, supplied, transported_pct}}, exclusive_rights_company, "
        "exclusive_rights_months}",
    ),
    Command(
        "inspect.industries",
        "Industries, paginated (limit <= 50); optionally only those producing or accepting a cargo id.",
        (
            Arg("offset", "int", False, 0),
            Arg("limit", "int", False, 50),
            Arg("producing", "int", False, -1, "cargo id"),
            Arg("accepting", "int", False, -1, "cargo id"),
        ),
        returns="{industries: [{id, name, type, type_name, x, y, produces: [{cargo, label, "
        "last_month_production, last_month_transported}], accepts: [label...], stations_around}], "
        "total, next_offset}",
        cost=3,
    ),
    Command(
        "inspect.industry",
        "One industry in detail.",
        (Arg("industry"),),
        returns="{id, name, type, type_name, x, y, produces: [...], accepts: [...], stations_around}",
    ),
    Command(
        "inspect.industry_types",
        "Industry types with the cargos they produce/accept.",
        returns="{types: [{id, name, produces: [label], accepts: [label], raw}]}",
    ),
    Command(
        "inspect.cargos",
        "The runtime cargo table (ids, labels, names, classes).",
        returns="{cargos: [{id, label, name, freight, town_effect}]}",
    ),
    Command(
        "inspect.companies",
        "Every company: name, value, performance, finances summary (exact cash/loan only for your own company).",
        visibility="company_filtered",
        returns="{companies: [company...]}",
        cost=2,
    ),
    Command(
        "inspect.company",
        "One company in detail (defaults to your own).",
        (Arg("company", "int", False, -1),),
        visibility="company_filtered",
        returns="{id, name, is_me, is_ai, bankrupt, cash*, loan*, company_value, performance, "
        "quarterly: [{income, expenses, delivered, value, performance}], operating_profit, "
        "vehicles: {rail, road, water, air}, stations, rail_pieces, road_pieces} (* own only)",
    ),
    Command(
        "inspect.stations",
        "Stations of a company (defaults to your own): location, types, "
        "waiting cargo and ratings; vehicle lists only for your own.",
        (Arg("company", "int", False, -1),),
        visibility="company_filtered",
        returns="{stations: [{id, name, x, y, owner, types, cargo: {LABEL: {waiting, rating}}, vehicles*}]}",
        cost=3,
    ),
    Command(
        "inspect.station",
        "One station in detail.",
        (Arg("station"),),
        visibility="company_filtered",
        returns="{id, name, x, y, owner, types, radius, cargo: {LABEL: {waiting, rating}}, nearest_town, vehicles*}",
    ),
    Command(
        "inspect.vehicles",
        "Your vehicles: type, state, location, cargo, profit.",
        visibility="own",
        returns="{vehicles: [{id, name, type, engine, state, x, y, wagons, "
        "profit_this_year, profit_last_year, cargo: {LABEL: {load, capacity}}, orders}]}",
        cost=3,
    ),
    Command(
        "inspect.vehicle",
        "One of your vehicles in detail.",
        (Arg("vehicle"),),
        visibility="own",
        returns="{id, name, type, engine, engine_name, state, x, y, wagons, age, max_speed, "
        "profit_this_year, profit_last_year, value, cargo: {...}, orders, order_list: [...]}",
    ),
    Command(
        "inspect.orders",
        "The order list of one of your vehicles.",
        (Arg("vehicle"),),
        visibility="own",
        returns="{vehicle, count, orders: [{position, destination_tile, x, y, station, flags, kind}], shared}",
    ),
    Command(
        "inspect.bridges",
        "Bridge types buildable for a span of `length` tiles.",
        (Arg("length"),),
        returns="{bridges: [{id, name, max_speed, price, min_length, max_length}]}",
    ),
    Command("inspect.subsidies", "Open and awarded subsidies.", returns="{subsidies: [...]}"),
    Command(
        "inspect.deliveries",
        "Cumulative cargo delivered by YOUR company, accumulated by the "
        "server from the game's cargo monitors for the task's monitored cargos.",
        (Arg("cargo", "int", False, -1, "cargo id (default: all monitored)"),),
        executor="CP",
        visibility="own",
        returns="{monitored: [label...], cargos: {LABEL: {total, towns: {id: n}, industries: {id: n}}}}",
    ),
)

# --- land: mode-neutral terraforming ------------------------------------------------------
add(
    Command(
        "land.demolish",
        "Demolish whatever is on a tile (trees, houses, your own objects).",
        _xy(),
        read_only=False,
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "land.level",
        "Level the land in the rectangle between two tiles.",
        (*_xy(), *_xy("to")),
        read_only=False,
        validate=True,
        returns=ENVELOPE,
        cost=3,
    ),
    Command(
        "land.raise",
        "Raise a corner/edge of a tile (slope names: W S E N NW SW SE NE EW NS).",
        (*_xy(), Arg("slope", "str", choices=SLOPES)),
        read_only=False,
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "land.lower",
        "Lower a corner/edge of a tile.",
        (*_xy(), Arg("slope", "str", choices=SLOPES)),
        read_only=False,
        validate=True,
        returns=ENVELOPE,
    ),
)

# --- rail --------------------------------------------------------------------------------
add(
    Command("rail.types", "Available rail types.", mode="rail", returns="{types: [{id, name, max_speed}]}"),
    Command(
        "rail.engines",
        "Buildable locomotives and wagons (paginated, limit <= 50).",
        (Arg("offset", "int", False, 0), Arg("limit", "int", False, 50)),
        mode="rail",
        returns="{engines: [{id, name, wagon, cargo, label, capacity, max_speed, power, price, "
        "running_cost, reliability, rail_type}], total, next_offset}",
        cost=2,
    ),
    Command(
        "rail.track",
        "Lay a straight (or 45-degree diagonal) run of track: FROM is the tile just "
        "before the run (it fixes the incoming direction, e.g. a station or existing track tile), VIA is the "
        "first tile built on, TO is the tile just AFTER the last tile built on (TO itself stays untouched; "
        "make it the tile you want to connect to, e.g. the next station's platform tile). "
        "FROM=(117,125) VIA=(118,125) TO=(139,125) builds tiles 118..138. A single tile with a curve is "
        "FROM=(x,y-1) VIA=(x,y) TO=(x+1,y).",
        (*_xy("from"), *_xy("via"), *_xy("to")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.remove_track",
        "Remove a run of track with the same FROM/VIA/TO semantics as rail.track.",
        (*_xy("from"), *_xy("via"), *_xy("to")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.piece",
        "Build one track piece on a tile.",
        (*_xy(), Arg("track", "str", choices=RAIL_TRACKS)),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.remove_piece",
        "Remove one track piece from a tile.",
        (*_xy(), Arg("track", "str", choices=RAIL_TRACKS)),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.station",
        "Build a rail station whose top-left tile is (x,y); platforms run along "
        "`direction`. `join_station` joins an existing station id.",
        (
            *_xy(),
            Arg("direction", "str", choices=RAIL_DIRECTIONS),
            Arg("platforms", "int", False, 1),
            Arg("length", "int", False, 3),
            Arg("join_station", "int", False, -1),
        ),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE + " + {station, footprint: {x, y, width, height}}",
    ),
    Command(
        "rail.remove_station",
        "Remove the rail station tile rectangle containing (x,y).",
        (*_xy(), *_xy("to")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.depot",
        "Build a rail depot at (x,y) whose exit faces the adjacent FRONT tile.",
        (*_xy(), *_xy("front")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.remove_depot",
        "Remove a rail depot.",
        _xy(),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.signal",
        "Build a signal on a track tile facing the adjacent FRONT tile.",
        (*_xy(), *_xy("front"), Arg("type", "str", False, "pbs", choices=SIGNAL_TYPES)),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.remove_signal",
        "Remove a signal.",
        (*_xy(), *_xy("front")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "rail.bridge",
        "Build a rail bridge between two tiles on one axis.",
        (
            *_xy(), *_xy("to"),
            Arg("bridge_type", "int", False, -1, "-1 = fastest spanning bridge (not filtered by affordability)"),
        ),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE + " + {bridge_type}",
        cost=2,
    ),
    Command(
        "rail.tunnel",
        "Build a rail tunnel starting at a sloped tile (the exit is found automatically).",
        _xy(),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE + " + {end_x, end_y}",
        cost=2,
    ),
    Command(
        "rail.remove_bridge", "Remove a bridge.", _xy(), read_only=False, mode="rail", validate=True, returns=ENVELOPE
    ),
    Command(
        "rail.remove_tunnel", "Remove a tunnel.", _xy(), read_only=False, mode="rail", validate=True, returns=ENVELOPE
    ),
    Command(
        "rail.connected",
        "Whether track connects FROM through VIA to TO.",
        (*_xy("from"), *_xy("via"), *_xy("to")),
        mode="rail",
        returns="{connected}",
    ),
)

# --- road --------------------------------------------------------------------------------
add(
    Command("road.types", "Available road types.", mode="road", returns="{types: [{id, name, max_speed}]}"),
    Command(
        "road.engines",
        "Buildable road vehicles (paginated, limit <= 50).",
        (Arg("offset", "int", False, 0), Arg("limit", "int", False, 50)),
        mode="road",
        returns="{engines: [{id, name, cargo, label, capacity, max_speed, price, running_cost, "
        "reliability, road_type}], total, next_offset}",
        cost=2,
    ),
    Command(
        "road.road",
        "Build a straight road between two tiles on one axis (`full` includes the "
        "end half-tiles so junctions connect).",
        (*_xy(), *_xy("to"), Arg("one_way", "bool", False, False), Arg("full", "bool", False, True)),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE,
        cost=2,
    ),
    Command(
        "road.remove_road",
        "Remove a straight road between two tiles.",
        (*_xy(), *_xy("to"), Arg("full", "bool", False, True)),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "road.stop",
        "Build a bus stop or truck bay at (x,y). Drive-through stops sit on a road "
        "tile with FRONT giving the road axis; bay stops face the adjacent FRONT road tile.",
        (
            *_xy(),
            *_xy("front"),
            Arg("kind", "str", choices=("bus", "truck")),
            Arg("drive_through", "bool", False, True),
            Arg("join_station", "int", False, -1),
        ),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE + " + {station}",
    ),
    Command(
        "road.remove_stop", "Remove a road stop.", _xy(), read_only=False, mode="road", validate=True, returns=ENVELOPE
    ),
    Command(
        "road.depot",
        "Build a road depot at (x,y) whose exit faces the adjacent FRONT tile.",
        (*_xy(), *_xy("front")),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "road.remove_depot",
        "Remove a road depot.",
        _xy(),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "road.bridge",
        "Build a road bridge between two tiles on one axis.",
        (*_xy(), *_xy("to"), Arg("bridge_type", "int", False, -1)),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE + " + {bridge_type}",
        cost=2,
    ),
    Command(
        "road.tunnel",
        "Build a road tunnel starting at a sloped tile.",
        _xy(),
        read_only=False,
        mode="road",
        validate=True,
        returns=ENVELOPE + " + {end_x, end_y}",
        cost=2,
    ),
    Command(
        "road.connected",
        "Whether two adjacent road tiles are connected.",
        (*_xy(), *_xy("to")),
        mode="road",
        returns="{connected}",
    ),
)

# --- vehicles: shared by rail and road ----------------------------------------------------
add(
    Command(
        "vehicles.build",
        "Build a vehicle of engine `engine` in the depot at (depot_x, depot_y); `cargo` refits it on build.",
        (*_xy("depot"), Arg("engine"), Arg("cargo", "int", False, -1)),
        read_only=False,
        validate=True,
        returns=ENVELOPE + " + {vehicle, type}",
    ),
    Command(
        "vehicles.add_wagon",
        "Build a wagon of engine `engine` and attach it to train `vehicle` (the train must be stopped in a depot).",
        (Arg("vehicle"), Arg("engine")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE + " + {wagons}",
    ),
    Command(
        "vehicles.clone",
        "Clone a vehicle (optionally sharing orders) in the depot at (depot_x, depot_y).",
        (*_xy("depot"), Arg("vehicle"), Arg("share_orders", "bool", False, False)),
        read_only=False,
        validate=True,
        returns=ENVELOPE + " + {vehicle}",
    ),
    Command(
        "vehicles.sell",
        "Sell a vehicle (must be stopped in a depot).",
        (Arg("vehicle"),),
        read_only=False,
        validate=True,
        returns=ENVELOPE,
    ),
    Command(
        "vehicles.sell_wagon",
        "Sell wagon number `wagon` of a train.",
        (Arg("vehicle"), Arg("wagon")),
        read_only=False,
        mode="rail",
        validate=True,
        returns=ENVELOPE,
    ),
    Command("vehicles.start", "Start a stopped vehicle.", (Arg("vehicle"),), read_only=False, returns=ENVELOPE),
    Command("vehicles.stop", "Stop a running vehicle.", (Arg("vehicle"),), read_only=False, returns=ENVELOPE),
    Command(
        "vehicles.refit",
        "Refit a vehicle (stopped in a depot) to carry `cargo`.",
        (Arg("vehicle"), Arg("cargo")),
        read_only=False,
        validate=True,
        returns=ENVELOPE + " + {capacity}",
    ),
    Command(
        "vehicles.send_to_depot",
        "Send a vehicle to the nearest depot.",
        (Arg("vehicle"),),
        read_only=False,
        returns=ENVELOPE,
    ),
    Command("vehicles.reverse", "Reverse a vehicle.", (Arg("vehicle"),), read_only=False, returns=ENVELOPE),
    Command(
        "vehicles.rename", "Name a vehicle.", (Arg("vehicle"), Arg("name", "str")), read_only=False, returns=ENVELOPE
    ),
)

# --- orders: asynchronous relay (tickets) --------------------------------------------------
ORDER_RET = "{ok, ticket} — resolve with `orders.ticket` or `ticket.wait()`; resolution reports {ok, error}"
add(
    Command(
        "orders.append",
        "Append a go-to-station order.",
        (Arg("vehicle"), Arg("station"), Arg("flags", "str_list", False, [], choices=tuple(ORDER_FLAGS))),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.append_depot",
        "Append a go-to-depot order for the depot at (x,y).",
        (Arg("vehicle"), *_xy(), Arg("flags", "str_list", False, [], choices=tuple(ORDER_FLAGS))),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.insert",
        "Insert a go-to-station order at `position`.",
        (
            Arg("vehicle"),
            Arg("position"),
            Arg("station"),
            Arg("flags", "str_list", False, [], choices=tuple(ORDER_FLAGS)),
        ),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.remove",
        "Remove the order at `position`.",
        (Arg("vehicle"), Arg("position")),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.set_flags",
        "Replace the flags of the order at `position`.",
        (Arg("vehicle"), Arg("position"), Arg("flags", "str_list", False, [], choices=tuple(ORDER_FLAGS))),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.skip_to",
        "Make the vehicle proceed to the order at `position`.",
        (Arg("vehicle"), Arg("position")),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.copy",
        "Copy the orders of `from_vehicle` onto `vehicle`.",
        (Arg("vehicle"), Arg("from_vehicle")),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.share",
        "Share the orders of `from_vehicle` with `vehicle`.",
        (Arg("vehicle"), Arg("from_vehicle")),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.unshare",
        "Stop sharing orders (this CLEARS the vehicle's orders).",
        (Arg("vehicle"),),
        read_only=False,
        returns=ORDER_RET,
    ),
    Command(
        "orders.ticket",
        "The state of an order ticket.",
        (Arg("ticket"),),
        executor="CP",
        visibility="own",
        returns="{ticket, status: pending|done|failed, ok, error, age_seconds}",
    ),
)

COMMANDS: dict[str, Command] = {c.name: c for c in _C}
NAMESPACES: tuple[str, ...] = tuple(dict.fromkeys(c.namespace for c in _C))


def command(name: str) -> Command:
    try:
        return COMMANDS[name]
    except KeyError:
        raise KeyError(f"unknown command {name!r}") from None


def by_namespace(namespace: str) -> list[Command]:
    return [c for c in _C if c.namespace == namespace]


def schema_dict() -> dict[str, Any]:
    return {
        "version": 3,
        "order_flags": ORDER_FLAGS,
        "commands": [
            {
                "name": c.name,
                "doc": c.doc,
                "read_only": c.read_only,
                "executor": c.executor,
                "mode": c.mode,
                "validate": c.validate,
                "visibility": c.visibility,
                "returns": c.returns,
                "cost": c.cost,
                "admin_only": c.admin_only,
                "args": [
                    {
                        "name": a.name,
                        "type": a.type,
                        "required": a.required,
                        "default": a.default,
                        "doc": a.doc,
                        "choices": list(a.choices) if a.choices else None,
                    }
                    for a in c.args
                ],
            }
            for c in _C
        ],
    }


def schema_json() -> str:
    return json.dumps(schema_dict(), indent=1, sort_keys=True)


__all__ = [
    "COMMANDS",
    "NAMESPACES",
    "ORDER_FLAGS",
    "Arg",
    "Command",
    "by_namespace",
    "command",
    "schema_dict",
    "schema_json",
]
