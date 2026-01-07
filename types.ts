
export type Company = 'Transocean' | 'Valaris' | 'Noble' | 'Seadrill';
export type Generation = '6G' | '7G' | '7G+' | '8G';
export type ContractStatus = 'Firm' | 'Option' | 'Idle' | 'Warm-Stacked' | 'Cold-Stacked';

export interface Contract {
  id: string;
  vesselId: string;
  startDate: string;
  endDate: string;
  dayRate: number;
  client: string;
  region: string;
  status: 'Firm' | 'Option'; // 계약 내부 상태
}

export interface Drillship {
  id: string;
  name: string;
  company: Company;
  generation: Generation;
  status: 'Active' | 'Idle' | 'Warm-Stacked' | 'Cold-Stacked';
  yearBuilt: number;
  contracts: Contract[];
}

export interface FleetData {
  drillships: Drillship[];
}
