"""Controlled reader outputs test the boundary; these never invoke a model."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.fleet_sync.codex_reader import CodexReader, _prepare_page_images
from automation.fleet_sync.reading_contracts import context_sha256, policy_sha256, source_sha256
from automation.fleet_sync.reading_service import (
    POLICY_SHA256, READER_POLICY, READER_PROMPT, ReadingService,
    CLI_RESPONSE_SCHEMA, ReaderConfigurationError, ReadingFailed, ReaderOutputError,
    build_reader_policy, structured_output_schema, build_cli_response_schema, reading_scope,
    STORED_RESULT_PROMPT, prompt_for_reading,
)


def request(kind="fleet-report", *, policy=READER_POLICY):
    pages = [{"locator": "page:1", "text": "Rig A extends Client work in Angola from Mid 2027 through 2028 for $182.5m over one year."},
             {"locator": "page:2", "text": "Rig B contract cancelled. Rig A letter of award. Rig A Active as of 2026-10-01."}]
    baseline = [{"id": "a", "company": "Seadrill", "name": "Rig A", "contracts": [
        {"id": "old-a", "vesselId": "a", "client": "Client", "region": "Angola", "startDate": "2027-07-01",
         "endDate": "2028-06-30", "dayRate": 0, "status": "Firm"}]},
        {"id": "b", "company": "Seadrill", "name": "Rig B", "contracts": [
        {"id": "old-b", "vesselId": "b", "client": "Client", "region": "Angola", "startDate": "2026-01-01",
         "endDate": "2026-12-31", "dayRate": 0, "status": "Firm"}]}]
    prior = [{"sourceId": "prior", "pages": [{"locator": "html:1", "text": "Prior official contract evidence."}]}]
    return dict(sourceId="s1", sourceSha256=source_sha256(pages), company="Seadrill", kind=kind,
                url="https://example.com/report", publishedAt="2026-10-01", pages=pages,
                baseline=baseline, priorEvidence=prior, contextSha256=context_sha256(baseline, prior),
                readerPolicySha256=policy_sha256(policy))


def interpretation():
    return dict(sourceStartText="Mid 2027", sourceEndText="2028", startPrecision="approximate",
                endPrecision="year", dateBasis="placement", placementBasis="기존 계약과 공시를 연결해 배치",
                awardType="extension", termText="1년 연장", optionText=None, valueText="$182.5m",
                rateBasis="backlog-derived", reportedUsdPerDay=None, derivedUsdPerDay=500000,
                valueUsd=182500000, baseDurationDays=365,
                durationAssumption="1 year = 365 days; 1 month = 365/12 days", maximumTerm=False,
                includes=[], excludes=[])


def decision():
    return dict(vesselId="a", contractId="old-a", action="upsert", row={
        "id": "old-a", "vesselId": "a", "startDate": "2027-07-01", "endDate": "2028-06-30", "dayRate": 0,
        "client": "Client", "region": "Angola", "status": "Firm", "interpretation": interpretation()},
        facts={"client": "Client", "region": "Angola", "interpretation": interpretation()},
        reasonKo="원문과 기존 계약을 연결해 근사 일정을 배치", evidence=[dict(
            fields=["facts.client", "facts.region", "facts.interpretation", "row.interpretation"],
            sourceId="s1", locator="page:1", quote=request()["pages"][0]["text"])])


def response(req):
    return dict(schemaVersion=2, sourceId=req["sourceId"], sourceSha256=req["sourceSha256"], company=req["company"],
                reportDate="2026-10-01", readLocators=[p["locator"] for p in req["pages"]],
                coverage=[{"vesselId": "a", "state": "included"}, {"vesselId": "b", "state": "not-mentioned"}],
                vesselUpdates=[], decisions=[decision()])


class Reader:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.inputs = []
        self.corrections = []

    def read(self, req):
        self.inputs.append(deepcopy(req))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def read_corrected(self, req, *, previous_response, error):
        self.corrections.append((previous_response, error))
        return self.read(req)


def test_policy_includes_schema_execution_and_extraction_identity():
    policy = json.loads(build_reader_policy(extraction_version="extract-v2"))
    assert policy["model"] == "gpt-6-luna"
    assert policy["reasoningEffort"] == "medium"
    assert policy["serviceTier"] == "default"
    assert policy["extractionVersion"] == "extract-v2"
    assert policy["responseSchema"]["properties"]["schemaVersion"]["const"] == 2
    assert policy["prompt"] == READER_PROMPT
    assert POLICY_SHA256 == policy_sha256(READER_POLICY)
    assert policy_sha256(build_reader_policy(extraction_version="extract-v3")) != policy_sha256(build_reader_policy(extraction_version="extract-v2"))


def test_approximate_dates_and_model_selected_extension_are_retained_without_prose_parser():
    req = request()
    out = response(req)
    outcome = ReadingService(Reader(out)).read(req)
    assert outcome.validation.decisions == out["decisions"]
    assert outcome.validation.issues == []
    assert outcome.response == out
    assert outcome.attempts == 1
    assert outcome.validation.decisions[0]["row"]["interpretation"]["sourceStartText"] == "Mid 2027"


def test_partial_invalid_decision_preserves_independent_valid_change_without_retry():
    req = request()
    out = response(req)
    invalid = deepcopy(out["decisions"][0])
    invalid.update(vesselId="b", contractId="old-b")
    invalid["row"].update(vesselId="b", id="old-b", endDate="not-an-iso-date")
    out["decisions"].append(invalid)
    reader = Reader(out)
    result = ReadingService(reader).read(req)
    assert result.validation.decisions == out["decisions"][:1]
    assert len(result.validation.issues) == 1
    assert result.validation.issues[0].index == 1
    assert len(reader.inputs) == 1


def test_missing_page_is_repaired_with_entire_unchanged_input():
    req = request()
    bad = response(req)
    bad["readLocators"] = ["page:1"]
    reader = Reader(bad, response(req))
    outcome = ReadingService(reader).read(req)
    assert outcome.attempts == 2
    assert reader.inputs == [req, req]
    assert reader.corrections[0][0] == bad
    assert "readLocators" in reader.corrections[0][1]


def test_malformed_json_is_repaired_and_default_attempt_limit_is_three():
    req = request()
    reader = Reader(ReaderOutputError("bad JSON", raw_output="{"), [], {})
    with pytest.raises(ReadingFailed) as error:
        ReadingService(reader).read(req)
    assert error.value.attempts == 3
    assert len(reader.inputs) == 3
    assert reader.corrections[0][0] == "{"


def test_operational_failure_is_not_format_retry_or_semantic_fallback():
    req = request()
    reader = Reader(RuntimeError("auth unavailable"))
    with pytest.raises(RuntimeError, match="auth unavailable"):
        ReadingService(reader).read(req)
    assert len(reader.inputs) == 1


def test_invalid_input_or_policy_fails_before_reader_invocation():
    req = request()
    req["readerPolicySha256"] = "0" * 64
    reader = Reader()
    with pytest.raises(ValueError, match="readerPolicySha256"):
        ReadingService(reader).read(req)
    assert reader.inputs == []


def test_uninformative_news_can_return_no_contract_decisions():
    req = request("official-news")
    out = response(req)
    out.update(coverage=[], decisions=[])
    result = ReadingService(Reader(out)).read(req)
    assert result.validation.decisions == []
    assert result.validation.issues == []


def test_omitted_contract_creates_no_withdrawal_but_explicit_model_cancellation_survives():
    req = request()
    out = response(req)
    # No code synthesizes a withdrawal for old-b's not-mentioned coverage.
    assert len(ReadingService(Reader(out)).read(req).validation.decisions) == 1
    cancelled = decision()
    cancelled.update(vesselId="b", contractId="old-b", action="withdraw", row=None, reasonKo="공식 취소 근거 확인")
    cancelled["evidence"][0]["fields"] = ["facts.client", "facts.region", "facts.interpretation"]
    cancelled["evidence"].append(dict(fields=["action"], sourceId="s1", locator="page:2", quote="Rig B contract cancelled."))
    out["decisions"].append(cancelled)
    assert ReadingService(Reader(out)).read(req).validation.decisions[-1]["action"] == "withdraw"


def test_loa_firm_proposal_is_isolated_and_reported_not_overwritten():
    req = request()
    out = response(req)
    out["decisions"][0]["row"]["interpretation"]["awardType"] = "letter-of-award"
    result = ReadingService(Reader(out)).read(req)
    assert result.validation.decisions == []
    assert "LOA" in result.validation.issues[0].message


def test_codex_cli_uses_chatgpt_isolation_pinned_settings_and_full_material(monkeypatch):
    req = request()
    req["pages"][1]["text"] += "x" * 100000 + " FINAL PAGE TAIL"
    req["sourceSha256"] = source_sha256(req["pages"])
    expected = response(req)
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api")
    monkeypatch.setenv("CODEX_API_KEY", "secret-codex")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://other-provider.example")
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "CODEX_API_KEY" not in kwargs["env"]
        assert "OPENAI_BASE_URL" not in kwargs["env"]
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        path = Path(command[command.index("--output-last-message") + 1])
        assert json.loads(Path(command[command.index("--output-schema") + 1]).read_text())["additionalProperties"] is False
        path.write_text(json.dumps(expected))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    assert CodexReader().read(req) == expected
    command, kwargs = calls[-1]
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    for config in ['model_reasoning_effort="medium"', 'service_tier="default"', 'features.shell_tool=false',
                   'features.apps=false', 'features.plugins=false', 'features.memories=false',
                   'skills.bundled.enabled=false', 'approval_policy="never"', 'web_search="disabled"']:
        assert config in command
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    supplied = json.loads(kwargs["input"].split("\nINPUT_JSON:\n", 1)[1])
    assert supplied == req
    assert supplied["pages"][-1]["text"].endswith("FINAL PAGE TAIL")
    assert kwargs["timeout"] == 600


@pytest.mark.parametrize("raw", ["{", "", '{"schemaVersion": NaN}', None])
def test_adapter_invalid_or_missing_json_is_corrected_with_full_input(monkeypatch, raw):
    req = request()
    expected = response(req)
    exec_inputs = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        exec_inputs.append(kwargs["input"])
        output = Path(command[command.index("--output-last-message") + 1])
        if len(exec_inputs) == 1:
            if raw is not None:
                output.write_text(raw)
        else:
            output.write_text(json.dumps(expected))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    outcome = ReadingService(CodexReader()).read(req)
    assert outcome.attempts == 2
    assert len(exec_inputs) == 2
    assert "CORRECTION_JSON:" in exec_inputs[-1]
    assert json.loads(exec_inputs[-1].split("\nINPUT_JSON:\n", 1)[1]) == req


def test_adapter_execution_failure_is_not_retried_as_malformed_output(monkeypatch):
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        return SimpleNamespace(returncode=7, stdout="", stderr="service unavailable")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    with pytest.raises(RuntimeError, match="exit 7"):
        ReadingService(CodexReader()).read(request())
    assert len(calls) == 2


def test_service_protects_caller_input_from_replaceable_adapter_mutation():
    req = request()
    original = deepcopy(req)
    expected = response(req)

    class MutatingReader:
        def read(self, supplied):
            supplied["pages"].clear()
            supplied["baseline"][0]["contracts"].clear()
            return expected

    assert ReadingService(MutatingReader()).read(req).validation.issues == []
    assert req == original


def test_explicit_extraction_policy_is_shared_by_collector_service_and_adapter(monkeypatch):
    policy = build_reader_policy(extraction_version="pdf-html-v4")
    req = request(policy=policy)
    out = response(req)
    assert ReadingService(Reader(out), policy=policy).read(req).validation.issues == []
    assert CodexReader(policy=policy).policy == policy


@pytest.mark.parametrize("auth", [(0, "Using API key"), (1, "Logged in using ChatGPT"),
                                  (0, "Using API key. Run codex login with ChatGPT to switch.")])
def test_codex_rejects_non_chatgpt_or_failed_auth_without_exec(monkeypatch, auth):
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=auth[0], stdout=auth[1], stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    with pytest.raises(RuntimeError, match="ChatGPT"):
        CodexReader().read(request())
    assert len(calls) == 1


@pytest.mark.parametrize("max_attempts", [0, -1, True, 1.5])
def test_invalid_retry_configuration(max_attempts):
    with pytest.raises(ValueError):
        ReadingService(Reader(), max_attempts=max_attempts)


# A real PNG fixture encoded inline keeps tests reproducible without retained
# generated files. The adapter transports pixels; it does not interpret them.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def image(data=PNG):
    return dict(mimeType="image/png", sha256=hashlib.sha256(data).hexdigest(),
                dataBase64=base64.b64encode(data).decode("ascii"))


@pytest.mark.parametrize("retry", [False, True])
def test_pdf_images_are_all_attached_in_page_order_without_inline_base64(monkeypatch, retry):
    req = request()
    req["pages"].insert(1, {"locator": "page:decoration", "text": "", "image": image()})
    req["pages"][0]["image"] = image()
    req["pages"][-1]["image"] = image()
    req["sourceSha256"] = source_sha256(req["pages"])
    original = deepcopy(req)
    expected = response(req)
    observed_paths = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        image_paths = [Path(command[index + 1]) for index, value in enumerate(command) if value == "--image"]
        observed_paths.extend(image_paths)
        assert [path.name for path in image_paths] == ["page-000001.png", "page-000002.png", "page-000003.png"]
        assert all(path.read_bytes() == PNG for path in image_paths)
        assert len({path.parent for path in image_paths}) == 1
        manifest = json.loads(kwargs["input"].split("\nPAGE_IMAGE_ATTACHMENTS_JSON:\n", 1)[1].split("\nINPUT_JSON:\n", 1)[0])
        assert [item["locator"] for item in manifest] == [p["locator"] for p in req["pages"]]
        assert [item["attachmentIndex"] for item in manifest] == [1, 2, 3]
        assert all(item["sourceId"] == "s1" for item in manifest)
        supplied = json.loads(kwargs["input"].split("\nINPUT_JSON:\n", 1)[1])
        assert supplied["pages"] == [{**page, "image": {k: v for k, v in page["image"].items() if k != "dataBase64"}} for page in req["pages"]]
        assert supplied["sourceSha256"] == req["sourceSha256"]
        assert "dataBase64" not in kwargs["input"]
        output = deepcopy(expected)
        if retry and len(observed_paths) == 3:
            output["readLocators"] = ["page:1"]
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(output))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    outcome = ReadingService(CodexReader()).read(req)
    assert outcome.response == expected
    assert outcome.attempts == (2 if retry else 1)
    assert len(observed_paths) == (6 if retry else 3)
    assert req == original
    assert all(not path.exists() for path in observed_paths)


@pytest.mark.parametrize("mutate", [
    lambda img: img.update(dataBase64="not base64!"),
    lambda img: img.update(dataBase64=img["dataBase64"] + "\n"),
    lambda img: img.update(sha256="0" * 64),
    lambda img: img.update(mimeType="image/jpeg"),
    lambda img: img.update(image(b"not a PNG")),
])
def test_invalid_page_image_fails_before_cli_invocation(monkeypatch, mutate):
    req = request()
    req["pages"][0]["image"] = image()
    mutate(req["pages"][0]["image"])
    req["sourceSha256"] = source_sha256(req["pages"])
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        CodexReader().read(req)
    assert calls == []


def test_original_image_input_hash_is_verified_before_metadata_only_transport(monkeypatch):
    req = request()
    req["pages"][0]["image"] = image()
    # Keep the previous text-only hash: stripping image data must never make
    # this stale identity appear to match the actual original input.
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="sourceSha256"):
        CodexReader().read(req)
    assert calls == []


def test_policy_identifies_multimodal_delivery():
    policy = json.loads(READER_POLICY)
    assert policy["imageDelivery"]["version"] == "png-page-attachments-v1"
    assert policy["imageDelivery"]["order"] == "input-page-order"
    assert policy["imageDelivery"]["inlineImageData"] == "omitted-after-validation"


def test_image_attachment_indices_preserve_text_only_page_gaps_and_untrusted_locators(tmp_path):
    req = request()
    req["pages"][0]["locator"] = "../../outside.png"
    req["pages"].append({"locator": "page:3", "text": "", "image": image()})
    req["pages"][0]["image"] = image()
    supplied, attachments = _prepare_page_images(req, tmp_path)
    assert [a["filename"] for a in attachments] == ["page-000001.png", "page-000003.png"]
    assert [a["attachmentIndex"] for a in attachments] == [1, 2]
    assert [a["pageIndex"] for a in attachments] == [1, 3]
    assert attachments[0]["locator"] == "../../outside.png"
    assert set(p.name for p in tmp_path.iterdir()) == {"page-000001.png", "page-000003.png"}
    assert supplied["pages"][1] == req["pages"][1]
    assert "dataBase64" not in supplied["pages"][0]["image"]
    assert "dataBase64" in req["pages"][0]["image"]


@pytest.mark.parametrize("mutate", [
    lambda img: img.update(dataBase64="invalid"),
    lambda img: img.update(dataBase64=img["dataBase64"] + "\n"),
    lambda img: img.update(sha256="0" * 64),
    lambda img: img.update(mimeType="image/jpeg"),
    lambda img: img.update(image(b"not a PNG")),
])
def test_image_file_boundary_rechecks_strict_decode_and_raw_identity(tmp_path, mutate):
    req = request()
    req["pages"][0]["image"] = image()
    mutate(req["pages"][0]["image"])
    with pytest.raises(ValueError):
        _prepare_page_images(req, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_cli_transport_schema_preserves_structure_and_supported_constraints():
    from automation.fleet_sync.reading_contracts import READER_RESPONSE_SCHEMA
    original = deepcopy(READER_RESPONSE_SCHEMA)
    transport = structured_output_schema(original)
    assert transport == CLI_RESPONSE_SCHEMA
    assert original == READER_RESPONSE_SCHEMA
    assert original["properties"]["readLocators"]["uniqueItems"] is True
    assert original["properties"]["sourceId"]["minLength"] == 1
    assert original["properties"]["schemaVersion"]["const"] == 2
    assert transport["properties"]["schemaVersion"] == {"type": "integer", "enum": [2]}
    assert transport["properties"]["sourceSha256"]["pattern"] == original["properties"]["sourceSha256"]["pattern"]
    assert transport["properties"]["reportDate"]["anyOf"][0]["format"] == "date"
    row = transport["properties"]["decisions"]["items"]["properties"]["row"]["anyOf"][0]
    assert row["properties"]["dayRate"]["minimum"] == 0
    assert row["properties"]["sourceUrl"]["type"] == ["string", "null"]
    assert row["additionalProperties"] is False
    assert set(row["required"]) == set(row["properties"])
    policy = json.loads(build_reader_policy())
    assert policy["transportResponseSchema"] == transport
    assert policy["responseSchema"] == original

    def check(node):
        assert not {"uniqueItems", "minLength", "maxLength", "$schema", "const"}.intersection(node)
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for name in ("properties", "$defs"):
            for child in node.get(name, {}).values():
                check(child)
        if "items" in node:
            check(node["items"])
        for child in node.get("anyOf", []):
            check(child)
    check(transport)
    evidence_fields = transport["properties"]["decisions"]["items"]["properties"]["evidence"]["items"]["properties"]["fields"]
    assert evidence_fields["minItems"] == 1


def test_schema_conversion_treats_property_names_and_enum_values_as_data():
    names = ["uniqueItems", "minLength", "const", "$schema", "properties", "enum"]
    schema = dict(type="object", properties={name: dict(type="string", minLength=1, enum=names) for name in names},
                  required=names, additionalProperties=False)
    transport = structured_output_schema(schema)
    assert list(transport["properties"]) == names
    for value in transport["properties"].values():
        assert value == {"type": "string", "enum": names}
    with pytest.raises(ValueError, match="unsupported.*if"):
        structured_output_schema({"type": "object", "if": {"type": "string"}})


@pytest.mark.parametrize("mutate,envelope", [
    (lambda out: out["readLocators"].append("page:1"), True),
    (lambda out: out.update(sourceId=""), True),
    (lambda out: out["decisions"][0].update(reasonKo=""), False),
    (lambda out: out["decisions"][0]["evidence"][0]["fields"].append("facts.client"), False),
    (lambda out: out["decisions"][0]["row"].update(dayRate=-1), False),
    (lambda out: out["decisions"][0]["row"]["interpretation"].update(derivedUsdPerDay=600000), False),
])
def test_local_final_validation_stays_strict_after_transport_conversion(mutate, envelope):
    from automation.fleet_sync.reading_contracts import DocumentValidationError, validate_reading_response
    req = request()
    out = response(req)
    mutate(out)
    if envelope:
        with pytest.raises(DocumentValidationError):
            validate_reading_response(out, req)
    else:
        checked = validate_reading_response(out, req)
        assert checked.decisions == [] and len(checked.issues) == 1


@pytest.mark.parametrize("stderr,configuration_error", [
    ('ERROR: {"type":"error","error":{"message":"unsupported uniqueItems","type":"invalid_request_error","param":"text.format.schema","code":"invalid_json_schema"},"status":400}\n', True),
    ('{"type":"error","error":{"code":"invalid_json_schema"}}\n', True),
    ('ERROR: {"type":"error","error":{"code":"invalid_json_schema"}}\nERROR: {"type":"error","error":{"code":"invalid_json_schema"}}\n', True),
    ('source says invalid_json_schema or {"code":"invalid_json_schema"}', False),
    ('ERROR: {"type":"error","error":{"code":"rate_limit_exceeded"}}', False),
    ('ERROR: {"type":"error","error":{"message":"invalid_json_schema"}}', False),
    ('ERROR: {"type":"error","error":{"code":"invalid_json_schema"}', False),
    ('{"type":"message","text":"invalid_json_schema","error":{"code":"invalid_json_schema"}}', False),
])
def test_adapter_classifies_only_framed_structured_schema_errors(monkeypatch, stderr, configuration_error):
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        transmitted = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert transmitted == build_cli_response_schema(request())
        assert "uniqueItems" not in transmitted["properties"]["readLocators"]
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    with pytest.raises(RuntimeError) as caught:
        ReadingService(CodexReader()).read(request())
    assert isinstance(caught.value, ReaderConfigurationError) is configuration_error
    assert stderr.strip() in str(caught.value)
    assert len(calls) == 2  # One auth check and one exec, never correction retry.


@pytest.mark.parametrize("kind,min_count", [("fleet-report", 2), ("official-news", 0)])
def test_request_wire_schema_limits_all_vessel_references_to_company_registry(kind, min_count):
    req = request(kind)
    req["baseline"].append({"id": "foreign-rig", "company": "Noble", "name": "Foreign Rig", "contracts": []})
    req["contextSha256"] = context_sha256(req["baseline"], req["priorEvidence"])
    original = deepcopy(CLI_RESPONSE_SCHEMA)
    schema = build_cli_response_schema(req)
    assert CLI_RESPONSE_SCHEMA == original
    arrays = schema["properties"]
    for collection in ("coverage", "vesselUpdates", "decisions"):
        assert arrays[collection]["items"]["properties"]["vesselId"]["enum"] == ["a", "b"]
    row = arrays["decisions"]["items"]["properties"]["row"]["anyOf"][0]
    assert row["properties"]["vesselId"]["enum"] == ["a", "b"]
    assert arrays["coverage"]["minItems"] == min_count
    assert arrays["coverage"]["maxItems"] == 2
    assert reading_scope(req) == dict(company="Seadrill", kind=kind, targetVessels=[
        {"vesselId": "a", "name": "Rig A"}, {"vesselId": "b", "name": "Rig B"}])
    req["baseline"].reverse()
    assert build_cli_response_schema(req) == schema


@pytest.mark.parametrize("full_registry", [False, True])
def test_wire_registry_scope_and_full_context_are_transmitted_without_id_guessing(monkeypatch, full_registry):
    req = request()
    if full_registry:
        req["baseline"] = json.loads((Path(__file__).resolve().parents[2] / "data/data_as_of_26_01_07.json").read_text())
        assert len(req["baseline"]) == 62
    else:
        req["baseline"].append({"id": "foreign", "company": "Valaris", "name": "Other Company Rig", "contracts": []})
    vessel = next(v for v in req["baseline"] if v["company"] == req["company"])
    vessel.update(id="registered-opaque-id", contracts=[])
    req["contextSha256"] = context_sha256(req["baseline"], req["priorEvidence"])
    out = response(req)
    observed = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        observed.append(schema)
        assert schema == build_cli_response_schema(req)
        scope = json.loads(kwargs["input"].split("\nINPUT_SCOPE_JSON:\n", 1)[1].split("\nINPUT_JSON:\n", 1)[0])
        assert scope == reading_scope(req)
        assert [v["vesselId"] for v in scope["targetVessels"]] == sorted(
            v["id"] for v in req["baseline"] if v["company"] == req["company"])
        assert any(v["vesselId"] == "registered-opaque-id" for v in scope["targetVessels"])
        supplied = json.loads(kwargs["input"].split("\nINPUT_JSON:\n", 1)[1])
        assert supplied == req  # All companies, contracts and prior evidence remain.
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(out))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    CodexReader().read(req)
    assert len(observed) == 1


@pytest.mark.parametrize("location", ["coverage", "vesselUpdates", "decisions", "row"])
def test_actual_wire_schema_rejects_nonregistry_vessel_at_each_reference_location(location):
    from jsonschema import Draft202012Validator
    req = request()
    out = response(req)
    out["decisions"][0]["row"].update(newsReviewed=None, sourceUrl=None, sourcePublishedAt=None, datePrecision=None)
    out["vesselUpdates"] = [dict(vesselId="a", status="Active", statusAsOf="2026-10-01", reasonKo="공시 상태",
        evidence=[dict(fields=["status", "statusAsOf"], sourceId="s1", locator="page:2", quote="Rig A Active as of 2026-10-01.")])]
    validator = Draft202012Validator(build_cli_response_schema(req))
    assert list(validator.iter_errors(out)) == []
    target = out["decisions"][0]["row"] if location == "row" else out[location][0]
    target["vesselId"] = "id-created-from-unregistered-vessel-name"
    assert list(validator.iter_errors(out))


def test_scope_policy_binds_base_schema_rules_and_context_reproduction():
    policy = json.loads(READER_POLICY)
    assert policy["transportResponseSchema"] == CLI_RESPONSE_SCHEMA
    assert policy["transportScope"]["version"] == "company-registry-v1"
    assert policy["transportScope"]["referenceField"] == "vesselId"
    assert policy["transportScope"]["selection"] == "baseline.company == request.company"
    assert "contextSha256" in policy["transportScope"]["reproduction"]
    first = request()
    second = deepcopy(first)
    second["baseline"].append({"id": "new-id", "name": "New Vessel", "company": "Seadrill", "contracts": []})
    second["contextSha256"] = context_sha256(second["baseline"], second["priorEvidence"])
    assert first["contextSha256"] != second["contextSha256"]
    assert build_cli_response_schema(first) != build_cli_response_schema(second)


def test_dynamic_schema_does_not_replace_final_duplicate_reference_validation():
    from automation.fleet_sync.reading_contracts import DocumentValidationError, validate_reading_response
    req = request()
    out = response(req)
    out["coverage"] = [{"vesselId": "a", "state": "included"}] * 2
    with pytest.raises(DocumentValidationError, match="unique"):
        validate_reading_response(out, req)


def test_generic_prompt_supports_explained_placement_contingent_loa_and_derived_average():
    assert "Coarse or approximate wording alone is not a hold reason" in READER_PROMPT
    assert "Contingent row" in READER_PROMPT
    assert "missing reported daily rate alone is not a hold reason" in READER_PROMPT
    assert "clearly matched base amount and base term" in READER_PROMPT
    assert "Do not construct IDs from vessel names" in READER_PROMPT
    for example in ("KG2", "Barents", "$300", "early 2031"):
        assert example not in READER_PROMPT


def test_model_selected_approximate_contingent_loa_and_conversion_are_retained():
    req = request()
    out = response(req)
    d = out["decisions"][0]
    d["row"]["status"] = "Contingent"
    for interpretation in (d["facts"]["interpretation"], d["row"]["interpretation"]):
        interpretation["awardType"] = "letter-of-award"
        interpretation["includes"] = ["추가 서비스와 이동비 포함"]
    d["evidence"][0]["fields"].append("row.status")
    d["evidence"].append(dict(fields=["facts.interpretation.awardType", "row.interpretation.awardType"],
        sourceId="s1", locator="page:2", quote="Rig A letter of award."))
    outcome = ReadingService(Reader(out)).read(req)
    assert outcome.validation.issues == []
    row = outcome.validation.decisions[0]["row"]
    assert row["status"] == "Contingent" and row["interpretation"]["dateBasis"] == "placement"
    assert row["interpretation"]["derivedUsdPerDay"] == 500000 and row["dayRate"] == 0
    assert row["interpretation"]["includes"] == ["추가 서비스와 이동비 포함"]


def stored_request():
    from automation.fleet_sync.reading_contracts import stored_record_sha256, stored_review_page
    req = request("official-news")
    record = dict(company=req["company"], url=req["url"], publishedAt=req["publishedAt"], title="저장된 공식 뉴스 판단",
        sourceSha256="1" * 64, reader={"reviewedAt": "2026-10-01T12:00:00Z"}, result={"vessels": [dict(
            vessel="Rig A", facts={"counterparty": "Client", "location": "Angola", "termText": "기본 1년", "valueText": "$182.5m",
                "announcedValueUsdApprox": 182500000, "expectedStart": "Mid 2027", "expectedEnd": "2028",
                "awardType": "extension", "dayRateDisclosure": "undisclosed", "dayRateUsd": None},
            comparison="이전 정책에서 정확 날짜가 없어 보류했습니다.", changes=[],
            evidence=[{"field": "termText", "quote": 'One year contract value "$182.5m".'}])]})
    digest = stored_record_sha256(record)
    req.update(inputMode="stored-result", storedReading=dict(legacyLedger="data/news/reviews.json", recordSha256=digest,
        originalSourceSha256=record["sourceSha256"], reviewedAt=record["reader"]["reviewedAt"]),
        sourceId="stored-reading-" + digest, pages=[stored_review_page(record)])
    req["sourceSha256"] = source_sha256(req["pages"])
    return req


def test_mode_selection_and_policy_preserve_document_prompt_and_track_stored_prompt():
    req = request()
    assert prompt_for_reading(req) == READER_PROMPT
    req["inputMode"] = "document"
    assert prompt_for_reading(req) == READER_PROMPT
    req["inputMode"] = "stored-result"
    assert prompt_for_reading(req) == STORED_RESULT_PROMPT
    req["inputMode"] = "unsupported"
    with pytest.raises(ValueError):
        prompt_for_reading(req)
    policy = json.loads(READER_POLICY)
    assert policy["storedResultPrompt"] == STORED_RESULT_PROMPT
    assert policy["inputModeSelection"]["default"] == "document"
    assert policy["inputModeSelection"]["stored-result"] == "storedResultPrompt"
    assert "Do not claim that you reread the original article" in STORED_RESULT_PROMPT
    assert "old comparison is a historical interpretation" in STORED_RESULT_PROMPT
    assert "short contiguous substring from the canonical stored JSON" in STORED_RESULT_PROMPT
    assert "Read every attached page image" not in STORED_RESULT_PROMPT


@pytest.mark.parametrize("retry", [False, True])
def test_stored_mode_transmits_complete_record_with_distinct_prompt_and_no_original_read_claim(monkeypatch, retry):
    req = stored_request()
    expected = response(req)
    expected.update(coverage=[], decisions=[])
    expected["readLocators"] = [p["locator"] for p in req["pages"]]
    calls = []
    monkeypatch.setattr("automation.fleet_sync.codex_reader.shutil.which", lambda _: "/bin/codex")

    def run(command, **kwargs):
        if command[1:3] == ["login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        calls.append(kwargs["input"])
        assert kwargs["input"].startswith(STORED_RESULT_PROMPT)
        assert not kwargs["input"].startswith(READER_PROMPT)
        assert "--image" not in command
        supplied = json.loads(kwargs["input"].split("\nINPUT_JSON:\n", 1)[1])
        assert supplied == req
        assert '"changes":[]' in supplied["pages"][0]["text"]
        assert '"comparison":' in supplied["pages"][0]["text"]
        assert '"evidence":' in supplied["pages"][0]["text"]
        output = deepcopy(expected)
        if retry and len(calls) == 1:
            output["readLocators"] = []
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(output))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("automation.fleet_sync.codex_reader.subprocess.run", run)
    outcome = ReadingService(CodexReader()).read(req)
    assert outcome.validation.issues == [] and outcome.attempts == (2 if retry else 1)
    assert len(calls) == (2 if retry else 1)


def test_stored_mode_uses_same_validation_without_treating_old_quotes_as_new_source_quotes():
    req = stored_request()
    out = response(req)
    d = out["decisions"][0]
    d["reasonKo"] = "저장 판단의 조건과 기존 계약을 연결해 누락 배치를 정규화했습니다."
    for evidence in d["evidence"]:
        evidence.update(sourceId=req["sourceId"], locator=req["pages"][0]["locator"], quote=req["pages"][0]["text"])
    result = ReadingService(Reader(out)).read(req)
    assert result.validation.issues == []
    d["evidence"][0]["quote"] = 'One year contract value "$182.5m".'
    result = ReadingService(Reader(out)).read(req)
    assert result.validation.decisions == [] and len(result.validation.issues) == 1
    d["evidence"][0]["quote"] = '"termText":"기본 1년"'
    d["row"]["interpretation"]["derivedUsdPerDay"] = 600000
    result = ReadingService(Reader(out)).read(req)
    assert result.validation.decisions == [] and len(result.validation.issues) == 1
