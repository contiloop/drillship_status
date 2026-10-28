from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Company = Literal["Transocean", "Valaris", "Noble", "Seadrill"]
ContractStatus = Literal["Firm", "Option", "Contingent"]
ShipStatus = Literal["Active", "Idle", "Warm-Stacked", "Cold-Stacked"]


@dataclass(frozen=True)
class ContractObservation:
    client: str
    region: str
    start_date: str
    end_date: str
    day_rate: int
    status: ContractStatus
    # HTML exhibits do not have a meaningful PDF page number.  ``row`` stays
    # required so every observation still has an exact source locator.
    page: int | None
    row: str
    date_precision: str = "month"
    rate_disclosure: str = "reported"

    def canonical_key(self) -> tuple[str, ...]:
        return (
            self.start_date,
            self.end_date,
            self.client.casefold(),
            self.region.casefold(),
            self.status,
            str(self.day_rate),
        )


@dataclass(frozen=True)
class OperationalObservation:
    note: str
    page: int | None
    row: str


@dataclass
class VesselSnapshot:
    name: str
    status: ShipStatus
    contracts: list[ContractObservation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    operational_observations: list[OperationalObservation] = field(default_factory=list)


@dataclass(frozen=True)
class SourceDocument:
    company: Company
    index_url: str
    document_url: str
    report_date: str
    sha256: str
    byte_size: int
    retrieved_at: str
    parser_version: str
    discovery: str
    content_type: str

    @property
    def document_sha256(self) -> str:
        """Original downloaded bytes; distinct from a reader input digest."""
        return self.sha256


@dataclass
class ParseResult:
    company: Company
    vessels: dict[str, VesselSnapshot]
    warnings: list[str] = field(default_factory=list)
    pending_events: list[dict] = field(default_factory=list)


# Version 2 reading contracts are additive: existing parser observations and
# published rows can continue to omit this metadata while adapters migrate.
DatePrecision = Literal["day", "month", "quarter", "half-year", "year", "approximate", "unknown"]
DateBasis = Literal["source", "placement", "unresolved"]
RateBasis = Literal["reported", "backlog-derived", "undisclosed"]
AwardType = Literal["signed-contract", "binding-letter-of-award", "letter-of-award", "option-exercised", "extension", "unreported"]
ReadingKind = Literal["fleet-report", "official-news"]
ReadingInputMode = Literal["document", "stored-result"]
ReadingAction = Literal["upsert", "withdraw", "no-change", "hold"]
