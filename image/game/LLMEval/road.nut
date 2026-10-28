// road.*: roads, bus stops, truck bays, depots, bridges, tunnels, engines.

function _road_type(ctx) {
    GSRoad.SetCurrentRoadType(GSRoad.ROADTYPE_ROAD);
    return GSRoad.ROADTYPE_ROAD;
}

Registry.Add("road.types", function(ctx) {
    local rows = [];
    foreach (t in [GSRoad.ROADTYPE_ROAD, GSRoad.ROADTYPE_TRAM]) {
        rows.append({ id = t, name = Util.Name(GSRoad.GetName(t)), max_speed = GSRoad.GetMaxSpeed(t), available = GSRoad.IsRoadTypeAvailable(t) });
    }
    return { types = rows };
});

Registry.Add("road.engines", function(ctx) {
    local company = Util.RequireSeat(ctx);
    local mode = GSCompanyMode(company);
    local page = Util.Page(ctx.args, 50);
    local rows = [];
    local skipped = 0;
    local total = 0;
    foreach (engine, _ in GSEngineList(GSVehicle.VT_ROAD)) {
        if (!GSEngine.IsBuildable(engine)) continue;
        total++;
        if (skipped++ < page.offset) continue;
        if (rows.len() >= page.limit) continue;
        local cargo = GSEngine.GetCargoType(engine);
        rows.append({
            id = engine, name = Util.Name(GSEngine.GetName(engine)),
            cargo = cargo, label = Util.IsCargo(cargo) ? Util.Label(cargo) : "",
            capacity = GSEngine.GetCapacity(engine), max_speed = GSEngine.GetMaxSpeed(engine),
            price = GSEngine.GetPrice(engine), running_cost = GSEngine.GetRunningCost(engine),
            reliability = GSEngine.GetReliability(engine), road_type = GSEngine.GetRoadType(engine),
            articulated = GSEngine.IsArticulated(engine),
        });
    }
    local next = page.offset + rows.len();
    return { engines = rows, total = total, next_offset = next < total ? next : -1, offset = page.offset };
});

Registry.Add("road.road", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to"),
                one_way = Util.Bool(ctx.args, "one_way", false), full = Util.Bool(ctx.args, "full", true) };
    return Util.Mutate(ctx, true, a, function(ctx, a) {
        _road_type(ctx);
        local ok;
        if (a.one_way) ok = a.full ? GSRoad.BuildOneWayRoadFull(a.from, a.to) : GSRoad.BuildOneWayRoad(a.from, a.to);
        else ok = a.full ? GSRoad.BuildRoadFull(a.from, a.to) : GSRoad.BuildRoad(a.from, a.to);
        return { ok = ok };
    });
}, { write = true, async = true });

Registry.Add("road.remove_road", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to"), full = Util.Bool(ctx.args, "full", true) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { _road_type(ctx); return { ok = a.full ? GSRoad.RemoveRoadFull(a.from, a.to) : GSRoad.RemoveRoad(a.from, a.to) }; });
}, { write = true, async = true });

Registry.Add("road.stop", function(ctx) {
    local kind = Util.Str(ctx.args, "kind");
    if (kind != "bus" && kind != "truck") throw "kind must be bus or truck";
    local a = { tile = Util.TileArg(ctx.args), front = Util.TileArg(ctx.args, "front"),
                rvt = kind == "bus" ? GSRoad.ROADVEHTYPE_BUS : GSRoad.ROADVEHTYPE_TRUCK,
                drive_through = Util.Bool(ctx.args, "drive_through", true) };
    if (GSMap.DistanceManhattan(a.tile, a.front) != 1) throw "front must be adjacent to the stop tile";
    local join = Util.Int(ctx.args, "join_station", -1);
    a.join_id <- join >= 0 ? join : GSStation.STATION_NEW;
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        _road_type(ctx);
        local ok = a.drive_through ? GSRoad.BuildDriveThroughRoadStation(a.tile, a.front, a.rvt, a.join_id)
                                   : GSRoad.BuildRoadStation(a.tile, a.front, a.rvt, a.join_id);
        local r = { ok = ok };
        if (ok && !ctx.validate) r.station <- GSStation.GetStationID(a.tile);
        return r;
    });
}, { write = true });

Registry.Add("road.remove_stop", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { _road_type(ctx); return { ok = GSRoad.RemoveRoadStation(a.tile) }; });
}, { write = true, async = true });

Registry.Add("road.depot", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), front = Util.TileArg(ctx.args, "front") };
    if (GSMap.DistanceManhattan(a.tile, a.front) != 1) throw "front must be adjacent to the depot tile";
    return Util.Mutate(ctx, true, a, function(ctx, a) { _road_type(ctx); return { ok = GSRoad.BuildRoadDepot(a.tile, a.front) }; });
}, { write = true, async = true });

Registry.Add("road.remove_depot", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { _road_type(ctx); return { ok = GSRoad.RemoveRoadDepot(a.tile) }; });
}, { write = true, async = true });

Registry.Add("road.bridge", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to") };
    a.length <- GSMap.DistanceManhattan(a.from, a.to) + 1;
    a.bridge <- _best_bridge(a.length, Util.Int(ctx.args, "bridge_type", -1));
    return Util.Mutate(ctx, true, a, function(ctx, a) {
        _road_type(ctx);
        return { ok = GSBridge.BuildBridge(GSVehicle.VT_ROAD, a.bridge, a.from, a.to), bridge_type = a.bridge, length = a.length };
    });
}, { write = true, async = true });

Registry.Add("road.tunnel", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) {
        _road_type(ctx);
        local ok = GSTunnel.BuildTunnel(GSVehicle.VT_ROAD, a.tile);
        local r = { ok = ok };
        if (ok) { local xy = Util.XY(GSTunnel.GetOtherTunnelEnd(a.tile)); r.end_x <- xy.x; r.end_y <- xy.y; }
        return r;
    });
}, { write = true, async = true });

Registry.Add("road.connected", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to") };
    return { connected = GSRoad.AreRoadTilesConnected(a.from, a.to) };
});
