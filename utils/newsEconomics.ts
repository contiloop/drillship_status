import type { Contract, ContractInterpretation, Drillship, OfficialEvent } from '../types';

/** Daily value derived by code from contract value ÷ base duration; never a disclosed dayrate. */
export interface NewsEconomics {
  scope: 'award' | 'extension';
  usdPerDay: number;
  valueUsd?: number;
  days?: number;
}
export interface TimelineContract extends Contract {
  economics?: NewsEconomics;
}

const positive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

/** A derived rate exists iff derivedUsdPerDay is a positive finite number. */
export function interpretationEconomics(value?: ContractInterpretation | null): NewsEconomics | undefined {
  if (!value || !positive(value.derivedUsdPerDay)) return undefined;
  return {
    scope: value.awardType === 'extension' || value.awardType === 'option-exercised' ? 'extension' : 'award',
    usdPerDay: value.derivedUsdPerDay,
    valueUsd: positive(value.valueUsd) ? value.valueUsd : undefined,
    days: positive(value.baseDurationDays) ? value.baseDurationDays : undefined,
  };
}

/** Interpretation attached to an event decision (new row shape first, legacy facts second). */
export function eventInterpretation(event: OfficialEvent): ContractInterpretation | undefined {
  const decision = event.readingDecision;
  return decision?.row?.interpretation || decision?.facts?.interpretation || undefined;
}

export function newsEconomics(event: OfficialEvent): NewsEconomics | undefined {
  return interpretationEconomics(eventInterpretation(event));
}

/** Bars come only from canonical fleet rows; news never adds or moves a period. */
export function timelineContracts(ship: Drillship): TimelineContract[] {
  return ship.contracts.map(row => ({ ...row, economics: interpretationEconomics(row.interpretation) }));
}

const usd = (value: number) => `$${Math.round(value).toLocaleString('en-US')}`;

export function backlogRateLabel(economics: NewsEconomics, compact = false) {
  const rate = compact ? `$${Math.round(economics.usdPerDay / 1000)}k` : usd(economics.usdPerDay);
  return `${economics.scope === 'extension' ? '연장 ' : ''}${rate}${compact ? '' : ' / 일'}`;
}

export function economicsDetails(economics: NewsEconomics) {
  const formula = economics.valueUsd && economics.days
    ? `${usd(economics.valueUsd)} ÷ ${economics.days.toLocaleString('en-US', { maximumFractionDigits: 2 })}일 = ${usd(economics.usdPerDay)} / 일`
    : `${usd(economics.usdPerDay)} / 일`;
  return `${formula} · 계약 가치의 일평균 환산액 · 공시 dayrate 아님`;
}
