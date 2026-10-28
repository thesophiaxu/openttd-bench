// Disposal-safe operating profit from OpenTTD's company economy ledger.
// CURRENT_QUARTER=0, completed quarters=1..24. Expenses are signed (normally
// negative). These counters include refits booked as running expenses, property
// expenses and interest, but NOT construction, vehicle purchases/sales or OTHER.
ProfitLedger <- { VERSION = "company-quarterly-v1", HISTORY = 24 };

ProfitLedger.New <- function(quarter, income, expenses) {
    return { quarter = quarter, completed_income = 0, completed_expenses = 0,
             baseline_income = income, baseline_expenses = expenses, last_date = null, error = null };
}

// Pure accumulator: history[0] is the newest completed quarter. Never integrate
// differences of live vehicle counters, and never bank a partial quarter twice.
ProfitLedger.Update <- function(state, quarter, income, expenses, history) {
    if (state.error == null) {
        local elapsed = quarter - state.quarter;
        if (elapsed < 0) state.error = "economy date moved backwards";
        else if (elapsed > ProfitLedger.HISTORY || elapsed > history.len()) {
            state.error = "missing completed company quarters";
        } else {
            for (local q = 0; q < elapsed; q++) {
                state.completed_income += history[q].income;
                state.completed_expenses += history[q].expenses;
            }
            state.quarter = quarter;
        }
    }
    local total_income = state.completed_income + income - state.baseline_income;
    local total_expenses = state.completed_expenses + expenses - state.baseline_expenses;
    return { version = ProfitLedger.VERSION, valid = state.error == null,
             error = state.error, quarter = quarter,
             income = total_income, expenses = total_expenses,
             operating_profit = state.error == null ? total_income + total_expenses : null };
}

ProfitLedger.Quarter <- function(date) {
    return GSDate.GetYear(date) * 4 + (GSDate.GetMonth(date) - 1) / 3;
}

ProfitLedger.CheckDate <- function(state, date) {
    if (state.last_date != null && date < state.last_date) state.error = "economy date moved backwards";
    state.last_date = date;
}

ProfitLedger.Start <- function(company) {
    local now = GSDate.GetCurrentDate();
    local state = ProfitLedger.New(ProfitLedger.Quarter(now),
        GSCompany.GetQuarterlyIncome(company, GSCompany.CURRENT_QUARTER),
        GSCompany.GetQuarterlyExpenses(company, GSCompany.CURRENT_QUARTER));
    ProfitLedger.CheckDate(state, now);
    return state;
}

ProfitLedger.Read <- function(company, state) {
    local now = GSDate.GetCurrentDate();
    ProfitLedger.CheckDate(state, now);
    local quarter = ProfitLedger.Quarter(now);
    local elapsed = quarter - state.quarter;
    local history = [];
    if (elapsed > 0 && elapsed <= ProfitLedger.HISTORY) {
        for (local q = 1; q <= elapsed; q++) {
            history.append({ income = GSCompany.GetQuarterlyIncome(company, q),
                             expenses = GSCompany.GetQuarterlyExpenses(company, q) });
        }
    }
    return ProfitLedger.Update(state, quarter,
        GSCompany.GetQuarterlyIncome(company, GSCompany.CURRENT_QUARTER),
        GSCompany.GetQuarterlyExpenses(company, GSCompany.CURRENT_QUARTER), history);
}
