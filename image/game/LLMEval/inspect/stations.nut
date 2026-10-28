// inspect.stations / inspect.station. The GS is deity: it reports raw data with the
// owner; the control plane's visibility policy trims what rivals may see.

function _station_cargo(station) {
    local cargo = {};
    foreach (c in Util.Cargos()) {
        local waiting = GSStation.GetCargoWaiting(station, c.id);
        local has_rating = GSStation.HasCargoRating(station, c.id);
        if (waiting <= 0 && !has_rating) continue;
        local row = { waiting = waiting };
        if (has_rating) row.rating <- GSStation.GetCargoRating(station, c.id);
        cargo[c.label] <- row;
    }
    return cargo;
}

function _station_vehicles(station) {
    local ids = [];
    foreach (vehicle, _ in GSVehicleList_Station(station)) ids.append(vehicle);
    return ids;
}

function _station_row(station, detail) {
    local tile = GSStation.GetLocation(station);
    local owner = GSStation.GetOwner(station);
    local r = {
        id = station, name = Util.Name(GSStation.GetName(station)),
        x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile),
        owner = owner == GSCompany.COMPANY_INVALID ? -1 : owner,
        types = Util.StationTypes(station),
        cargo = _station_cargo(station),
        vehicles = _station_vehicles(station),
    };
    if (detail) {
        r.radius <- GSStation.GetStationCoverageRadius(station);
        r.nearest_town <- GSStation.GetNearestTown(station);
        r.construction_date <- GSStation.GetConstructionDate(station);
    }
    return r;
}

Registry.Add("inspect.stations", function(ctx) {
    local company = Util.Int(ctx.args, "company", -1);
    if (company < 0) company = ctx.company;
    local rows = [];
    local count = 0;
    foreach (station, _ in GSStationList(GSStation.STATION_ANY)) {
        if (company >= 0 && GSStation.GetOwner(station) != company) continue;
        if (count++ >= 200) break;
        rows.append(_station_row(station, false));
    }
    return { stations = rows, company = company };
});

Registry.Add("inspect.station", function(ctx) {
    local station = Util.Int(ctx.args, "station");
    if (!GSStation.IsValidStation(station)) throw "station " + station + " does not exist";
    return _station_row(station, true);
});
