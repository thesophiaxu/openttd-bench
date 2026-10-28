// Admin-only lifecycle commands (the control plane and init_game are the callers).

Registry.Add("sys.status", function(ctx) {
    local now = GSDate.GetCurrentDate();
    return {
        date = now,
        year = GSDate.GetYear(now),
        month = GSDate.GetMonth(now),
        day = GSDate.GetDayOfMonth(now),
        paused = GSGame.IsPaused(),
        tick = GSController.GetTick(),
        companies = Util.CompanyIds(),
        map_x = GSMap.GetMapSizeX(),
        map_y = GSMap.GetMapSizeY(),
        ops_left = GSController.GetOpsTillSuspend(),
        ops_low_water = Executor.ops_low_water,
        configured = Meter.config != null,
    };
}, { admin = true });

Registry.Add("sys.registry", function(ctx) {
    return { commands = Registry.names };
}, { admin = true });

Registry.Add("sys.company_states", function(ctx) {
    return { companies = Meter.AllStates(), date = GSDate.GetCurrentDate() };
}, { admin = true });

Registry.Add("sys.found_town", function(ctx) {
    local tile = Util.TileArg(ctx.args);
    local size_name = Util.Str(ctx.args, "size", "medium");
    local size = GSTown.TOWN_SIZE_MEDIUM;
    if (size_name == "small") size = GSTown.TOWN_SIZE_SMALL;
    if (size_name == "large") size = GSTown.TOWN_SIZE_LARGE;
    local city = Util.Bool(ctx.args, "city", false);
    local name = Util.Has(ctx.args, "name") ? Util.Str(ctx.args, "name") : null;
    local before = GSTown.GetTownCount();
    local ok = GSTown.FoundTown(tile, size, city, GSTown.ROAD_LAYOUT_BETTER_ROADS, name);
    if (!ok) return { ok = false, error = Util.Err("FoundTown failed") };
    // The founded town is the one closest to the tile.
    local best = -1;
    local best_d = 1 << 30;
    foreach (town, _ in GSTownList()) {
        local d = GSTown.GetDistanceManhattanToTile(town, tile);
        if (d < best_d) { best_d = d; best = town; }
    }
    return { ok = true, town = best, towns = GSTown.GetTownCount(), before = before, distance = best_d };
}, { admin = true, write = true });

// Diagnostic: how this Squirrel build captures outer variables (closure rules).
Registry.Add("sys.probe", function(ctx) {
    local a = 5;
    local r = {};
    local f1 = function() { return a + 1; };
    try { r.local_capture <- f1(); } catch (e) { r.local_capture <- "" + e; }
    local f2 = function() { return ctx.company; };
    try { r.param_capture <- f2(); } catch (e) { r.param_capture <- "" + e; }
    local g = function(c) { return c.company; };
    try { r.param_passed <- g(ctx); } catch (e) { r.param_passed <- "" + e; }
    local t = { v = 7 };
    local f3 = function() { return t.v; };
    try { r.table_capture <- f3(); } catch (e) { r.table_capture <- "" + e; }
    local f4 = function() { return Util.Mutate_probe_helper(a); };
    try { r.nested_call <- f4(); } catch (e) { r.nested_call <- "" + e; }
    return r;
}, { admin = true });

Util.Mutate_probe_helper <- function(x) { return x * 2; }

// Diagnostic: outstanding order tickets as the GameScript sees them.
Registry.Add("sys.tickets", function(ctx) {
    local rows = [];
    foreach (ticket, item in Orders.pending) {
        local mode = GSCompanyMode(item.company);
        local valid = GSSign.IsValidSign(item.sign);
        rows.append({ ticket = ticket, sign = item.sign, valid = valid, name = valid ? Util.Name(GSSign.GetName(item.sign)) : "", company = item.company, op = item.op, vehicle = item.vehicle, age = GSController.GetTick() - item.tick });
    }
    local signs = [];
    for (local company = 0; company < Meter.LlmSeats(); company++) {
        if (!Util.CompanyExists(company)) continue;
        local mode = GSCompanyMode(company);
        foreach (sign, _ in GSSignList()) signs.append({ id = sign, name = Util.Name(GSSign.GetName(sign)), owner = company });
    }
    return { pending = rows, signs = signs, tick = GSController.GetTick() };
}, { admin = true });
