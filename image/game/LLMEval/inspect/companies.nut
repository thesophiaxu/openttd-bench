// inspect.companies / inspect.company: raw company measurements (policy-trimmed later).

Registry.Add("inspect.companies", function(ctx) {
    local rows = [];
    foreach (s in Meter.AllStates()) {
        s.is_me <- (s.id == ctx.company);
        rows.append(s);
    }
    return { companies = rows, caller = ctx.company };
});

Registry.Add("inspect.company", function(ctx) {
    local company = Util.Int(ctx.args, "company", -1);
    if (company < 0) company = ctx.company;
    if (company < 0) throw "no company selected";
    if (!Util.CompanyExists(company)) {
        if (company in Meter.states) {
            local s = Meter.states[company];
            s.bankrupt = true;
            s.is_me <- (company == ctx.company);
            return s;
        }
        throw "company " + company + " does not exist";
    }
    local s = Meter.CompanyState(company);
    s.is_me <- (company == ctx.company);
    return s;
});
