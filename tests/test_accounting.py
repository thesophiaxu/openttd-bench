import copy
from pathlib import Path

import pytest
from openttd_bench.accounting import ACCOUNTING_VERSION, validate_accounting


def snapshot():
    return {"accounting_snapshot":{"fresh":True},"companies":{"0":{"operating_profit":-15,"accounting":{
        "version":ACCOUNTING_VERSION,"valid":True,"income":10,"expenses":-25}}}}


def test_signed_expenses_and_losses():
    validate_accounting(snapshot())


@pytest.mark.parametrize("mutation", ["missing", "version", "invalid", "profit", "total", "bankrupt"])
def test_reject_incomplete_measurements(mutation):
    s=copy.deepcopy(snapshot());c=s["companies"]["0"]
    if mutation=="missing": del c["accounting"]
    elif mutation=="version": c["accounting"]["version"]="legacy-vehicle-counters"
    elif mutation=="invalid": c["accounting"]["valid"]=False
    elif mutation=="profit": c["operating_profit"]=35
    elif mutation=="total": del c["accounting"]["income"]
    elif mutation=="bankrupt": c["bankrupt"]=True
    with pytest.raises(ValueError):validate_accounting(s)


def test_empty_snapshot_rejected():
    with pytest.raises(ValueError):validate_accounting({"companies":{}})


def test_stale_snapshot_and_partial_roster_rejected():
    s=snapshot()
    with pytest.raises(ValueError):validate_accounting(s, range(2))
    validate_accounting(s, range(1))
    s["accounting_snapshot"]["fresh"]=False
    with pytest.raises(ValueError):validate_accounting(s)


def test_meter_uses_company_ledger_not_vehicle_profit():
    root=Path(__file__).resolve().parents[1]
    meter=(root/"image/game/LLMEval/meter.nut").read_text()
    assert "GetProfitThisYear" not in meter and "GetProfitLastYear" not in meter
    assert "ProfitLedger.Read" in meter and "ProfitLedger.Start" in meter
    assert "accounting = accounting" in meter
    main=(root/"image/game/LLMEval/main.nut").read_text()
    assert "Meter.Save()" in main and "Meter.Load(data.meter)" in main
