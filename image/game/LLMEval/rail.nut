// rail.*: track, stations, depots, signals, bridges, tunnels, engines.

function _rail_type(ctx) {
    local rt = Util.Int(ctx.args, "rail_type", -1);
    if (rt < 0) {
        foreach (t, _ in GSRailTypeList()) { if (GSRail.IsRailTypeAvailable(t)) { rt = t; break; } }
        if (rt < 0) rt = 0;
    }
    GSRail.SetCurrentRailType(rt);
    return rt;
}

function _best_bridge(length, requested) {
    if (requested >= 0) return requested;
    local best = -1;
    local best_speed = -1;
    foreach (bridge, _ in GSBridgeList_Length(length)) {
        local speed = GSBridge.GetMaxSpeed(bridge);
        if (speed > best_speed) { best_speed = speed; best = bridge; }
    }
    if (best < 0) throw "no bridge type spans " + length + " tiles";
    return best;
}

Registry.Add("rail.types", function(ctx) {
    local rows = [];
    foreach (t, _ in GSRailTypeList()) {
        rows.append({ id = t, name = Util.Name(GSRail.GetName(t)), max_speed = GSRail.GetMaxSpeed(t), available = GSRail.IsRailTypeAvailable(t) });
    }
    return { types = rows };
});

Registry.Add("rail.engines", function(ctx) {
    local company = Util.RequireSeat(ctx);
    local mode = GSCompanyMode(company);
    local page = Util.Page(ctx.args, 50);
    local rows = [];
    local skipped = 0;
    local total = 0;
    foreach (engine, _ in GSEngineList(GSVehicle.VT_RAIL)) {
        if (!GSEngine.IsBuildable(engine)) continue;
        total++;
        if (skipped++ < page.offset) continue;
        if (rows.len() >= page.limit) continue;
        local cargo = GSEngine.GetCargoType(engine);
        rows.append({
            id = engine, name = Util.Name(GSEngine.GetName(engine)), wagon = GSEngine.IsWagon(engine),
            cargo = cargo, label = Util.IsCargo(cargo) ? Util.Label(cargo) : "",
            capacity = GSEngine.GetCapacity(engine), max_speed = GSEngine.GetMaxSpeed(engine),
            power = GSEngine.GetPower(engine), price = GSEngine.GetPrice(engine),
            running_cost = GSEngine.GetRunningCost(engine), reliability = GSEngine.GetReliability(engine),
            rail_type = GSEngine.GetRailType(engine),
        });
    }
    local next = page.offset + rows.len();
    return { engines = rows, total = total, next_offset = next < total ? next : -1, offset = page.offset };
});

function _triple(ctx) {
    return { from = Util.TileArg(ctx.args, "from"), via = Util.TileArg(ctx.args, "via"), to = Util.TileArg(ctx.args, "to") };
}

Registry.Add("rail.track", function(ctx) {
    return Util.Mutate(ctx, true, _triple(ctx), function(ctx, a) { _rail_type(ctx); return { ok = GSRail.BuildRail(a.from, a.via, a.to) }; });
}, { write = true, async = true });

Registry.Add("rail.remove_track", function(ctx) {
    return Util.Mutate(ctx, true, _triple(ctx), function(ctx, a) { return { ok = GSRail.RemoveRail(a.from, a.via, a.to) }; });
}, { write = true, async = true });

Registry.Add("rail.piece", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), track = Util.RailTrack(Util.Str(ctx.args, "track")) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { _rail_type(ctx); return { ok = GSRail.BuildRailTrack(a.tile, a.track) }; });
}, { write = true, async = true });

Registry.Add("rail.remove_piece", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), track = Util.RailTrack(Util.Str(ctx.args, "track")) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSRail.RemoveRailTrack(a.tile, a.track) }; });
}, { write = true, async = true });

Registry.Add("rail.station", function(ctx) {
    local dir = Util.Str(ctx.args, "direction");
    if (dir != "NE_SW" && dir != "NW_SE") throw "direction must be NE_SW or NW_SE";
    local a = {
        tile = Util.TileArg(ctx.args),
        direction = dir == "NE_SW" ? GSRail.RAILTRACK_NE_SW : GSRail.RAILTRACK_NW_SE,
        platforms = Util.Int(ctx.args, "platforms", 1),
        length = Util.Int(ctx.args, "length", 3),
        join = Util.Int(ctx.args, "join_station", -1),
    };
    if (a.platforms < 1 || a.platforms > 8 || a.length < 1 || a.length > 16) throw "platforms must be 1..8 and length 1..16";
    a.join_id <- a.join >= 0 ? a.join : GSStation.STATION_NEW;
    // Stations return an id, so they execute synchronously.
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        _rail_type(ctx);
        local ok = GSRail.BuildRailStation(a.tile, a.direction, a.platforms, a.length, a.join_id);
        local r = { ok = ok };
        if (ok && !ctx.validate) r.station <- GSStation.GetStationID(a.tile);
        local w = a.direction == GSRail.RAILTRACK_NE_SW ? a.length : a.platforms;
        local h = a.direction == GSRail.RAILTRACK_NE_SW ? a.platforms : a.length;
        r.footprint <- { x = GSMap.GetTileX(a.tile), y = GSMap.GetTileY(a.tile), width = w, height = h };
        return r;
    });
}, { write = true });

Registry.Add("rail.remove_station", function(ctx) {
    local a = { from = Util.TileArg(ctx.args) };
    a.to <- Util.Has(ctx.args, "to_x") ? Util.TileArg(ctx.args, "to") : a.from;
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSRail.RemoveRailStationTileRectangle(a.from, a.to, false) }; });
}, { write = true, async = true });

Registry.Add("rail.depot", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), front = Util.TileArg(ctx.args, "front") };
    if (GSMap.DistanceManhattan(a.tile, a.front) != 1) throw "front must be adjacent to the depot tile";
    return Util.Mutate(ctx, true, a, function(ctx, a) { _rail_type(ctx); return { ok = GSRail.BuildRailDepot(a.tile, a.front) }; });
}, { write = true, async = true });

Registry.Add("rail.remove_depot", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTile.DemolishTile(a.tile) }; });
}, { write = true, async = true });

Registry.Add("rail.signal", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), front = Util.TileArg(ctx.args, "front"), type = Util.SignalType(Util.Str(ctx.args, "type", "pbs")) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSRail.BuildSignal(a.tile, a.front, a.type) }; });
}, { write = true, async = true });

Registry.Add("rail.remove_signal", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), front = Util.TileArg(ctx.args, "front") };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSRail.RemoveSignal(a.tile, a.front) }; });
}, { write = true, async = true });

Registry.Add("rail.bridge", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to") };
    a.length <- GSMap.DistanceManhattan(a.from, a.to) + 1;
    a.bridge <- _best_bridge(a.length, Util.Int(ctx.args, "bridge_type", -1));
    return Util.Mutate(ctx, true, a, function(ctx, a) {
        _rail_type(ctx);
        return { ok = GSBridge.BuildBridge(GSVehicle.VT_RAIL, a.bridge, a.from, a.to), bridge_type = a.bridge, length = a.length };
    });
}, { write = true, async = true });

Registry.Add("rail.tunnel", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) {
        _rail_type(ctx);
        local ok = GSTunnel.BuildTunnel(GSVehicle.VT_RAIL, a.tile);
        local r = { ok = ok };
        if (ok) { local xy = Util.XY(GSTunnel.GetOtherTunnelEnd(a.tile)); r.end_x <- xy.x; r.end_y <- xy.y; }
        return r;
    });
}, { write = true, async = true });

Registry.Add("rail.remove_bridge", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSBridge.RemoveBridge(a.tile) }; });
}, { write = true, async = true });

Registry.Add("rail.remove_tunnel", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTunnel.RemoveTunnel(a.tile) }; });
}, { write = true, async = true });

Registry.Add("rail.connected", function(ctx) {
    local a = _triple(ctx);
    return { connected = GSRail.AreTilesConnected(a.from, a.via, a.to) };
});
