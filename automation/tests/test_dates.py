from datetime import date

import pytest

from automation.fleet_sync.dates import (
    parse_period_end_exclusive_boundary,
    parse_period_end_inclusive,
    parse_period_start,
)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Aug-26", date(2026, 8, 1)),
        ("Aug 26", date(2026, 8, 1)),
        ("Q3-29", date(2029, 7, 1)),
        ("Q1 2027", date(2027, 1, 1)),
    ],
)
def test_period_start(token: str, expected: date) -> None:
    assert parse_period_start(token) == expected


def test_inclusive_month_end() -> None:
    assert parse_period_end_inclusive("Feb-28") == date(2028, 2, 29)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Feb-27", date(2027, 1, 31)),
        ("Q3-29", date(2029, 6, 30)),
    ],
)
def test_noble_exclusive_boundary(token: str, expected: date) -> None:
    assert parse_period_end_exclusive_boundary(token) == expected
