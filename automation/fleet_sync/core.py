from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .model import ContractObservation, VesselSnapshot


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def split_lines(value: object) -> list[str]:
    if value is None:
        return []
    return [clean_text(item) for item in str(value).splitlines() if clean_text(item)]


def parse_day_rate(value: object) -> tuple[int, str]:
    text = clean_text(value)
    match = re.search(r"\$?\b(\d{1,3}(?:,\d{3})+)\b", text)
    if not match:
        return 0, "undisclosed"
    return int(match.group(1).replace(",", "")), "reported"


def number_word(value: str) -> int | None:
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    token = value.strip().casefold()
    return int(token) if token.isdigit() else words.get(token)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: object, length: int = 16) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_status(snapshot: VesselSnapshot, as_of: str) -> str:
    if snapshot.status in {"Cold-Stacked", "Warm-Stacked"}:
        return snapshot.status
    if any(
        contract.status == "Firm"
        and contract.start_date <= as_of <= contract.end_date
        for contract in snapshot.contracts
    ):
        return "Active"
    return "Idle"


def merge_completed_history(
    existing: list[dict],
    observed: list[ContractObservation],
    report_date: str,
) -> list[dict]:
    merged: list[dict] = []
    report_day = date.fromisoformat(report_date)
    observed_intervals: list[tuple[date, date]] = []
    for item in observed:
        if item.status != "Firm":
            continue
        start_day = date.fromisoformat(item.start_date)
        end_day = date.fromisoformat(item.end_date)
        if start_day > end_day:
            raise ValueError(
                f"invalid observed interval: {item.start_date} > {item.end_date}"
            )
        observed_intervals.append((start_day, end_day))
    observed_intervals.sort()

    for contract in existing:
        if contract.get("status") != "Firm":
            continue
        start_day = date.fromisoformat(contract.get("startDate", ""))
        end_day = date.fromisoformat(contract.get("endDate", ""))
        if start_day > end_day:
            raise ValueError(
                f"invalid historical interval: {start_day.isoformat()} > {end_day.isoformat()}"
            )
        if end_day >= report_day:
            continue

        # Dates parsed from month-precision reports are projected to closed ISO
        # intervals. Adjacent contracts can therefore overlap for the projected
        # boundary month. Subtract the observed Firm days instead of discarding
        # the entire legacy contract, preserving only demonstrably disjoint days.
        historical_intervals = [(start_day, end_day)]
        for observed_start, observed_end in observed_intervals:
            remaining: list[tuple[date, date]] = []
            for historical_start, historical_end in historical_intervals:
                if observed_end < historical_start or historical_end < observed_start:
                    remaining.append((historical_start, historical_end))
                    continue
                if historical_start < observed_start:
                    remaining.append(
                        (historical_start, observed_start - timedelta(days=1))
                    )
                if observed_end < historical_end:
                    remaining.append(
                        (observed_end + timedelta(days=1), historical_end)
                    )
            historical_intervals = remaining
            if not historical_intervals:
                break

        for historical_start, historical_end in historical_intervals:
            historical = {key: value for key, value in contract.items() if key != "id"}
            historical["startDate"] = historical_start.isoformat()
            historical["endDate"] = historical_end.isoformat()
            # The seed snapshot predates the provenance pipeline. Keep its useful
            # interval history, but do not continue publishing an unverifiable or
            # contract-value-derived dayrate as though it were disclosed.
            historical["dayRate"] = 0
            merged.append(historical)

    for item in observed:
        merged.append(
            {
                "vesselId": "",
                "startDate": item.start_date,
                "endDate": item.end_date,
                "dayRate": item.day_rate,
                "client": item.client,
                "region": item.region,
                "status": item.status,
            }
        )

    unique: dict[tuple[str, ...], dict] = {}
    for item in merged:
        key = (
            item["startDate"],
            item["endDate"],
            item["client"].casefold(),
            item["region"].casefold(),
            item["status"],
            str(item["dayRate"]),
        )
        unique[key] = item
    return sorted(unique.values(), key=lambda item: (item["startDate"], item["endDate"], item["status"]))


def assign_contract_ids(vessel_id: str, contracts: Iterable[dict]) -> list[dict]:
    result: list[dict] = []
    used: set[str] = set()
    for contract in contracts:
        item = dict(contract)
        identity = {
            "vesselId": vessel_id,
            "startDate": item["startDate"],
            "client": clean_text(item["client"]).casefold(),
            "region": clean_text(item["region"]).casefold(),
            "status": item["status"],
        }
        suffix = content_hash(identity, length=10)
        candidate = f"{vessel_id}-{suffix}"
        collision = 2
        while candidate in used:
            candidate = f"{vessel_id}-{suffix}-{collision}"
            collision += 1
        used.add(candidate)
        item["id"] = candidate
        item["vesselId"] = vessel_id
        result.append(item)
    return result


def validate_iso_date(value: str) -> None:
    date.fromisoformat(value)
