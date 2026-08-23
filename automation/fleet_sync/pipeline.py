from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import PARSER_VERSION
from .core import (
    assign_contract_ids,
    content_hash,
    dump_json,
    file_sha256,
    load_json,
    merge_completed_history,
    validate_iso_date,
)
from .model import ParseResult, SourceDocument
from .parsers import (
    parse_noble_pdf,
    parse_seadrill_pdf,
    parse_transocean_pdf,
    parse_valaris_html,
    parse_valaris_pdf,
)
from .sources import (
    HttpClient,
    SOURCE_SPECS,
    collect_official_news,
    discover_document,
    retrieved_at,
)


COMPANY_COUNTS = {
    "Transocean": 20,
    "Valaris": 13,
    "Noble": 17,
    "Seadrill": 12,
}
ALLOWED_GENERATIONS = {"6G", "7G", "7G+", "8G"}
ALLOWED_VESSEL_STATUSES = {"Active", "Idle", "Warm-Stacked", "Cold-Stacked"}
ALLOWED_CONTRACT_STATUSES = {"Firm", "Option", "Contingent"}
OFFLINE_FILES = {
    "Transocean": "automation/tests/fixtures/reports/transocean-2026-08.pdf",
    "Valaris": "automation/tests/fixtures/reports/valaris-2026-08.pdf",
    "Noble": "automation/tests/fixtures/reports/noble-2026-07.pdf",
    "Seadrill": "automation/tests/fixtures/reports/seadrill-2026-08.pdf",
}
# The four current golden reports contain 33/15/27/20 contract rows. A parser
# must retain at least 75% of that known-good coverage; a larger legitimate
# fleet change still needs a reviewed baseline update instead of silent publish.
KNOWN_GOOD_CONTRACT_COUNTS = {
    "Transocean": 33,
    "Valaris": 15,
    "Noble": 27,
    "Seadrill": 20,
}
MIN_PARSED_CONTRACTS = {
    company: (count * 3 + 3) // 4
    for company, count in KNOWN_GOOD_CONTRACT_COUNTS.items()
}
# Fleet-status reports are normally quarterly. Direct official discovery is
# given extra headroom; a hard-coded fallback expires after four months or four
# consecutive scheduled runs, whichever comes first.
MAX_REPORT_AGE_DAYS = 180
MAX_FALLBACK_REPORT_AGE_DAYS = 120
MAX_FUTURE_REPORT_DAYS = 2
MAX_CONSECUTIVE_FALLBACK_RUNS = 4


class PipelineError(RuntimeError):
    """Raised when a source or semantic validation gate fails closed."""


def _load_current_fleet(root: Path) -> list[dict[str, Any]]:
    manifest_path = root / "public" / "data" / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("fleetFile"), str):
            raise PipelineError("existing manifest has an invalid fleetFile")
        filename = manifest["fleetFile"]
        if Path(filename).name != filename:
            raise PipelineError("existing manifest has an unsafe fleetFile")
        current_path = manifest_path.parent / filename
        current = load_json(current_path)
    else:
        current = load_json(root / "data" / "data_as_of_26_01_07.json")
    if not isinstance(current, list):
        raise PipelineError("fleet seed is not an array")
    return current


def _parse_document(company: str, content: bytes, names: set[str], content_type: str) -> tuple[ParseResult, str]:
    if content.startswith(b"%PDF-"):
        parser = {
            "Transocean": parse_transocean_pdf,
            "Valaris": parse_valaris_pdf,
            "Noble": parse_noble_pdf,
            "Seadrill": parse_seadrill_pdf,
        }[company]
        return parser(content, names)
    if company == "Valaris" and (
        "html" in content_type.casefold()
        or b"Fleet Status Report" in content[:500_000]
    ):
        return parse_valaris_html(content, names)
    raise PipelineError(f"{company}: source is neither the expected PDF nor supported SEC HTML")


def _validate_source_content(company: str, content: bytes, content_type: str) -> None:
    if len(content) < 20_000:
        raise PipelineError(f"{company}: source document is unexpectedly small ({len(content)} bytes)")
    if content.startswith(b"%PDF-"):
        if b"%%EOF" not in content[-8192:]:
            raise PipelineError(f"{company}: PDF has no EOF marker")
        return
    if company == "Valaris" and "html" in content_type.casefold():
        lowered = content.casefold()
        if b"fleet status report" not in lowered or b"asset category" not in lowered:
            raise PipelineError("Valaris: SEC HTML does not contain the fleet table markers")
        return
    raise PipelineError(f"{company}: unexpected content type {content_type!r}")


def _previous_report_dates(root: Path) -> dict[str, str]:
    manifest_path = root / "public" / "data" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        return {}
    return {
        item["company"]: item["reportDate"]
        for item in manifest.get("sources", [])
        if isinstance(item, dict)
        and isinstance(item.get("company"), str)
        and isinstance(item.get("reportDate"), str)
    }


def _previous_source_health(root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = root / "public" / "data" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        return {}
    health = manifest.get("sourceHealth", [])
    if not isinstance(health, list):
        return {}
    return {
        item["company"]: item
        for item in health
        if isinstance(item, dict) and isinstance(item.get("company"), str)
    }


def _load_previous_events(root: Path) -> list[dict[str, Any]]:
    manifest_path = root / "public" / "data" / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("eventsFile"), str):
        return []
    filename = manifest["eventsFile"]
    if Path(filename).name != filename:
        raise PipelineError("existing manifest has an unsafe eventsFile")
    events_path = manifest_path.parent / filename
    if not events_path.exists():
        raise PipelineError(f"existing event payload is missing: {filename}")
    payload = load_json(events_path)
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
        raise PipelineError("existing event payload is invalid")
    return [dict(item) for item in events]


def _load_previous_observations(root: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    path = root / "data" / "provenance" / "observations.json"
    if not path.exists():
        return {}
    payload = load_json(path)
    rows = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    carried: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("company"), row.get("vesselId"), row.get("canonicalContractId"))
        if all(isinstance(item, str) and item for item in key):
            carried[key] = row
    return carried


def _validate_contract_coverage(company: str, result: ParseResult) -> None:
    actual = sum(len(vessel.contracts) for vessel in result.vessels.values())
    minimum = MIN_PARSED_CONTRACTS[company]
    if actual < minimum:
        raise PipelineError(
            f"{company}: suspicious parser coverage: {actual} contracts < {minimum} minimum "
            f"(known-good report has {KNOWN_GOOD_CONTRACT_COUNTS[company]})"
        )


def _fleet_source_health(
    company: str,
    report_date: str,
    discovery: str,
    previous_report_date: str | None,
    *,
    as_of: date | None = None,
    enforce_age: bool = True,
    fallback_streak: int = 0,
) -> tuple[str, str | None]:
    """Validate report chronology and classify stable discovery health."""

    validate_iso_date(report_date)
    if previous_report_date and previous_report_date > report_date:
        raise PipelineError(f"{company}: source rollback {report_date} < {previous_report_date}")

    fallback = discovery.startswith("fallback-after-")
    health = "degraded-fallback" if fallback else "healthy"
    if discovery.startswith("official-index-after-sec-"):
        health = "healthy-official-alternate"
    elif discovery == "offline-official-fixture":
        health = "offline-fixture"

    if enforce_age:
        current_day = as_of or date.today()
        parsed_day = date.fromisoformat(report_date)
        age_days = (current_day - parsed_day).days
        if age_days < -MAX_FUTURE_REPORT_DAYS:
            raise PipelineError(
                f"{company}: report date {report_date} is more than {MAX_FUTURE_REPORT_DAYS} days in the future"
            )
        max_age = MAX_FALLBACK_REPORT_AGE_DAYS if fallback else MAX_REPORT_AGE_DAYS
        if age_days > max_age:
            mode = "fallback" if fallback else "officially discovered"
            raise PipelineError(
                f"{company}: {mode} report {report_date} is {age_days} days old; "
                f"maximum allowed age is {max_age} days"
            )
        if fallback and fallback_streak > MAX_CONSECUTIVE_FALLBACK_RUNS:
            raise PipelineError(
                f"{company}: identical fallback report persisted for {fallback_streak} consecutive runs; "
                f"maximum allowed is {MAX_CONSECUTIVE_FALLBACK_RUNS}"
            )

    warning = None
    if fallback:
        warning = (
            f"{company} fleet discovery degraded: {discovery}; validated fallback report "
            f"{report_date} is accepted for at most {MAX_FALLBACK_REPORT_AGE_DAYS} days "
            f"and {MAX_CONSECUTIVE_FALLBACK_RUNS} consecutive runs "
            f"(current streak {fallback_streak})."
        )
    elif discovery.startswith("sec-submissions-after-official-"):
        health = "degraded-regulatory-alternate"
        warning = (
            f"{company} official investor index discovery degraded: {discovery}; "
            "a validated SEC fleet-status exhibit was used."
        )
    return health, warning


def _next_fallback_streak(
    discovery: str,
    previous: dict[str, Any] | None,
    *,
    report_date: str,
    sha256: str,
) -> int:
    if not discovery.startswith("fallback-after-"):
        return 0
    if not previous or previous.get("fleetReport") != "degraded-fallback":
        return 1
    same_document = (
        previous.get("reportDate") == report_date
        and previous.get("sha256") == sha256
    )
    if not same_document:
        return 1
    prior_streak = previous.get("fallbackStreak", 0)
    if not isinstance(prior_streak, int) or prior_streak < 0:
        prior_streak = 0
    return prior_streak + 1


def _restore_verified_history_rate(
    contract: dict[str, Any],
    prior: dict[str, Any] | None,
) -> bool:
    if not prior or not prior.get("sourceUrl") or not prior.get("sourceSha256"):
        return False
    disclosure = prior.get("rate_disclosure", prior.get("rateDisclosure"))
    prior_rate = prior.get("day_rate")
    if disclosure != "reported" or not isinstance(prior_rate, int) or prior_rate <= 0:
        return False
    contract["dayRate"] = prior_rate
    return True


def _event_identity(event: dict[str, Any]) -> tuple[str, ...]:
    url = str(event.get("url") or "")
    if url:
        return (str(event.get("company") or ""), str(event.get("classification") or ""), url)
    return (
        str(event.get("company") or ""),
        str(event.get("classification") or ""),
        str(event.get("vessel") or ""),
        str(event.get("title") or ""),
        str(event.get("start") or ""),
        str(event.get("end") or ""),
    )


def _merge_news_events(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union monitored news by stable identity so partial reads cannot erase history."""

    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for event in [*previous, *current]:
        if event.get("classification") != "official-news-signal":
            continue
        identity = _event_identity(event)
        prior = merged.get(identity, {})
        combined = {**prior, **event}
        prior_vessels = prior.get("vessels")
        current_vessels = event.get("vessels")
        combined["vessels"] = sorted(
            {
                str(vessel)
                for vessel in [
                    *(prior_vessels if isinstance(prior_vessels, list) else []),
                    *(current_vessels if isinstance(current_vessels, list) else []),
                ]
                if str(vessel)
            }
        )
        merged[identity] = combined
    return list(merged.values())


def _parse_event_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip()
    try:
        return date.fromisoformat(token[:10])
    except ValueError:
        pass
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(token, pattern).date()
        except ValueError:
            continue
    return None


def _annotate_event_review(
    events: list[dict[str, Any]],
    report_dates: dict[str, str],
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for original in events:
        event = dict(original)
        if event.get("classification") == "official-news-signal":
            published = _parse_event_date(event.get("publishedAt"))
            report_date = report_dates.get(str(event.get("company") or ""))
            is_pending = bool(published and report_date and published.isoformat() > report_date)
            event["pendingReview"] = is_pending
            event["reviewStatus"] = (
                "pending" if is_pending else "acknowledged" if published else "date-unverified"
            )
        else:
            is_pending = not bool(event.get("autoApplied"))
            event["pendingReview"] = is_pending
            event["reviewStatus"] = "pending" if is_pending else "applied"
        annotated.append(event)
    return annotated


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    return sorted(set(warnings))


def _prune_obsolete_payloads(public_data: Path, keep: set[str]) -> None:
    """Keep only the content-addressed payloads referenced by the manifest."""

    for pattern in ("fleet.*.json", "events.*.json"):
        for path in public_data.glob(pattern):
            if path.name not in keep:
                path.unlink()


def _semantic_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    old = {ship["id"]: ship for ship in before}
    new = {ship["id"]: ship for ship in after}
    companies: dict[str, dict[str, int]] = {
        company: {
            "statusChanges": 0,
            "contractsAdded": 0,
            "contractsRemoved": 0,
            "contractsChanged": 0,
        }
        for company in COMPANY_COUNTS
    }
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
        summary["contractsChanged"] += sum(
            old_contracts[key] != new_contracts[key]
            for key in old_contracts.keys() & new_contracts.keys()
        )
    return {
        "companies": companies,
        "shipsAdded": len(new.keys() - old.keys()),
        "shipsRemoved": len(old.keys() - new.keys()),
    }


def validate_fleet(fleet: list[dict[str, Any]], expected_names: dict[str, set[str]]) -> None:
    if len(fleet) != sum(COMPANY_COUNTS.values()):
        raise PipelineError(f"fleet count changed: expected 62, got {len(fleet)}")
    ids = [ship.get("id") for ship in fleet]
    if len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item for item in ids):
        raise PipelineError("vessel IDs are missing or duplicated")

    actual_counts = Counter(ship.get("company") for ship in fleet)
    if dict(actual_counts) != COMPANY_COUNTS:
        raise PipelineError(f"company fleet counts changed: {dict(actual_counts)}")

    for company, names in expected_names.items():
        actual_names = {ship["name"] for ship in fleet if ship["company"] == company}
        if actual_names != names:
            raise PipelineError(f"{company}: vessel registry changed")

    contract_ids: set[str] = set()
    for ship in fleet:
        if ship.get("generation") not in ALLOWED_GENERATIONS:
            raise PipelineError(f"{ship.get('name')}: invalid generation")
        if ship.get("status") not in ALLOWED_VESSEL_STATUSES:
            raise PipelineError(f"{ship.get('name')}: invalid vessel status")
        if not isinstance(ship.get("statusAsOf"), str):
            raise PipelineError(f"{ship.get('name')}: missing statusAsOf")
        validate_iso_date(ship["statusAsOf"])
        if not isinstance(ship.get("yearBuilt"), int):
            raise PipelineError(f"{ship.get('name')}: invalid build year")
        for contract in ship.get("contracts", []):
            contract_id = contract.get("id")
            if not isinstance(contract_id, str) or contract_id in contract_ids:
                raise PipelineError(f"{ship['name']}: duplicate or missing contract ID")
            contract_ids.add(contract_id)
            if contract.get("vesselId") != ship["id"]:
                raise PipelineError(f"{ship['name']}: contract parent mismatch")
            if contract.get("status") not in ALLOWED_CONTRACT_STATUSES:
                raise PipelineError(f"{ship['name']}: unsupported contract status")
            validate_iso_date(contract["startDate"])
            validate_iso_date(contract["endDate"])
            if contract["startDate"] > contract["endDate"]:
                raise PipelineError(f"{ship['name']}: contract dates are reversed")
            if not isinstance(contract.get("dayRate"), int) or contract["dayRate"] < 0:
                raise PipelineError(f"{ship['name']}: invalid dayrate")
            if not contract.get("client") or not contract.get("region"):
                raise PipelineError(f"{ship['name']}: blank client or region")


def _build_outputs(
    root: Path,
    *,
    offline: bool,
) -> tuple[
    list[dict[str, Any]],
    list[SourceDocument],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    list[dict[str, Any]],
]:
    current = _load_current_fleet(root)
    by_company = {
        company: [ship for ship in current if ship.get("company") == company]
        for company in COMPANY_COUNTS
    }
    expected_names = {
        company: {ship["name"] for ship in ships}
        for company, ships in by_company.items()
    }
    previous_dates = _previous_report_dates(root)
    previous_health = _previous_source_health(root)
    previous_events = _load_previous_events(root)
    previous_observations = _load_previous_observations(root)
    client = HttpClient()
    parsed: dict[str, tuple[ParseResult, str]] = {}
    sources: list[SourceDocument] = []
    news_events: list[dict[str, Any]] = []
    monitor_warnings: list[str] = []
    source_health: dict[str, dict[str, Any]] = {}

    for spec in SOURCE_SPECS:
        if offline:
            document_url = spec.fallback_document_url
            discovery = "offline-official-fixture"
            path = root / OFFLINE_FILES[spec.company]
            if not path.exists():
                raise PipelineError(f"offline fixture is missing: {path}")
            content = path.read_bytes()
            content_type = "application/pdf"
        else:
            document_url, discovery = discover_document(client, spec)
            response = client.get(document_url)
            content = response.content
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            document_url = response.url

        _validate_source_content(spec.company, content, content_type)
        result, report_date = _parse_document(
            spec.company,
            content,
            expected_names[spec.company],
            content_type,
        )
        _validate_contract_coverage(spec.company, result)
        document_sha = file_sha256(content)
        fallback_streak = _next_fallback_streak(
            discovery,
            previous_health.get(spec.company),
            report_date=report_date,
            sha256=document_sha,
        )
        fleet_health, fleet_warning = _fleet_source_health(
            spec.company,
            report_date,
            discovery,
            previous_dates.get(spec.company),
            enforce_age=not offline,
            fallback_streak=fallback_streak,
        )
        if fleet_warning:
            monitor_warnings.append(fleet_warning)
        source_health[spec.company] = {
            "company": spec.company,
            "fleetReport": fleet_health,
            "discovery": discovery,
            "newsMonitor": "not-run-offline" if offline else "healthy",
            "preservedNewsEvents": 0,
            "reportDate": report_date,
            "documentUrl": document_url,
            "sha256": document_sha,
            "fallbackStreak": fallback_streak,
        }
        parsed[spec.company] = (result, report_date)
        sources.append(
            SourceDocument(
                company=spec.company,
                index_url=spec.index_url,
                document_url=document_url,
                report_date=report_date,
                sha256=document_sha,
                byte_size=len(content),
                retrieved_at=retrieved_at(),
                parser_version=PARSER_VERSION,
                discovery=discovery,
                content_type=content_type,
            )
        )

        previous_company_news = [
            event
            for event in previous_events
            if event.get("company") == spec.company
            and event.get("classification") == "official-news-signal"
        ]
        if offline:
            news_events.extend(previous_company_news)
        else:
            events, error = collect_official_news(
                client,
                spec,
                sorted(expected_names[spec.company]),
            )
            merged_events = _merge_news_events(previous_company_news, events)
            news_events.extend(merged_events)
            if error:
                preserved_count = len(previous_company_news)
                source_health[spec.company]["newsMonitor"] = (
                    "degraded-preserved" if preserved_count else "degraded-empty"
                )
                source_health[spec.company]["preservedNewsEvents"] = preserved_count
                monitor_warnings.append(
                    f"{spec.company} news monitor degraded: {error}; preserved "
                    f"{preserved_count} last-known official news events."
                )

    source_by_company = {source.company: source for source in sources}
    fleet: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    pending_events: list[dict[str, Any]] = []
    legacy_history_count = 0

    for old_ship in current:
        company = old_ship["company"]
        result, report_date = parsed[company]
        snapshot = result.vessels[old_ship["name"]]
        contracts = merge_completed_history(
            old_ship.get("contracts", []),
            snapshot.contracts,
            report_date,
        )
        assigned = assign_contract_ids(old_ship["id"], contracts)
        fleet.append(
            {
                "id": old_ship["id"],
                "name": old_ship["name"],
                "company": company,
                "generation": old_ship["generation"],
                "status": snapshot.status,
                "statusAsOf": report_date,
                "yearBuilt": old_ship["yearBuilt"],
                "contracts": assigned,
            }
        )
        source = source_by_company[company]
        observed_by_key = {
            observation.canonical_key(): observation
            for observation in snapshot.contracts
        }
        for contract in assigned:
            key = (
                contract["startDate"],
                contract["endDate"],
                contract["client"].casefold(),
                contract["region"].casefold(),
                contract["status"],
                str(contract["dayRate"]),
            )
            observation = observed_by_key.get(key)
            if observation is None:
                prior = previous_observations.get((company, old_ship["id"], contract["id"]))
                _restore_verified_history_rate(contract, prior)
                if prior and prior.get("sourceUrl") and prior.get("sourceSha256"):
                    carried = dict(prior)
                    carried.update(
                        {
                            "company": company,
                            "vesselId": old_ship["id"],
                            "vessel": old_ship["name"],
                            "canonicalContractId": contract["id"],
                            "provenanceClass": "official-prior-report-history",
                            "start_date": contract["startDate"],
                            "end_date": contract["endDate"],
                            "client": contract["client"],
                            "region": contract["region"],
                            "status": contract["status"],
                            "day_rate": contract["dayRate"],
                        }
                    )
                    if "rate_disclosure" not in carried:
                        carried["rate_disclosure"] = carried.pop(
                            "rateDisclosure", "unknown"
                        )
                    observations.append(carried)
                    continue
                legacy_history_count += 1
                observations.append(
                    {
                        "company": company,
                        "vesselId": old_ship["id"],
                        "vessel": old_ship["name"],
                        "canonicalContractId": contract["id"],
                        "provenanceClass": "legacy-history-import",
                        "sourceUrl": None,
                        "sourceSha256": None,
                        "reportDate": None,
                        "rate_disclosure": "unverified-serialized-as-zero",
                        "start_date": contract["startDate"],
                        "end_date": contract["endDate"],
                        "client": contract["client"],
                        "region": contract["region"],
                        "status": contract["status"],
                        "day_rate": contract["dayRate"],
                    }
                )
                continue
            observations.append(
                {
                    "company": company,
                    "vesselId": old_ship["id"],
                    "vessel": old_ship["name"],
                    "canonicalContractId": contract["id"],
                    "provenanceClass": "official-current-report",
                    "sourceSha256": source.sha256,
                    "sourceUrl": source.document_url,
                    "reportDate": report_date,
                    **asdict(observation),
                }
            )
        operational_rows = getattr(snapshot, "operational_observations", [])
        if operational_rows:
            serialized_operational_rows = [
                (item.note, item.page, item.row) for item in operational_rows
            ]
        else:
            serialized_operational_rows = [
                (note, None, "legacy-note-without-source-row") for note in snapshot.notes
            ]
        for note, page, row in serialized_operational_rows:
            observations.append(
                {
                    "company": company,
                    "vesselId": old_ship["id"],
                    "vessel": old_ship["name"],
                    "sourceSha256": source.sha256,
                    "sourceUrl": source.document_url,
                    "reportDate": report_date,
                    "provenanceClass": "official-current-report-operational-note",
                    "rate_disclosure": "not-applicable",
                    "operationalNote": note,
                    "page": page,
                    "row": row,
                }
            )

    for result, _ in parsed.values():
        pending_events.extend(result.pending_events)
        monitor_warnings.extend(result.warnings)
    if legacy_history_count:
        monitor_warnings.append(
            f"{legacy_history_count} completed legacy history records have no source evidence; dayrates are serialized as zero."
        )
    pending_events.extend(news_events)
    pending_events = _annotate_event_review(
        pending_events,
        {company: report_date for company, (_, report_date) in parsed.items()},
    )
    pending_events.sort(
        key=lambda item: (
            str(item.get("company", "")),
            str(item.get("publishedAt", "")),
            str(item.get("url", "")),
            str(item.get("vessel", "")),
        )
    )

    validate_fleet(fleet, expected_names)
    return (
        fleet,
        sources,
        observations,
        pending_events,
        monitor_warnings,
        [source_health[company] for company in sorted(source_health)],
    )


def run(root: Path, *, write: bool, offline: bool = False) -> dict[str, Any]:
    before = _load_current_fleet(root)
    fleet, sources, observations, events, warnings, source_health = _build_outputs(
        root, offline=offline
    )
    warnings = _dedupe_warnings(warnings)
    fleet_hash = content_hash(fleet, length=20)
    events_hash = content_hash(events, length=20)
    health_by_company = {item["company"]: item for item in source_health}
    source_facts = [
        {
            "company": item.company,
            "indexUrl": item.index_url,
            "documentUrl": item.document_url,
            "reportDate": item.report_date,
            "sha256": item.sha256,
            "byteSize": item.byte_size,
            "parserVersion": item.parser_version,
            "discovery": item.discovery,
            "contentType": item.content_type,
            "health": health_by_company[item.company]["fleetReport"],
            "newsMonitor": health_by_company[item.company]["newsMonitor"],
        }
        for item in sorted(sources, key=lambda source: source.company)
    ]
    state_fingerprint = content_hash(
        {
            "fleetHash": fleet_hash,
            "sources": source_facts,
            "eventsHash": events_hash,
            "sourceHealth": source_health,
            "warningsHash": content_hash(warnings, length=20),
        },
        length=20,
    )
    public_data = root / "public" / "data"
    existing_manifest_path = public_data / "manifest.json"
    existing_manifest = (
        load_json(existing_manifest_path) if existing_manifest_path.exists() else {}
    )
    unchanged = (
        isinstance(existing_manifest, dict)
        and existing_manifest.get("stateFingerprint") == state_fingerprint
    )
    generated_at = (
        existing_manifest.get("generatedAt")
        if unchanged and isinstance(existing_manifest, dict)
        else retrieved_at()
    )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "updatedAsOf": max(item.report_date for item in sources),
        "dataHash": fleet_hash,
        "stateFingerprint": state_fingerprint,
        "fleetFile": f"fleet.{fleet_hash}.json",
        "eventsFile": f"events.{events_hash}.json",
        "eventsHash": events_hash,
        "shipCount": len(fleet),
        "contractCount": sum(len(ship["contracts"]) for ship in fleet),
        "sourceCount": len(sources),
        "sources": source_facts,
        "sourceHealth": source_health,
        "warningsCount": len(warnings),
        "eventCount": len(events),
        "pendingEventCount": sum(event.get("pendingReview") is True for event in events),
    }
    changes = {
        "generatedAt": generated_at,
        "fromDataHash": (
            existing_manifest.get("dataHash")
            if isinstance(existing_manifest, dict)
            else None
        ),
        "toDataHash": fleet_hash,
        **_semantic_changes(before, fleet),
    }
    source_provenance = {
        "generatedAt": generated_at,
        "documents": [asdict(item) for item in sorted(sources, key=lambda source: source.company)],
        "health": source_health,
        "warnings": warnings,
    }
    observation_provenance = {
        "generatedAt": generated_at,
        "parserVersion": PARSER_VERSION,
        "observations": observations,
    }

    if write and not unchanged:
        dump_json(public_data / manifest["fleetFile"], fleet)
        dump_json(public_data / manifest["eventsFile"], {"generatedAt": generated_at, "events": events})
        dump_json(public_data / "changes.json", changes)
        dump_json(root / "data" / "provenance" / "sources.json", source_provenance)
        dump_json(root / "data" / "provenance" / "observations.json", observation_provenance)
        # Publish the manifest only after its content-addressed payloads exist.
        dump_json(existing_manifest_path, manifest)

    if write:
        # Vercel deploys each Git revision atomically, so payloads referenced by
        # older deployments remain available there without accumulating in main.
        _prune_obsolete_payloads(
            public_data,
            {manifest["fleetFile"], manifest["eventsFile"]},
        )

    return {
        "ok": True,
        "writeRequested": write,
        "changed": not unchanged,
        "manifest": manifest,
        "events": len(events),
        "warnings": len(warnings),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize official drillship fleet reports")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write validated generated artifacts")
    mode.add_argument("--check", action="store_true", help="fetch, parse, and validate without writing")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use local official PDF fixtures (for development only)",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = run(args.root.resolve(), write=args.write, offline=args.offline)
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
