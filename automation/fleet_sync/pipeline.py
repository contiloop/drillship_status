"""CLI entry point and whole-dataset integrity rules for the published fleet.

``python -m automation.fleet_sync --write [--mode server|local]``

* server (default; GitHub Actions): discover and fetch official sources,
  compare raw document hashes and publish the job queue and source status.
  Never calls a model and never changes contract data.
* local (operator's Mac): additionally triages news titles and reads pending
  documents with Codex, applying only validated items.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

COMPANY_COUNTS = {
    "Transocean": 20,
    "Valaris": 13,
    "Noble": 17,
    "Seadrill": 12,
}
ALLOWED_GENERATIONS = {"6G", "7G", "7G+", "8G"}
ALLOWED_VESSEL_STATUSES = {"Active", "Idle", "Warm-Stacked", "Cold-Stacked"}
ALLOWED_CONTRACT_STATUSES = {"Firm", "Option", "Contingent"}


class IntegrityError(ValueError):
    """The dataset as a whole is not safe to publish."""


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _registry(fleet: list[dict[str, Any]]) -> set[tuple]:
    return {(ship.get("id"), ship.get("name"), ship.get("company"), ship.get("generation")) for ship in fleet}


def validate_fleet(fleet: list[dict[str, Any]], registered: list[dict[str, Any]]) -> None:
    """Raise IntegrityError unless ``fleet`` is a safe successor of ``registered``.

    Checks: 62 vessels with the fixed per-company counts, registered
    id/name/company/generation unchanged, unique contract ids owned by their
    vessel, real ISO dates with start <= end, finite non-negative dayRate,
    allowed enums and non-empty client/region.
    """
    if not isinstance(fleet, list) or len(fleet) != sum(COMPANY_COUNTS.values()):
        raise IntegrityError("fleet must contain the 62 registered vessels")
    if dict(Counter(ship.get("company") for ship in fleet)) != COMPANY_COUNTS:
        raise IntegrityError("company vessel counts changed")
    if _registry(fleet) != _registry(registered) or len({ship.get("id") for ship in fleet}) != len(fleet):
        raise IntegrityError("registered vessel id/name/company/generation changed")
    try:
        json.dumps(fleet, allow_nan=False)
    except ValueError as error:
        raise IntegrityError("fleet contains non-finite numbers") from error
    contract_ids: set[str] = set()
    for ship in fleet:
        name = ship.get("name")
        if ship.get("generation") not in ALLOWED_GENERATIONS or ship.get("status") not in ALLOWED_VESSEL_STATUSES:
            raise IntegrityError(f"{name}: invalid generation or status")
        if isinstance(ship.get("yearBuilt"), bool) or not isinstance(ship.get("yearBuilt"), int):
            raise IntegrityError(f"{name}: invalid build year")
        if "statusAsOf" in ship and not _iso_date(ship["statusAsOf"]):
            raise IntegrityError(f"{name}: invalid statusAsOf")
        for row in ship.get("contracts") or []:
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id or row_id in contract_ids:
                raise IntegrityError(f"{name}: missing or duplicate contract id")
            contract_ids.add(row_id)
            if row.get("vesselId") != ship.get("id"):
                raise IntegrityError(f"{name}: contract parent mismatch")
            if row.get("status") not in ALLOWED_CONTRACT_STATUSES:
                raise IntegrityError(f"{name}: unsupported contract status")
            if not (_iso_date(row.get("startDate")) and _iso_date(row.get("endDate"))):
                raise IntegrityError(f"{name}: contract dates are not ISO dates")
            if row["startDate"] > row["endDate"]:
                raise IntegrityError(f"{name}: contract dates are reversed")
            rate = row.get("dayRate")
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
                raise IntegrityError(f"{name}: invalid dayrate")
            if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("client", "region")):
                raise IntegrityError(f"{name}: blank client or region")


def _prune_obsolete_payloads(public_data: Path, keep: set[str]) -> None:
    """Keep only the content-addressed payloads referenced by the manifest."""
    for pattern in ("fleet.*.json", "events.*.json"):
        for path in public_data.glob(pattern):
            if path.name not in keep:
                path.unlink()


def _semantic_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    old = {ship["id"]: ship for ship in before}
    new = {ship["id"]: ship for ship in after}
    companies = {company: {"statusChanges": 0, "contractsAdded": 0, "contractsRemoved": 0, "contractsChanged": 0}
                 for company in COMPANY_COUNTS}
    for vessel_id, ship in new.items():
        prior = old.get(vessel_id)
        if prior is None:
            continue
        summary = companies[ship["company"]]
        if prior.get("status") != ship.get("status"):
            summary["statusChanges"] += 1
        old_contracts = {item["id"]: item for item in prior.get("contracts", [])}
        new_contracts = {item["id"]: item for item in ship.get("contracts", [])}
        summary["contractsAdded"] += len(new_contracts.keys() - old_contracts.keys())
        summary["contractsRemoved"] += len(old_contracts.keys() - new_contracts.keys())
        summary["contractsChanged"] += sum(old_contracts[key] != new_contracts[key]
                                           for key in old_contracts.keys() & new_contracts.keys())
    return {"companies": companies, "shipsAdded": len(new.keys() - old.keys()),
            "shipsRemoved": len(old.keys() - new.keys())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect official drillship sources and (locally) read them")
    parser.add_argument("--write", action="store_true", help="write public data and the record file")
    parser.add_argument("--mode", choices=("server", "local"), default="server")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    from .reading_pipeline import execute
    try:
        result = execute(args.root.resolve(), mode=args.mode, write=args.write)
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    result.pop("manifest", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1
