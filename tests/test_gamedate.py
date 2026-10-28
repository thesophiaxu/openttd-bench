import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "image"))

from server import gamedate  # noqa: E402


def test_known_anchor_matches_the_engine():
    # The admin WELCOME packet reported start_date 712223 for 1950-01-01 (probed on 15.3).
    assert gamedate.date_from_ymd(1950, 0, 1) == 712223
    assert gamedate.ymd_from_date(712223) == (1950, 0, 1)


def test_round_trip_and_leap_years():
    for year in (1950, 1951, 1952, 2000, 2100):
        for month in range(12):
            date = gamedate.date_from_ymd(year, month, 1)
            assert gamedate.ymd_from_date(date) == (year, month, 1)
    assert gamedate.is_leap(1952) and not gamedate.is_leap(1950)
    assert gamedate.is_leap(2000) and not gamedate.is_leap(2100)
    assert gamedate.date_from_ymd(1952, 2, 1) - gamedate.date_from_ymd(1952, 1, 1) == 29


def test_add_months_clamps_the_day():
    jan31 = gamedate.date_from_ymd(1950, 0, 31)
    assert gamedate.ymd_from_date(gamedate.add_months(jan31, 1)) == (1950, 1, 28)
    assert gamedate.ymd_from_date(gamedate.add_months(jan31, 12)) == (1951, 0, 31)
