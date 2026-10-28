// Company funding, per-company measurements and cargo-monitor polling. Measurements
// are pushed as data; nothing here knows which of them anyone cares about.

Meter <- {
    config = null,
    funded = {},
    profit = {},          // company -> persistent ProfitLedger state
    last_date = -1,
    last_monitor_date = -1,
    states = {},
};

Meter.Configure <- function(config) {
    Meter.config = config;
}

Meter.CompanyConfig <- function(company) {
    if (Meter.config == null || !("companies" in Meter.config)) return null;
    foreach (row in Meter.config.companies) {
        if (("slot" in row) && row.slot == company) return row;
    }
    return null;
}

// Apply the configured starting cash/loan the first time a company appears.
Meter.FundCompanies <- function() {
    if (Meter.config == null) return;
    foreach (company in Util.CompanyIds()) {
        if (company in Meter.funded) continue;
        local row = Meter.CompanyConfig(company);
        local cash = 2000000;
        local loan = 0;
        if (row != null) {
            if ("starting_cash" in row) cash = row.starting_cash;
            if ("starting_loan" in row) loan = row.starting_loan;
        }
        local current_loan = 0;
        {
            local mode = GSCompanyMode(company);
            current_loan = GSCompany.GetLoanAmount();
        }
        local current_cash = GSCompany.GetBankBalance(company);
        // Land at cash = starting_cash once the loan is set to starting_loan.
        local delta = (cash + current_loan - loan) - current_cash;
        if (delta != 0) {
            GSCompany.ChangeBankBalance(company, delta, GSCompany.EXPENSES_OTHER, GSMap.TILE_INVALID);
        }
        {
            local mode = GSCompanyMode(company);
            if (!GSCompany.SetLoanAmount(loan)) {
                GSLog.Warning("company " + company + ": could not set loan to " + loan + ": " + GSError.GetLastErrorString());
            }
            if (row != null && ("name" in row) && row.name != null) {
                if (!GSCompany.SetName(row.name)) GSLog.Warning("company " + company + ": could not set name");
            }
        }
        Meter.funded[company] <- true;
        Meter.profit[company] <- ProfitLedger.Start(company);
        local name = GSCompany.GetName(company);
        Util.Send({ push = "company_funded", company = company, cash = GSCompany.GetBankBalance(company), name = Util.Name(name) });
        Util.Info("funded company " + company + " (" + Util.Name(name) + ")");
    }
}

// Persist accounting and funding bookkeeping in saves. This does not persist the
// Python control-plane clock, cargo accumulator, or in-flight order tickets.
Meter.Save <- function() {
    return { accounting_version = ProfitLedger.VERSION, config = Meter.config,
             funded = Meter.funded, profit = Meter.profit, states = Meter.states,
             last_date = Meter.last_date, last_monitor_date = Meter.last_monitor_date };
}

Meter.Load <- function(data) {
    if (!("accounting_version" in data) || data.accounting_version != ProfitLedger.VERSION) {
        throw "unsupported saved accounting version";
    }
    Meter.config = data.config;
    Meter.funded = data.funded;
    Meter.profit = data.profit;
    Meter.states = data.states;
    Meter.last_date = data.last_date;
    Meter.last_monitor_date = data.last_monitor_date;
}

Meter.CompanyRemoved <- function(company) {
    // We cannot recover transactions between the last poll and bankruptcy. Do
    // not silently grade a stale snapshot, or reuse its ledger for a new company.
    if (company in Meter.profit) Meter.profit[company].error = "company removed; final ledger unavailable";
    if (company in Meter.states) {
        Meter.states[company].bankrupt = true;
        Meter.states[company].operating_profit = null;
        Meter.states[company].accounting.valid = false;
        Meter.states[company].accounting.error = "company removed; final ledger unavailable";
    }
}

Meter.CompanyState <- function(company) {
    local mode = GSCompanyMode(company);
    local now = GSDate.GetCurrentDate();
    local year = GSDate.GetYear(now);
    local vehicles = { rail = 0, road = 0, water = 0, air = 0 };
    if (!(company in Meter.profit)) throw "company ledger not initialized before measurement";
    local accounting = ProfitLedger.Read(company, Meter.profit[company]);
    local with_orders = 0;
    foreach (vehicle, _ in GSVehicleList()) {
        if (!GSVehicle.IsPrimaryVehicle(vehicle)) continue;
        local vt = GSVehicle.GetVehicleType(vehicle);
        local key = Util.VehicleTypeName(vt);
        if (key in vehicles) vehicles[key] = vehicles[key] + 1;
        if (GSOrder.GetOrderCount(vehicle) >= 2) with_orders++;
    }
    local stations = 0;
    foreach (station, _ in GSStationList(GSStation.STATION_ANY)) stations++;
    local quarterly = [];
    for (local q = 0; q < 3; q++) {
        quarterly.append({
            income = GSCompany.GetQuarterlyIncome(company, q),
            expenses = GSCompany.GetQuarterlyExpenses(company, q),
            delivered = GSCompany.GetQuarterlyCargoDelivered(company, q),
            performance = GSCompany.GetQuarterlyPerformanceRating(company, q),
            value = GSCompany.GetQuarterlyCompanyValue(company, q),
        });
    }
    local name = GSCompany.GetName(company);
    return {
        id = company,
        name = Util.Name(name),
        is_ai = company >= Meter.LlmSeats(),
        bankrupt = false,
        cash = GSCompany.GetBankBalance(company),
        loan = GSCompany.GetLoanAmount(),
        max_loan = GSCompany.GetMaxLoanAmount(),
        company_value = GSCompany.GetQuarterlyCompanyValue(company, GSCompany.CURRENT_QUARTER),
        performance = GSCompany.GetQuarterlyPerformanceRating(company, GSCompany.CURRENT_QUARTER),
        quarterly = quarterly,
        operating_profit = accounting.operating_profit,
        accounting = accounting,
        vehicles = vehicles,
        vehicles_with_orders = with_orders,
        stations = stations,
        rail_pieces = GSInfrastructure.GetInfrastructurePieceCount(company, GSInfrastructure.INFRASTRUCTURE_RAIL),
        road_pieces = GSInfrastructure.GetInfrastructurePieceCount(company, GSInfrastructure.INFRASTRUCTURE_ROAD),
        date = now,
        year = year,
    };
}

Meter.LlmSeats <- function() {
    if (Meter.config != null && ("llm_seats" in Meter.config)) return Meter.config.llm_seats;
    return 1;
}

Meter.AllStates <- function() {
    local rows = [];
    foreach (company in Util.CompanyIds()) {
        local s = Meter.CompanyState(company);
        Meter.states[company] <- s;
        rows.append(s);
    }
    // Companies that vanished (bankruptcy) keep their last measurement, flagged.
    foreach (company, s in Meter.states) {
        if (!Util.CompanyExists(company)) {
            Meter.CompanyRemoved(company);
            s.bankrupt = true;
            s.cash = 0;
            s.company_value = 0;
            rows.append(s);
        }
    }
    return rows;
}

Meter.rename_attempts <- {};

// The engine names an AI-held company ("X & Co.") when its script first runs, which is
// after funding; re-apply the configured seat name until it sticks (a few tries).
Meter.EnsureNames <- function() {
    foreach (company in Util.CompanyIds()) {
        local row = Meter.CompanyConfig(company);
        if (row == null || !("name" in row) || row.name == null) continue;
        local attempts = (company in Meter.rename_attempts) ? Meter.rename_attempts[company] : 0;
        if (attempts >= 5) continue;
        local current = GSCompany.GetName(company);
        if (current == row.name) continue;
        local mode = GSCompanyMode(company);
        GSCompany.SetName(row.name);
        Meter.rename_attempts[company] <- attempts + 1;
    }
}

// Per-day work: measurements + monitor polling. Free while paused (date is frozen).
Meter.Tick <- function() {
    if (Meter.config == null) return;
    local now = GSDate.GetCurrentDate();
    if (now == Meter.last_date) return;
    Meter.last_date = now;
    Meter.EnsureNames();
    Meter.PollMonitors(now);
    foreach (company in Util.CompanyIds()) {
        local s = Meter.CompanyState(company);
        Meter.states[company] <- s;
        Util.Send({ push = "company", company = company, state = s });
    }
}

Meter.PollMonitors <- function(now) {
    local plan = ("monitor" in Meter.config) ? Meter.config.monitor : null;
    if (plan == null || !("cargos" in plan) || plan.cargos.len() == 0) return;
    local cadence = ("cadence_days" in plan) ? plan.cadence_days : 1;
    if (Meter.last_monitor_date >= 0 && now - Meter.last_monitor_date < cadence) return;
    Meter.last_monitor_date = now;
    local kinds = ("kinds" in plan) ? plan.kinds : ["town", "industry"];
    local want_town = false;
    local want_industry = false;
    foreach (k in kinds) { if (k == "town") want_town = true; if (k == "industry") want_industry = true; }
    local companies = Util.CompanyIds();
    foreach (cargo in plan.cargos) {
        if (!Util.IsCargo(cargo)) continue;
        if (want_town) {
            foreach (town, _ in GSTownList()) {
                foreach (company in companies) {
                    local amount = GSCargoMonitor.GetTownDeliveryAmount(company, cargo, town, true);
                    if (amount > 0) {
                        Util.Send({ push = "monitor", company = company, cargo = cargo, kind = "town", target = town, amount = amount, date = now });
                    }
                }
            }
        }
        if (want_industry) {
            foreach (industry, _ in GSIndustryList()) {
                foreach (company in companies) {
                    local amount = GSCargoMonitor.GetIndustryDeliveryAmount(company, cargo, industry, true);
                    if (amount > 0) {
                        Util.Send({ push = "monitor", company = company, cargo = cargo, kind = "industry", target = industry, amount = amount, date = now });
                    }
                }
            }
        }
    }
}
