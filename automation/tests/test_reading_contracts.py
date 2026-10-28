from __future__ import annotations

from copy import deepcopy
import math

import pytest

from automation.fleet_sync.reading_contracts import (
    DocumentValidationError, READER_RESPONSE_SCHEMA, context_sha256, policy_sha256,
    source_sha256, validate_reading_input, validate_reading_response, validate_news_queue,
)


def interpretation():
    return dict(sourceStartText="Mid 2027", sourceEndText="2028", startPrecision="approximate",
                endPrecision="year", dateBasis="placement", placementBasis="공시와 기존 계약을 연결한 배치",
                awardType="signed-contract", termText="1년", optionText=None, valueText="$182.5m",
                rateBasis="backlog-derived", reportedUsdPerDay=None, derivedUsdPerDay=500000,
                valueUsd=182500000, baseDurationDays=365, durationAssumption="1 year = 365 days; 1 month = 365/12 days",
                maximumTerm=False, includes=[], excludes=[])


def request(kind="fleet-report"):
    pages = [{"locator": "page:1", "text": "Mid 2027 through 2028; one year contract value $182.5m."},
             {"locator": "page:2", "text": "Rig A is Active as of 2026-10-01."}]
    baseline = [{"id": "rig-a", "company": "Seadrill", "name": "Rig A", "contracts": [
        {"id": "old-a", "vesselId": "rig-a", "client": "Client", "region": "Angola", "startDate": "2027-07-01",
         "endDate": "2028-06-30", "dayRate": 0, "status": "Firm"}]},
        {"id": "rig-b", "company": "Seadrill", "name": "Rig B", "contracts": []}]
    return dict(sourceId="s1", sourceSha256=source_sha256(pages), company="Seadrill", kind=kind,
                url="https://example.com/report", publishedAt="2026-10-01", pages=pages,
                baseline=baseline, priorEvidence=[], contextSha256=context_sha256(baseline, []),
                readerPolicySha256=policy_sha256("policy"))


def evidence(fields):
    return [dict(fields=fields, sourceId="s1", locator="page:1", quote="Mid 2027 through 2028; one year contract value $182.5m.")]


def decision():
    return dict(vesselId="rig-a", contractId="old-a", action="upsert", row={
        "id": "old-a", "vesselId": "rig-a", "startDate": "2027-07-01", "endDate": "2028-06-30", "dayRate": 0,
        "client": "New client", "region": "Angola", "status": "Firm", "interpretation": interpretation()},
        facts={"client": "New client", "region": "Angola", "interpretation": interpretation()},
        reasonKo="공시 근거를 반영", evidence=evidence(["client", "region", "interpretation"]))


def response(req=None):
    req = req or request()
    return dict(schemaVersion=2, sourceId=req["sourceId"], sourceSha256=req["sourceSha256"], company=req["company"],
                reportDate="2026-10-01", readLocators=["page:1", "page:2"],
                coverage=[{"vesselId": "rig-a", "state": "included"}, {"vesselId": "rig-b", "state": "not-mentioned"}],
                vesselUpdates=[], decisions=[decision()])


def test_valid_contract_preserves_raw_dates_and_derived_rate():
    req = request()
    validate_reading_input(req, reader_policy="policy")
    result = validate_reading_response(response(req), req)
    assert result.decisions[0]["row"]["dayRate"] == 0
    assert result.decisions[0]["row"]["interpretation"]["derivedUsdPerDay"] == 500000
    assert result.issues == []
    assert READER_RESPONSE_SCHEMA["properties"]["schemaVersion"]["const"] == 2


@pytest.mark.parametrize("field", ["sourceSha256", "contextSha256", "readerPolicySha256"])
def test_invalid_input_hashes(field):
    req = request()
    req[field] = "0" * 64
    with pytest.raises(DocumentValidationError, match=field):
        validate_reading_input(req, reader_policy="policy")


def test_source_hash_covers_page_locations_and_all_text():
    req = request()
    changed = deepcopy(req["pages"])
    changed[0]["locator"] = "page:3"
    assert source_sha256(changed) != req["sourceSha256"]
    req["pages"][1]["text"] += " missing input"
    with pytest.raises(DocumentValidationError):
        validate_reading_response(response(req), req)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(sourceSha256="0" * 64),
    lambda r: r.update(readLocators=["page:1"]),
    lambda r: r.update(coverage=r["coverage"][:1]),
    lambda r: r["coverage"].append(r["coverage"][0]),
    lambda r: r.update(reportDate="2026-02-30"),
])
def test_envelope_failures_fail_document(mutate):
    req = request()
    result = response(req)
    mutate(result)
    with pytest.raises(DocumentValidationError):
        validate_reading_response(result, req)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(vesselId="unknown"),
    lambda d: d.update(contractId="unknown"),
    lambda d: d["row"].update(endDate="2026-01-01"),
    lambda d: d["row"].update(dayRate=-1),
    lambda d: d["row"].update(dayRate=math.inf),
    lambda d: d["row"].pop("dayRate"),
    lambda d: d["evidence"][0].update(quote="fabricated"),
    lambda d: d["evidence"][0].update(locator="page:100"),
    lambda d: d["evidence"][0].update(sourceId="other"),
    lambda d: d["evidence"][0].update(fields=["region"]),
    lambda d: d["row"]["interpretation"].update(derivedUsdPerDay=600000),
    lambda d: d["row"]["interpretation"].update(maximumTerm=True),
    lambda d: d["row"]["interpretation"].update(dateBasis="unresolved"),
    lambda d: d["row"].update(id="other"),
    lambda d: d.update(action="withdraw", contractId=None, row=None),
])
def test_individual_invalid_decisions_do_not_block_independent_ones(mutate):
    req = request()
    invalid = decision()
    mutate(invalid)
    valid = decision()
    valid.update(vesselId="rig-b", contractId=None)
    valid["row"].update(id=None, vesselId="rig-b")
    valid["evidence"] = evidence(["startDate", "endDate", "dayRate", "client", "region", "status", "interpretation"])
    out = response(req)
    out["decisions"] = [invalid, valid]
    result = validate_reading_response(out, req)
    assert result.decisions == [valid]
    assert len(result.issues) == 1
    assert result.issues[0].index == 0


def test_foreign_company_and_wrong_vessel_contract_refs_are_rejected():
    req = request()
    req["baseline"].append({"id": "foreign", "company": "Valaris", "contracts": [{"id": "foreign-contract", "vesselId": "foreign"}]})
    req["contextSha256"] = context_sha256(req["baseline"], [])
    out = response(req)
    out["decisions"][0]["contractId"] = "foreign-contract"
    assert len(validate_reading_response(out, req).issues) == 1
    req["baseline"][0]["contracts"][0]["vesselId"] = "rig-b"
    req["contextSha256"] = context_sha256(req["baseline"], [])
    with pytest.raises(DocumentValidationError):
        validate_reading_input(req)


def test_hold_with_unresolved_facts_and_no_absence_quote_is_valid():
    req = request("official-news")
    out = response(req)
    out["coverage"] = []
    d = decision()
    d.update(action="hold", row=None, evidence=[])
    d["facts"] = {"client": None, "region": None, "interpretation": interpretation()}
    d["facts"]["interpretation"].update(dateBasis="unresolved", sourceStartText=None, sourceEndText=None,
        placementBasis=None, startPrecision="unknown", endPrecision="unknown", termText=None, valueText=None, rateBasis="undisclosed", derivedUsdPerDay=None,
        valueUsd=None, baseDurationDays=None, durationAssumption=None, awardType="unreported")
    d["reasonKo"] = "전체 본문에 계약 정보가 없어 보류"
    out["decisions"] = [d]
    assert validate_reading_response(out, req).issues == []
    out["decisions"] = []
    assert validate_reading_response(out, req).decisions == []


def test_duplicate_contract_decisions_are_isolated():
    req = request()
    out = response(req)
    out["decisions"].append(deepcopy(out["decisions"][0]))
    result = validate_reading_response(out, req)
    assert not result.decisions
    assert len(result.issues) == 2


def test_status_changes_require_field_evidence_and_valid_as_of():
    req = request()
    out = response(req)
    update = dict(vesselId="rig-a", status="Active", statusAsOf="2026-10-01", reasonKo="상태 공시",
                  evidence=[dict(fields=["status", "statusAsOf"], sourceId="s1", locator="page:2", quote="Rig A is Active as of 2026-10-01.")])
    out["vesselUpdates"] = [update]
    assert validate_reading_response(out, req).vessel_updates == [update]
    update["evidence"][0]["fields"] = ["status"]
    assert len(validate_reading_response(out, req).issues) == 1


def test_news_queue_identity_and_hash_integrity():
    job = dict(jobId="job-1", kind="official-news", company="Seadrill", url="https://example.com/news",
               sourceSha256="a" * 64, contextSha256="b" * 64, readerPolicySha256="c" * 64)
    queue = dict(schemaVersion=2, pendingCount=1, fingerprint="d" * 64, jobs=[job])
    validate_news_queue(queue)
    queue["jobs"].append(job)
    queue["pendingCount"] = 2
    with pytest.raises(DocumentValidationError):
        validate_news_queue(queue)


@pytest.mark.parametrize("field,value", [("valueUsd", None), ("baseDurationDays", None),
    ("baseDurationDays", 0), ("derivedUsdPerDay", None), ("reportedUsdPerDay", math.nan)])
def test_incomplete_or_nonfinite_rate_metadata_is_isolated(field, value):
    req = request()
    out = response(req)
    out["decisions"][0]["row"]["interpretation"][field] = value
    result = validate_reading_response(out, req)
    assert not result.decisions
    assert len(result.issues) == 1


def test_existing_source_metadata_is_retained_without_new_required_fields():
    from automation.fleet_sync.model import SourceDocument
    req = request()
    out = response(req)
    row = out["decisions"][0]["row"]
    row.update(sourceUrl=req["url"], sourcePublishedAt="2026-10-01", newsReviewed=True,
               datePrecision={"start": "approximate", "end": "year"})
    assert validate_reading_response(out, req).decisions[0]["row"] == row
    document = SourceDocument("Seadrill", "index", "doc", "2026-10-01", "abc", 1, "today", "v1", "test", "text/html")
    assert document.document_sha256 == document.sha256 == "abc"


@pytest.mark.parametrize("award", ["letter-of-award", "binding-letter-of-award"])
def test_loa_enum_cannot_be_promoted_to_firm(award):
    req = request()
    out = response(req)
    out["decisions"][0]["row"]["interpretation"]["awardType"] = award
    assert len(validate_reading_response(out, req).issues) == 1


def test_withdrawal_requires_existing_reference_and_action_evidence():
    req = request()
    out = response(req)
    d = out["decisions"][0]
    d.update(action="withdraw", row=None)
    d["evidence"] = evidence(["client", "region", "interpretation", "action"])
    assert validate_reading_response(out, req).decisions == [d]
    d["evidence"] = []
    assert len(validate_reading_response(out, req).issues) == 1


def test_malformed_items_and_missing_arrays_are_not_silently_accepted():
    req = request()
    out = response(req)
    out["decisions"] = [None, {"contractId": []}]
    result = validate_reading_response(out, req)
    assert len(result.issues) == 2
    out.pop("decisions")
    with pytest.raises(DocumentValidationError):
        validate_reading_response(out, req)


def test_numeric_duration_conversion_uses_declared_assumptions():
    from automation.fleet_sync.reading_contracts import duration_days
    assert duration_days(years=1) == 365
    assert duration_days(months=12) == 365
    assert duration_days(years=1, months=6, days=2) == 549.5
    with pytest.raises(ValueError):
        duration_days(months=math.inf)


def test_every_decoder_object_has_fixed_required_and_no_extra_properties():
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(READER_RESPONSE_SCHEMA)

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(READER_RESPONSE_SCHEMA)
    req = request()
    out = response(req)
    out["decisions"][0]["row"].update(newsReviewed=None, sourceUrl=None, sourcePublishedAt=None, datePrecision=None)
    assert validate_reading_response(out, req).issues == []


@pytest.mark.parametrize("timestamp,valid", [("2026-10-01T12:30:00Z", True),
    ("2026-10-01T12:30:00+09:00", True), ("2026-10-01", True),
    ("not-an-iso-date", False), ("2026-02-30T12:30:00Z", False)])
def test_input_published_at_requires_real_iso_date_or_timestamp(timestamp, valid):
    req = request()
    req["publishedAt"] = timestamp
    if valid:
        validate_reading_input(req)
    else:
        with pytest.raises(DocumentValidationError):
            validate_reading_input(req)
