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
    page: int
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


@dataclass
class VesselSnapshot:
    name: str
    status: ShipStatus
    contracts: list[ContractObservation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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


@dataclass
class ParseResult:
    company: Company
    vessels: dict[str, VesselSnapshot]
    warnings: list[str] = field(default_factory=list)
    pending_events: list[dict] = field(default_factory=list)

