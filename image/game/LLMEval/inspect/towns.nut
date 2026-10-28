// inspect.towns / inspect.town

function _town_row(town) {
    local tile = GSTown.GetLocation(town);
    local rights = GSTown.GetExclusiveRightsCompany(town);
    return {
        id = town, name = Util.Name(GSTown.GetName(town)),
        x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile),
        population = GSTown.GetPopulation(town), houses = GSTown.GetHouseCount(town),
        city = GSTown.IsCity(town), growth_rate = GSTown.GetGrowthRate(town),
        exclusive_rights_company = rights == GSCompany.COMPANY_INVALID ? -1 : rights,
        exclusive_rights_months = GSTown.GetExclusiveRightsDuration(town),
    };
}

Registry.Add("inspect.towns", function(ctx) {
    local page = Util.Page(ctx.args, 50);
    local r = Util.Paginate(GSTownList(), page, _town_row);
    return { towns = r.rows, total = r.total, next_offset = r.next_offset, offset = page.offset };
});

Registry.Add("inspect.town", function(ctx) {
    local town = Util.Int(ctx.args, "town");
    if (!GSTown.IsValidTown(town)) throw "town " + town + " does not exist";
    local row = _town_row(town);
    local production = {};
    foreach (c in Util.Cargos()) {
        local produced = GSTown.GetLastMonthProduction(town, c.id);
        if (produced <= 0) continue;
        production[c.label] <- {
            produced = produced,
            supplied = GSTown.GetLastMonthSupplied(town, c.id),
            transported_pct = GSTown.GetLastMonthTransportedPercentage(town, c.id),
        };
    }
    row.production <- production;
    if (ctx.company >= 0 && Util.CompanyExists(ctx.company)) {
        row.rating <- GSTown.GetRating(town, ctx.company);
        row.detailed_rating <- GSTown.GetDetailedRating(town, ctx.company);
    }
    row.allowed_noise <- GSTown.GetAllowedNoise(town);
    return row;
});
