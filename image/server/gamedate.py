"""OpenTTD calendar dates: days since 0000-01-01, proleptic Gregorian (year 0 is leap)."""

from __future__ import annotations

MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_before_year(year: int) -> int:
    leaps = (year + 3) // 4 - (year + 99) // 100 + (year + 399) // 400
    return year * 365 + leaps


def date_from_ymd(year: int, month: int, day: int) -> int:
    """month 0..11, day 1..31."""
    days = days_before_year(year)
    for m in range(month):
        days += MONTH_DAYS[m] + (1 if m == 1 and is_leap(year) else 0)
    return days + day - 1


def ymd_from_date(date: int) -> tuple[int, int, int]:
    year = max(0, int(date // 365.2425))
    while days_before_year(year + 1) <= date:
        year += 1
    while days_before_year(year) > date:
        year -= 1
    rest = date - days_before_year(year)
    for month in range(12):
        length = MONTH_DAYS[month] + (1 if month == 1 and is_leap(year) else 0)
        if rest < length:
            return year, month, rest + 1
        rest -= length
    return year, 11, 31


def add_months(date: int, months: int) -> int:
    year, month, day = ymd_from_date(date)
    index = year * 12 + month + months
    year, month = divmod(index, 12)
    length = MONTH_DAYS[month] + (1 if month == 1 and is_leap(year) else 0)
    return date_from_ymd(year, month, min(day, length))


__all__ = ["add_months", "date_from_ymd", "days_before_year", "is_leap", "ymd_from_date"]
