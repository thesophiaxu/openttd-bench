// Asynchronous order relay. GSOrder's write calls are AI-only in every engine version,
// so each order edit is a mailbox sign the seat's LLMCompany AI applies. A ticket is a
// monotonically increasing id mapped to the sign; the AI renames the sign as the ack;
// the per-tick sweep resolves it. Signs are company-owned, so every sign lookup runs
// under that company's mode (a deity sees only deity signs as "valid").

Orders <- { pending = {}, timeout_ticks = 3000, seq = 0 };

Orders.Enqueue <- function(ctx, op, vehicle, fields) {
    local company = Util.RequireSeat(ctx);
    Util.OwnVehicle(ctx, vehicle);
    local name = "LLM:" + op + ":" + vehicle;
    foreach (f in fields) name += ":" + f;
    if (name.len() > 31) throw "order mailbox item too long";
    local tile = GSVehicle.GetLocation(vehicle);
    local mode = GSCompanyMode(company);
    local before = GSOrder.GetOrderCount(vehicle);
    local sign = GSSign.BuildSign(tile, name);
    if (sign == null || sign == false || !GSSign.IsValidSign(sign)) {
        throw Util.Err("could not enqueue order (sign)");
    }
    Orders.seq++;
    local ticket = Orders.seq;
    Orders.pending[ticket] <- { sign = sign, company = company, op = op, vehicle = vehicle, before = before, tick = GSController.GetTick() };
    GSLog.Warning("ticket " + ticket + " enqueued as sign " + sign + ": " + name);
    return { ok = true, ticket = ticket, sign = sign, vehicle = vehicle, op = op };
}

Orders.Resolve <- function(ticket, item, ok, error) {
    delete Orders.pending[ticket];
    local after = -1;
    {
        local mode = GSCompanyMode(item.company);
        if (GSSign.IsValidSign(item.sign)) GSSign.RemoveSign(item.sign);
        if (GSVehicle.IsValidVehicle(item.vehicle)) after = GSOrder.GetOrderCount(item.vehicle);
    }
    local msg = { push = "ticket", ticket = ticket, sign = item.sign, company = item.company, ok = ok, vehicle = item.vehicle, op = item.op, orders_before = item.before, orders_after = after, ticks = GSController.GetTick() - item.tick };
    if (error != null) msg.error <- error;
    GSLog.Warning("ticket " + ticket + " resolved ok=" + ok + " after " + msg.ticks + " ticks" + (error == null ? "" : (": " + error)));
    Util.Send(msg);
}

Orders.Sweep <- function() {
    if (Orders.pending.len() == 0) return;
    local now = GSController.GetTick();
    local done = [];
    foreach (ticket, item in Orders.pending) {
        local mode = GSCompanyMode(item.company);
        if (!GSSign.IsValidSign(item.sign)) {
            done.append([ticket, item, false, "mailbox sign vanished before the company AI applied it"]);
            continue;
        }
        local name = GSSign.GetName(item.sign);
        if (Util.StartsWith(name, "LLMR:")) {
            local parts = Util.Split(name, ":");
            local ok = parts.len() >= 2 && parts[1] == "1";
            local err = null;
            if (!ok) err = parts.len() >= 3 ? ("company AI could not apply the order: " + parts[2]) : "company AI could not apply the order";
            done.append([ticket, item, ok, err]);
        } else if (now - item.tick > Orders.timeout_ticks) {
            done.append([ticket, item, false, "timed out waiting for the company AI (is the clock running?)"]);
        }
    }
    foreach (d in done) Orders.Resolve(d[0], d[1], d[2], d[3]);
}

// AI-only native events are relayed the same way: the AI drops an `LLME:` sign.
Orders.SweepEvents <- function() {
    local seats = Meter.LlmSeats();
    for (local company = 0; company < seats; company++) {
        if (!Util.CompanyExists(company)) continue;
        local mode = GSCompanyMode(company);
        local scanned = 0;
        foreach (sign, _ in GSSignList()) {
            if (scanned++ > 64) break;
            local name = GSSign.GetName(sign);
            if (!Util.StartsWith(name, "LLME:")) continue;
            local parts = Util.Split(name, ":");
            local msg = { push = "event", type = parts.len() > 1 ? parts[1] : "native", company = company, source = "company_ai" };
            if (parts.len() > 2) msg.vehicle <- parts[2];
            if (parts.len() > 3) msg.detail <- parts[3];
            Util.Send(msg);
            GSSign.RemoveSign(sign);
        }
    }
}

function _order_flags(ctx) { return Util.Int(ctx.args, "flags", 0); }

Registry.Add("orders.append", function(ctx) {
    local vehicle = Util.Int(ctx.args, "vehicle");
    local station = Util.Int(ctx.args, "station");
    if (!GSStation.IsValidStation(station)) throw "station " + station + " does not exist";
    return Orders.Enqueue(ctx, "A", vehicle, [GSStation.GetLocation(station), _order_flags(ctx)]);
}, { write = true });

Registry.Add("orders.append_depot", function(ctx) {
    local vehicle = Util.Int(ctx.args, "vehicle");
    local tile = Util.TileArg(ctx.args);
    if (!GSRail.IsRailDepotTile(tile) && !GSRoad.IsRoadDepotTile(tile)) throw "tile is not a depot";
    return Orders.Enqueue(ctx, "D", vehicle, [tile, _order_flags(ctx)]);
}, { write = true });

Registry.Add("orders.insert", function(ctx) {
    local vehicle = Util.Int(ctx.args, "vehicle");
    local station = Util.Int(ctx.args, "station");
    if (!GSStation.IsValidStation(station)) throw "station " + station + " does not exist";
    return Orders.Enqueue(ctx, "I", vehicle, [Util.Int(ctx.args, "position"), GSStation.GetLocation(station), _order_flags(ctx)]);
}, { write = true });

Registry.Add("orders.remove", function(ctx) {
    return Orders.Enqueue(ctx, "R", Util.Int(ctx.args, "vehicle"), [Util.Int(ctx.args, "position")]);
}, { write = true });

Registry.Add("orders.set_flags", function(ctx) {
    return Orders.Enqueue(ctx, "F", Util.Int(ctx.args, "vehicle"), [Util.Int(ctx.args, "position"), _order_flags(ctx)]);
}, { write = true });

Registry.Add("orders.skip_to", function(ctx) {
    return Orders.Enqueue(ctx, "K", Util.Int(ctx.args, "vehicle"), [Util.Int(ctx.args, "position")]);
}, { write = true });

Registry.Add("orders.copy", function(ctx) {
    local from = Util.Int(ctx.args, "from_vehicle");
    Util.OwnVehicle(ctx, from);
    return Orders.Enqueue(ctx, "C", Util.Int(ctx.args, "vehicle"), [from]);
}, { write = true });

Registry.Add("orders.share", function(ctx) {
    local from = Util.Int(ctx.args, "from_vehicle");
    Util.OwnVehicle(ctx, from);
    return Orders.Enqueue(ctx, "H", Util.Int(ctx.args, "vehicle"), [from]);
}, { write = true });

Registry.Add("orders.unshare", function(ctx) {
    return Orders.Enqueue(ctx, "U", Util.Int(ctx.args, "vehicle"), []);
}, { write = true });
