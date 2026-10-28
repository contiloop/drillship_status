
export type Company = 'Transocean' | 'Valaris' | 'Noble' | 'Seadrill';
export type Generation = '6G' | '7G' | '7G+' | '8G';
export type ContractStatus = 'Firm' | 'Option' | 'Contingent';
export type VesselStatus = 'Active' | 'Idle' | 'Warm-Stacked' | 'Cold-Stacked';

export interface Contract {
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
  readingDecision?: ReadingDecision;
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


// Reader port v2. Existing published Contract rows remain compatible while new
// proposals must carry interpretation and receive an ID only during application.
export type DatePrecision = 'day' | 'month' | 'quarter' | 'half-year' | 'year' | 'approximate' | 'unknown';
export type DateBasis = 'source' | 'placement' | 'unresolved';
export type RateBasis = 'reported' | 'backlog-derived' | 'undisclosed';
export type AwardType = 'signed-contract' | 'binding-letter-of-award' | 'letter-of-award' | 'option-exercised' | 'extension' | 'unreported';
export type ReadingKind = 'fleet-report' | 'official-news';

export interface ContractInterpretation {
  sourceStartText: string | null;
  sourceEndText: string | null;
  startPrecision: DatePrecision;
  endPrecision: DatePrecision;
  dateBasis: DateBasis;
  placementBasis: string | null;
  awardType: AwardType;
  termText: string | null;
  optionText: string | null;
  valueText: string | null;
  rateBasis: RateBasis;
  reportedUsdPerDay: number | null;
  derivedUsdPerDay: number | null;
  valueUsd: number | null;
  baseDurationDays: number | null;
  durationAssumption: string | null;
  maximumTerm: boolean;
  includes: string[];
  excludes: string[];
}

export interface ReadingPage {
  locator: string;
  text: string;
  image?: { mimeType: 'image/png'; sha256: string; dataBase64: string };
}

export interface ReadingEvidence {
  fields: string[];
  sourceId: string;
  locator: string;
  quote: string;
}

export interface ReadingInput {
  sourceId: string;
  sourceSha256: string;
  company: Company;
  kind: ReadingKind;
  url: string;
  publishedAt: string | null;
  pages: ReadingPage[];
  baseline: Drillship[];
  priorEvidence: Record<string, unknown>[];
  contextSha256: string;
  readerPolicySha256: string;
}

export interface ReadingFacts {
  client: string | null;
  region: string | null;
  interpretation: ContractInterpretation;
}

export interface ReadingRow extends Omit<Contract, 'id' | 'interpretation' | 'newsReviewed' | 'sourceUrl' | 'sourcePublishedAt' | 'datePrecision'> {
  id: string | null;
  interpretation: ContractInterpretation;
  newsReviewed?: boolean | null;
  sourceUrl?: string | null;
  sourcePublishedAt?: string | null;
  datePrecision?: { start: string; end: string } | null;
}

export interface ReadingDecision {
  vesselId: string;
  contractId: string | null;
  action: 'upsert' | 'withdraw' | 'no-change' | 'hold';
  row: ReadingRow | null;
  facts: ReadingFacts;
  reasonKo: string;
  evidence: ReadingEvidence[];
}

export interface VesselUpdate {
  vesselId: string;
  status: VesselStatus;
  statusAsOf: string;
  reasonKo: string;
  evidence: ReadingEvidence[];
}

export interface VesselCoverage {
  vesselId: string;
  state: 'included' | 'not-mentioned' | 'held';
}

export interface ReadingResponse {
  schemaVersion: 2;
  sourceId: string;
  sourceSha256: string;
  company: Company;
  reportDate: string | null;
  readLocators: string[];
  coverage: VesselCoverage[];
  vesselUpdates: VesselUpdate[];
  decisions: ReadingDecision[];
}

export interface NewsJob {
  jobId: string;
  kind: ReadingKind;
  company: Company;
  url: string;
  sourceSha256: string;
  contextSha256: string;
  readerPolicySha256: string;
}

export interface NewsQueue {
  schemaVersion: 2;
  pendingCount: number;
  fingerprint: string;
  jobs: NewsJob[];
}
