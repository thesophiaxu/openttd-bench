// Shared helpers for every namespace. Pure utility: nothing here knows a goal.

Util <- {};

Util.log_level <- 1;

Util.Info <- function(msg) { if (Util.log_level >= 1) GSLog.Info(msg); }
Util.Debug <- function(msg) { if (Util.log_level >= 2) GSLog.Info(msg); }

// GSAdmin.Send only serializes integers, strings, booleans, arrays and tables:
// a null (or float) anywhere makes the whole packet vanish. Clean recursively.
Util.Clean <- function(v) {
    local t = typeof v;
    if (t == "table") {
        local out = {};
        foreach (k, x in v) {
            if (x == null) continue;
            local c = Util.Clean(x);
            if (c != null) out[k] <- c;
        }
        return out;
    }
    if (t == "array") {
        local out = [];
        foreach (x in v) {
            if (x == null) continue;
            local c = Util.Clean(x);
            if (c != null) out.append(c);
        }
        return out;
    }
    if (t == "float") return v.tointeger();
    if (t == "integer" || t == "string" || t == "bool") return v;
    return "" + v;
}

Util.Send <- function(table) {
    local ok = GSAdmin.Send(Util.Clean(table));
    if (!ok) GSLog.Error("GSAdmin.Send refused a packet");
    return ok;
}

Util.Has <- function(args, name) { return (name in args) && args[name] != null; }

Util.Int <- function(args, name, dflt = null) {
    if (!Util.Has(args, name)) {
        if (dflt == null) throw "missing argument '" + name + "'";
        return dflt;
    }
    local v = args[name];
    local t = typeof v;
    if (t == "integer") return v;
    if (t == "float") return v.tointeger();
    if (t == "bool") return v ? 1 : 0;
    if (t == "string") {
        try { return v.tointeger(); } catch (e) { throw "argument '" + name + "' must be an integer"; }
    }
    throw "argument '" + name + "' must be an integer";
}

Util.Bool <- function(args, name, dflt = false) {
    if (!Util.Has(args, name)) return dflt;
    local v = args[name];
    if (typeof v == "bool") return v;
    if (typeof v == "integer") return v != 0;
    if (typeof v == "string") return v == "true" || v == "1" || v == "yes";
    return dflt;
}

Util.Str <- function(args, name, dflt = null) {
    if (!Util.Has(args, name)) {
        if (dflt == null) throw "missing argument '" + name + "'";
        return dflt;
    }
    return "" + args[name];
}

Util.Tile <- function(x, y) {
    if (x < 1 || y < 1 || x >= GSMap.GetMapSizeX() - 1 || y >= GSMap.GetMapSizeY() - 1) {
        throw "tile (" + x + "," + y + ") is outside the buildable map";
    }
    return GSMap.GetTileIndex(x, y);
}

Util.TileArg <- function(args, prefix = "") {
    local px = prefix == "" ? "x" : prefix + "_x";
    local py = prefix == "" ? "y" : prefix + "_y";
    return Util.Tile(Util.Int(args, px), Util.Int(args, py));
}

Util.XY <- function(tile) {
    if (tile == null || tile == GSMap.TILE_INVALID || !GSMap.IsValidTile(tile)) return { x = -1, y = -1 };
    return { x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile) };
}

Util.Err <- function(prefix) {
    local s = GSError.GetLastErrorString();
    if (s == null) s = "unknown error";
    return prefix + ": " + s;
}

Util.Page <- function(args, max_limit) {
    local offset = Util.Int(args, "offset", 0);
    local limit = Util.Int(args, "limit", max_limit);
    if (offset < 0) offset = 0;
    if (limit < 1) limit = 1;
    if (limit > max_limit) limit = max_limit;
    return { offset = offset, limit = limit };
}

Util.Paginate <- function(list, page, fn) {
    // Bounded per-call working set: only the requested window is materialized.
    local rows = [];
    local skipped = 0;
    local total = list.Count();
    foreach (id, _ in list) {
        if (skipped++ < page.offset) continue;
        if (rows.len() >= page.limit) break;
        local row = fn(id);
        if (row != null) rows.append(row);
    }
    local next = page.offset + rows.len();
    return { rows = rows, total = total, next_offset = next < total ? next : -1 };
}

// --- cargo table (runtime enumeration; labels are identity) --------------------
Util.cargo_rows <- null;
Util.cargo_by_id <- null;

Util.Cargos <- function() {
    if (Util.cargo_rows != null) return Util.cargo_rows;
    local rows = [];
    local by_id = {};
    foreach (cargo, _ in GSCargoList()) {
        local label = GSCargo.GetCargoLabel(cargo);
        local name = GSCargo.GetName(cargo);
        local row = {
            id = cargo,
            label = label == null ? ("C" + cargo) : label,
            name = name == null ? "" : name,
            freight = GSCargo.IsFreight(cargo),
            town_effect = GSCargo.GetTownEffect(cargo),
            passengers = GSCargo.HasCargoClass(cargo, GSCargo.CC_PASSENGERS),
            mail = GSCargo.HasCargoClass(cargo, GSCargo.CC_MAIL),
        };
        rows.append(row);
        by_id[cargo] <- row;
    }
    Util.cargo_rows = rows;
    Util.cargo_by_id = by_id;
    return rows;
}

Util.Label <- function(cargo) {
    Util.Cargos();
    if (cargo in Util.cargo_by_id) return Util.cargo_by_id[cargo].label;
    return "C" + cargo;
}

Util.IsCargo <- function(cargo) {
    return cargo != null && cargo >= 0 && GSCargo.IsValidCargo(cargo);
}

// --- companies --------------------------------------------------------------------
Util.CompanyIds <- function() {
    local ids = [];
    for (local c = GSCompany.COMPANY_FIRST; c < GSCompany.COMPANY_LAST; c++) {
        if (GSCompany.ResolveCompanyID(c) != GSCompany.COMPANY_INVALID) ids.append(c);
    }
    return ids;
}

Util.CompanyExists <- function(c) {
    return c != null && c >= 0 && GSCompany.ResolveCompanyID(c) != GSCompany.COMPANY_INVALID;
}

Util.RequireSeat <- function(ctx) {
    if (ctx.company < 0) throw "this command acts as a company; it needs a seat";
    if (!Util.CompanyExists(ctx.company)) throw "company " + ctx.company + " does not exist yet";
    return ctx.company;
}

Util.OwnVehicle <- function(ctx, vehicle) {
    if (!GSVehicle.IsValidVehicle(vehicle)) throw "vehicle " + vehicle + " does not exist";
    if (GSVehicle.GetOwner(vehicle) != ctx.company) throw "vehicle " + vehicle + " is not yours";
    return vehicle;
}

// --- mutation envelope ------------------------------------------------------------
// Every mutator runs as the acting company with accounting; `validate` wraps it in
// GSTestMode (cost + possibility, no execution); `async` queues it (GSAsyncMode) so
// hundreds of construction commands fit in one tick; the executor yields a tick
// before any later item that must observe the queued world.
// NOTE: this Squirrel build captures NO outer variables in nested functions, so every
// value the body needs travels in `a` and the body is `function(ctx, a) {...}`.
Util.Mutate <- function(ctx, async, a, fn) {
    local company = Util.RequireSeat(ctx);
    local mode = GSCompanyMode(company);
    local acc = GSAccounting();
    local test = null;
    local queue = null;
    if (ctx.validate) {
        test = GSTestMode();
    } else if (async) {
        queue = GSAsyncMode(true);
        ctx.executor.queued = true;
    }
    local ok = false;
    local error = null;
    local extra = null;
    try {
        extra = fn(ctx, a);
        ok = extra != null && (!("ok" in extra) || extra.ok);
    } catch (e) {
        ok = false;
        error = "" + e;
    }
    local cost = acc.GetCosts();
    if (!ok && error == null) {
        local s = GSError.GetLastErrorString();
        error = s == null ? "command failed" : s;
    }
    local r = { ok = ok, possible = ok, executed = ok && !ctx.validate, queued = (ok && !ctx.validate && async), cost = cost };
    if (!ok) r.error <- error;
    if (extra != null && typeof extra == "table") {
        foreach (k, v in extra) if (k != "ok") r[k] <- v;
    }
    return r;
}

Util.Slope <- function(name) {
    switch (name) {
        case "W": return GSTile.SLOPE_W;   case "S": return GSTile.SLOPE_S;
        case "E": return GSTile.SLOPE_E;   case "N": return GSTile.SLOPE_N;
        case "NW": return GSTile.SLOPE_NW; case "SW": return GSTile.SLOPE_SW;
        case "SE": return GSTile.SLOPE_SE; case "NE": return GSTile.SLOPE_NE;
        case "EW": return GSTile.SLOPE_EW; case "NS": return GSTile.SLOPE_NS;
    }
    throw "unknown slope '" + name + "'";
}

Util.RailTrack <- function(name) {
    switch (name) {
        case "NE_SW": return GSRail.RAILTRACK_NE_SW; case "NW_SE": return GSRail.RAILTRACK_NW_SE;
        case "NW_NE": return GSRail.RAILTRACK_NW_NE; case "SW_SE": return GSRail.RAILTRACK_SW_SE;
        case "NW_SW": return GSRail.RAILTRACK_NW_SW; case "NE_SE": return GSRail.RAILTRACK_NE_SE;
    }
    throw "unknown rail track '" + name + "'";
}

Util.RailTrackName <- function(bits) {
    local names = [];
    if (bits & GSRail.RAILTRACK_NE_SW) names.append("NE_SW");
    if (bits & GSRail.RAILTRACK_NW_SE) names.append("NW_SE");
    if (bits & GSRail.RAILTRACK_NW_NE) names.append("NW_NE");
    if (bits & GSRail.RAILTRACK_SW_SE) names.append("SW_SE");
    if (bits & GSRail.RAILTRACK_NW_SW) names.append("NW_SW");
    if (bits & GSRail.RAILTRACK_NE_SE) names.append("NE_SE");
    return names;
}

Util.SignalType <- function(name) {
    switch (name) {
        case "normal": return GSRail.SIGNALTYPE_NORMAL; case "entry": return GSRail.SIGNALTYPE_ENTRY;
        case "exit": return GSRail.SIGNALTYPE_EXIT;     case "combo": return GSRail.SIGNALTYPE_COMBO;
        case "pbs": return GSRail.SIGNALTYPE_PBS;       case "pbs_oneway": return GSRail.SIGNALTYPE_PBS_ONEWAY;
        case "normal_twoway": return GSRail.SIGNALTYPE_NORMAL_TWOWAY;
        case "entry_twoway": return GSRail.SIGNALTYPE_ENTRY_TWOWAY;
        case "exit_twoway": return GSRail.SIGNALTYPE_EXIT_TWOWAY;
        case "combo_twoway": return GSRail.SIGNALTYPE_COMBO_TWOWAY;
    }
    throw "unknown signal type '" + name + "'";
}

Util.VehicleTypeName <- function(vt) {
    if (vt == GSVehicle.VT_RAIL) return "rail";
    if (vt == GSVehicle.VT_ROAD) return "road";
    if (vt == GSVehicle.VT_WATER) return "water";
    if (vt == GSVehicle.VT_AIR) return "air";
    return "unknown";
}

Util.VehicleStateName <- function(state) {
    switch (state) {
        case GSVehicle.VS_RUNNING: return "running";
        case GSVehicle.VS_STOPPED: return "stopped";
        case GSVehicle.VS_IN_DEPOT: return "in_depot";
        case GSVehicle.VS_AT_STATION: return "at_station";
        case GSVehicle.VS_BROKEN: return "broken";
        case GSVehicle.VS_CRASHED: return "crashed";
    }
    return "invalid";
}

Util.StationTypes <- function(station) {
    local types = [];
    if (GSStation.HasStationType(station, GSStation.STATION_TRAIN)) types.append("rail");
    if (GSStation.HasStationType(station, GSStation.STATION_BUS_STOP)) types.append("bus");
    if (GSStation.HasStationType(station, GSStation.STATION_TRUCK_STOP)) types.append("truck");
    if (GSStation.HasStationType(station, GSStation.STATION_AIRPORT)) types.append("airport");
    if (GSStation.HasStationType(station, GSStation.STATION_DOCK)) types.append("dock");
    return types;
}

Util.Name <- function(s) { return s == null ? "" : s; }

Util.StartsWith <- function(s, prefix) {
    return s != null && s.len() >= prefix.len() && s.slice(0, prefix.len()) == prefix;
}

Util.Split <- function(value, sep) {
    local parts = [];
    local start = 0;
    for (local i = 0; i < value.len(); i++) {
        if (value.slice(i, i + 1) != sep) continue;
        parts.append(value.slice(start, i));
        start = i + 1;
    }
    parts.append(value.slice(start));
    return parts;
}
