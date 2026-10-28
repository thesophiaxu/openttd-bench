// vehicles.*: build/clone/refit/sell/start/stop, shared by rail and road.

function _depot_tile(ctx) {
    local tile = Util.TileArg(ctx.args, "depot");
    if (!GSRail.IsRailDepotTile(tile) && !GSRoad.IsRoadDepotTile(tile)) throw "(depot_x, depot_y) is not a depot tile";
    return tile;
}

function _allowed_type(ctx, engine) {
    // The control plane stamps `allowed_types` from the task rules; the script stays
    // generic and merely honours a per-request constraint.
    if (!Util.Has(ctx.args, "allowed_types")) return;
    local vt = Util.VehicleTypeName(GSEngine.GetVehicleType(engine));
    foreach (t in ctx.args.allowed_types) if (t == vt) return;
    throw "vehicle type '" + vt + "' is not allowed by this task's rules";
}

function _id_ok(id) {
    return id != null && id != false && id != GSVehicle.VEHICLE_INVALID;
}

Registry.Add("vehicles.build", function(ctx) {
    local a = { depot = _depot_tile(ctx), engine = Util.Int(ctx.args, "engine"), cargo = Util.Int(ctx.args, "cargo", -1) };
    if (!GSEngine.IsValidEngine(a.engine)) throw "engine " + a.engine + " does not exist";
    _allowed_type(ctx, a.engine);
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        local vehicle;
        if (a.cargo >= 0) vehicle = GSVehicle.BuildVehicleWithRefit(a.depot, a.engine, a.cargo);
        else vehicle = GSVehicle.BuildVehicle(a.depot, a.engine);
        if (ctx.validate) return { ok = _id_ok(vehicle) };
        local ok = _id_ok(vehicle) && GSVehicle.IsValidVehicle(vehicle);
        local r = { ok = ok };
        if (ok) { r.vehicle <- vehicle; r.type <- Util.VehicleTypeName(GSVehicle.GetVehicleType(vehicle)); }
        return r;
    });
}, { write = true });

Registry.Add("vehicles.add_wagon", function(ctx) {
    local a = { vehicle = Util.Int(ctx.args, "vehicle"), engine = Util.Int(ctx.args, "engine") };
    Util.OwnVehicle(ctx, a.vehicle);
    if (!GSEngine.IsValidEngine(a.engine) || !GSEngine.IsWagon(a.engine)) throw "engine " + a.engine + " is not a wagon";
    if (!GSVehicle.IsStoppedInDepot(a.vehicle)) throw "the train must be stopped in a depot";
    a.depot <- GSVehicle.GetLocation(a.vehicle);
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        local wagon = GSVehicle.BuildVehicle(a.depot, a.engine);
        if (ctx.validate) return { ok = _id_ok(wagon) };
        if (!_id_ok(wagon) || !GSVehicle.IsValidVehicle(wagon)) return { ok = false };
        local ok = GSVehicle.MoveWagonChain(wagon, 0, a.vehicle, GSVehicle.GetNumWagons(a.vehicle) - 1);
        if (!ok) {
            local err = GSError.GetLastErrorString();
            GSVehicle.SellVehicle(wagon);
            return { ok = false, error = "wagon built but could not be attached: " + (err == null ? "?" : err) };
        }
        return { ok = true, wagons = GSVehicle.GetNumWagons(a.vehicle) };
    });
}, { write = true });

Registry.Add("vehicles.clone", function(ctx) {
    local a = { depot = _depot_tile(ctx), vehicle = Util.Int(ctx.args, "vehicle"), share = Util.Bool(ctx.args, "share_orders", false) };
    Util.OwnVehicle(ctx, a.vehicle);
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        local copy = GSVehicle.CloneVehicle(a.depot, a.vehicle, a.share);
        if (ctx.validate) return { ok = _id_ok(copy) };
        local ok = _id_ok(copy) && GSVehicle.IsValidVehicle(copy);
        local r = { ok = ok };
        if (ok) r.vehicle <- copy;
        return r;
    });
}, { write = true });

Registry.Add("vehicles.sell", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")) };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.SellVehicle(a.vehicle) }; });
}, { write = true });

Registry.Add("vehicles.sell_wagon", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")), wagon = Util.Int(ctx.args, "wagon") };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.SellWagon(a.vehicle, a.wagon) }; });
}, { write = true });

Registry.Add("vehicles.start", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")) };
    local state = GSVehicle.GetState(a.vehicle);
    if (state != GSVehicle.VS_STOPPED && state != GSVehicle.VS_IN_DEPOT) return { ok = true, possible = true, executed = false, cost = 0, note = "already running" };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.StartStopVehicle(a.vehicle) }; });
}, { write = true });

Registry.Add("vehicles.stop", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")) };
    local state = GSVehicle.GetState(a.vehicle);
    if (state == GSVehicle.VS_STOPPED || state == GSVehicle.VS_IN_DEPOT) return { ok = true, possible = true, executed = false, cost = 0, note = "already stopped" };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.StartStopVehicle(a.vehicle) }; });
}, { write = true });

Registry.Add("vehicles.refit", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")), cargo = Util.Int(ctx.args, "cargo") };
    if (!Util.IsCargo(a.cargo)) throw "unknown cargo id " + a.cargo;
    return Util.Mutate(ctx, false, a, function(ctx, a) {
        local ok = GSVehicle.RefitVehicle(a.vehicle, a.cargo);
        local r = { ok = ok };
        if (ok && !ctx.validate) r.capacity <- GSVehicle.GetCapacity(a.vehicle, a.cargo);
        return r;
    });
}, { write = true });

Registry.Add("vehicles.send_to_depot", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")) };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.SendVehicleToDepot(a.vehicle) }; });
}, { write = true });

Registry.Add("vehicles.reverse", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")) };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.ReverseVehicle(a.vehicle) }; });
}, { write = true });

Registry.Add("vehicles.rename", function(ctx) {
    local a = { vehicle = Util.OwnVehicle(ctx, Util.Int(ctx.args, "vehicle")), name = Util.Str(ctx.args, "name") };
    return Util.Mutate(ctx, false, a, function(ctx, a) { return { ok = GSVehicle.SetName(a.vehicle, a.name) }; });
}, { write = true });
