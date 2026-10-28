// inspect.cargos / inspect.bridges / inspect.subsidies

Registry.Add("inspect.cargos", function(ctx) {
    return { cargos = Util.Cargos() };
});

Registry.Add("inspect.bridges", function(ctx) {
    local length = Util.Int(ctx.args, "length");
    if (length < 1 || length > 64) throw "length must be within 1..64";
    local rows = [];
    foreach (bridge, _ in GSBridgeList_Length(length)) {
        rows.append({
            id = bridge, name = Util.Name(GSBridge.GetName(bridge, GSVehicle.VT_RAIL)),
            max_speed = GSBridge.GetMaxSpeed(bridge), price = GSBridge.GetPrice(bridge, length),
            min_length = GSBridge.GetMinLength(bridge), max_length = GSBridge.GetMaxLength(bridge),
        });
    }
    return { length = length, bridges = rows };
});

Registry.Add("inspect.subsidies", function(ctx) {
    local rows = [];
    foreach (subsidy, _ in GSSubsidyList()) {
        local awarded = GSSubsidy.GetAwardedTo(subsidy);
        rows.append({
            id = subsidy, cargo = GSSubsidy.GetCargoType(subsidy), label = Util.Label(GSSubsidy.GetCargoType(subsidy)),
            awarded = GSSubsidy.IsAwarded(subsidy), awarded_to = awarded == GSCompany.COMPANY_INVALID ? -1 : awarded,
            source_type = GSSubsidy.GetSourceType(subsidy), source = GSSubsidy.GetSourceIndex(subsidy),
            destination_type = GSSubsidy.GetDestinationType(subsidy), destination = GSSubsidy.GetDestinationIndex(subsidy),
            expires = GSSubsidy.GetExpireDate(subsidy),
        });
    }
    return { subsidies = rows };
});
