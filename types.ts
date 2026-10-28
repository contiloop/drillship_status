
export type Company = 'Transocean' | 'Valaris' | 'Noble' | 'Seadrill';
export type Generation = '6G' | '7G' | '7G+' | '8G';
export type ContractStatus = 'Firm' | 'Option' | 'Contingent';
export type VesselStatus = 'Active' | 'Idle' | 'Warm-Stacked' | 'Cold-Stacked';

export type ReadingInputMode = 'document' | 'stored-result';

// Stored-result rows record only when the saved review was originally read.
// Legacy hash fields may still appear in old published data.
export interface StoredReadingProvenance {
  reviewedAt?: string | null;
  legacyLedger?: string;
  recordSha256?: string;
  originalSourceSha256?: string | null;
}

export interface Contract {
  inputMode?: ReadingInputMode;
  storedReading?: StoredReadingProvenance;
  id: string;
  vesselId: string;
  startDate: string;
  endDate: string;
  dayRate: number;
  client: string;
  region: string;
  status: ContractStatus;
  newsReviewed?: boolean;
  sourceUrl?: string;
  sourcePublishedAt?: string;
  datePrecision?: { start: string; end: string };
  interpretation?: ContractInterpretation;
}

export interface Drillship {
  id: string;
  name: string;
  company: Company;
  generation: Generation;
  status: VesselStatus;
  statusAsOf?: string;
  yearBuilt: number;
  contracts: Contract[];
}

export interface FleetData {
  drillships: Drillship[];
}

export interface FleetSource {
  company: Company;
  indexUrl: string;
  documentUrl: string;
  reportDate: string;
  sha256: string;
  documentSha256?: string;
  discovery: string;
}

export interface FleetManifest {
  schemaVersion: number;
  generatedAt: string;
  updatedAsOf: string;
  dataHash: string;
  stateFingerprint: string;
  fleetFile: string;
  eventsFile: string;
  eventsHash: string;
  shipCount: number;
  contractCount: number;
  sourceCount: number;
  sources: FleetSource[];
  warningsCount: number;
  pendingEventCount: number;
  newsQueue?: NewsQueue;
  newsReview?: { engine: string; model: string; reasoningEffort: string; fast: boolean; reviewedVesselEvents: number; detailsIncompleteCount: number };
}

export interface OfficialEvent {
  /** News-list tag: 계약 · 실적 · 일정 · 자금·주주 · 경영·인사 · Fleet Status · 기타 */
  newsCategory?: string;
  inputMode?: ReadingInputMode;
  storedReading?: StoredReadingProvenance;
  readingDecision?: EventReadingDecision;
  company: Company;
  title?: string;
  url?: string;
  publishedAt?: string;
  vessels?: string[];
  vessel?: string;
  classification: string;
  autoApplied: boolean;
  pendingReview?: boolean;
  reviewStatus?: 'pending' | 'acknowledged' | 'date-unverified' | 'applied' | 'details-incomplete' | 'reviewed' | 'conflict' | 'superseded' | 'covered-by-report' | 'not-contract-news';
  needsDetails?: boolean;
  llmReview?: { engine: string; model: string; reasoningEffort: string; fast: boolean; reviewedAt: string };
  evidence?: { field: string; quote: string }[];
  start?: string;
  end?: string;
  reason?: string;
  facts?: {
    counterparty?: string;
    location?: string;
    expectedStart?: string;
    expectedEnd?: string;
    startPrecision?: string;
    awardType?: string;
    awardTermYears?: number;
    optionTermYears?: number;
    optionEndIfFullyExercised?: string;
    announcedValueUsdApprox?: number;
    valueIncludes?: string[];
    valueExcludes?: string[];
    termText?: string;
    optionText?: string;
    valueText?: string;
    dayRateDisclosure?: 'reported' | 'undisclosed' | 'not-extracted';
    dayRateUsd?: number;
    exactDatesInferred?: boolean;
    dayRateInferred?: boolean;
  };
}


// Reader port. Code checks only screen safety; reasonKo/quote/locator are
// optional display text. Legacy published fields stay optional so old data loads.
export type AwardType = 'signed-contract' | 'letter-of-award' | 'option-exercised' | 'extension' | 'unreported'
  | 'binding-letter-of-award'; // legacy
export type ReadingKind = 'fleet-report' | 'official-news';
export type ReadingAction = 'upsert' | 'withdraw' | 'no-change' | 'hold';
/** @deprecated legacy interpretation enums, no longer produced by the reader */
export type DatePrecision = 'day' | 'month' | 'quarter' | 'half-year' | 'year' | 'approximate' | 'unknown';
/** @deprecated */
export type DateBasis = 'source' | 'placement' | 'unresolved';
/** @deprecated */
export type RateBasis = 'reported' | 'backlog-derived' | 'undisclosed';

export interface ContractInterpretation {
  sourceStartText?: string | null;
  sourceEndText?: string | null;
  /** Korean explanation of how undated text was placed on the timeline. */
  placementBasis?: string | null;
  awardType?: AwardType | null;
  termText?: string | null;
  optionText?: string | null;
  valueText?: string | null;
  valueUsd?: number | null;
  baseDurationDays?: number | null;
  includes?: string[];
  excludes?: string[];
  /** Computed by code as valueUsd / baseDurationDays; never taken from the model. */
  derivedUsdPerDay?: number | null;
  // Legacy fields (read-only, old published rows).
  dateBasis?: DateBasis;
  startPrecision?: DatePrecision;
  endPrecision?: DatePrecision;
  rateBasis?: RateBasis;
  reportedUsdPerDay?: number | null;
  maximumTerm?: boolean;
  durationAssumption?: string | null;
  /** Never set; lets callers accept `Contract | ContractInterpretation` and read `.interpretation`. */
  interpretation?: never;
}

export interface ReadingPage {
  locator: string;
  text: string;
  image?: { mimeType: 'image/png'; dataBase64: string; sha256?: string };
}

export interface ReadingInput {
  sourceId: string;
  company: Company;
  kind: ReadingKind;
  url: string;
  publishedAt: string | null;
  pages: ReadingPage[];
  baseline: Drillship[];
  openHolds?: Record<string, unknown>[];
  inputMode?: ReadingInputMode;
}

export interface ReadingRow {
  startDate: string;
  endDate: string;
  dayRate: number;
  client: string;
  region: string;
  status: ContractStatus;
  interpretation?: ContractInterpretation;
}

export interface ReadingDecision {
  vesselId: string;
  contractId: string | null;
  action: ReadingAction;
  row: ReadingRow | null;
  reasonKo?: string | null;
  quote?: string | null;
  locator?: string | null;
}

/** @deprecated legacy evidence shape on old published events */
export interface ReadingEvidence {
  fields?: string[];
  sourceId?: string;
  locator?: string;
  quote: string;
}

/** Legacy facts block on old published events. */
export interface ReadingFacts {
  client?: string | null;
  region?: string | null;
  interpretation?: ContractInterpretation;
}

/** Decision as stored on a published OfficialEvent. */
export interface EventReadingDecision {
  action: ReadingAction;
  contractId: string | null;
  row: ReadingRow | null;
  vesselId?: string;
  reasonKo?: string | null;
  quote?: string | null;
  locator?: string | null;
  /** Why code held this item (screen-safety check), if it did. */
  holdReason?: string;
  facts?: ReadingFacts;
  evidence?: ReadingEvidence[];
}

export interface VesselUpdate {
  vesselId: string;
  status: VesselStatus;
  statusAsOf: string;
  reasonKo?: string | null;
  quote?: string | null;
}

export interface ReadingResponse {
  reportDate: string | null;
  vesselUpdates: VesselUpdate[];
  decisions: ReadingDecision[];
}

export interface TitleTriageResponse {
  selected: string[];
}

export interface NewsJob {
  jobId: string;
  kind: ReadingKind;
  company: Company;
  url: string;
  documentSha256: string;
}

export interface NewsQueue {
  schemaVersion: 2;
  pendingCount: number;
  fingerprint?: string;
  jobs: NewsJob[];
}
