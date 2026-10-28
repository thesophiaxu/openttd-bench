// inspect.tile / inspect.region / inspect.acceptance: raw tile lookups (deity view).

function _tile_signals(tile) {
    local result = [];
    local x = GSMap.GetTileX(tile);
    local y = GSMap.GetTileY(tile);
    local fronts = [GSMap.GetTileIndex(x - 1, y), GSMap.GetTileIndex(x + 1, y),
                    GSMap.GetTileIndex(x, y - 1), GSMap.GetTileIndex(x, y + 1)];
    foreach (front in fronts) {
        if (!GSMap.IsValidTile(front)) continue;
        local st = GSRail.GetSignalType(tile, front);
        if (st == GSRail.SIGNALTYPE_NONE) continue;
        result.append({ type = st, front_x = GSMap.GetTileX(front), front_y = GSMap.GetTileY(front) });
    }
    return result;
}

function _tile_info(tile) {
    local rail = GSRail.IsRailTile(tile);
    local rail_depot = GSRail.IsRailDepotTile(tile);
    local rail_station = GSRail.IsRailStationTile(tile);
    local road = GSRoad.IsRoadTile(tile);
    local road_depot = GSRoad.IsRoadDepotTile(tile);
    local road_station = GSRoad.IsRoadStationTile(tile);
    local bridge = GSBridge.IsBridgeTile(tile);
    local tunnel = GSTunnel.IsTunnelTile(tile);
    local industry = GSIndustry.GetIndustryID(tile);
    local station = GSTile.IsStationTile(tile) ? GSStation.GetStationID(tile) : -1;
    local owner = GSTile.GetOwner(tile);
    local r = {
        tile = tile, x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile),
        slope = GSTile.GetSlope(tile), flat = GSTile.GetSlope(tile) == GSTile.SLOPE_FLAT,
        min_height = GSTile.GetMinHeight(tile), max_height = GSTile.GetMaxHeight(tile),
        buildable = GSTile.IsBuildable(tile), water = GSTile.IsWaterTile(tile),
        coast = GSTile.IsCoastTile(tile), tree = GSTile.HasTreeOnTile(tile),
        house = GSTile.IsHouseTile(tile), farm = GSTile.IsFarmTile(tile),
        owner = owner == GSCompany.COMPANY_INVALID ? -1 : owner,
        rail = rail, rail_depot = rail_depot, rail_station = rail_station,
        road = road, road_depot = road_depot, road_station = road_station,
        drive_through = road_station && GSRoad.IsDriveThroughRoadStationTile(tile),
        station = station, level_crossing = GSRail.IsLevelCrossingTile(tile),
        bridge = bridge, tunnel = tunnel,
        industry = GSIndustry.IsValidIndustry(industry) ? industry : -1,
        closest_town = GSTile.GetClosestTown(tile),
    };
    if (rail) {
        r.rail_type <- GSRail.GetRailType(tile);
        r.rail_tracks <- Util.RailTrackName(GSRail.GetRailTracks(tile));
        if (!rail_station && !rail_depot) r.signals <- _tile_signals(tile);
    }
    if (rail_station) r.station_direction <- GSRail.GetRailStationDirection(tile) == GSRail.RAILTRACK_NE_SW ? "NE_SW" : "NW_SE";
    if (rail_depot) r.depot_front <- Util.XY(GSRail.GetRailDepotFrontTile(tile));
    if (road_depot) r.depot_front <- Util.XY(GSRoad.GetRoadDepotFrontTile(tile));
    if (bridge) r.bridge_end <- Util.XY(GSBridge.GetOtherBridgeEnd(tile));
    if (tunnel) r.tunnel_end <- Util.XY(GSTunnel.GetOtherTunnelEnd(tile));
    if (r.industry >= 0) r.industry_type <- GSIndustry.GetIndustryType(industry);
    return r;
}

Registry.Add("inspect.tile", function(ctx) {
    return _tile_info(Util.TileArg(ctx.args));
});

REGION_LEGEND <- ".=clear_flat ^=clear_slope c=occupied_flat x=occupied_slope t=trees h=house W=water I=industry S=station R=rail G=signalled_rail D=rail_depot r=road d=road_depot L=level_crossing B=bridge T=tunnel";

Registry.Add("inspect.region", function(ctx) {
    local x0 = Util.Int(ctx.args, "x");
    local y0 = Util.Int(ctx.args, "y");
    local width = Util.Int(ctx.args, "width", 16);
    local height = Util.Int(ctx.args, "height", 16);
    if (width < 1 || height < 1 || width > 32 || height > 32) throw "width and height must be within 1..32";
    Util.Tile(x0, y0);
    Util.Tile(x0 + width - 1, y0 + height - 1);
    local rows = [];
    for (local dy = 0; dy < height; dy++) {
        local row = "";
        for (local dx = 0; dx < width; dx++) {
            local tile = GSMap.GetTileIndex(x0 + dx, y0 + dy);
            local sym;
            if (GSBridge.IsBridgeTile(tile)) sym = "B";
            else if (GSTunnel.IsTunnelTile(tile)) sym = "T";
            else if (GSRail.IsLevelCrossingTile(tile)) sym = "L";
            else if (GSRail.IsRailDepotTile(tile)) sym = "D";
            else if (GSRoad.IsRoadDepotTile(tile)) sym = "d";
            else if (GSTile.IsStationTile(tile)) sym = "S";
            else if (GSRail.IsRailTile(tile)) sym = _tile_signals(tile).len() > 0 ? "G" : "R";
            else if (GSRoad.IsRoadTile(tile)) sym = "r";
            else if (GSTile.IsWaterTile(tile)) sym = "W";
            else if (GSIndustry.IsValidIndustry(GSIndustry.GetIndustryID(tile))) sym = "I";
            else if (GSTile.IsHouseTile(tile)) sym = "h";
            else if (GSTile.HasTreeOnTile(tile)) sym = "t";
            else if (GSTile.IsBuildable(tile)) sym = GSTile.GetSlope(tile) == GSTile.SLOPE_FLAT ? "." : "^";
            else sym = GSTile.GetSlope(tile) == GSTile.SLOPE_FLAT ? "c" : "x";
            row += sym;
        }
        rows.append(row);
    }
    return { x = x0, y = y0, width = width, height = height, rows = rows, legend = REGION_LEGEND };
});

Registry.Add("inspect.acceptance", function(ctx) {
    local tile = Util.TileArg(ctx.args);
    local width = Util.Int(ctx.args, "width", 1);
    local height = Util.Int(ctx.args, "height", 1);
    local radius = Util.Int(ctx.args, "radius", 3);
    if (width < 1 || height < 1 || width > 16 || height > 16 || radius < 0 || radius > 8) throw "footprint must be within 1..16 and radius within 0..8";
    local accepts = {};
    local produces = {};
    foreach (row in Util.Cargos()) {
        local a = GSTile.GetCargoAcceptance(tile, row.id, width, height, radius);
        local p = GSTile.GetCargoProduction(tile, row.id, width, height, radius);
        if (a > 0) accepts[row.label] <- a;
        if (p > 0) produces[row.label] <- p;
    }
    return { x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile), width = width, height = height, radius = radius, accepts = accepts, produces = produces, accepted_threshold = 8 };
});
