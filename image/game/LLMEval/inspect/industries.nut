// inspect.industries / inspect.industry / inspect.industry_types

function _industry_row(industry) {
    local tile = GSIndustry.GetLocation(industry);
    local type = GSIndustry.GetIndustryType(industry);
    local produces = [];
    foreach (cargo, _ in GSIndustryType.GetProducedCargo(type)) {
        produces.append({
            cargo = cargo, label = Util.Label(cargo),
            last_month_production = GSIndustry.GetLastMonthProduction(industry, cargo),
            last_month_transported = GSIndustry.GetLastMonthTransported(industry, cargo),
        });
    }
    local accepts = [];
    foreach (cargo, _ in GSIndustryType.GetAcceptedCargo(type)) {
        if (GSIndustry.IsCargoAccepted(industry, cargo) != GSIndustry.CAS_NOT_ACCEPTED) accepts.append(Util.Label(cargo));
    }
    return {
        id = industry, name = Util.Name(GSIndustry.GetName(industry)),
        type = type, type_name = Util.Name(GSIndustryType.GetName(type)),
        x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile),
        produces = produces, accepts = accepts,
        stations_around = GSIndustry.GetAmountOfStationsAround(industry),
    };
}

Registry.Add("inspect.industries", function(ctx) {
    local page = Util.Page(ctx.args, 50);
    local producing = Util.Int(ctx.args, "producing", -1);
    local accepting = Util.Int(ctx.args, "accepting", -1);
    local list;
    if (producing >= 0) {
        if (!Util.IsCargo(producing)) throw "unknown cargo id " + producing;
        list = GSIndustryList_CargoProducing(producing);
    } else if (accepting >= 0) {
        if (!Util.IsCargo(accepting)) throw "unknown cargo id " + accepting;
        list = GSIndustryList_CargoAccepting(accepting);
    } else {
        list = GSIndustryList();
    }
    local r = Util.Paginate(list, page, _industry_row);
    return { industries = r.rows, total = r.total, next_offset = r.next_offset, offset = page.offset };
});

Registry.Add("inspect.industry", function(ctx) {
    local industry = Util.Int(ctx.args, "industry");
    if (!GSIndustry.IsValidIndustry(industry)) throw "industry " + industry + " does not exist";
    return _industry_row(industry);
});

Registry.Add("inspect.industry_types", function(ctx) {
    local types = [];
    foreach (type, _ in GSIndustryTypeList()) {
        local produces = [];
        foreach (cargo, _ in GSIndustryType.GetProducedCargo(type)) produces.append(Util.Label(cargo));
        local accepts = [];
        foreach (cargo, _ in GSIndustryType.GetAcceptedCargo(type)) accepts.append(Util.Label(cargo));
        types.append({ id = type, name = Util.Name(GSIndustryType.GetName(type)), produces = produces, accepts = accepts, raw = GSIndustryType.IsRawIndustry(type) });
    }
    return { types = types };
});
