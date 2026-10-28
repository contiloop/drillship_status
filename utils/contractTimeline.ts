import type { Contract, ContractInterpretation } from '../types';

const DAY = 86_400_000;

/** Quarter axis uses equal calendar-month spacing; width excludes the frozen vessel column. */
export function quarterScrollOffset(month: number, totalMonths: number, timelineWidth: number) {
  return Math.floor(month / 3) * 3 / totalMonths * timelineWidth;
}

export function contractSpan(contract: Contract, year: number, endYear: number) {
  if (contract.interpretation?.dateBasis === 'unresolved') return null;
  if (!isIsoDate(contract.startDate) || !isIsoDate(contract.endDate)) return null;
  const origin = Date.UTC(year, 0, 1);
  const limit = Date.UTC(endYear + 1, 0, 1);
  const start = Math.max(origin, Date.parse(`${contract.startDate}T00:00:00Z`));
  // Stored contract end dates are inclusive; a month-end covers the whole month.
  const end = Math.min(limit, Date.parse(`${contract.endDate}T00:00:00Z`) + DAY);
  if (!Number.isFinite(start + end) || end <= start) return null;
  return { left: (start - origin) / (limit - origin) * 100, width: (end - start) / (limit - origin) * 100 };
}

export function contractPeriod(contract: Contract) {
  const label = (key: 'start' | 'end') => {
    const value = key === 'start' ? contract.startDate : contract.endDate;
    const precision = contract.interpretation?.[key === 'start' ? 'startPrecision' : 'endPrecision'] || contract.datePrecision?.[key];
    return contract.interpretation?.dateBasis === 'placement' || precision === 'day' ? value : value.slice(0, 7);
  };
  return `${label('start')} ~ ${label('end')}`;
}

export function contractSource(value?: string) {
  try { const url = new URL(value || ''); return url.protocol === 'https:' ? url.href : undefined; }
  catch { return undefined; }
}

/** ISO calendar validation only; natural-language dates belong to the reader. */
export function isIsoDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

/** Preserve legacy/month-based statistics while excluding model placement. */
export function isStatisticalContract(contract: Contract) {
  if (!isIsoDate(contract.startDate) || !isIsoDate(contract.endDate) || contract.endDate < contract.startDate) return false;
  const value = contract.interpretation;
  return !value || (value.dateBasis === 'source'
    && ['day', 'month'].includes(value.startPrecision) && ['day', 'month'].includes(value.endPrecision));
}

export function reportedRate(contract: Contract): number {
  const value = contract.interpretation;
  if (value && (value.rateBasis !== 'reported' || value.reportedUsdPerDay !== contract.dayRate)) return 0;
  return Number.isFinite(contract.dayRate) && contract.dayRate > 0 ? contract.dayRate : 0;
}

export function isCurrentReportedRate(contract: Contract, today: string) {
  return isStatisticalContract(contract) && contract.status === 'Firm' && reportedRate(contract) > 0
    && contract.startDate <= today && contract.endDate >= today;
}

export function interpretationDetails(contract: Contract | ContractInterpretation) {
  const value = 'dateBasis' in contract ? contract : contract.interpretation;
  if (!value) return '';
  return [value.sourceStartText ? `원문 시작: ${value.sourceStartText} (${value.startPrecision})` : '',
    value.sourceEndText ? `원문 종료: ${value.sourceEndText} (${value.endPrecision})` : '',
    value.placementBasis ? `일정 배치: ${value.placementBasis}` : '',
    value.dateBasis === 'unresolved' ? '배치 보류 · 일정 근거 확인 필요' : value.dateBasis === 'placement' ? '표시 날짜는 배치 기준이며 공개된 정확한 일자가 아닙니다. 가동률·평균 공시 dayrate 집계에서 제외합니다.' : '',
    value.termText, value.optionText, value.valueText,
    value.maximumTerm ? '최대 기간만 공개 · 단일 환산 요율 미산출' : '',
    value.includes.length ? `포함: ${value.includes.join('·')}` : '',
    value.excludes.length ? `제외: ${value.excludes.join('·')}` : '',
  ].filter(Boolean).join(' · ');
}
