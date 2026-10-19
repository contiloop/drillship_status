from __future__ import annotations

import copy

import pytest

from automation.fleet_sync.core import dump_json
from automation.fleet_sync.news_review import (
    REVIEW_PATH, REVIEW_VERSION, apply_changes, reconcile_reviews, validate_reading,
)
from automation.fleet_sync.pipeline import _merge_news_events


TEXT = "All remaining priced options have been exercised for Rig A, committing the rig through January 2030 in Angola."


def ship():
    return {"id": "rig-a", "name": "Rig A", "company": "Seadrill", "contracts": [{
        "id": "old-option", "vesselId": "rig-a", "startDate": "2027-03-01", "endDate": "2030-01-31",
        "client": "Sonangol", "region": "Angola", "status": "Option", "dayRate": 0,
    }]}


def item():
    return {"vessel": "Rig A", "needsDetails": True, "comparison": "옵션이 확정으로 전환됨",
            "facts": {"awardType": "option-exercised", "dayRateDisclosure": "undisclosed", "dayRateUsd": None},
            "evidence": [{"field": "awardType", "quote": TEXT}],
            "changes": [{"contractId": "old-option", "startDate": "2027-03-01", "endDate": "2030-01-31",
                         "startPrecision": "month", "endPrecision": "month", "dayRate": 0,
                         "client": "Sonangol", "region": "Angola", "status": "Firm", "quotes": [TEXT], "reason": "옵션 행사"}]}


def review():
    return {"version": REVIEW_VERSION, "company": "Seadrill", "title": "Extension", "url": "https://ir.seadrill.com/news/one",
            "publishedAt": "2026-10-12", "sourceSha256": "hash", "reader": {"model": "gpt-6-luna", "fast": False},
            "result": {"vessels": [item()]}}


def event():
    return {"company": "Seadrill", "title": "Extension", "url": review()["url"],
            "classification": "official-news-signal", "articleSha256": "hash", "pendingReview": True}


def ledger(root, r=None):
    dump_json(root / REVIEW_PATH, {"version": REVIEW_VERSION, "reviews": [r or review()]})


def test_exact_quotes_and_known_vessel_required():
    reading = {"vessels": [item()]}
    validate_reading(reading, TEXT, [ship()])
    reading["vessels"][0]["evidence"][0]["quote"] = "A made-up quotation about a contract"
    with pytest.raises(ValueError, match="verbatim"):
        validate_reading(reading, TEXT, [ship()])
    reading["vessels"][0]["vessel"] = "Foreign Rig"
    with pytest.raises(ValueError, match="unknown"):
        validate_reading(reading, TEXT, [ship()])


def test_inline_html_punctuation_spacing_is_not_a_fabricated_quote():
    validate_reading({"vessels": [item()]}, TEXT.replace("Rig A,", "Rig A ,"), [ship()])


def test_loa_cannot_become_firm():
    row = item()
    row["facts"]["awardType"] = "binding-letter-of-award"
    with pytest.raises(ValueError, match="LOA"):
        validate_reading({"vessels": [row]}, TEXT, [ship()])


def test_reported_rate_needs_explicit_source_not_backlog_ratio():
    row = item()
    row["facts"].update({"dayRateDisclosure": "reported", "dayRateUsd": 500000})
    row["evidence"].append({"field": "dayRateUsd", "quote": TEXT})
    with pytest.raises(ValueError, match="direct disclosure"):
        validate_reading({"vessels": [row]}, TEXT, [ship()])


def test_exercised_option_promoted_without_fabricated_rate():
    s = ship()
    assert apply_changes(s, item(), review()) == ["옵션 행사"]
    assert len(s["contracts"]) == 1
    assert s["contracts"][0]["status"] == "Firm"
    assert s["contracts"][0]["dayRate"] == 0
    assert s["contracts"][0]["datePrecision"]["end"] == "month"


def test_quarter_does_not_authorize_a_month_or_day():
    s, row = ship(), item()
    original = copy.deepcopy(s)
    row["changes"][0]["startDate"] = "2027-01-01"
    row["changes"][0]["quotes"] = ["Expected to commence in the first quarter of 2027."]
    with pytest.raises(ValueError, match="source evidence"):
        apply_changes(s, row, review())
    assert s == original


def test_month_precision_requires_month_boundary():
    s, row = ship(), item()
    row["changes"][0]["endDate"] = "2030-01-15"
    with pytest.raises(ValueError, match="source evidence"):
        apply_changes(s, row, review())


def test_news_replay_is_deterministic_and_records_source(tmp_path):
    ledger(tmp_path)
    a, b = [ship()], [ship()]
    ea, wa = reconcile_reviews(tmp_path, a, [event()], {"Seadrill": "2026-08-10"})
    eb, wb = reconcile_reviews(tmp_path, b, [event()], {"Seadrill": "2026-08-10"})
    assert (a, ea, wa) == (b, eb, wb)
    assert ea[0]["reviewStatus"] == "applied"
    assert not ea[0]["pendingReview"]
    assert a[0]["contracts"][0]["sourceUrl"] == review()["url"]


def test_new_fsr_supersedes_older_news_overlay(tmp_path):
    ledger(tmp_path)
    fleet = [ship()]
    original = copy.deepcopy(fleet)
    events, warnings = reconcile_reviews(tmp_path, fleet, [event()], {"Seadrill": "2026-11-01"})
    assert fleet == original
    assert events[0]["reviewStatus"] == "covered-by-report"
    assert not warnings


def test_changed_article_needs_rereading(tmp_path):
    ledger(tmp_path)
    fleet, e = [ship()], event()
    e["articleSha256"] = "new-hash"
    events, warnings = reconcile_reviews(tmp_path, fleet, [e], {"Seadrill": "2026-08-10"})
    assert fleet[0]["contracts"][0]["status"] == "Option"
    assert events[0]["pendingReview"]
    assert "re-reading" in warnings[0]


def test_temporary_article_error_preserves_last_verified_hash():
    before, current = event(), event()
    current.update({"articleSha256": None, "articleReadError": "HTTP 403"})
    merged = _merge_news_events([before], [current])
    assert merged[0]["articleSha256"] == "hash"
    assert merged[0]["articleReadError"] == "HTTP 403"


def test_detail_incomplete_is_not_pending_approval(tmp_path):
    r = review()
    r["result"]["vessels"][0]["changes"] = []
    ledger(tmp_path, r)
    events, warnings = reconcile_reviews(tmp_path, [ship()], [event()], {"Seadrill": "2026-08-10"})
    assert events[0]["reviewStatus"] == "details-incomplete"
    assert events[0]["pendingReview"] is False
    assert not warnings


def test_multiple_explicit_new_contracts_and_duplicates_are_checked_atomically():
    s, row = ship(), item()
    row["changes"] = []
    for year in (2031, 2032):
        row["changes"].append({"contractId": None, "startDate": f"{year}-01-01", "endDate": f"{year}-06-30",
                               "startPrecision": "month", "endPrecision": "month", "dayRate": 0,
                               "client": "Undisclosed", "region": "Angola", "status": "Firm",
                               "quotes": [f"A firm contract in Angola from January {year} through June {year}."], "reason": "새 계약"})
    apply_changes(s, row, review())
    assert len(s["contracts"]) == 3
    before = copy.deepcopy(s)
    with pytest.raises(ValueError, match="duplicate"):
        apply_changes(s, row, review())
    assert s == before


def test_unknown_dayrate_does_not_block_explicit_continuous_firm_extension():
    text = "Rig A is now expected to work in direct continuation through February 2031."
    s, row = ship(), item()
    s["contracts"][0]["status"] = "Firm"
    row["changes"] = []
    row["facts"]["expectedEnd"] = "February 2031"
    row["evidence"] = [{"field": "awardType", "quote": text}, {"field": "expectedEnd", "quote": text}]
    with pytest.raises(ValueError, match="Unknown tranche rates do not prevent"):
        validate_reading({"vessels": [row]}, text, [s])
