from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta


MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _year(two_or_four: str) -> int:
    value = int(two_or_four)
    return 2000 + value if value < 100 else value


def parse_period_start(token: str) -> date:
    value = token.strip().replace("–", "-").replace("—", "-")
    quarter = re.fullmatch(r"Q([1-4])[- ](\d{2,4})", value, re.I)
    if quarter:
        q = int(quarter.group(1))
        return date(_year(quarter.group(2)), (q - 1) * 3 + 1, 1)

    month = re.fullmatch(r"([A-Za-z]{3,4})[- ](\d{2,4})", value)
    if not month or month.group(1).casefold() not in MONTHS:
        raise ValueError(f"unsupported period token: {token!r}")
    return date(_year(month.group(2)), MONTHS[month.group(1).casefold()], 1)


def parse_period_end_inclusive(token: str) -> date:
    start = parse_period_start(token)
    value = token.strip()
    if value.upper().startswith("Q"):
        end_month = start.month + 2
    else:
        end_month = start.month
    return date(start.year, end_month, calendar.monthrange(start.year, end_month)[1])


def parse_period_end_exclusive_boundary(token: str) -> date:
    return parse_period_start(token) - timedelta(days=1)


def parse_long_date(value: str) -> date:
    return datetime.strptime(re.sub(r"\s+", " ", value.strip()), "%B %d, %Y").date()


def iso(value: date) -> str:
    return value.isoformat()


def next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def previous_day(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()

