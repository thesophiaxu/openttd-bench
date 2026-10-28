import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "image"))

from server.policy import NotVisible, apply, event_audience  # noqa: E402


def test_rival_company_loses_cash_and_loan():
    row = {"id": 1, "name": "Rival", "cash": 5, "loan": 3, "company_value": 9, "performance": 1}
    view = apply("inspect.company", 0, dict(row))
    assert "cash" not in view and "loan" not in view and view["company_value"] == 9 and view["is_me"] is False
    own = apply("inspect.company", 1, dict(row))
    assert own["cash"] == 5


def test_rival_station_keeps_cargo_but_loses_vehicles():
    row = {"stations": [{"id": 3, "owner": 1, "cargo": {"PASS": {"waiting": 10, "rating": 60}}, "vehicles": [7]}]}
    view = apply("inspect.stations", 0, row)
    assert "vehicles" not in view["stations"][0] and view["stations"][0]["cargo"]["PASS"]["rating"] == 60


def test_rival_vehicles_are_not_visible():
    with pytest.raises(NotVisible):
        apply("inspect.vehicle", 0, {"id": 4, "owner": 1})
    with pytest.raises(NotVisible):
        apply("inspect.vehicles", 0, {"company": 1, "vehicles": []})
    assert apply("inspect.vehicle", 1, {"id": 4, "owner": 1})["id"] == 4


def test_event_audiences():
    seats = [0, 1, 2]
    assert event_audience({"type": "company_bankrupt", "company": 1, "source": "game"}, seats) == seats
    assert event_audience({"type": "vehicle_lost", "company": 1, "source": "company_ai"}, seats) == [1]
    assert event_audience({"push": "ticket", "type": "order_ticket", "company": 2}, seats) == [2]
    assert event_audience({"type": "receipt", "company": 0, "source": "receipt"}, seats) == [0]
