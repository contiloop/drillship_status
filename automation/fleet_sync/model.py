from __future__ import annotations

from typing import Literal


Company = Literal["Transocean", "Valaris", "Noble", "Seadrill"]
ContractStatus = Literal["Firm", "Option", "Contingent"]
ShipStatus = Literal["Active", "Idle", "Warm-Stacked", "Cold-Stacked"]


# Reader-port vocabulary. Legacy interpretation enums (date/rate basis,
# precision, binding-letter-of-award) may still appear in published rows but
# are no longer produced by the reader.
AwardType = Literal["signed-contract", "letter-of-award", "option-exercised", "extension", "unreported"]
ReadingKind = Literal["fleet-report", "official-news"]
ReadingInputMode = Literal["document", "stored-result"]
ReadingAction = Literal["upsert", "withdraw", "no-change", "hold"]
