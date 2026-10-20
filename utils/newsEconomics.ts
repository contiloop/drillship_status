import type { Contract, Drillship, OfficialEvent } from '../types';
import { contractSource } from './contractTimeline';

const DAY = 86_400_000;
const numbers: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
const numberToken = '(?:[0-9][0-9,]*(?:\\.[0-9]+)?|one|two|three|four|five|six|seven|eight|nine|ten)';
const numeric = (value: string) => numbers[value.toLowerCase()] ?? Number(value.replaceAll(',', ''));
const iso = (value: number) => new Date(value).toISOString().slice(0, 10);
const quotes = (event: OfficialEvent, field: string) => [...new Set((event.evidence || []).filter(e => e.field === field).map(e => e.quote))];

export interface AwardDuration {
  amount: number;
  unit: 'day' | 'month' | 'year';
  days: number;
  label: string;
  assumption?: string;
  maximum: boolean;
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
  forecast?: { startLabel: string; duration: AwardDuration; awardLabel: string; windowLabel: string };
}

function durations(text: string): AwardDuration[] {
  return [...text.matchAll(new RegExp(`\\b(${numberToken})[-\\s]+(day|month|year)s?\\b`, 'gi'))].map(match => {
    const amount = numeric(match[1]);
    const unit = match[2].toLowerCase() as AwardDuration['unit'];
    const maximum = /up to|maximum|at most/i.test(text.slice(Math.max(0, match.index! - 20), match.index));
    return { amount, unit, days: amount * (unit === 'year' ? 365 : unit === 'month' ? 365 / 12 : 1), maximum,
      label: `${maximum ? '최대 ' : ''}${amount.toLocaleString('en-US')}${{ day: '일', month: '개월', year: '년' }[unit]}`,
      assumption: unit === 'year' ? '1년=365일 가정' : unit === 'month' ? '1개월=365/12일 가정' : undefined };
  }).filter(d => d.amount > 0 && d.days < 36_500 && (d.unit === 'day' || Number.isInteger(d.amount)));
}

export function awardDuration(event: OfficialEvent): AwardDuration | undefined {
  const found = durations(quotes(event, 'termText').join(' '));
  // Ambiguous tranches/options must not silently become a single base term.
  const distinct = [...new Map(found.map(d => [`${d.amount}-${d.unit}-${d.maximum}`, d])).values()];
  return distinct.length === 1 ? distinct[0] : undefined;
}

function money(text: string) {
  return [...text.matchAll(/\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?/gi)]
    .map(m => Number(m[1].replaceAll(',', '')) * (m[2]?.toLowerCase() === 'billion' ? 1e9 : m[2] ? 1e6 : 1));
}

export function newsEconomics(event: OfficialEvent): NewsEconomics | undefined {
  if (!usableNews(event)) return undefined;
  const facts = event.facts!;
  const duration = awardDuration(event);
  const value = facts.announcedValueUsdApprox;
  let rates: BacklogRate[] = [];
  const scope = ['extension', 'option-exercised'].includes(facts.awardType || '') ? 'extension' : 'award';
  if (scope === 'award' && value && Number.isFinite(value) && value > 0 && duration && !duration.maximum
      && quotes(event, 'announcedValueUsdApprox').some(q => money(q).includes(value))) {
    rates = [{ valueUsd: value, duration, usdPerDay: value / duration.days }];
  } else if (scope === 'extension') {
    // Pair value and days only within the same quoted sentence, never divide
    // extension backlog by the whole legacy contract or average separate rates.
    const sentences = [...new Set(quotes(event, 'termText').flatMap(q => q.split(/(?<=[.!?])\s+(?=[A-Z])/)))];
    for (const sentence of sentences) {
      const terms = durations(sentence);
      const values = money(sentence);
      if (terms.length === 1 && !terms[0].maximum && values.length === 1 && values[0] > 0) {
        rates.push({ valueUsd: values[0], duration: terms[0], usdPerDay: values[0] / terms[0].days });
      }
    }
  }
  rates = [...new Map(rates.map(r => [`${r.valueUsd}-${r.duration.days}`, r])).values()];
  return rates.length ? { scope, rates, includes: facts.valueIncludes || [], excludes: facts.valueExcludes || [] } : undefined;
}

function usableNews(event: OfficialEvent) {
  return Boolean(event.llmReview && event.vessel && event.facts && contractSource(event.url)
    && ['applied', 'details-incomplete', 'reviewed'].includes(event.reviewStatus || ''));
}

function startWindow(value: string): { start: number; end: number; label: string } | undefined {
  const quarter = /^(?:Q([1-4])\s+(\d{4})|(first|second|third|fourth) quarter(?: of)? (\d{4}))$/i.exec(value);
  const half = /^H([12])\s+(\d{4})$/i.exec(value);
  const yearOnly = /^(\d{4})$/.exec(value);
  let year: number, month: number, months: number, label: string;
  if (quarter) {
    const q = Number(quarter[1]) || ['first', 'second', 'third', 'fourth'].indexOf(quarter[3].toLowerCase()) + 1;
    year = Number(quarter[2] || quarter[4]); month = (q - 1) * 3; months = 3; label = `${year}년 ${q}분기`;
  } else if (half) {
    year = Number(half[2]); month = (Number(half[1]) - 1) * 6; months = 6; label = `${year}년 ${half[1] === '1' ? '상' : '하'}반기`;
  } else if (yearOnly) {
    year = Number(yearOnly[1]); month = 0; months = 12; label = `${year}년`;
  } else return undefined;
  return year >= 2000 && year <= 2100 ? { start: Date.UTC(year, month, 1), end: Date.UTC(year, month + months, 1) - DAY, label } : undefined;
}

function finish(start: number, duration: AwardDuration) {
  if (duration.unit === 'day') return start + (duration.amount - 1) * DAY;
  const date = new Date(start);
  // Calendar months/years for placement; rate conversion assumptions stay explicit.
  const months = duration.amount * (duration.unit === 'year' ? 12 : 1);
  const boundary = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1));
  const lastDay = new Date(Date.UTC(boundary.getUTCFullYear(), boundary.getUTCMonth() + 1, 0)).getUTCDate();
  return Date.UTC(boundary.getUTCFullYear(), boundary.getUTCMonth(), Math.min(date.getUTCDate(), lastDay)) - DAY;
}

/** View-only projection. Canonical contracts and all fleet metrics are untouched. */
export function timelineContracts(ship: Drillship, events: OfficialEvent[]): TimelineContract[] {
  const rows: TimelineContract[] = ship.contracts.map(c => ({ ...c }));
  const seen = new Set<string>();
  for (const event of events) {
    if (!usableNews(event) || event.company !== ship.company || event.vessel !== ship.name || seen.has(event.url!)) continue;
    seen.add(event.url!);
    const economics = newsEconomics(event);
    const existing = rows.filter(c => c.sourceUrl === event.url);
    if (existing.length) {
      // No guessed matching by vessel/client alone, or distributing one award to
      // several rows. Extension rates are explicitly scoped to those tranches.
      if (existing.length === 1 && economics) existing[0].economics = economics;
      continue;
    }
    if (event.autoApplied || !['signed-contract', 'binding-letter-of-award', 'letter-of-award'].includes(event.facts!.awardType || '')) continue;
    const window = startWindow(event.facts!.expectedStart || '');
    const duration = awardDuration(event);
    if (!window || !duration) continue;
    const signed = event.facts!.awardType === 'signed-contract';
    const awardLabel = signed ? 'Firm' : 'LOA';
    const end = finish(window.end, duration);
    const endLabel = duration.maximum ? `최대 기간 기준 종료 ~${iso(end).slice(0, 7)}`
      : `종료 예상 ${iso(finish(window.start, duration)).slice(0, 7)}–${iso(end).slice(0, 7)}`;
    rows.push({ id: `forecast-${ship.id}-${event.url}`, vesselId: ship.id,
      startDate: iso(window.start), endDate: iso(end), dayRate: event.facts!.dayRateDisclosure === 'reported' ? event.facts!.dayRateUsd || 0 : 0,
      client: event.facts!.counterparty || 'Undisclosed', region: event.facts!.location || 'Undisclosed', status: signed ? 'Firm' : 'Contingent',
      sourceUrl: event.url, sourcePublishedAt: event.publishedAt, economics,
      forecast: { startLabel: window.label, duration, awardLabel,
        windowLabel: `${window.label} 개시 예정 · ${duration.label} · ${endLabel}` } });
  }
  return rows;
}

export function backlogRateLabel(economics: NewsEconomics, compact = false) {
  return `${economics.scope === 'extension' ? '연장 ' : ''}${economics.rates.map(r => compact
    ? `$${Math.round(r.usdPerDay / 1000)}k` : `$${Math.round(r.usdPerDay).toLocaleString('en-US')}`).join(' / ')}${compact ? '' : ' / 일'}`;
}

export function economicsDetails(economics: NewsEconomics) {
  const calculations = economics.rates.map(r => `$${r.valueUsd.toLocaleString('en-US')} ÷ ${r.duration.days.toLocaleString('en-US', { maximumFractionDigits: 2 })}일${r.duration.assumption ? ` (${r.duration.assumption})` : ''}`);
  return [...calculations, economics.scope === 'extension' ? '표시 요율은 연장분 각각에만 해당 · 기존 계약 전체 요율 아님' : '계약 가치의 일평균 환산액 · 공시 dayrate 아님',
    economics.includes.length ? `포함: ${economics.includes.join('·')}` : '추가 비용 포함 여부는 원문 기준',
    economics.excludes.length ? `제외: ${economics.excludes.join('·')}` : ''].filter(Boolean).join(' · ');
}
