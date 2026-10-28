"""Fail closed on missing/incomplete disposal-safe company accounting."""
from typing import Any

ACCOUNTING_VERSION = "company-quarterly-v1"


def validate_accounting(snapshot: dict[str, Any], expected_companies=None) -> None:
    if (snapshot.get("accounting_snapshot") or {}).get("fresh") is not True:
        raise ValueError("accounting snapshot is stale or freshness is unknown")
    companies = snapshot.get("companies") or {}
    if not companies:
        raise ValueError("snapshot has no company accounting")
    if expected_companies is not None and set(companies) != {str(c) for c in expected_companies}:
        raise ValueError("accounting snapshot does not cover the configured company roster")
    for cid, company in companies.items():
        ledger = company.get("accounting") or {}
        if ledger.get("version") != ACCOUNTING_VERSION or ledger.get("valid") is not True:
            raise ValueError(f"company {cid}: invalid accounting: {ledger}")
        if company.get("bankrupt"):
            raise ValueError(f"company {cid}: removed company has no authoritative terminal ledger")
        income, expenses = ledger.get("income"), ledger.get("expenses")
        if not isinstance(income, int) or not isinstance(expenses, int):
            raise ValueError(f"company {cid}: missing integer ledger totals")
        if company.get("operating_profit") != income + expenses:
            raise ValueError(f"company {cid}: profit disagrees with signed company ledger")
