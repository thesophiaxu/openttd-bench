"""Human-player visibility (plan section 9.3): one table, applied per primitive per caller.

The GameScript runs deity and reports raw; this trims what a rival may see:
public: town/map/industry data, company existence/value/performance/finance summary,
station waiting cargo + ratings, exclusive-rights holders. Own only: exact cash/loan,
vehicle detail and orders, action receipts.
"""

from __future__ import annotations

from typing import Any

OWN_ONLY_COMPANY_FIELDS = ("cash", "loan", "vehicles_with_orders")


class NotVisible(PermissionError):
    pass


def _trim_company(row: dict[str, Any], caller: int) -> dict[str, Any]:
    if row.get("id") == caller:
        return row
    out = {k: v for k, v in row.items() if k not in OWN_ONLY_COMPANY_FIELDS}
    out["is_me"] = False
    return out


def _trim_station(row: dict[str, Any], caller: int) -> dict[str, Any]:
    if row.get("owner") == caller:
        return row
    out = dict(row)
    out.pop("vehicles", None)
    return out


def apply(command: str, caller: int, result: Any) -> Any:
    """Return the caller-visible view of `result` for `command`."""
    if not isinstance(result, dict) or caller < 0:
        return result  # admin sees raw
    if command == "inspect.companies":
        result["companies"] = [_trim_company(r, caller) for r in result.get("companies", [])]
        return result
    if command == "inspect.company":
        return _trim_company(result, caller)
    if command == "inspect.stations":
        result["stations"] = [_trim_station(r, caller) for r in result.get("stations", [])]
        return result
    if command == "inspect.station":
        return _trim_station(result, caller)
    if command in ("inspect.vehicle", "inspect.orders"):
        if result.get("owner") != caller:
            raise NotVisible("that vehicle belongs to another company; rival vehicle detail is not visible")
        return result
    if command == "inspect.vehicles":
        if result.get("company") != caller:
            raise NotVisible("rival vehicle lists are not visible")
        return result
    return result


PUBLIC_EVENT_TYPES = frozenset(
    {
        "company_new",
        "company_bankrupt",
        "company_in_trouble",
        "subsidy_offer",
        "subsidy_awarded",
        "subsidy_expired",
        "industry_open",
        "industry_close",
        "town_founded",
        "engine_available",
        "exclusive_transport_rights",
        "vehicle_crashed",
        "company_funded",
    }
)


def event_audience(event: dict[str, Any], seats: list[int]) -> list[int]:
    """Which seats' feeds an event lands in."""
    source = event.get("source")
    company = event.get("company")
    if source == "company_ai" or event.get("push") == "ticket":
        return [company] if company in seats else []
    if event.get("type") in PUBLIC_EVENT_TYPES:
        return list(seats)
    return [company] if company in seats else []


__all__ = ["NotVisible", "apply", "event_audience"]
