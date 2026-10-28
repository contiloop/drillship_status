from __future__ import annotations

import base64
from copy import deepcopy
import json
import math

import pytest

from automation.fleet_sync.reading_contracts import (
    DocumentValidationError, MalformedResponseError, READER_RESPONSE_SCHEMA, TITLE_TRIAGE_SCHEMA,
    derived_usd_per_day, duration_days, normalize_interpretation, parse_reading_response,
    select_triage_ids, stored_review_page, validate_news_queue, validate_reading_input,
    validate_reading_response,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def request(kind="fleet-report", **extra):
    baseline = [
        {"id": "rig-a", "company": "Seadrill", "name": "Rig A", "contracts": [
            {"id": "old-a", "vesselId": "rig-a", "client": "Client", "region": "Angola",
             "startDate": "2027-07-01", "endDate": "2028-06-30", "dayRate": 0, "status": "Firm"}]},
        {"id": "rig-b", "company": "Seadrill", "name": "Rig B", "contracts": []},
        {"id": "rig-x", "company": "Valaris", "name": "Rig X", "contracts": [
            {"id": "foreign", "vesselId": "rig-x", "client": "C", "region": "R",
             "startDate": "2027-01-01", "endDate": "2027-12-31", "dayRate": 1, "status": "Firm"}]},
    ]
    value = dict(sourceId="s1", company="Seadrill", kind=kind, url="https://example.com/report",
                 publishedAt="2026-10-01", baseline=baseline,
                 pages=[{"locator": "page:1", "text": "One year contract value $182.5m."}])
    value.update(extra)
    return value


def row(**patch):
    value = dict(startDate="2027-07-01", endDate="2028-06-30", dayRate=0, client="New client",
                 region="Angola", status="Firm", interpretation=dict(
                     sourceStartText="Mid 2027", sourceEndText=None, placementBasis="배치 근거",
                     awardType="signed-contract", termText="1년", optionText=None, valueText="$182.5m",
                     valueUsd=182_500_000, baseDurationDays=365, includes=[], excludes=[]))
    value.update(patch)
    return value


def decision(**patch):
    value = dict(vesselId="rig-a", contractId="old-a", action="upsert", row=row(),
                 reasonKo="공시 근거를 반영", quote="One year contract value $182.5m.", locator="page:1")
    value.update(patch)
    return value


def update(**patch):
    value = dict(vesselId="rig-b", status="Idle", statusAsOf="2026-10-01")
    value.update(patch)
    return value


def response(decisions=None, updates=None):
    return dict(reportDate="2026-10-01", vesselUpdates=updates if updates is not None else [update()],
                decisions=decisions if decisions is not None else [decision()])


# --- valid responses -------------------------------------------------------

def test_valid_response_passes_and_carries_derived_rate():
    result = validate_reading_response(response(), request())
    assert result.issues == []
    assert result.report_date == "2026-10-01"
    assert len(result.vessel_updates) == 1
    validated = result.decisions[0]
    assert validated["row"]["interpretation"]["derivedUsdPerDay"] == 500_000
    assert validated["reasonKo"] == "공시 근거를 반영"


def test_missing_reason_quote_locator_and_interpretation_still_pass():
    item = decision(row=row(interpretation=None))
    for key in ("reasonKo", "quote", "locator"):
        item.pop(key)
    no_interp = row()
    no_interp.pop("interpretation")
    other = decision(contractId=None, row=no_interp)
    for key in ("reasonKo", "quote", "locator"):
        other.pop(key)
    result = validate_reading_response(response([item, other], [update()]), request())
    assert result.issues == []
    assert len(result.decisions) == 2
    assert result.decisions[0]["row"]["interpretation"]["derivedUsdPerDay"] is None


def test_mismatched_quote_and_unknown_locator_do_not_hold():
    item = decision(quote="text that is nowhere in the source", locator="page:99")
    result = validate_reading_response(response([item]), request())
    assert result.issues == []
    assert result.decisions[0]["quote"] == "text that is nowhere in the source"


def test_envelope_fields_are_tolerated_but_not_required():
    value = response()
    value.update(schemaVersion=2, sourceId="other", sourceSha256="0" * 64, company="Seadrill",
                 readLocators=[], coverage=[])
    assert validate_reading_response(value, request()).issues == []
    value = response()
    value.pop("reportDate")
    assert validate_reading_response(value, request()).report_date is None


def test_non_upsert_actions_and_nullable_contract_id():
    items = [decision(action="no-change", row=None), decision(action="hold", row=None, contractId=None),
             decision(action="withdraw", row=None), decision(action="no-change", row=row())]
    result = validate_reading_response(response(items), request())
    assert result.issues == []
    assert [item["action"] for item in result.decisions] == ["no-change", "hold", "withdraw", "no-change"]
    assert result.decisions[3]["row"] is None


# --- per-item holds --------------------------------------------------------

BAD_DECISIONS = {
    "unregistered vessel": decision(vesselId="rig-zz"),
    "other company vessel": decision(vesselId="rig-x", contractId=None),
    "foreign contractId": decision(contractId="foreign"),
    "unknown contractId": decision(contractId="nope"),
    "withdraw without id": decision(action="withdraw", contractId=None, row=None),
    "upsert without row": decision(row=None),
    "bad action": decision(action="delete"),
    "reversed dates": decision(row=row(startDate="2028-07-01", endDate="2028-06-30")),
    "impossible date": decision(row=row(startDate="2027-02-30")),
    "date format": decision(row=row(startDate="2027/07/01")),
    "date with time": decision(row=row(endDate="2028-06-30T00:00:00")),
    "negative rate": decision(row=row(dayRate=-1)),
    "infinite rate": decision(row=row(dayRate=math.inf)),
    "nan rate": decision(row=row(dayRate=math.nan)),
    "bool rate": decision(row=row(dayRate=True)),
    "string rate": decision(row=row(dayRate="100")),
    "negative value": decision(row=row(interpretation={"valueUsd": -5})),
    "zero days": decision(row=row(interpretation={"baseDurationDays": 0})),
    "infinite days": decision(row=row(interpretation={"baseDurationDays": math.inf})),
    "bad award": decision(row=row(interpretation={"awardType": "handshake"})),
    "bad status": decision(row=row(status="Probable")),
    "empty client": decision(row=row(client=" ")),
    "missing region": decision(row={k: v for k, v in row().items() if k != "region"}),
    "not object": "upsert rig-a",
}


@pytest.mark.parametrize("name", sorted(BAD_DECISIONS))
def test_bad_decision_is_held_alone(name):
    good = decision(action="no-change", row=None)
    result = validate_reading_response(response([good, BAD_DECISIONS[name]], [update()]), request())
    assert [item["action"] for item in result.decisions] == ["no-change"]
    assert len(result.vessel_updates) == 1
    assert [(issue.collection, issue.index) for issue in result.issues] == [("decisions", 1)]
    assert result.issues[0].message
    assert result.issues[0].item == BAD_DECISIONS[name]


BAD_UPDATES = {
    "unregistered vessel": update(vesselId="rig-zz"),
    "other company": update(vesselId="rig-x"),
    "bad status": update(status="Sold"),
    "bad date": update(statusAsOf="2026-13-01"),
    "missing date": {"vesselId": "rig-b", "status": "Idle"},
}


@pytest.mark.parametrize("name", sorted(BAD_UPDATES))
def test_bad_vessel_update_is_held_alone(name):
    result = validate_reading_response(response(None, [BAD_UPDATES[name], update(vesselId="rig-a")]), request())
    assert [item["vesselId"] for item in result.vessel_updates] == ["rig-a"]
    assert len(result.decisions) == 1
    assert [(issue.collection, issue.index) for issue in result.issues] == [("vesselUpdates", 0)]


def test_invalid_report_date_is_dropped_without_holding_items():
    value = response()
    value["reportDate"] = "2026-02-31"
    result = validate_reading_response(value, request())
    assert result.report_date is None
    assert len(result.decisions) == 1 and len(result.vessel_updates) == 1
    assert [issue.collection for issue in result.issues] == ["reportDate"]


# --- malformed responses ---------------------------------------------------

@pytest.mark.parametrize("value", [None, [], "text", {"decisions": []}, {"vesselUpdates": []},
                                   {"decisions": {}, "vesselUpdates": []}])
def test_malformed_response_requires_rerequest(value):
    with pytest.raises(MalformedResponseError):
        validate_reading_response(value, request())
    assert issubclass(MalformedResponseError, DocumentValidationError)


def test_parse_reading_response_rejects_non_json():
    with pytest.raises(MalformedResponseError):
        parse_reading_response("not json {")
    with pytest.raises(MalformedResponseError):
        parse_reading_response('{"decisions": [], "vesselUpdates": [], "x": NaN}')
    assert parse_reading_response(json.dumps(response()))["decisions"]


# --- derived rate ----------------------------------------------------------

def test_model_supplied_derived_rate_is_replaced():
    interp = dict(row()["interpretation"], derivedUsdPerDay=1)
    result = validate_reading_response(response([decision(row=row(interpretation=interp))]), request())
    assert result.decisions[0]["row"]["interpretation"]["derivedUsdPerDay"] == 500_000


@pytest.mark.parametrize("amount,days", [(None, 365), (182_500_000, None), (None, None)])
def test_missing_amount_or_days_gives_no_derived_rate(amount, days):
    interp = dict(row()["interpretation"], valueUsd=amount, baseDurationDays=days, derivedUsdPerDay=123)
    result = validate_reading_response(response([decision(row=row(interpretation=interp))]), request())
    assert result.decisions[0]["row"]["interpretation"]["derivedUsdPerDay"] is None
    assert derived_usd_per_day(amount, days) is None


def test_derived_rate_and_duration_helpers():
    assert derived_usd_per_day(182_500_000, 365) == 500_000
    assert derived_usd_per_day(1, 0) is None
    assert duration_days(years=1) == 365
    assert duration_days(months=6) == pytest.approx(182.5)
    with pytest.raises(ValueError):
        duration_days(years=-1)


def test_legacy_public_interpretation_is_accepted():
    legacy = dict(sourceStartText="Q4 2026", sourceEndText=None, startPrecision="quarter", endPrecision="unknown",
                  dateBasis="placement", placementBasis="배치", awardType="binding-letter-of-award", termText=None,
                  optionText=None, valueText="$100m", rateBasis="backlog-derived", reportedUsdPerDay=None,
                  derivedUsdPerDay=250_000, valueUsd=100_000_000, baseDurationDays=400,
                  durationAssumption="1 year = 365 days", maximumTerm=False, includes=[], excludes=[])
    value = normalize_interpretation(deepcopy(legacy))
    assert value["dateBasis"] == "placement" and value["rateBasis"] == "backlog-derived"
    assert value["derivedUsdPerDay"] == 250_000
    assert normalize_interpretation(None)["derivedUsdPerDay"] is None


# --- wire schemas ----------------------------------------------------------

def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


@pytest.mark.parametrize("schema", [READER_RESPONSE_SCHEMA, TITLE_TRIAGE_SCHEMA])
def test_wire_schema_uses_structured_output_subset(schema):
    for path, node in _walk(schema):
        assert not {"uniqueItems", "minLength", "maxLength", "const", "$schema"} & set(node), path
        if node.get("type") == "object" or (isinstance(node.get("type"), list) and "object" in node["type"]):
            assert node["additionalProperties"] is False, path
            assert sorted(node["required"]) == sorted(node["properties"]), path


def test_wire_schema_shape():
    props = READER_RESPONSE_SCHEMA["properties"]
    assert set(props) == {"reportDate", "vesselUpdates", "decisions"}
    decision_props = props["decisions"]["items"]["properties"]
    assert set(decision_props) == {"vesselId", "contractId", "action", "row", "reasonKo", "quote", "locator"}
    for key in ("reasonKo", "quote", "locator", "contractId"):
        assert "null" in decision_props[key]["type"]
    row_schema = decision_props["row"]["anyOf"][0]
    interp = row_schema["properties"]["interpretation"]
    assert "derivedUsdPerDay" not in interp["properties"]
    assert "null" in interp["properties"]["awardType"]["type"]


def test_wire_schema_is_accepted_by_reading_service_translator():
    from automation.fleet_sync.reading_service import structured_output_schema
    assert structured_output_schema(READER_RESPONSE_SCHEMA) == READER_RESPONSE_SCHEMA


def test_triage_returns_only_known_ids_in_input_order():
    assert select_triage_ids({"selected": ["b", "zzz", "a", "b", 3]}, ["a", "b", "c"]) == ["a", "b"]
    assert select_triage_ids({"selected": []}, ["a"]) == []
    with pytest.raises(MalformedResponseError):
        select_triage_ids({"chosen": ["a"]}, ["a"])
    with pytest.raises(MalformedResponseError):
        select_triage_ids(["a"], ["a"])


# --- reading input ---------------------------------------------------------

def test_reading_input_basic_shape_and_no_hashes_required():
    validate_reading_input(request())
    validate_reading_input(request(openHolds=[{"contractId": "old-a"}], inputMode="document"),
                           reader_policy="ignored")
    # Old hash envelope fields may still be present and are not verified.
    validate_reading_input(request(sourceSha256="0" * 64, contextSha256="1" * 64, readerPolicySha256="x"))


@pytest.mark.parametrize("patch", [
    {"company": "Other"}, {"kind": "blog"}, {"sourceId": ""}, {"url": ""}, {"pages": []},
    {"pages": [{"locator": "p", "text": "a"}, {"locator": "p", "text": "b"}]},
    {"pages": [{"locator": "", "text": "a"}]}, {"baseline": {}}, {"inputMode": "other"},
    {"openHolds": "none"}, {"publishedAt": 3},
])
def test_reading_input_rejects_bad_shape(patch):
    with pytest.raises(DocumentValidationError):
        validate_reading_input(dict(request(), **patch))


def test_page_images_need_base64_png_signature_and_size_cap():
    good = {"mimeType": "image/png", "dataBase64": base64.b64encode(PNG).decode()}
    validate_reading_input(request(pages=[{"locator": "page:1", "text": "", "image": good}]))
    for image in ({"mimeType": "image/png", "dataBase64": "@@@"},
                  {"mimeType": "image/png", "dataBase64": base64.b64encode(b"GIF89a....").decode()},
                  {"mimeType": "image/jpeg", "dataBase64": good["dataBase64"]},
                  {"mimeType": "image/png", "dataBase64": base64.b64encode(PNG + b"\x00" * (6 * 1024 * 1024)).decode()}):
        with pytest.raises(DocumentValidationError):
            validate_reading_input(request(pages=[{"locator": "page:1", "text": "", "image": image}]))


def test_stored_result_mode_is_one_json_page_without_hash_binding():
    record = {"company": "Seadrill", "url": "https://example.com/report", "result": {"decisions": []}}
    page = stored_review_page(record)
    assert json.loads(page["text"]) == record
    validate_reading_input(request(inputMode="stored-result", pages=[page]))
    for pages in ([page, dict(page, locator="other")], [{"locator": "x", "text": "not json"}],
                  [dict(page, image={"mimeType": "image/png", "dataBase64": base64.b64encode(PNG).decode()})]):
        with pytest.raises(DocumentValidationError):
            validate_reading_input(request(inputMode="stored-result", pages=pages))


# --- queue -----------------------------------------------------------------

def job(job_id="j1", **patch):
    value = dict(jobId=job_id, kind="official-news", company="Noble", url="https://example.com/n",
                 documentSha256="a" * 64)
    value.update(patch)
    return value


def test_news_queue_validation():
    validate_news_queue({"schemaVersion": 2, "pendingCount": 2, "fingerprint": "f" * 64,
                         "jobs": [job("j1"), job("j2", status="pending")]})
    bad = [
        {"schemaVersion": 1, "pendingCount": 0, "jobs": []},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": []},
        {"schemaVersion": 2, "pendingCount": 2, "jobs": [job("j1"), job("j1")]},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": [{k: v for k, v in job().items() if k != "documentSha256"}]},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": [job(company="Other")]},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": [job(kind="blog")]},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": [job(url="")]},
        {"schemaVersion": 2, "pendingCount": 1, "jobs": [job(documentSha256="xyz")]},
    ]
    for queue in bad:
        with pytest.raises(DocumentValidationError):
            validate_news_queue(queue)
