"""Controlled reader outputs test the boundary; these never invoke a model."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.fleet_sync.codex_reader import CodexReader
from automation.fleet_sync.reading_contracts import context_sha256, policy_sha256, source_sha256
from automation.fleet_sync.reading_service import (
    POLICY_SHA256, READER_POLICY, READER_PROMPT, ReadingService,
    ReadingFailed, ReaderOutputError, build_reader_policy,
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
