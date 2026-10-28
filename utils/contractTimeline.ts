import type { Contract, ContractInterpretation } from '../types';

const DAY = 86_400_000;

/** Quarter axis uses equal calendar-month spacing; width excludes the frozen vessel column. */
export function quarterScrollOffset(month: number, totalMonths: number, timelineWidth: number) {
  return Math.floor(month / 3) * 3 / totalMonths * timelineWidth;
}

export function contractSpan(contract: Pick<Contract, 'startDate' | 'endDate'>, year: number, endYear: number) {
  if (!isIsoDate(contract.startDate) || !isIsoDate(contract.endDate)) return null;
  const origin = Date.UTC(year, 0, 1);
  const limit = Date.UTC(endYear + 1, 0, 1);
  const start = Math.max(origin, Date.parse(`${contract.startDate}T00:00:00Z`));
  // Stored contract end dates are inclusive; a month-end covers the whole month.
  const end = Math.min(limit, Date.parse(`${contract.endDate}T00:00:00Z`) + DAY);
  if (!Number.isFinite(start + end) || end <= start) return null;
  return { left: (start - origin) / (limit - origin) * 100, width: (end - start) / (limit - origin) * 100 };
}

/** Reader rows carry the dates to display; legacy report rows keep their month presentation. */
export function contractPeriod(contract: Pick<Contract, 'startDate' | 'endDate' | 'datePrecision' | 'interpretation'>) {
  const full = (key: 'start' | 'end') => Boolean(contract.interpretation) || contract.datePrecision?.[key] === 'day';
  const label = (key: 'start' | 'end') => {
    const value = key === 'start' ? contract.startDate : contract.endDate;
    return full(key) ? value : value.slice(0, 7);
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

/** Statistics use every row with a valid ISO period, including reader-placed dates. */
export function isStatisticalContract(contract: Pick<Contract, 'startDate' | 'endDate'>) {
  return isIsoDate(contract.startDate) && isIsoDate(contract.endDate) && contract.startDate <= contract.endDate;
}

/** dayRate holds only a disclosed rate; derived values live in interpretation. */
export function reportedRate(contract: Pick<Contract, 'dayRate'>): number {
  return Number.isFinite(contract.dayRate) && contract.dayRate > 0 ? contract.dayRate : 0;
}

export function isCurrentReportedRate(contract: Contract, today: string) {
  return isStatisticalContract(contract) && contract.status === 'Firm' && reportedRate(contract) > 0
    && contract.startDate <= today && contract.endDate >= today;
}

const list = (values: unknown) => Array.isArray(values) ? values.filter(value => typeof value === 'string' && value) : [];

/** Display-only reader text; legacy enum fields are ignored. */
export function interpretationDetails(contract: Contract | ContractInterpretation | null | undefined) {
  const value: ContractInterpretation | null | undefined = contract && 'startDate' in contract ? (contract as Contract).interpretation : contract as ContractInterpretation | null | undefined;
  if (!value) return '';
  const includes = list(value.includes);
  const excludes = list(value.excludes);
  return [value.sourceStartText ? `원문 시작: ${value.sourceStartText}` : '',
    value.sourceEndText ? `원문 종료: ${value.sourceEndText}` : '',
    value.placementBasis ? `일정 배치: ${value.placementBasis}` : '',
    value.termText, value.optionText, value.valueText,
    includes.length ? `포함: ${includes.join('·')}` : '',
    excludes.length ? `제외: ${excludes.join('·')}` : '',
  ].filter(Boolean).join(' · ');
}

/** Audit label only: do not replace the original review time with completion time. */
export function storedReadingDetails(value: Pick<Contract, 'inputMode' | 'storedReading'>) {
  if (value.inputMode !== 'stored-result') return '';
  return `저장된 읽기 결과로 보완 · 원래 읽은 시각 ${value.storedReading?.reviewedAt || '확인되지 않음'}`;
}
