// land.*: mode-neutral terraforming and clearing.

Registry.Add("land.demolish", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTile.DemolishTile(a.tile) }; });
}, { write = true, async = true });

Registry.Add("land.level", function(ctx) {
    local a = { from = Util.TileArg(ctx.args), to = Util.TileArg(ctx.args, "to") };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTile.LevelTiles(a.from, a.to) }; });
}, { write = true, async = true });

Registry.Add("land.raise", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), slope = Util.Slope(Util.Str(ctx.args, "slope")) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTile.RaiseTile(a.tile, a.slope) }; });
}, { write = true, async = true });

Registry.Add("land.lower", function(ctx) {
    local a = { tile = Util.TileArg(ctx.args), slope = Util.Slope(Util.Str(ctx.args, "slope")) };
    return Util.Mutate(ctx, true, a, function(ctx, a) { return { ok = GSTile.LowerTile(a.tile, a.slope) }; });
}, { write = true, async = true });
