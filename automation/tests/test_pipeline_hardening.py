from __future__ import annotations

from datetime import date, timedelta

import pytest
import requests

from automation.fleet_sync.model import ContractObservation, ParseResult, VesselSnapshot
from automation.fleet_sync.core import assign_contract_ids
from automation.fleet_sync.pipeline import (
    MAX_CONSECUTIVE_FALLBACK_RUNS,
    MAX_FALLBACK_REPORT_AGE_DAYS,
    MIN_PARSED_CONTRACTS,
    PipelineError,
    _annotate_event_review,
    _dedupe_warnings,
    _fleet_source_health,
    _merge_news_events,
    _next_fallback_streak,
    _prune_obsolete_payloads,
    _restore_verified_history_rate,
    _validate_contract_coverage,
)
from automation.fleet_sync import sources as source_module
from automation.fleet_sync.sources import HttpClient, SOURCE_SPECS, _extract_review_facts


def _observation() -> ContractObservation:
    return ContractObservation(
        client="Client",
        region="Region",
        start_date="2026-01-01",
        end_date="2026-12-31",
        day_rate=0,
        status="Firm",
        page=1,
        row="row=1",
    )


def _result(company: str, count: int) -> ParseResult:
    vessel = VesselSnapshot(name="Rig", status="Active", contracts=[_observation()] * count)
    return ParseResult(company=company, vessels={"Rig": vessel})


def test_contract_coverage_rejects_layout_drift() -> None:
    company = "Transocean"
    _validate_contract_coverage(company, _result(company, MIN_PARSED_CONTRACTS[company]))
    with pytest.raises(PipelineError, match="suspicious parser coverage"):
        _validate_contract_coverage(company, _result(company, MIN_PARSED_CONTRACTS[company] - 1))


def test_fallback_is_explicit_and_expires() -> None:
    report_day = date(2026, 8, 5)
    health, warning = _fleet_source_health(
        "Transocean",
        report_day.isoformat(),
        "fallback-after-HTTPError",
        report_day.isoformat(),
        as_of=report_day + timedelta(days=MAX_FALLBACK_REPORT_AGE_DAYS),
    )
    assert health == "degraded-fallback"
    assert warning and "accepted for at most" in warning

    with pytest.raises(PipelineError, match="maximum allowed age"):
        _fleet_source_health(
            "Transocean",
            report_day.isoformat(),
            "fallback-after-HTTPError",
            report_day.isoformat(),
            as_of=report_day + timedelta(days=MAX_FALLBACK_REPORT_AGE_DAYS + 1),
        )


def test_identical_fallback_is_bounded_across_scheduled_runs() -> None:
    previous = {
        "fleetReport": "degraded-fallback",
        "reportDate": "2026-08-05",
        "documentUrl": "https://www.deepwater.com/report.pdf",
        "sha256": "same-document",
        "fallbackStreak": MAX_CONSECUTIVE_FALLBACK_RUNS,
    }
    streak = _next_fallback_streak(
        "fallback-after-HTTPError",
        previous,
        report_date="2026-08-05",
        sha256="same-document",
    )
    assert streak == MAX_CONSECUTIVE_FALLBACK_RUNS + 1
    with pytest.raises(PipelineError, match="identical fallback report persisted"):
        _fleet_source_health(
            "Transocean",
            "2026-08-05",
            "fallback-after-HTTPError",
            "2026-08-05",
            as_of=date(2026, 8, 6),
            fallback_streak=streak,
        )


def test_valaris_prefers_stable_official_index_before_sec(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = next(item for item in SOURCE_SPECS if item.company == "Valaris")
    calls: list[str] = []

    def official(_client: object, _spec: object) -> str:
        calls.append("official")
        return "https://s23.q4cdn.com/report.pdf"

    def sec(_client: object) -> str:
        calls.append("sec")
        raise AssertionError("SEC should not be queried after official discovery succeeds")

    monkeypatch.setattr(source_module, "_discover_valaris_official", official)
    monkeypatch.setattr(source_module, "_discover_valaris_sec", sec)
    url, mode = source_module.discover_document(object(), spec)  # type: ignore[arg-type]
    assert (url, mode) == ("https://s23.q4cdn.com/report.pdf", "official-index")
    assert calls == ["official"]


def test_verified_history_restores_reported_rate_after_end_clip() -> None:
    original = assign_contract_ids(
        "rig-a",
        [
            {
                "startDate": "2025-01-01",
                "endDate": "2025-12-31",
                "dayRate": 450_000,
                "client": "Client",
                "region": "Region",
                "status": "Firm",
            }
        ],
    )[0]
    clipped = assign_contract_ids(
        "rig-a",
        [{**original, "endDate": "2025-11-30", "dayRate": 0}],
    )[0]
    assert clipped["id"] == original["id"]
    prior = {
        "sourceUrl": "https://www.deepwater.com/report.pdf",
        "sourceSha256": "abc",
        "rate_disclosure": "reported",
        "day_rate": 450_000,
    }
    assert _restore_verified_history_rate(clipped, prior) is True
    assert clipped["dayRate"] == 450_000


def test_news_union_preserves_prior_records_and_enrichment() -> None:
    prior = [
        {
            "company": "Transocean",
            "classification": "official-news-signal",
            "url": "https://investor.deepwater.com/news/one",
            "title": "Prior title",
            "vessels": ["Dhirubhai Deepwater KG2"],
            "facts": {"dayRateInferred": False},
        },
        {
            "company": "Transocean",
            "classification": "official-news-signal",
            "url": "https://investor.deepwater.com/news/two",
            "title": "Last-known event",
            "vessels": [],
        },
    ]
    partial = [
        {
            "company": "Transocean",
            "classification": "official-news-signal",
            "url": "https://investor.deepwater.com/news/one",
            "title": "Prior title",
            "vessels": [],
        }
    ]
    merged = _merge_news_events(prior, partial)
    assert len(merged) == 2
    enriched = next(event for event in merged if event["url"].endswith("/one"))
    assert enriched["facts"] == {"dayRateInferred": False}
    assert enriched["vessels"] == ["Dhirubhai Deepwater KG2"]


def test_only_post_report_news_is_pending() -> None:
    base = {
        "company": "Transocean",
        "classification": "official-news-signal",
        "autoApplied": False,
    }
    events = _annotate_event_review(
        [
            {**base, "publishedAt": "August 5, 2026"},
            {**base, "publishedAt": "2026-08-20"},
            {**base, "publishedAt": None},
            {
                "company": "Valaris",
                "classification": "letter-of-award",
                "autoApplied": False,
            },
        ],
        {"Transocean": "2026-08-05", "Valaris": "2026-08-05"},
    )
    assert [event["reviewStatus"] for event in events] == [
        "acknowledged",
        "pending",
        "date-unverified",
        "pending",
    ]
    assert sum(event["pendingReview"] for event in events) == 2


def test_warning_count_input_is_deduplicated_deterministically() -> None:
    assert _dedupe_warnings(["b", "a", "b"]) == ["a", "b"]


def test_obsolete_content_addressed_payloads_are_pruned(tmp_path) -> None:
    for name in (
        "fleet.keep.json",
        "events.keep.json",
        "fleet.old.json",
        "events.old.json",
        "manifest.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    _prune_obsolete_payloads(tmp_path, {"fleet.keep.json", "events.keep.json"})

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "events.keep.json",
        "fleet.keep.json",
        "manifest.json",
    ]


def test_kg2_review_facts_do_not_infer_dates_or_dayrate() -> None:
    text = (
        "Transocean announced a two-year binding Letter of Award for the "
        "Dhirubhai Deepwater KG2 with ONGC in India. The campaign is expected "
        "to commence in the first quarter of 2027 and contribute approximately "
        "$300 million, inclusive of additional services and mobilization fees. "
        "The contract includes two years of priced options that, if fully exercised, "
        "would result in the drillship working in India into early 2031."
    )
    facts = _extract_review_facts(
        "Transocean",
        "Transocean Ltd. Announces $300 Million Contract For Ultra-Deepwater Drillship",
        text,
    )
    assert facts == {
        "counterparty": "ONGC",
        "location": "India",
        "expectedStart": "Q1 2027",
        "startPrecision": "quarter",
        "awardType": "binding-letter-of-award",
        "awardTermYears": 2,
        "optionTermYears": 2,
        "announcedValueUsdApprox": 300_000_000,
        "valueIncludes": ["additional services", "mobilization fee"],
        "exactDatesInferred": False,
        "dayRateInferred": False,
        "optionEndIfFullyExercised": "early 2031",
        "dayRateDisclosure": "undisclosed",
    }
    # A changed release mentioning a rate must not be labelled undisclosed.
    updated = _extract_review_facts(
        "Transocean",
        "Transocean Ltd. Announces $300 Million Contract For Ultra-Deepwater Drillship",
        text + " A daily rate is now provided in the fleet report.",
    )
    assert updated["dayRateDisclosure"] == "not-extracted"
    assert "dayRateUsd" not in updated
    without_option_end = _extract_review_facts(
        "Transocean",
        "Transocean Ltd. Announces $300 Million Contract For Ultra-Deepwater Drillship",
        text.replace("into early 2031", "for a longer period"),
    )
    assert "optionEndIfFullyExercised" not in without_option_end


class _FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def request(self, method: str, url: str, **_: object) -> requests.Response:
        self.calls.append(url)
        return self.responses.pop(0)


def _response(url: str, status: int, body: bytes = b"", **headers: str) -> requests.Response:
    response = requests.Response()
    response.url = url
    response.status_code = status
    response.headers.update(headers)
    response._content = body
    response._content_consumed = True
    return response


def test_http_client_rejects_redirect_before_unallowlisted_request() -> None:
    client = HttpClient()
    session = _FakeSession(
        [
            _response(
                "https://www.deepwater.com/start",
                302,
                location="https://example.com/escape",
            )
        ]
    )
    client.session = session  # type: ignore[assignment]
    with pytest.raises(ValueError, match="not allowlisted"):
        client.get("https://www.deepwater.com/start")
    assert session.calls == ["https://www.deepwater.com/start"]


def test_http_client_stops_stream_at_hard_byte_cap() -> None:
    client = HttpClient()
    session = _FakeSession(
        [_response("https://www.deepwater.com/report.pdf", 200, b"123456")]
    )
    client.session = session  # type: ignore[assignment]
    with pytest.raises(ValueError, match="exceeds 5 bytes"):
        client._request(
            "GET",
            "https://www.deepwater.com/report.pdf",
            timeout=1,
            byte_limit=5,
        )
