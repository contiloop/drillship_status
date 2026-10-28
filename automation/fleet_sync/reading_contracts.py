"""Version 2 data contracts at the model-reader port.

This module validates structure and mechanically checkable claims, never prose
semantics or reconciliation priorities. Adapters supply the registry as the
complete baseline (all vessels, including those with no contracts).

Hash format: UTF-8 JSON with ensure_ascii=False, sort_keys=True, separators=(",",
":") and allow_nan=False. sourceSha256 covers the ordered pages array, including
locators and all input text. contextSha256 covers {baseline, priorEvidence}.
The source document's original bytes remain a separate documentSha256 ledger
concern; neither of these hashes claims to be a document-byte digest.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from jsonschema import Draft202012Validator, FormatChecker

from .model import AwardType, Company, ContractStatus, DateBasis, DatePrecision, RateBasis, ReadingAction, ReadingKind, ShipStatus


class ReadingPage(TypedDict):
    locator: str
    text: str


class ReadingEvidence(TypedDict):
    """Claim paths, e.g. facts.client or row.interpretation.valueUsd.

    A complete interpretation path may cite the object. A leaf path supports
    only that leaf. Bare aliases require equal facts/row values. Source metadata
    is checked against input/baseline rather than requiring body quotations.
    """

    fields: list[str]
    sourceId: str
    locator: str
    quote: str


class ContractInterpretation(TypedDict):
    sourceStartText: str | None
    sourceEndText: str | None
    startPrecision: DatePrecision
    endPrecision: DatePrecision
    dateBasis: DateBasis
    placementBasis: str | None
    awardType: AwardType
    termText: str | None
    optionText: str | None
    valueText: str | None
    rateBasis: RateBasis
    reportedUsdPerDay: float | None
    derivedUsdPerDay: float | None
    valueUsd: float | None
    baseDurationDays: float | None
    durationAssumption: str | None
    maximumTerm: bool
    includes: list[str]
    excludes: list[str]


class ReadingFacts(TypedDict):
    client: str | None
    region: str | None
    interpretation: ContractInterpretation


class ReadingRow(TypedDict):
    id: str | None
    vesselId: str
    startDate: str
    endDate: str
    dayRate: float
    client: str
    region: str
    status: ContractStatus
    interpretation: ContractInterpretation
    newsReviewed: NotRequired[bool | None]
    sourceUrl: NotRequired[str | None]
    sourcePublishedAt: NotRequired[str | None]
    datePrecision: NotRequired[dict[str, str] | None]


class ReadingDecision(TypedDict):
    vesselId: str
    contractId: str | None
    action: ReadingAction
    row: ReadingRow | None
    facts: ReadingFacts
    reasonKo: str
    evidence: list[ReadingEvidence]


class VesselUpdate(TypedDict):
    vesselId: str
    status: ShipStatus
    statusAsOf: str
    reasonKo: str
    evidence: list[ReadingEvidence]


class VesselCoverage(TypedDict):
    vesselId: str
    state: Literal["included", "not-mentioned", "held"]


class ReadingInput(TypedDict):
    sourceId: str
    sourceSha256: str
    company: Company
    kind: ReadingKind
    url: str
    publishedAt: str | None
    pages: list[ReadingPage]
    baseline: list[dict[str, Any]]
    priorEvidence: list[dict[str, Any]]
    contextSha256: str
    readerPolicySha256: str


class ReadingResponse(TypedDict):
    schemaVersion: Literal[2]
    sourceId: str
    sourceSha256: str
    company: Company
    reportDate: str | None
    readLocators: list[str]
    coverage: list[VesselCoverage]
    vesselUpdates: list[VesselUpdate]
    decisions: list[ReadingDecision]


class NewsJob(TypedDict):
    jobId: str
    kind: ReadingKind
    company: Company
    url: str
    sourceSha256: str
    contextSha256: str
    readerPolicySha256: str


class NewsQueue(TypedDict):
    schemaVersion: Literal[2]
    pendingCount: int
    fingerprint: str
    jobs: list[NewsJob]


class ReadingPort(Protocol):
    """Replaceable reader adapter; invocation and persistence live outside it."""

    def read(self, request: ReadingInput) -> ReadingResponse: ...


class DocumentValidationError(ValueError):
    """The input/envelope is untrusted; no part of this document may be applied."""


@dataclass(frozen=True)
class ValidationIssue:
    collection: str
    index: int
    message: str


@dataclass
class ReadingValidationResult:
    """Only structurally valid items; issues preserve original response indices.

    This is validation, not approval: callers still reconcile semantic conflicts,
    keep holds/no-change decisions and persist withdrawals in their history.
    """

    decisions: list[ReadingDecision] = field(default_factory=list)
    vessel_updates: list[VesselUpdate] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


COMPANIES = ["Transocean", "Valaris", "Noble", "Seadrill"]
STRING = {"type": "string"}
ID = {"type": "string", "minLength": 1}
NULL_STRING = {"type": ["string", "null"]}
NULL_NUMBER = {"type": ["number", "null"], "minimum": 0}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
DATE = {"type": "string", "format": "date"}
ISO_TIMESTAMP = {"anyOf": [DATE, {"type": "string", "format": "date-time"}, {"type": "null"}]}


def _object(properties: dict, optional: dict | None = None) -> dict:
    return {"type": "object", "properties": {**properties, **(optional or {})},
            "required": list(properties), "additionalProperties": False}


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


def _enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


PAGE_SCHEMA = _object({"locator": ID, "text": STRING})
EVIDENCE_SCHEMA = _object({"fields": {**_array(ID), "minItems": 1, "uniqueItems": True},
                           "sourceId": ID, "locator": ID, "quote": ID})
INTERPRETATION_SCHEMA = _object({
    "sourceStartText": NULL_STRING, "sourceEndText": NULL_STRING,
    "startPrecision": _enum("day", "month", "quarter", "half-year", "year", "approximate", "unknown"),
    "endPrecision": _enum("day", "month", "quarter", "half-year", "year", "approximate", "unknown"),
    "dateBasis": _enum("source", "placement", "unresolved"), "placementBasis": NULL_STRING,
    "awardType": _enum("signed-contract", "binding-letter-of-award", "letter-of-award", "option-exercised", "extension", "unreported"),
    "termText": NULL_STRING, "optionText": NULL_STRING, "valueText": NULL_STRING,
    "rateBasis": _enum("reported", "backlog-derived", "undisclosed"),
    "reportedUsdPerDay": NULL_NUMBER, "derivedUsdPerDay": NULL_NUMBER,
    "valueUsd": NULL_NUMBER, "baseDurationDays": NULL_NUMBER, "durationAssumption": NULL_STRING,
    "maximumTerm": {"type": "boolean"}, "includes": _array(STRING), "excludes": _array(STRING),
})
# These are the existing published source fields. They are optional on proposals,
# retained verbatim when supplied, and never reconstructed from English text.
ROW_SCHEMA = _object({
    "id": {"type": ["string", "null"], "minLength": 1}, "vesselId": ID,
    "startDate": DATE, "endDate": DATE, "dayRate": {"type": "number", "minimum": 0},
    "client": STRING, "region": STRING, "status": _enum("Firm", "Option", "Contingent"),
    "interpretation": INTERPRETATION_SCHEMA,
}, {"newsReviewed": {"type": ["boolean", "null"]}, "sourceUrl": NULL_STRING, "sourcePublishedAt": NULL_STRING,
    "datePrecision": {"anyOf": [_object({"start": STRING, "end": STRING}), {"type": "null"}]}})
FACTS_SCHEMA = _object({"client": NULL_STRING, "region": NULL_STRING, "interpretation": INTERPRETATION_SCHEMA})
DECISION_SCHEMA = _object({"vesselId": ID, "contractId": {"type": ["string", "null"], "minLength": 1},
    "action": _enum("upsert", "withdraw", "no-change", "hold"),
    "row": {"anyOf": [ROW_SCHEMA, {"type": "null"}]}, "facts": FACTS_SCHEMA,
    "reasonKo": ID, "evidence": _array(EVIDENCE_SCHEMA)})
VESSEL_UPDATE_SCHEMA = _object({"vesselId": ID, "status": _enum("Active", "Idle", "Warm-Stacked", "Cold-Stacked"),
    "statusAsOf": DATE, "reasonKo": ID, "evidence": _array(EVIDENCE_SCHEMA)})
COVERAGE_SCHEMA = _object({"vesselId": ID, "state": _enum("included", "not-mentioned", "held")})
READING_INPUT_SCHEMA = _object({
    "sourceId": ID, "sourceSha256": HASH, "company": _enum(*COMPANIES),
    "kind": _enum("fleet-report", "official-news"), "url": ID, "publishedAt": ISO_TIMESTAMP,
    "pages": {**_array(PAGE_SCHEMA), "minItems": 1}, "baseline": _array({"type": "object"}),
    "priorEvidence": _array({"type": "object"}), "contextSha256": HASH, "readerPolicySha256": HASH,
})
_RESPONSE_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", **_object({
    "schemaVersion": {"type": "integer", "const": 2}, "sourceId": ID, "sourceSha256": HASH,
    "company": _enum(*COMPANIES), "reportDate": {"anyOf": [DATE, {"type": "null"}]},
    "readLocators": {**_array(ID), "uniqueItems": True}, "coverage": _array(COVERAGE_SCHEMA),
    "vesselUpdates": _array(VESSEL_UPDATE_SCHEMA), "decisions": _array(DECISION_SCHEMA),
})}
NEWS_JOB_SCHEMA = _object({"jobId": ID, "kind": _enum("fleet-report", "official-news"), "company": _enum(*COMPANIES),
    "url": ID, "sourceSha256": HASH, "contextSha256": HASH, "readerPolicySha256": HASH})
NEWS_QUEUE_SCHEMA = _object({"schemaVersion": {"type": "integer", "const": 2},
    "pendingCount": {"type": "integer", "minimum": 0}, "fingerprint": HASH, "jobs": _array(NEWS_JOB_SCHEMA)})
# Validate array membership separately so one invalid decision does not poison
# the otherwise trustworthy response envelope.
_ENVELOPE_SCHEMA = {**_RESPONSE_SCHEMA, "properties": {**_RESPONSE_SCHEMA["properties"],
    "decisions": {"type": "array"}, "vesselUpdates": {"type": "array"}}}
def _strict_reader_schema(value: Any) -> Any:
    # Decoder schemas require every object property in required. Optional row
    # provenance is nullable, representing no supplied value; application must
    # preserve existing provenance when a model returns null for those fields.
    if isinstance(value, list):
        return [_strict_reader_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_reader_schema(item) for key, item in value.items()}
    if result.get("type") == "object":
        result["required"] = list(result["properties"])
        result["additionalProperties"] = False
    return result


READER_RESPONSE_SCHEMA = _strict_reader_schema(_RESPONSE_SCHEMA)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=(ValueError, OverflowError))
def _iso_datetime(value: Any) -> bool:
    # jsonschema's RFC3339 checker is an optional extra, absent from the pinned
    # runtime. Keep date-time validation active using the standard library.
    if not isinstance(value, str):
        return True
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})", value) is None:
        return False
    datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    return True


DURATION_ASSUMPTION = "1 year = 365 days; 1 month = 365/12 days"


def _validate_schema(value: Any, schema: dict, *, check_finite: bool = True) -> None:
    errors = sorted(Draft202012Validator(schema, format_checker=_FORMAT_CHECKER).iter_errors(value),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ValueError(f"{path}: {error.message}")
    if check_finite:
        _finite_numbers(value)


def _finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("numbers must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _finite_numbers(item)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def duration_days(*, years: float = 0, months: float = 0, days: float = 0) -> float:
    """Convert already-selected numeric units without parsing source prose."""
    values = (years, months, days)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
           not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("duration units must be finite nonnegative numbers")
    result = years * 365 + months * (365 / 12) + days
    if not math.isfinite(result):
        raise ValueError("duration result must be finite")
    return result


def source_sha256(pages: list[ReadingPage]) -> str:
    """Digest exactly the ordered locator/text input, not original PDF bytes."""
    return _digest(pages)


def context_sha256(baseline: list[dict], prior_evidence: list[dict]) -> str:
    return _digest({"baseline": baseline, "priorEvidence": prior_evidence})


def policy_sha256(reader_policy: str) -> str:
    return hashlib.sha256(reader_policy.encode("utf-8")).hexdigest()


def _registry(request: ReadingInput) -> tuple[dict[str, dict], dict[str, tuple[str, dict]]]:
    vessels: dict[str, dict] = {}
    contracts: dict[str, tuple[str, dict]] = {}
    for vessel in request["baseline"]:
        vessel_id = vessel.get("id")
        if not isinstance(vessel_id, str) or not vessel_id or vessel_id in vessels:
            raise ValueError("baseline vessel IDs must be nonempty and unique")
        if vessel.get("company") not in COMPANIES or not isinstance(vessel.get("contracts"), list):
            raise ValueError("baseline vessel company/contracts are required")
        vessels[vessel_id] = vessel
        for contract in vessel["contracts"]:
            if not isinstance(contract, dict):
                raise ValueError("baseline contracts must be objects")
            contract_id = contract.get("id")
            if not isinstance(contract_id, str) or not contract_id or contract_id in contracts:
                raise ValueError("baseline contract IDs must be nonempty and globally unique")
            if contract.get("vesselId") != vessel_id:
                raise ValueError("baseline contract vesselId does not match its vessel")
            contracts[contract_id] = (vessel_id, contract)
    return vessels, contracts


def validate_reading_input(request: ReadingInput, *, reader_policy: str | None = None) -> None:
    """Raise DocumentValidationError on invalid input or stale input hashes.

    Provide reader_policy to verify its digest against actual policy text. When
    omitted, only hash syntax can be checked at this boundary.
    """
    try:
        _validate_schema(request, READING_INPUT_SCHEMA)
        locators = [page["locator"] for page in request["pages"]]
        if len(set(locators)) != len(locators):
            raise ValueError("pages locators must be unique")
        if request["sourceSha256"] != source_sha256(request["pages"]):
            raise ValueError("sourceSha256 does not match locator/text input")
        if request["contextSha256"] != context_sha256(request["baseline"], request["priorEvidence"]):
            raise ValueError("contextSha256 does not match baseline/priorEvidence")
        if reader_policy is not None and request["readerPolicySha256"] != policy_sha256(reader_policy):
            raise ValueError("readerPolicySha256 does not match reader policy")
        _registry(request)
    except (ValueError, TypeError, OverflowError) as error:
        raise DocumentValidationError(str(error)) from error


def _check_vessel(vessel_id: str, request: ReadingInput, vessels: dict[str, dict]) -> None:
    if vessel_id not in vessels or vessels[vessel_id]["company"] != request["company"]:
        raise ValueError(f"unknown vessel or company mismatch: {vessel_id}")


def _check_interpretation(value: ContractInterpretation, *, public: bool = False) -> None:
    if public and value["dateBasis"] == "unresolved":
        raise ValueError("unresolved dates are facts only; no public row")
    if value["dateBasis"] == "placement" and not value["placementBasis"]:
        raise ValueError("placement dates require placementBasis")
    reported, derived = value["reportedUsdPerDay"], value["derivedUsdPerDay"]
    basis = value["rateBasis"]
    if basis == "reported" and reported is None:
        raise ValueError("reported rate requires reportedUsdPerDay")
    if basis == "backlog-derived" and derived is None:
        raise ValueError("backlog-derived rate requires derivedUsdPerDay")
    if basis == "undisclosed" and (reported is not None or derived is not None):
        raise ValueError("undisclosed rate must not carry a reported or derived rate")
    if derived is not None:
        if value["maximumTerm"]:
            raise ValueError("maximum term cannot produce a single derived rate")
        amount, days = value["valueUsd"], value["baseDurationDays"]
        if amount is None or days is None or days <= 0 or not value["durationAssumption"]:
            raise ValueError("derived rate requires valueUsd, positive baseDurationDays and durationAssumption")
        # USD/day supplied by the model; tolerate at most half a dollar of
        # rounding without selecting another amount/term pair from the source.
        if not math.isclose(derived, amount / days, rel_tol=1e-9, abs_tol=0.5):
            raise ValueError("derivedUsdPerDay does not equal valueUsd / baseDurationDays")


_MISSING = object()


def _value_at_path(target: dict, path: str) -> Any:
    value: Any = target
    for key in path.split("."):
        if not key or not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _evidence_paths(name: str, target: dict) -> set[str]:
    """Resolve a real claim path; aliases never merge different claim values."""
    if name in {"row", "facts"} or name.split(".")[0] in {"evidence", "reasonKo"}:
        raise ValueError(f"evidence field must identify a claim: {name}")
    direct = _value_at_path(target, name)
    if direct is not _MISSING:
        return {name}
    if name.split(".")[0] in {"row", "facts"}:
        raise ValueError(f"evidence field does not exist: {name}")
    candidates = [(f"{scope}.{name}", _value_at_path(target, f"{scope}.{name}"))
                  for scope in ("facts", "row")]
    candidates = [(path, value) for path, value in candidates if value is not _MISSING]
    if not candidates:
        raise ValueError(f"evidence field does not exist: {name}")
    # A null fact makes no affirmative claim. It need not make an otherwise
    # unambiguous row field alias ambiguous, or acquire an invented quotation.
    present = [(path, value) for path, value in candidates if value is not None]
    candidates = present or candidates
    if any(value != candidates[0][1] for _, value in candidates[1:]):
        raise ValueError(f"ambiguous evidence field; use facts./row. path: {name}")
    return {path for path, _ in candidates}


def _check_evidence(items: list[ReadingEvidence], request: ReadingInput, required: set[str], target: dict) -> None:
    """Check exact source text and coverage of actual, scoped claim paths.

    A citation naming an entire interpretation object supports its descendants.
    A citation naming one leaf supports only that leaf, never its siblings or
    parent. Bare aliases are accepted only for equal facts/row claim values.
    """
    pages = {page["locator"]: page["text"] for page in request["pages"]}
    covered: set[str] = set()
    for item in items:
        if item["sourceId"] != request["sourceId"] or item["locator"] not in pages:
            raise ValueError("evidence sourceId/locator is not in the reading input")
        if item["quote"] not in pages[item["locator"]]:
            raise ValueError("evidence quote must be a verbatim substring at its locator")
        for name in item["fields"]:
            covered.update(_evidence_paths(name, target))
    missing = {name for name in required if not any(
        name == key or name.startswith(key + ".") for key in covered)}
    if missing:
        raise ValueError(f"changed fields lack evidence: {sorted(missing)}")


def _interpretation_evidence_fields(value: ContractInterpretation) -> set[str]:
    # These explicit absence/default values are established by reading the whole
    # input. They do not require a quotation claiming that a source said nothing.
    absence = {"startPrecision": "unknown", "endPrecision": "unknown", "dateBasis": "unresolved",
               "awardType": "unreported", "rateBasis": "undisclosed", "maximumTerm": False}
    return {key for key, item in value.items() if item is not None and item != [] and
            not (key in absence and item == absence[key])}


def _facts_evidence_fields(facts: ReadingFacts) -> set[str]:
    fields = {f"facts.{key}" for key in ("client", "region") if facts[key] is not None}
    fields |= {f"facts.interpretation.{key}" for key in _interpretation_evidence_fields(facts["interpretation"])}
    return fields


def _check_row_metadata(row: ReadingRow, baseline: dict | None, request: ReadingInput) -> None:
    """Model provenance must preserve baseline or match verified input values.

    Null/missing values are unsupplied, never instructions to clear provenance.
    These system links need no artificial quote from a document's body.
    """
    verified = {"sourceUrl": request["url"], "sourcePublishedAt": request["publishedAt"],
                "newsReviewed": request["kind"] == "official-news",
                "datePrecision": {"start": row["interpretation"]["startPrecision"],
                                  "end": row["interpretation"]["endPrecision"]}}
    for name, expected in verified.items():
        supplied = row.get(name)
        if supplied is None:
            continue
        if baseline is not None and supplied == baseline.get(name, _MISSING):
            continue
        if supplied != expected:
            raise ValueError(f"{name} must preserve baseline or match verified reading input/interpretation")


def _check_decision(item: ReadingDecision, request: ReadingInput, vessels: dict, contracts: dict) -> None:
    _validate_schema(item, DECISION_SCHEMA)
    _check_vessel(item["vesselId"], request, vessels)
    contract_id = item["contractId"]
    baseline = None
    if contract_id is not None:
        if contract_id not in contracts or contracts[contract_id][0] != item["vesselId"]:
            raise ValueError("contractId is unknown or belongs to another vessel/company")
        baseline = contracts[contract_id][1]
    if item["action"] == "withdraw" and contract_id is None:
        raise ValueError("withdraw requires an existing contractId")
    row = item["row"]
    if (item["action"] == "upsert") != (row is not None):
        raise ValueError("upsert requires row; all other actions require null row")
    _check_interpretation(item["facts"]["interpretation"])
    required = _facts_evidence_fields(item["facts"])
    if item["action"] == "withdraw":
        required.add("action")
    if row is not None:
        if row["vesselId"] != item["vesselId"] or row["id"] != contract_id:
            raise ValueError("row IDs must match decision vesselId/contractId; new IDs must be null")
        if row["startDate"] > row["endDate"]:
            raise ValueError("row startDate must not exceed endDate")
        _check_interpretation(row["interpretation"], public=True)
        if row["status"] == "Firm" and row["interpretation"]["awardType"] in {"letter-of-award", "binding-letter-of-award"}:
            raise ValueError("LOA enum cannot be promoted to Firm")
        reported = row["interpretation"]["reportedUsdPerDay"]
        expected_day_rate = reported if row["interpretation"]["rateBasis"] == "reported" else 0
        if row["dayRate"] != expected_day_rate:
            raise ValueError("dayRate must preserve the disclosed reported rate, otherwise zero")
        _check_row_metadata(row, baseline, request)
        fields = {"startDate", "endDate", "dayRate", "client", "region", "status"}
        # Zero is the public representation of a non-reported rate, not a new
        # source claim. A source-absence quote must never be invented for it.
        if row["interpretation"]["rateBasis"] != "reported":
            fields.remove("dayRate")
        required |= {f"row.{key}" for key in fields if baseline is None or row[key] != baseline.get(key)}
        old_interpretation = (baseline or {}).get("interpretation") or {}
        required |= {f"row.interpretation.{key}" for key in _interpretation_evidence_fields(row["interpretation"])
                     if row["interpretation"][key] != old_interpretation.get(key, _MISSING)}
    _check_evidence(item["evidence"], request, required, item)


def validate_reading_response(response: ReadingResponse, request: ReadingInput) -> ReadingValidationResult:
    """Validate the envelope atomically and isolate individual item failures.

    Report coverage uses the fixed key ``state``. It must contain every registry
    vessel in request.company exactly once. News may have no relevant decisions.
    readLocators must cover every input locator, for either kind of source.
    """
    validate_reading_input(request)
    try:
        # Do not recursively inspect item numbers at this stage: NaN/Infinity in
        # one decision must be isolated along with its other schema errors.
        _validate_schema(response, _ENVELOPE_SCHEMA, check_finite=False)
        for key in ("sourceId", "sourceSha256", "company"):
            if response[key] != request[key]:
                raise ValueError(f"response {key} does not match input")
        if set(response["readLocators"]) != {page["locator"] for page in request["pages"]}:
            raise ValueError("readLocators must cover all input locations")
        vessels, contracts = _registry(request)
        coverage = response["coverage"]
        coverage_ids = [item["vesselId"] for item in coverage]
        if len(coverage_ids) != len(set(coverage_ids)):
            raise ValueError("coverage vessel IDs must be unique")
        for vessel_id in coverage_ids:
            _check_vessel(vessel_id, request, vessels)
        expected = {key for key, vessel in vessels.items() if vessel["company"] == request["company"]}
        if request["kind"] == "fleet-report" and set(coverage_ids) != expected:
            raise ValueError("report coverage must include every company registry vessel")
    except (ValueError, TypeError, AttributeError) as error:
        raise DocumentValidationError(str(error)) from error

    result = ReadingValidationResult()
    # Reject all competing uses of one existing ID; never choose a winner by
    # response order. New null-ID rows are distinct proposals for reconciliation.
    decision_ids = Counter(item.get("contractId") for item in response["decisions"]
                           if isinstance(item, dict) and isinstance(item.get("contractId"), str))
    update_ids = Counter(item.get("vesselId") for item in response["vesselUpdates"]
                         if isinstance(item, dict) and isinstance(item.get("vesselId"), str))
    for index, item in enumerate(response["decisions"]):
        try:
            _check_decision(item, request, vessels, contracts)
            if item["contractId"] is not None and decision_ids[item["contractId"]] > 1:
                raise ValueError("duplicate contractId decisions")
            result.decisions.append(item)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            result.issues.append(ValidationIssue("decisions", index, str(error)))
    for index, item in enumerate(response["vesselUpdates"]):
        try:
            _validate_schema(item, VESSEL_UPDATE_SCHEMA)
            _check_vessel(item["vesselId"], request, vessels)
            if update_ids[item["vesselId"]] > 1:
                raise ValueError("duplicate vesselUpdates vesselId")
            _check_evidence(item["evidence"], request, {"status", "statusAsOf"}, item)
            result.vessel_updates.append(item)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            result.issues.append(ValidationIssue("vesselUpdates", index, str(error)))
    return result


def validate_news_queue(queue: NewsQueue) -> None:
    """Validate persisted job identities. URLs/job IDs are data, never commands.

    The scheduling layer owns fingerprint construction and source-file digests;
    this boundary checks digest syntax, job identity and pending count.
    """
    try:
        _validate_schema(queue, NEWS_QUEUE_SCHEMA)
        if queue["pendingCount"] != len(queue["jobs"]):
            raise ValueError("pendingCount does not match jobs")
        ids = [item["jobId"] for item in queue["jobs"]]
        if len(ids) != len(set(ids)):
            raise ValueError("jobId must be unique")
    except (ValueError, TypeError) as error:
        raise DocumentValidationError(str(error)) from error
