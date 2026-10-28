// inspect.vehicles / inspect.vehicle / inspect.orders (own vehicles; policy enforces).

function _order_kind(vehicle, pos) {
    if (GSOrder.IsGotoStationOrder(vehicle, pos)) return "station";
    if (GSOrder.IsGotoDepotOrder(vehicle, pos)) return "depot";
    if (GSOrder.IsGotoWaypointOrder(vehicle, pos)) return "waypoint";
    if (GSOrder.IsConditionalOrder(vehicle, pos)) return "conditional";
    return "other";
}

function _vehicle_orders(vehicle) {
    local orders = [];
    local count = GSOrder.GetOrderCount(vehicle);
    for (local pos = 0; pos < count; pos++) {
        local dest = GSOrder.GetOrderDestination(vehicle, pos);
        local xy = Util.XY(dest);
        local row = { position = pos, kind = _order_kind(vehicle, pos), x = xy.x, y = xy.y, destination_tile = dest, flags = GSOrder.GetOrderFlags(vehicle, pos) };
        if (row.kind == "station" && GSMap.IsValidTile(dest)) row.station <- GSStation.GetStationID(dest);
        orders.append(row);
    }
    return orders;
}

function _vehicle_cargo(vehicle) {
    local cargo = {};
    foreach (c in Util.Cargos()) {
        local cap = GSVehicle.GetCapacity(vehicle, c.id);
        if (cap <= 0) continue;
        cargo[c.label] <- { capacity = cap, load = GSVehicle.GetCargoLoad(vehicle, c.id) };
    }
    return cargo;
}

function _vehicle_row(vehicle, detail) {
    local tile = GSVehicle.GetLocation(vehicle);
    local engine = GSVehicle.GetEngineType(vehicle);
    local r = {
        id = vehicle, name = Util.Name(GSVehicle.GetName(vehicle)),
        owner = GSVehicle.GetOwner(vehicle),
        type = Util.VehicleTypeName(GSVehicle.GetVehicleType(vehicle)),
        engine = engine, engine_name = Util.Name(GSEngine.GetName(engine)),
        state = Util.VehicleStateName(GSVehicle.GetState(vehicle)),
        x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile),
        wagons = GSVehicle.GetNumWagons(vehicle),
        profit_this_year = GSVehicle.GetProfitThisYear(vehicle),
        profit_last_year = GSVehicle.GetProfitLastYear(vehicle),
        cargo = _vehicle_cargo(vehicle),
        orders = GSOrder.GetOrderCount(vehicle),
        in_depot = GSVehicle.IsInDepot(vehicle),
        stopped_in_depot = GSVehicle.IsStoppedInDepot(vehicle),
    };
    if (detail) {
        r.age <- GSVehicle.GetAge(vehicle);
        r.max_age <- GSVehicle.GetMaxAge(vehicle);
        r.speed <- GSVehicle.GetCurrentSpeed(vehicle);
        r.max_speed <- GSEngine.GetMaxSpeed(engine);
        r.value <- GSVehicle.GetCurrentValue(vehicle);
        r.running_cost <- GSVehicle.GetRunningCost(vehicle);
        r.reliability <- GSVehicle.GetReliability(vehicle);
        r.shared_orders <- GSVehicle.HasSharedOrders(vehicle);
        r.order_list <- _vehicle_orders(vehicle);
        local wagon_engines = [];
        for (local w = 0; w < r.wagons; w++) wagon_engines.append(GSVehicle.GetWagonEngineType(vehicle, w));
        r.wagon_engines <- wagon_engines;
    }
    return r;
}

Registry.Add("inspect.vehicles", function(ctx) {
    local company = Util.RequireSeat(ctx);
    local rows = [];
    local mode = GSCompanyMode(company);
    foreach (vehicle, _ in GSVehicleList()) {
        if (!GSVehicle.IsPrimaryVehicle(vehicle)) continue;
        if (rows.len() >= 200) break;
        rows.append(_vehicle_row(vehicle, false));
    }
    return { vehicles = rows, company = company };
});

Registry.Add("inspect.vehicle", function(ctx) {
    local vehicle = Util.Int(ctx.args, "vehicle");
    if (!GSVehicle.IsValidVehicle(vehicle)) throw "vehicle " + vehicle + " does not exist";
    return _vehicle_row(vehicle, true);
});

Registry.Add("inspect.orders", function(ctx) {
    local vehicle = Util.Int(ctx.args, "vehicle");
    if (!GSVehicle.IsValidVehicle(vehicle)) throw "vehicle " + vehicle + " does not exist";
    return { vehicle = vehicle, owner = GSVehicle.GetOwner(vehicle), count = GSOrder.GetOrderCount(vehicle), orders = _vehicle_orders(vehicle), shared = GSVehicle.HasSharedOrders(vehicle) };
});
