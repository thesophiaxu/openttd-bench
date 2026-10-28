// Injected ONLY into the isolated smoke-test payload; never shipped to agents.
function ledger_check(condition, label) { if (!condition) throw "ledger test: " + label; }

Registry.Add("sys.test_ledger_pure", function(ctx) {
    local t = ProfitLedger.New(7800, 0, 0);
    ledger_check(ProfitLedger.Update(t, 7800, 100, -25, []).operating_profit == 75, "signed expenses");
    ledger_check(ProfitLedger.Update(t, 7800, 100, -25, []).operating_profit == 75, "repeat is idempotent");
    ledger_check(ProfitLedger.Update(t, 7800, 120, -200, []).operating_profit == -80, "losses retained");
    // A sold vehicle does not participate: only the engine's company ledger is read.
    ledger_check(ProfitLedger.Update(t, 7801, 20, -5, [{income=120, expenses=-200}]).operating_profit == -65, "quarter rollover");
    ledger_check(ProfitLedger.Update(t, 7801, 20, -5, []).operating_profit == -65, "rollover banked once");
    ledger_check(ProfitLedger.Update(t, 7804, 10, -2,
        [{income=30, expenses=-3}, {income=40, expenses=-4}, {income=20, expenses=-5}]).operating_profit == 6, "skipped quarters and year boundary");
    local baseline = ProfitLedger.New(7800, 500, -100);
    ledger_check(ProfitLedger.Update(baseline,7800,550,-110,[]).operating_profit == 40, "episode baseline");
    ledger_check(ProfitLedger.Update(baseline,7801,10,-1,[{income=550,expenses=-110}]).operating_profit == 49, "baseline across rollover");
    local history=[];
    for(local i=0;i<24;i++) history.append({income=10,expenses=-2});
    ledger_check(ProfitLedger.Update(ProfitLedger.New(0,0,0),24,0,0,history).operating_profit == 192, "full retained history");
    local invalid=ProfitLedger.New(0,0,0);
    ledger_check(!ProfitLedger.Update(invalid,25,0,0,history).valid, "history gap rejected");
    ledger_check(!ProfitLedger.Update(invalid,0,0,0,[]).valid, "failure sticky");
    ledger_check(!ProfitLedger.Update(ProfitLedger.New(5,0,0),4,0,0,[]).valid, "backwards date rejected");
    ledger_check(!ProfitLedger.Update(ProfitLedger.New(0,0,0),2,0,0,[{income=0,expenses=0}]).valid, "missing quarter rejected");
    ledger_check(ProfitLedger.Update(ProfitLedger.New(0,0,0),0,0,-1,[]).operating_profit == -1, "negative one is valid expense");
    ledger_check(ProfitLedger.Quarter(GSDate.GetDate(1950,3,31)) == 7800, "March quarter");
    ledger_check(ProfitLedger.Quarter(GSDate.GetDate(1950,4,1)) == 7801, "April quarter");
    ledger_check(ProfitLedger.Quarter(GSDate.GetDate(1951,1,1)) == 7804, "January year rollover");
    local backwards=ProfitLedger.New(7800,0,0);
    ProfitLedger.CheckDate(backwards,GSDate.GetDate(1950,3,30));
    ProfitLedger.CheckDate(backwards,GSDate.GetDate(1950,1,2));
    ledger_check(!ProfitLedger.Update(backwards,7800,0,0,[]).valid, "backwards date within same quarter rejected");
    // Round-trip actual meter state, including baseline and completed quarters.
    local original=Meter.Save();
    local test_state={accounting_version=ProfitLedger.VERSION, config=Meter.config,
        funded={}, profit={}, states={}, last_date=-1, last_monitor_date=-1};
    test_state.profit[0] <- t;
    Meter.Load(test_state);
    local saved=Meter.Save();
    Meter.Load(saved);
    ledger_check(ProfitLedger.Update(Meter.profit[0],7804,10,-2,[]).operating_profit == 6, "meter save/load round trip");
    Meter.Load(original);
    return {passed=true, cases=19};
}, {admin=true});

Registry.Add("sys.test_ledger_finances", function(ctx) {
    local company=ctx.args.company;
    local before=Meter.CompanyState(company).operating_profit;
    // Exercise actual engine expense-category booking, not a Python replica.
    ledger_check(GSCompany.ChangeBankBalance(company,10000,GSCompany.EXPENSES_TRAIN_INC,GSMap.TILE_INVALID),"income transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,-3000,GSCompany.EXPENSES_TRAIN_RUN,GSMap.TILE_INVALID),"running transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,-200,GSCompany.EXPENSES_PROPERTY,GSMap.TILE_INVALID),"property transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,-100,GSCompany.EXPENSES_LOAN_INT,GSMap.TILE_INVALID),"interest transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,-5000,GSCompany.EXPENSES_CONSTRUCTION,GSMap.TILE_INVALID),"construction transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,-4000,GSCompany.EXPENSES_NEW_VEHICLES,GSMap.TILE_INVALID),"purchase transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,2000,GSCompany.EXPENSES_NEW_VEHICLES,GSMap.TILE_INVALID),"sale transaction");
    ledger_check(GSCompany.ChangeBankBalance(company,999,GSCompany.EXPENSES_OTHER,GSMap.TILE_INVALID),"other transaction");
    local after=Meter.CompanyState(company);
    ledger_check(after.operating_profit-before == 6700,"company category sum");
    return {passed=true,company=company,before=before,after=after.operating_profit,accounting=after.accounting};
}, {admin=true});

Registry.Add("sys.test_ledger_sell", function(ctx) {
    local company=ctx.args.company;
    local mode=GSCompanyMode(company);
    local before=Meter.CompanyState(company).operating_profit;
    local sold=[];
    foreach(vehicle,_ in GSVehicleList()) {
        if(!GSVehicle.IsPrimaryVehicle(vehicle) || !GSVehicle.IsStoppedInDepot(vehicle)) continue;
        local profit=GSVehicle.GetProfitThisYear(vehicle);
        local engine=GSVehicle.GetEngineType(vehicle);
        local tile=GSVehicle.GetLocation(vehicle);
        ledger_check(GSVehicle.SellVehicle(vehicle),"direct/native sale");
        sold.append({vehicle=vehicle,profit_this_year=profit,engine=engine,
            depot_x=GSMap.GetTileX(tile),depot_y=GSMap.GetTileY(tile)});
    }
    local after=Meter.CompanyState(company).operating_profit;
    ledger_check(before == after,"selling vehicles cannot erase company ledger");
    return {passed=true,company=company,before=before,after=after,sold=sold};
}, {admin=true});
