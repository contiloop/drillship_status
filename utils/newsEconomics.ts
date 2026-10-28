import type { Contract, ContractInterpretation, Drillship, OfficialEvent } from '../types';

export interface AwardDuration {
  days: number;
  label: string;
  assumption?: string;
}
export interface BacklogRate {
  usdPerDay: number;
  valueUsd: number;
  duration: AwardDuration;
}
export interface NewsEconomics {
  scope: 'award' | 'extension';
  rates: BacklogRate[];
  includes: string[];
  excludes: string[];
}
export interface TimelineContract extends Contract {
  economics?: NewsEconomics;
}

const positive = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

/** Validate the model's numeric pairing; never select amounts or terms from prose. */
export function interpretationEconomics(value?: ContractInterpretation): NewsEconomics | undefined {
  if (!value || value.rateBasis !== 'backlog-derived' || value.maximumTerm
    || !positive(value.valueUsd) || !positive(value.baseDurationDays) || !positive(value.derivedUsdPerDay)) return undefined;
  const expected = value.valueUsd / value.baseDurationDays;
  if (Math.abs(expected - value.derivedUsdPerDay) > Math.max(1, expected * 0.00001)) return undefined;
  return {
    scope: ['extension', 'option-exercised'].includes(value.awardType) ? 'extension' : 'award',
    rates: [{ valueUsd: value.valueUsd, usdPerDay: value.derivedUsdPerDay,
      duration: { days: value.baseDurationDays, label: value.termText || `${value.baseDurationDays.toLocaleString('en-US')}일`, assumption: value.durationAssumption || undefined } }],
    includes: value.includes, excludes: value.excludes,
  };
}

export function newsEconomics(event: OfficialEvent): NewsEconomics | undefined {
  return interpretationEconomics(event.readingDecision?.facts.interpretation);
}

/** Canonical rows already contain model-resolved identity, dates and status.
 * News is detail/provenance only: it must never synthesize a second schedule. */
export function timelineContracts(ship: Drillship, _events: OfficialEvent[] = []): TimelineContract[] {
  return ship.contracts.map(row => ({ ...row, economics: interpretationEconomics(row.interpretation) }));
}

export function backlogRateLabel(economics: NewsEconomics, compact = false) {
  return `${economics.scope === 'extension' ? '연장 ' : ''}${economics.rates.map(r => compact
    ? `$${Math.round(r.usdPerDay / 1000)}k` : `$${Math.round(r.usdPerDay).toLocaleString('en-US')}`).join(' / ')}${compact ? '' : ' / 일'}`;
}

export function economicsDetails(economics: NewsEconomics) {
  const calculations = economics.rates.map(r => `$${r.valueUsd.toLocaleString('en-US')} ÷ ${r.duration.days.toLocaleString('en-US', { maximumFractionDigits: 2 })}일${r.duration.assumption ? ` (${r.duration.assumption})` : ''}`);
  return [...calculations, '계약 가치의 일평균 환산액 · 공시 dayrate 아님',
    economics.includes.length ? `포함: ${economics.includes.join('·')}` : '추가 비용 포함 여부는 원문 기준',
    economics.excludes.length ? `제외: ${economics.excludes.join('·')}` : ''].filter(Boolean).join(' · ');
}
