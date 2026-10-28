"""Contracts at the model-reader port: wire schemas plus a screen-safety validator.

The reader (Codex CLI) returns JSON decisions about one document. Code checks
only what keeps the screen safe; everything about meaning (reasons, quotes,
page coverage, arithmetic) is the model's job and never blocks application.

Reading response (``READER_RESPONSE_SCHEMA`` for ``codex --output-schema``)::

    {"reportDate": "YYYY-MM-DD" | null,
     "vesselUpdates": [{"vesselId", "status", "statusAsOf", "reasonKo"?, "quote"?}],
     "decisions": [{"vesselId", "contractId": str | null,
                    "action": "upsert" | "withdraw" | "no-change" | "hold",
                    "row": {startDate, endDate, dayRate, client, region, status,
                            interpretation} | null,          # upsert only
                    "reasonKo"?, "quote"?, "locator"?}]}

The wire schema follows the Structured Outputs subset (all properties listed in
``required``; optional values are nullable). Locally, optional fields may be
missing, and extra envelope fields (schemaVersion, sourceId, sourceSha256,
company, readLocators, coverage, ...) are tolerated and ignored.

``validate_reading_response(response, request)``:
  * raises ``MalformedResponseError`` (a ``DocumentValidationError``) when the
    response is not an object or lacks ``decisions``/``vesselUpdates`` arrays;
    the caller should re-request (up to 3 times).
  * otherwise classifies each item as valid or held. Held items are returned in
    ``issues`` with their original index, reason and raw item; valid items
    are normalized and applied independently of held ones.

Checks per item: vesselId is a registered vessel of ``request.company``; a
non-null contractId is an existing contract of that vessel; withdraw needs a
contractId; upsert needs a row; dates are real YYYY-MM-DD and start <= end;
numbers are finite and >= 0 (baseDurationDays > 0); status enums. Missing or
unmatched reasonKo/quote/locator never hold anything.

Normalized decision: ``{vesselId, contractId, action, row}`` plus any of
``reasonKo``/``quote``/``locator`` given as non-empty strings. ``row`` is None
unless action is upsert; then it holds exactly ``startDate, endDate, dayRate,
client, region, status, interpretation``. ``interpretation`` always exists and
``derivedUsdPerDay`` is recomputed by ``derived_usd_per_day`` (the model value
is discarded). Normalized vessel update: ``{vesselId, status, statusAsOf}``
plus optional ``reasonKo``/``quote``.

No natural-language dates or amounts are parsed here.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from .model import AwardType, Company, ContractStatus, ReadingAction, ReadingInputMode, ReadingKind, ShipStatus

COMPANIES = ("Transocean", "Valaris", "Noble", "Seadrill")
KINDS = ("fleet-report", "official-news")
INPUT_MODES = ("document", "stored-result")
ACTIONS = ("upsert", "withdraw", "no-change", "hold")
CONTRACT_STATUSES = ("Firm", "Option", "Contingent")
SHIP_STATUSES = ("Active", "Idle", "Warm-Stacked", "Cold-Stacked")
AWARD_TYPES = ("signed-contract", "letter-of-award", "option-exercised", "extension", "unreported")
# Accepted when reading previously published rows; never offered to the model.
LEGACY_AWARD_TYPES = ("binding-letter-of-award",)
INTERPRETATION_TEXT_FIELDS = ("sourceStartText", "sourceEndText", "placementBasis",
                              "termText", "optionText", "valueText")
ROW_FIELDS = ("startDate", "endDate", "dayRate", "client", "region", "status", "interpretation")

DURATION_ASSUMPTION = "1 year = 365 days; 1 month = 365/12 days"
MAX_PAGE_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_IMAGE_BYTES = 40 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_SHA256 = re.compile(r"[a-f0-9]{64}", re.ASCII)
STORED_REVIEW_LOCATOR = "stored-review"


# --- types -----------------------------------------------------------------

class ContractInterpretation(TypedDict, total=False):
    sourceStartText: str | None
    sourceEndText: str | None
    placementBasis: str | None
    awardType: AwardType | None
    termText: str | None
    optionText: str | None
    valueText: str | None
    valueUsd: float | None
    baseDurationDays: float | None
    includes: list[str]
    excludes: list[str]
    derivedUsdPerDay: float | None  # computed by code only


class ReadingRow(TypedDict):
    startDate: str
    endDate: str
    dayRate: float
    client: str
    region: str
    status: ContractStatus
    interpretation: ContractInterpretation


class ReadingDecision(TypedDict):
    vesselId: str
    contractId: str | None
    action: ReadingAction
    row: ReadingRow | None
    reasonKo: NotRequired[str]
    quote: NotRequired[str]
    locator: NotRequired[str]


class VesselUpdate(TypedDict):
    vesselId: str
    status: ShipStatus
    statusAsOf: str
    reasonKo: NotRequired[str]
    quote: NotRequired[str]


class ReadingResponse(TypedDict):
    reportDate: NotRequired[str | None]
    vesselUpdates: list[VesselUpdate]
    decisions: list[ReadingDecision]


class ReadingPageImage(TypedDict):
    mimeType: Literal["image/png"]
    dataBase64: str
    sha256: NotRequired[str]


class ReadingPage(TypedDict):
    locator: str
    text: str
    image: NotRequired[ReadingPageImage]


class ReadingInput(TypedDict):
    sourceId: str
    company: Company
    kind: ReadingKind
    url: str
    publishedAt: str | None
    pages: list[ReadingPage]
    baseline: list[dict[str, Any]]
    openHolds: NotRequired[list[dict[str, Any]]]
    inputMode: NotRequired[ReadingInputMode]


class NewsJob(TypedDict):
    jobId: str
    kind: ReadingKind
    company: Company
    url: str
    documentSha256: str


class NewsQueue(TypedDict):
    schemaVersion: Literal[2]
    pendingCount: int
    fingerprint: NotRequired[str]
    jobs: list[NewsJob]


class ReadingPort(Protocol):
    """Replaceable reader adapter; invocation and persistence live outside it."""

    def read(self, request: ReadingInput) -> ReadingResponse: ...


class DocumentValidationError(ValueError):
    """The input or response as a whole is unusable; nothing from it applies."""


class MalformedResponseError(DocumentValidationError):
    """The model output is not the expected JSON shape; re-request it."""


@dataclass(frozen=True)
class ValidationIssue:
    """One held item: collection ("decisions", "vesselUpdates", "reportDate"),
    its index in the response, a short reason and the raw item as received."""

    collection: str
    index: int
    message: str
    item: Any = None


@dataclass
class ReadingValidationResult:
    decisions: list[ReadingDecision] = field(default_factory=list)
    vessel_updates: list[VesselUpdate] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    report_date: str | None = None


# --- wire schemas (Structured Outputs subset) ------------------------------

def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _nullable(kind: str) -> dict:
    return {"type": [kind, "null"]}


def _enum(values: tuple[str, ...], *, nullable: bool = False) -> dict:
    if nullable:
        return {"type": ["string", "null"], "enum": [*values, None]}
    return {"type": "string", "enum": list(values)}


_WIRE_DATE = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
_WIRE_INTERPRETATION = _object({
    **{name: _nullable("string") for name in INTERPRETATION_TEXT_FIELDS},
    "awardType": _enum(AWARD_TYPES, nullable=True),
    "valueUsd": _nullable("number"), "baseDurationDays": _nullable("number"),
    "includes": {"type": "array", "items": {"type": "string"}},
    "excludes": {"type": "array", "items": {"type": "string"}},
})
_WIRE_ROW = _object({
    "startDate": _WIRE_DATE, "endDate": _WIRE_DATE, "dayRate": {"type": "number"},
    "client": {"type": "string"}, "region": {"type": "string"},
    "status": _enum(CONTRACT_STATUSES), "interpretation": _WIRE_INTERPRETATION,
})
READER_RESPONSE_SCHEMA = _object({
    "reportDate": {"type": ["string", "null"], "pattern": _WIRE_DATE["pattern"]},
    "vesselUpdates": {"type": "array", "items": _object({
        "vesselId": {"type": "string"}, "status": _enum(SHIP_STATUSES), "statusAsOf": _WIRE_DATE,
        "reasonKo": _nullable("string"), "quote": _nullable("string")})},
    "decisions": {"type": "array", "items": _object({
        "vesselId": {"type": "string"}, "contractId": _nullable("string"), "action": _enum(ACTIONS),
        "row": {"anyOf": [_WIRE_ROW, {"type": "null"}]},
        "reasonKo": _nullable("string"), "quote": _nullable("string"), "locator": _nullable("string")})},
})
TITLE_TRIAGE_SCHEMA = _object({"selected": {"type": "array", "items": {"type": "string"}}})


# --- small checks ----------------------------------------------------------

def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_texts(source: dict, names: tuple[str, ...]) -> dict:
    """Keep optional display text only when it is a non-empty string."""
    return {name: source[name] for name in names if _nonempty(source.get(name))}


# --- derived rate ----------------------------------------------------------

def duration_days(*, years: float = 0, months: float = 0, days: float = 0) -> float:
    """Convert already-selected numeric units (1 year = 365, 1 month = 365/12)."""
    values = (years, months, days)
    if not all(_is_number(value) and value >= 0 for value in values):
        raise ValueError("duration units must be finite nonnegative numbers")
    return years * 365 + months * (365 / 12) + days


def derived_usd_per_day(value_usd: Any, base_duration_days: Any) -> float | None:
    """valueUsd / baseDurationDays when both are usable numbers, else None."""
    if not (_is_number(value_usd) and _is_number(base_duration_days)):
        return None
    if value_usd < 0 or base_duration_days <= 0:
        return None
    return value_usd / base_duration_days


def normalize_interpretation(value: Any) -> ContractInterpretation:
    """Check an interpretation's safety-relevant types and recompute the rate.

    Missing or null means an empty interpretation. Unknown keys (including the
    legacy dateBasis/startPrecision/rateBasis/... of published rows) are kept
    verbatim; the model's derivedUsdPerDay is always replaced. Raises
    ValueError for unsafe values.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("interpretation must be an object")
    result = dict(value)
    for name in INTERPRETATION_TEXT_FIELDS:
        if result.get(name) is not None and not isinstance(result[name], str):
            raise ValueError(f"interpretation.{name} must be text")
    award = result.get("awardType")
    if award is not None and award not in AWARD_TYPES + LEGACY_AWARD_TYPES:
        raise ValueError(f"interpretation.awardType is invalid: {award}")
    amount, days = result.get("valueUsd"), result.get("baseDurationDays")
    if amount is not None and not (_is_number(amount) and amount >= 0):
        raise ValueError("interpretation.valueUsd must be a finite number >= 0")
    if days is not None and not (_is_number(days) and days > 0):
        raise ValueError("interpretation.baseDurationDays must be a finite number > 0")
    for name in ("includes", "excludes"):
        items = result.get(name)
        if items is None:
            result[name] = []
        elif not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError(f"interpretation.{name} must be a list of text")
    result["derivedUsdPerDay"] = derived_usd_per_day(amount, days)
    return result  # type: ignore[return-value]


# --- response validation ---------------------------------------------------

def parse_reading_response(text: str) -> dict:
    """Decode model output text; non-JSON or non-object raises MalformedResponseError."""
    def reject_constant(name: str) -> Any:
        raise ValueError(f"non-JSON number {name}")
    try:
        value = json.loads(text, parse_constant=reject_constant)
    except (TypeError, ValueError) as error:
        raise MalformedResponseError(f"response is not JSON: {error}") from error
    _check_envelope(value)
    return value


def _check_envelope(response: Any) -> None:
    if not isinstance(response, dict):
        raise MalformedResponseError("response must be a JSON object")
    for key in ("decisions", "vesselUpdates"):
        if not isinstance(response.get(key), list):
            raise MalformedResponseError(f"response.{key} must be an array")


def _registry(request: ReadingInput) -> tuple[set[str], dict[str, str]]:
    """Vessel IDs of request.company and {contractId: vesselId} for them."""
    vessels: set[str] = set()
    contracts: dict[str, str] = {}
    for vessel in request["baseline"]:
        if not isinstance(vessel, dict) or vessel.get("company") != request["company"]:
            continue
        vessel_id = vessel.get("id")
        if not isinstance(vessel_id, str):
            continue
        vessels.add(vessel_id)
        for contract in vessel.get("contracts") or []:
            if isinstance(contract, dict) and isinstance(contract.get("id"), str):
                contracts[contract["id"]] = vessel_id
    return vessels, contracts


def _row(value: Any) -> ReadingRow:
    if not isinstance(value, dict):
        raise ValueError("upsert requires a row object")
    for name in ("startDate", "endDate"):
        if not _is_date(value.get(name)):
            raise ValueError(f"row.{name} must be a real YYYY-MM-DD date")
    if value["startDate"] > value["endDate"]:
        raise ValueError("row.startDate is after row.endDate")
    if not (_is_number(value.get("dayRate")) and value["dayRate"] >= 0):
        raise ValueError("row.dayRate must be a finite number >= 0")
    for name in ("client", "region"):
        if not _nonempty(value.get(name)):
            raise ValueError(f"row.{name} must be non-empty text")
    if value.get("status") not in CONTRACT_STATUSES:
        raise ValueError(f"row.status is invalid: {value.get('status')}")
    row = {name: value[name] for name in ROW_FIELDS if name != "interpretation"}
    row["interpretation"] = normalize_interpretation(value.get("interpretation"))
    return row  # type: ignore[return-value]


def _decision(item: Any, vessels: set[str], contracts: dict[str, str]) -> ReadingDecision:
    if not isinstance(item, dict):
        raise ValueError("decision must be an object")
    vessel_id, contract_id, action = item.get("vesselId"), item.get("contractId"), item.get("action")
    if vessel_id not in vessels:
        raise ValueError(f"vessel is not registered for this company: {vessel_id}")
    if action not in ACTIONS:
        raise ValueError(f"action is invalid: {action}")
    if contract_id is not None and contracts.get(contract_id) != vessel_id:
        raise ValueError(f"contractId is not an existing contract of {vessel_id}: {contract_id}")
    if action == "withdraw" and contract_id is None:
        raise ValueError("withdraw requires contractId")
    row = _row(item.get("row")) if action == "upsert" else None
    return {"vesselId": vessel_id, "contractId": contract_id, "action": action, "row": row,
            **_optional_texts(item, ("reasonKo", "quote", "locator"))}


def _vessel_update(item: Any, vessels: set[str]) -> VesselUpdate:
    if not isinstance(item, dict):
        raise ValueError("vessel update must be an object")
    if item.get("vesselId") not in vessels:
        raise ValueError(f"vessel is not registered for this company: {item.get('vesselId')}")
    if item.get("status") not in SHIP_STATUSES:
        raise ValueError(f"vessel status is invalid: {item.get('status')}")
    if not _is_date(item.get("statusAsOf")):
        raise ValueError("statusAsOf must be a real YYYY-MM-DD date")
    return {"vesselId": item["vesselId"], "status": item["status"], "statusAsOf": item["statusAsOf"],
            **_optional_texts(item, ("reasonKo", "quote"))}


def validate_reading_response(response: Any, request: ReadingInput) -> ReadingValidationResult:
    """Screen-safety validation; see module docstring for the rules."""
    _check_envelope(response)
    vessels, contracts = _registry(request)
    result = ReadingValidationResult()
    report_date = response.get("reportDate")
    if report_date is not None:
        if _is_date(report_date):
            result.report_date = report_date
        else:
            result.issues.append(ValidationIssue("reportDate", 0, "reportDate is not a real YYYY-MM-DD date", report_date))
    for index, item in enumerate(response["decisions"]):
        try:
            result.decisions.append(_decision(item, vessels, contracts))
        except (ValueError, TypeError) as error:
            result.issues.append(ValidationIssue("decisions", index, str(error), item))
    for index, item in enumerate(response["vesselUpdates"]):
        try:
            result.vessel_updates.append(_vessel_update(item, vessels))
        except (ValueError, TypeError) as error:
            result.issues.append(ValidationIssue("vesselUpdates", index, str(error), item))
    return result


def select_triage_ids(response: Any, candidate_ids: list[str]) -> list[str]:
    """IDs from ``{"selected": [...]}`` that exist in candidate_ids, in candidate order."""
    if not isinstance(response, dict) or not isinstance(response.get("selected"), list):
        raise MalformedResponseError("triage response must be {\"selected\": [ids]}")
    chosen = {item for item in response["selected"] if isinstance(item, str)}
    return [item for item in dict.fromkeys(candidate_ids) if item in chosen]


# --- reading input ---------------------------------------------------------

def stored_review_page(record: dict[str, Any]) -> ReadingPage:
    """The single page of a stored-result input: the saved review as JSON text."""
    return {"locator": STORED_REVIEW_LOCATOR,
            "text": json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)}


def _check_image(locator: str, image: Any) -> int:
    if not isinstance(image, dict) or image.get("mimeType") != "image/png":
        raise ValueError(f"{locator}: image must be an image/png object")
    encoded = image.get("dataBase64")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_PAGE_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError(f"{locator}: image data missing or over the size limit")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{locator}: image data is not base64") from error
    if len(content) > MAX_PAGE_IMAGE_BYTES:
        raise ValueError(f"{locator}: image is over the size limit")
    if not content.startswith(_PNG_SIGNATURE):
        raise ValueError(f"{locator}: image is not a PNG")
    return len(content)


def validate_reading_input(request: Any, *, reader_policy: str | None = None) -> None:
    """Basic shape and page-image checks; raises DocumentValidationError.

    ``reader_policy`` is accepted for compatibility and ignored. Extra fields
    (old hashes, priorEvidence, storedReading, ...) are tolerated, not verified.
    """
    try:
        if not isinstance(request, dict):
            raise ValueError("reading input must be an object")
        for name in ("sourceId", "url"):
            if not _nonempty(request.get(name)):
                raise ValueError(f"{name} must be non-empty text")
        if request.get("company") not in COMPANIES:
            raise ValueError("company is invalid")
        if request.get("kind") not in KINDS:
            raise ValueError("kind is invalid")
        if request.get("publishedAt") is not None and not isinstance(request["publishedAt"], str):
            raise ValueError("publishedAt must be text or null")
        mode = request.get("inputMode", "document")
        if mode not in INPUT_MODES:
            raise ValueError("inputMode is invalid")
        baseline = request.get("baseline")
        if not isinstance(baseline, list) or not all(isinstance(item, dict) for item in baseline):
            raise ValueError("baseline must be a list of vessel objects")
        if "openHolds" in request and not isinstance(request["openHolds"], list):
            raise ValueError("openHolds must be a list")
        pages = request.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("pages must be a non-empty list")
        locators: set[str] = set()
        total = 0
        for page in pages:
            if not isinstance(page, dict) or not _nonempty(page.get("locator")) or not isinstance(page.get("text"), str):
                raise ValueError("each page needs a locator and text")
            if page["locator"] in locators:
                raise ValueError(f"duplicate page locator: {page['locator']}")
            locators.add(page["locator"])
            if "image" in page:
                total += _check_image(page["locator"], page["image"])
        if total > MAX_DOCUMENT_IMAGE_BYTES:
            raise ValueError("document images are over the size limit")
        if mode == "stored-result":
            if len(pages) != 1 or "image" in pages[0]:
                raise ValueError("stored-result input is one text page with the saved review JSON")
            if not isinstance(json.loads(pages[0]["text"]), dict):
                raise ValueError("stored review page must be a JSON object")
    except (ValueError, TypeError) as error:
        raise DocumentValidationError(str(error)) from error


# --- news queue ------------------------------------------------------------

def validate_news_queue(queue: Any) -> None:
    """schemaVersion 2, pendingCount == len(jobs), unique jobId, required job fields."""
    try:
        if not isinstance(queue, dict) or queue.get("schemaVersion") != 2:
            raise ValueError("news queue must be schemaVersion 2")
        jobs = queue.get("jobs")
        if not isinstance(jobs, list) or queue.get("pendingCount") != len(jobs):
            raise ValueError("pendingCount does not match jobs")
        ids: set[str] = set()
        for job in jobs:
            if not isinstance(job, dict) or not _nonempty(job.get("jobId")) or not _nonempty(job.get("url")):
                raise ValueError("job needs jobId and url")
            if job.get("kind") not in KINDS or job.get("company") not in COMPANIES:
                raise ValueError(f"job {job['jobId']} has invalid kind/company")
            if not isinstance(job.get("documentSha256"), str) or _SHA256.fullmatch(job["documentSha256"]) is None:
                raise ValueError(f"job {job['jobId']} needs a documentSha256")
            if job["jobId"] in ids:
                raise ValueError("jobId must be unique")
            ids.add(job["jobId"])
    except (ValueError, TypeError) as error:
        raise DocumentValidationError(str(error)) from error


# --- compatibility (callers outside this task still import these) ----------

def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_sha256(pages: list[ReadingPage]) -> str:
    """Compatibility: digest of pages. Not verified by this module."""
    return _digest(pages)


def context_sha256(baseline: list[dict], prior_evidence: list[dict]) -> str:
    """Compatibility: digest of reader context. Not verified by this module."""
    return _digest({"baseline": baseline, "priorEvidence": prior_evidence})


def policy_sha256(reader_policy: str) -> str:
    """Compatibility: digest of reader policy text. Not verified by this module."""
    return hashlib.sha256(reader_policy.encode("utf-8")).hexdigest()
