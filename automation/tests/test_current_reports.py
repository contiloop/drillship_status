from collections import Counter
from pathlib import Path

import pytest

from automation.fleet_sync.core import file_sha256, load_json
from automation.fleet_sync.parsers import (
    parse_noble_pdf,
    parse_seadrill_pdf,
    parse_transocean_pdf,
    parse_valaris_pdf,
)


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "automation" / "tests" / "fixtures" / "reports"


@pytest.mark.parametrize(
    ("company", "filename", "sha256", "parser", "report_date", "statuses"),
    [
        ("Transocean", "transocean-2026-08.pdf", "f2f98b03f750b5c4f6d299bff9b55d0ecc4dbeb0cb4f069054193968894db8b6", parse_transocean_pdf, "2026-08-05", {"Firm": 27, "Option": 4, "Contingent": 2}),
        ("Valaris", "valaris-2026-08.pdf", "c94713719e350294eb91b0361212df7e5951c8d00b9e108ff7afa4a797e55e80", parse_valaris_pdf, "2026-08-05", {"Firm": 15}),
        ("Noble", "noble-2026-07.pdf", "ddfdfe43090fe4f2589352abb7bf14cb292ee7323569f29a9f89e20c079807a6", parse_noble_pdf, "2026-07-27", {"Firm": 20, "Option": 7}),
        ("Seadrill", "seadrill-2026-08.pdf", "b774357daafdb62dcc8c371fe5f5d118fb8d7d51c91badd05cd6cae3c9a39cb1", parse_seadrill_pdf, "2026-08-10", {"Firm": 18, "Option": 2}),
    ],
)
def test_current_official_report_golden_counts(company, filename, sha256, parser, report_date, statuses) -> None:
    content = (REPORTS / filename).read_bytes()
    assert file_sha256(content) == sha256
    seed = load_json(ROOT / "data" / "data_as_of_26_01_07.json")
    names = {ship["name"] for ship in seed if ship["company"] == company}
    result, actual_report_date = parser(content, names)
    assert actual_report_date == report_date
    assert Counter(item.status for vessel in result.vessels.values() for item in vessel.contracts) == statuses


def test_current_noble_held_for_sale_note_is_preserved() -> None:
    content = (REPORTS / "noble-2026-07.pdf").read_bytes()
    seed = load_json(ROOT / "data" / "data_as_of_26_01_07.json")
    names = {ship["name"] for ship in seed if ship["company"] == "Noble"}

    result, _ = parse_noble_pdf(content, names)

    vessel = result.vessels["Noble Globetrotter II"]
    assert vessel.notes == ["Held for sale."]
    assert vessel.operational_observations[0].page == 4
    assert vessel.operational_observations[0].row == "row=9"


def test_valaris_loa_card_keeps_source_precision_and_provenance() -> None:
    from automation.fleet_sync.pipeline import _build_outputs

    fleet, sources, _, events, _, _ = _build_outputs(ROOT, offline=True)
    event = next(item for item in events if item.get("vessel") == "VALARIS DS-18")
    source = next(item for item in sources if item.company == "Valaris")
    assert event["url"] == source.document_url
    assert event["publishedAt"] == "2026-08-05"
    assert event["sourceSha256"] == source.sha256
    assert event["page"] == 4
    assert event["row"]
    assert event["facts"]["expectedStart"] == "Nov 26"
    assert event["facts"]["expectedEnd"] == "May 27"
    assert event["facts"]["startPrecision"] == "month"
    assert event["facts"]["awardType"] == "letter-of-award"
    assert event["autoApplied"] is False
    assert not any(
        contract["startDate"].startswith("2026-11")
        for ship in fleet if ship["name"] == "VALARIS DS-18"
        for contract in ship["contracts"]
    )
