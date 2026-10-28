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
    lambda d: d["row"].update(sourceUrl="https://fabricated.example/report"),
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
    req = request("official-news")
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


@pytest.mark.parametrize("metadata", [
    {"sourceUrl": "https://untrusted.example/fabricated"},
    {"sourcePublishedAt": "2000-01-01"},
    {"datePrecision": {"start": "day", "end": "day"}},
    {"newsReviewed": True},
])
def test_model_cannot_invent_source_metadata_even_with_an_existing_quote(metadata):
    req = request()
    out = response(req)
    out["decisions"][0]["row"].update(metadata)
    result = validate_reading_response(out, req)
    assert result.decisions == []
    assert len(result.issues) == 1


@pytest.mark.parametrize("fields", [
    ["client.nonexistent", "region.nonexistent", "interpretation.sourceStartText"],
    ["client", "region", "interpretation.sourceStartText"],
    ["client", "region", "interpretation.nonexistent"],
    ["client", "region", "row"],
])
def test_evidence_does_not_expand_partial_or_nonexistent_paths(fields):
    req = request()
    out = response(req)
    out["decisions"][0]["evidence"] = evidence(fields)
    result = validate_reading_response(out, req)
    assert result.decisions == []
    assert len(result.issues) == 1


def test_all_exact_interpretation_leaf_paths_can_support_their_own_changes():
    req = request()
    out = response(req)
    interp = out["decisions"][0]["facts"]["interpretation"]
    fields = ["client", "region"] + ["interpretation." + key for key, value in interp.items()
                                    if value is not None and value is not False and value != []]
    out["decisions"][0]["evidence"] = evidence(fields)
    assert validate_reading_response(out, req).issues == []


def test_fact_citation_cannot_support_a_different_row_claim():
    req = request()
    out = response(req)
    d = out["decisions"][0]
    d["row"]["client"] = "Different row client"
    d["evidence"] = evidence(["facts.client", "region", "interpretation"])
    assert len(validate_reading_response(out, req).issues) == 1
    d["evidence"][0]["fields"].append("row.client")
    assert validate_reading_response(out, req).issues == []


def test_nullable_and_verified_metadata_need_no_fabricated_metadata_quotes():
    req = request("official-news")
    out = response(req)
    row = out["decisions"][0]["row"]
    row.update(sourceUrl=req["url"], sourcePublishedAt=req["publishedAt"], newsReviewed=True,
               datePrecision={"start": row["interpretation"]["startPrecision"], "end": row["interpretation"]["endPrecision"]})
    assert validate_reading_response(out, req).issues == []
    row.update(sourceUrl=None, sourcePublishedAt=None, newsReviewed=None, datePrecision=None)
    assert validate_reading_response(out, req).issues == []


def test_original_source_metadata_can_be_preserved_from_the_baseline():
    req = request()
    metadata = dict(sourceUrl="https://example.com/prior-report", sourcePublishedAt="2025-01-01",
                    newsReviewed=True, datePrecision={"start": "month", "end": "month"})
    req["baseline"][0]["contracts"][0].update(metadata)
    req["contextSha256"] = context_sha256(req["baseline"], req["priorEvidence"])
    out = response(req)
    out["decisions"][0]["row"].update(metadata)
    assert validate_reading_response(out, req).issues == []


def test_missing_interpretation_leaf_citation_does_not_borrow_its_sibling_quote():
    req = request()
    out = response(req)
    interp = out["decisions"][0]["facts"]["interpretation"]
    fields = ["client", "region"] + ["interpretation." + key for key, value in interp.items()
                                    if value is not None and value is not False and value != []]
    fields.remove("interpretation.valueUsd")
    out["decisions"][0]["evidence"] = evidence(fields)
    result = validate_reading_response(out, req)
    assert len(result.issues) == 1
    assert "interpretation.valueUsd" in result.issues[0].message


def test_bare_field_alias_is_rejected_when_facts_and_row_values_differ():
    req = request()
    out = response(req)
    out["decisions"][0]["row"]["client"] = "Different row client"
    result = validate_reading_response(out, req)
    assert len(result.issues) == 1
    assert "ambiguous evidence field" in result.issues[0].message


def test_undisclosed_public_zero_needs_no_absence_quote():
    req = request()
    req["baseline"][0]["contracts"][0]["dayRate"] = 500000
    req["contextSha256"] = context_sha256(req["baseline"], req["priorEvidence"])
    out = response(req)
    d = out["decisions"][0]
    for interp in (d["facts"]["interpretation"], d["row"]["interpretation"]):
        interp.update(rateBasis="undisclosed", reportedUsdPerDay=None, derivedUsdPerDay=None,
                      valueUsd=None, baseDurationDays=None, durationAssumption=None)
    assert "dayRate" not in d["evidence"][0]["fields"]
    assert validate_reading_response(out, req).issues == []


def png_image(size=(2, 2)):
    import base64
    import hashlib
    from io import BytesIO
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    content = output.getvalue()
    return {"mimeType": "image/png", "sha256": hashlib.sha256(content).hexdigest(),
            "dataBase64": base64.b64encode(content).decode("ascii")}


def image_request(*, text="", image=None):
    req = request("official-news")
    req["pages"][0] = {"locator": "page:1", "text": text, "image": png_image() if image is None else image}
    req["sourceSha256"] = source_sha256(req["pages"])
    return req


def test_image_only_page_is_valid_and_needs_no_decoration_quote():
    req = image_request()
    validate_reading_input(req)
    out = response(req)
    out.update(decisions=[], coverage=[])
    assert validate_reading_response(out, req).issues == []
    out["readLocators"] = ["page:2"]
    with pytest.raises(DocumentValidationError, match="readLocators"):
        validate_reading_response(out, req)


@pytest.mark.parametrize("text", ["", "  \n\t"])
def test_empty_page_cannot_claim_reading_without_attached_image(text):
    req = request()
    req["pages"][0]["text"] = text
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError, match="text|image"):
        validate_reading_input(req)


def test_image_content_and_metadata_are_part_of_the_source_hash():
    req = image_request()
    original = req["sourceSha256"]
    changed = deepcopy(req["pages"])
    changed[0]["image"] = png_image((3, 3))
    assert source_sha256(changed) != original
    req["pages"] = changed
    with pytest.raises(DocumentValidationError, match="sourceSha256"):
        validate_reading_input(req)


@pytest.mark.parametrize("change", [
    lambda image: image.update(dataBase64="not base64!"),
    lambda image: image.update(dataBase64=image["dataBase64"] + "\n"),
    lambda image: image.update(dataBase64="data:image/png;base64," + image["dataBase64"]),
    lambda image: image.update(sha256="0" * 64),
    lambda image: image.update(mimeType="image/jpeg"),
])
def test_invalid_image_transport_or_digest_fails_document(change):
    image = png_image()
    change(image)
    with pytest.raises(DocumentValidationError):
        validate_reading_input(image_request(image=image))


@pytest.mark.parametrize("content", [b"not a PNG", b"\x89PNG\r\n\x1a\n", b"\x89PNG\r\n\x1a\ntruncated"])
def test_self_consistent_hash_does_not_make_broken_png_valid(content):
    import base64
    import hashlib
    image = {"mimeType": "image/png", "sha256": hashlib.sha256(content).hexdigest(),
             "dataBase64": base64.b64encode(content).decode("ascii")}
    with pytest.raises(DocumentValidationError):
        validate_reading_input(image_request(image=image))


def test_per_page_encoded_image_limit_precedes_decode(monkeypatch):
    from automation.fleet_sync import reading_contracts as contracts
    monkeypatch.setattr(contracts, "MAX_PAGE_IMAGE_BYTES", 1)
    with pytest.raises(DocumentValidationError, match="limit"):
        validate_reading_input(image_request())


def test_document_total_image_limit_counts_every_attached_page(monkeypatch):
    import base64
    from automation.fleet_sync import reading_contracts as contracts
    image = png_image()
    image_size = len(base64.b64decode(image["dataBase64"]))
    monkeypatch.setattr(contracts, "MAX_DOCUMENT_IMAGE_BYTES", image_size * 2 - 1)
    req = image_request(image=image)
    req["pages"][1]["image"] = deepcopy(image)
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError, match="document.*limit"):
        validate_reading_input(req)


def test_png_pixel_limit_precedes_full_pixel_decode(monkeypatch):
    from automation.fleet_sync import reading_contracts as contracts
    monkeypatch.setattr(contracts, "MAX_PAGE_IMAGE_PIXELS", 3)
    with pytest.raises(DocumentValidationError, match="pixel.*limit"):
        validate_reading_input(image_request(image=png_image((2, 2))))


def test_ocr_text_keeps_exact_quote_boundary_with_attached_image():
    req = image_request(text=request()["pages"][0]["text"])
    assert validate_reading_response(response(req), req).issues == []
    req["pages"][0]["text"] = ""
    req["sourceSha256"] = source_sha256(req["pages"])
    out = response(req)
    assert len(validate_reading_response(out, req).issues) == 1


@pytest.mark.parametrize("damage", ["missing-iend-crc", "altered-iend-crc", "trailing-after-iend", "nonempty-iend"])
def test_png_end_integrity_is_required_even_after_all_hashes_are_recomputed(damage):
    import base64
    import binascii
    import hashlib

    image = png_image()
    content = base64.b64decode(image["dataBase64"])
    assert content[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"
    if damage == "missing-iend-crc":
        content = content[:-4]
    elif damage == "altered-iend-crc":
        content = content[:-1] + bytes([content[-1] ^ 1])
    elif damage == "trailing-after-iend":
        content += b"trailing bytes"
    else:
        payload = b"IENDx"
        content = content[:-12] + (1).to_bytes(4, "big") + payload + binascii.crc32(payload).to_bytes(4, "big")
    image.update(dataBase64=base64.b64encode(content).decode("ascii"), sha256=hashlib.sha256(content).hexdigest())
    # image_request recomputes sourceSha256 too: the rejection must come from
    # PNG integrity, not a stale transport/content digest.
    with pytest.raises(DocumentValidationError, match="PNG"):
        validate_reading_input(image_request(image=image))


def stored_record():
    return {"version": "legacy-v1", "company": "Seadrill", "url": "https://example.com/report",
            "publishedAt": "2026-10-01", "sourceSha256": "a" * 64,
            "reader": {"engine": "codex-cli", "model": "old-model", "reviewedAt": "2026-10-02T12:00:00Z"},
            "result": {"vessels": [{"vessel": "Rig A", "facts": {"expectedStart": "Mid 2027",
                "expectedEnd": "2028", "counterparty": "New client", "location": "Angola",
                "announcedValueUsdApprox": 182500000, "termText": "1 year"}}]},
            "unrecognizedLegacyField": {"keep": ["every", "original", "value"]}}


def stored_request(record=None):
    import hashlib
    import json
    record = stored_record() if record is None else record
    text = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    record_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    req = request("official-news")
    req.update(sourceId="stored-reading-" + record_hash, inputMode="stored-result",
               pages=[{"locator": "stored-review:" + record_hash, "text": text}],
               storedReading={"legacyLedger": "data/news/reviews.json", "recordSha256": record_hash,
                              "originalSourceSha256": record.get("sourceSha256"),
                              "reviewedAt": record.get("reader", {}).get("reviewedAt")})
    req["sourceSha256"] = source_sha256(req["pages"])
    return req


def stored_response(req):
    out = response(req)
    out["readLocators"] = [req["pages"][0]["locator"]]
    out["decisions"][0]["evidence"] = [{"fields": ["client", "region", "interpretation"],
        "sourceId": req["sourceId"], "locator": req["pages"][0]["locator"], "quote": req["pages"][0]["text"]}]
    return out


def test_stored_result_is_a_separate_valid_input_without_an_original_body():
    req = stored_request()
    validate_reading_input(req, reader_policy="policy")
    result = validate_reading_response(stored_response(req), req)
    assert result.issues == []
    assert len(result.decisions) == 1
    assert req["sourceSha256"] != req["storedReading"]["originalSourceSha256"]


def test_stored_record_helpers_preserve_every_unknown_legacy_field():
    from automation.fleet_sync.reading_contracts import stored_record_sha256, stored_review_page
    record = stored_record()
    before = deepcopy(record)
    req = stored_request(record)
    assert stored_review_page(record) == req["pages"][0]
    assert stored_record_sha256(record) == req["storedReading"]["recordSha256"]
    del record["unrecognizedLegacyField"]
    assert stored_record_sha256(record) != stored_record_sha256(before)
    assert before == stored_record()


def test_explicit_document_mode_keeps_existing_document_contract():
    req = request()
    req["inputMode"] = "document"
    validate_reading_input(req)
    assert validate_reading_response(response(req), req).issues == []


@pytest.mark.parametrize("mutate", [
    lambda req: req.pop("storedReading"),
    lambda req: req["storedReading"].update(recordSha256="0" * 64),
    lambda req: req["storedReading"].update(originalSourceSha256="0" * 64),
    lambda req: req["storedReading"].update(originalSourceSha256=None),
    lambda req: req["storedReading"].update(reviewedAt="2026-10-03T12:00:00Z"),
    lambda req: req["storedReading"].update(reviewedAt=None),
    lambda req: req["storedReading"].update(legacyLedger=""),
    lambda req: req["pages"][0].update(locator="page:1"),
    lambda req: req["pages"][0].update(image=png_image()),
    lambda req: req["pages"].append({"locator": "page:2", "text": "Original body must not be appended"}),
    lambda req: req.update(company="Noble"),
    lambda req: req.update(url="https://example.com/another-original"),
    lambda req: req.update(publishedAt="2025-01-01"),
])
def test_stored_result_requires_consistent_provenance_and_only_its_record_page(mutate):
    req = stored_request()
    mutate(req)
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError):
        validate_reading_input(req)


@pytest.mark.parametrize("mode", [None, "document"])
def test_document_mode_cannot_masquerade_stored_pages_or_provenance_as_original_reading(mode):
    req = stored_request()
    if mode is None:
        req.pop("inputMode")
    else:
        req["inputMode"] = mode
    with pytest.raises(DocumentValidationError):
        validate_reading_input(req)
    req.pop("storedReading")
    with pytest.raises(DocumentValidationError):
        validate_reading_input(req)


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"result":{}}', '{"same":1,"same":2}'])
def test_stored_pages_cannot_substitute_summaries_or_non_record_json(text):
    req = stored_request()
    req["pages"][0]["text"] = text
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError):
        validate_reading_input(req)


def test_original_hash_and_review_date_nullability_follows_the_record():
    record = stored_record()
    record.pop("sourceSha256")
    record["reader"].pop("reviewedAt")
    req = stored_request(record)
    validate_reading_input(req)
    assert req["storedReading"]["originalSourceSha256"] is None
    assert req["storedReading"]["reviewedAt"] is None


def test_stored_result_does_not_allow_fake_original_evidence_or_bypass_calculation():
    req = stored_request()
    out = stored_response(req)
    out["decisions"][0]["evidence"][0].update(locator="page:1")
    assert len(validate_reading_response(out, req).issues) == 1
    out = stored_response(req)
    out["decisions"][0]["evidence"][0].update(sourceId="original-document-id")
    assert len(validate_reading_response(out, req).issues) == 1
    out = stored_response(req)
    out["decisions"][0]["evidence"][0].update(quote="Original words absent from this saved JSON")
    assert len(validate_reading_response(out, req).issues) == 1
    out = stored_response(req)
    out["decisions"][0]["row"]["interpretation"]["derivedUsdPerDay"] = 600000
    assert len(validate_reading_response(out, req).issues) == 1


def test_stored_fleet_report_still_requires_registry_coverage_and_input_locator():
    req = stored_request()
    req["kind"] = "fleet-report"
    out = stored_response(req)
    out["coverage"] = []
    with pytest.raises(DocumentValidationError, match="coverage"):
        validate_reading_response(out, req)
    out = stored_response(req)
    out["readLocators"] = ["page:1"]
    with pytest.raises(DocumentValidationError, match="readLocators"):
        validate_reading_response(out, req)


def test_stored_v2_output_wrapper_is_opaque_and_preserves_optional_audit_fields():
    record = stored_record()
    record["result"] = {"readingResponse": {"schemaVersion": 2, "futureField": [1, {"keep": True}]}}
    record["originalDocumentSha256"] = "b" * 64
    validate_reading_input(stored_request(record))


def test_stored_page_is_exact_canonical_json_and_record_hash_covers_unknown_fields():
    import json
    req = stored_request()
    req["pages"][0]["text"] = json.dumps(stored_record(), indent=2)
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError, match="canonical"):
        validate_reading_input(req)
    record = stored_record()
    record.pop("unrecognizedLegacyField")
    req["pages"][0]["text"] = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    req["sourceSha256"] = source_sha256(req["pages"])
    with pytest.raises(DocumentValidationError, match="recordSha256"):
        validate_reading_input(req)


@pytest.mark.parametrize("mutate", [
    lambda decision: decision.update(contractId="unknown-contract"),
    lambda decision: decision["row"].update(endDate="2020-01-01"),
    lambda decision: decision["row"].update(dayRate=math.inf),
])
def test_stored_results_preserve_existing_reference_date_and_finite_number_checks(mutate):
    req = stored_request()
    out = stored_response(req)
    mutate(out["decisions"][0])
    result = validate_reading_response(out, req)
    assert len(result.issues) == 1
    assert not result.decisions
