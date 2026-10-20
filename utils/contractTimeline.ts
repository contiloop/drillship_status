import type { Contract } from '../types';

const DAY = 86_400_000;

/** Quarter axis uses equal calendar-month spacing; width excludes the frozen vessel column. */
export function quarterScrollOffset(month: number, totalMonths: number, timelineWidth: number) {
  return Math.floor(month / 3) * 3 / totalMonths * timelineWidth;
}

export function contractSpan(contract: Contract, year: number, endYear: number) {
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
    return contract.datePrecision?.[key] === 'day' ? value : value.slice(0, 7);
  };
  return `${label('start')} ~ ${label('end')}`;
}

export function contractSource(value?: string) {
  try { const url = new URL(value || ''); return url.protocol === 'https:' ? url.href : undefined; }
  catch { return undefined; }
}
