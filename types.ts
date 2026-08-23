
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
}

export interface Drillship {
  id: string;
  name: string;
  company: Company;
  generation: Generation;
  status: VesselStatus;
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
}

export interface OfficialEvent {
  company: Company;
  title?: string;
  url?: string;
  publishedAt?: string;
  vessels?: string[];
  vessel?: string;
  classification: string;
  autoApplied: boolean;
  reason?: string;
}
