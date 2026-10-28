// The command registry and the batch executor. Namespaces self-register handlers by
// `namespace.command`; the executor runs one admin request (a list of items) and
// replies one packet per item plus a `done` packet.

Registry <- { handlers = {}, names = [] };

Registry.Add <- function(name, fn, opts = null) {
    local h = { fn = fn, write = false, admin = false, async = false };
    if (opts != null) foreach (k, v in opts) h[k] <- v;
    Registry.handlers[name] <- h;
    Registry.names.append(name);
}

Registry.Get <- function(name) {
    if (!(name in Registry.handlers)) throw "unknown command '" + name + "'";
    return Registry.handlers[name];
}

Executor <- { queued = false, ops_low_water = 250000 };

// Run one request: {id, seat, items:[{i, cmd, args, validate}]}.
Executor.Run <- function(request) {
    local id = ("id" in request) ? ("" + request.id) : "?";
    local seat = ("seat" in request) ? request.seat : -1;
    if (typeof seat != "integer") seat = -1;
    local items = ("items" in request && typeof request.items == "array") ? request.items : [];
    Executor.queued = false;
    local n = 0;
    foreach (item in items) {
        n++;
        local index = ("i" in item) ? item.i : (n - 1);
        local ok = true;
        local result = null;
        local error = null;
        local handler = null;
        local cmd = ("cmd" in item) ? ("" + item.cmd) : "";
        try {
            handler = Registry.Get(cmd);
            if (handler.admin && seat >= 0) throw "'" + cmd + "' is admin-only";
            local validate = ("validate" in item) && item.validate == true;
            local wants_fresh_world = !(handler.async && handler.write && !validate);
            if (Executor.queued && wants_fresh_world) {
                // Earlier construction in this batch is queued for the next frame; a
                // read, a validation or an id-returning build must see it applied.
                GSController.Sleep(1);
                Executor.queued = false;
            }
            local ctx = {
                company = seat,
                validate = validate,
                args = (("args" in item) && typeof item.args == "table") ? item.args : {},
                executor = Executor,
            };
            result = handler.fn(ctx);
        } catch (e) {
            ok = false;
            error = "" + e;
        }
        local reply = { id = id, i = index, ok = ok };
        if (result != null) reply.result <- result;
        if (error != null) reply.error <- error;
        Util.Send(reply);
    }
    if (Executor.queued) {
        // Let the last queued construction land before the client re-queries.
        GSController.Sleep(1);
        Executor.queued = false;
    }
    local ops = GSController.GetOpsTillSuspend();
    if (ops < Executor.ops_low_water) Executor.ops_low_water = ops;
    Util.Send({ id = id, done = true, n = n, ops_left = ops, tick = GSController.GetTick() });
}
