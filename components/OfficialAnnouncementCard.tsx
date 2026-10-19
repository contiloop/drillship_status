import React from 'react';
import type { FleetSource, OfficialEvent } from '../types';

const UNKNOWN = '확인된 정보 없음';
const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function periodLabel(value?: string): string {
  if (!value) return UNKNOWN;
  const quarter = /^Q([1-4])\s+(\d{4})$/i.exec(value);
  if (quarter) return `${quarter[2]}년 ${quarter[1]}분기`;
  const wordQuarter = /^(first|second|third|fourth) quarter(?: of)? (\d{4})$/i.exec(value);
  if (wordQuarter) return `${wordQuarter[2]}년 ${['first', 'second', 'third', 'fourth'].indexOf(wordQuarter[1].toLowerCase()) + 1}분기`;
  const half = /^H([12])\s+(\d{4})$/i.exec(value);
  if (half) return `${half[2]}년 ${half[1] === '1' ? '상' : '하'}반기`;
  const month = /^([A-Za-z]{3})[- ](\d{2}|\d{4})$/.exec(value);
  if (month) {
    const index = monthNames.findIndex(name => name.toLowerCase() === month[1].toLowerCase());
    if (index >= 0) return `${month[2].length === 2 ? `20${month[2]}` : month[2]}년 ${index + 1}월`;
  }
  const earlyYear = /^early (\d{4})$/i.exec(value);
  return earlyYear ? `${earlyYear[1]}년 초` : value;
}

function publicationLabel(value?: string): string {
  if (!value) return '발표일 확인되지 않음';
  // Publication dates are calendar dates, not local-midnight timestamps.
  // Avoid shifting an English date to the previous day in Korean time zones.
  const iso = /^(\d{4}-\d{2}-\d{2})(?:T|$)/.exec(value);
  if (iso) return iso[1];
  const english = /^([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})$/.exec(value);
  if (english) {
    const month = monthNames.findIndex(name => name.toLowerCase() === english[1].slice(0, 3).toLowerCase());
    if (month >= 0) return `${english[3]}-${String(month + 1).padStart(2, '0')}-${english[2].padStart(2, '0')}`;
  }
  return value;
}

function sourceHref(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function disclosedText(value: string | undefined, fallback: string): string {
  if (!value) return fallback;
  return /^(undisclosed(?: operator| customer| client)?|not disclosed)$/i.test(value) ? '미공개' : value;
}

export function announcementDetails(event: OfficialEvent): [string, string][] {
  const facts = event.facts;
  const unknown = event.llmReview ? '원문 미공개' : UNKNOWN;
  const awardType = facts?.awardType || event.classification;
  const award = awardType === 'binding-letter-of-award'
    ? '구속력 있는 LOA (수주 통지)'
    : awardType === 'letter-of-award' ? 'LOA (수주 통지) · 확정 계약으로 미분류'
      : ({ 'signed-contract': '확정 계약 수주', 'option-exercised': '옵션 행사 · 확정 계약', extension: '계약 연장' }[awardType] || UNKNOWN);
  const start = facts?.expectedStart || event.start;
  const approximateStart = ['quarter', 'month', 'half-year', 'year'].includes(facts?.startPrecision || '')
    || Boolean(start && /^(Q[1-4]\s+\d{4}|[A-Za-z]{3}[- ]\d{2,4})$/i.test(start));
  const term = facts?.termText || (facts?.awardTermYears
    ? `${facts.awardTermYears}년`
    : start && (facts?.expectedEnd || event.end)
      ? `${periodLabel(start)}–${periodLabel(facts?.expectedEnd || event.end)} (원문 기간)`
      : unknown);
  const option = facts?.optionText || (facts?.optionTermYears
    ? `추가 ${facts.optionTermYears}년${facts.optionEndIfFullyExercised ? ` · 모두 행사 시 ${periodLabel(facts.optionEndIfFullyExercised)}까지 작업 예정` : ' · 종료 시점 확인되지 않음'}`
    : unknown);
  const included = (facts?.valueIncludes || []).map(value => ({
    'additional services': '추가 서비스',
    'mobilization fee': '이동 비용',
  }[value] || value));
  const value = facts?.valueText || (facts?.announcedValueUsdApprox && facts.announcedValueUsdApprox > 0
    ? `약 ${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(facts.announcedValueUsdApprox / 100_000_000)}억 달러${included.length ? ` · ${included.join('·')} 포함` : ''}`
    : unknown);
  const rate = facts?.dayRateDisclosure === 'reported' && (facts.dayRateUsd ?? 0) > 0
    ? `$${new Intl.NumberFormat('en-US').format(facts.dayRateUsd!)} / 일`
    : facts?.dayRateDisclosure === 'undisclosed' ? '미공개' : UNKNOWN;
  return [
    ['고객·지역', `${disclosedText(facts?.counterparty, event.llmReview ? '원문 미공개' : '고객 확인되지 않음')} · ${facts?.location === 'India' ? '인도' : disclosedText(facts?.location, event.llmReview ? '원문 미공개' : '지역 확인되지 않음')}`],
    ['수주 형태', award],
    ['작업 시작', start ? `${periodLabel(start)}${approximateStart ? ' 예정 · 정확한 날짜 미공개' : ''}` : unknown],
    ['기본 기간', term],
    ['옵션', option],
    ['기본 계약 가치', value],
    ['Dayrate', rate],
  ];
}

export default function OfficialAnnouncementCard({ event, source }: { event: OfficialEvent; source?: FleetSource }) {
  const vessel = event.vessel || event.vessels?.join(', ');
  const isReport = event.classification === 'letter-of-award';
  // Only report-derived events may fall back to the company's FSR provenance.
  const href = sourceHref(event.url || (isReport ? source?.documentUrl : undefined));
  const publishedAt = publicationLabel(event.publishedAt || (isReport ? source?.reportDate : undefined));
  return (
    <article className="min-w-0 rounded-2xl border border-slate-700 bg-slate-950/60 p-4 sm:p-6">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-bold text-blue-300">{event.company}</span>
        <span className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-amber-200">{event.reviewStatus === 'conflict' ? '계약 대조 충돌 · 검토 필요' : event.autoApplied ? 'Codex 대조 · 계약표 반영' : event.llmReview ? (event.needsDetails === false ? '원문 확인 · 대조 완료' : '원문 확인 · 상세 조건 미공개') : '원문 대조 대기'}</span>
      </div>
      <h4 className="mt-3 break-words text-lg font-bold text-white">{vessel || event.title || `${event.company} 공식 발표`}</h4>
      {event.title && vessel && <p className="mt-2 break-words text-sm leading-relaxed text-slate-400">{event.title}</p>}
      <dl className="mt-5 space-y-3">
        {announcementDetails(event).map(([label, value]) => (
          <div key={label} className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-3">
            <dt className="text-sm text-slate-400">{label}</dt>
            <dd className="min-w-0 break-words text-base leading-relaxed text-slate-100">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-5 border-t border-slate-800 pt-4 text-sm leading-relaxed text-slate-300">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="inline-block rounded py-2 text-blue-300 underline underline-offset-4 hover:text-blue-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-400">
            출처: {event.company} {isReport ? '공식 보고서' : '공식 발표'} · {publishedAt} ↗
          </a>
        ) : <p>출처 원문 링크 확인되지 않음 · {publishedAt}</p>}
        {event.reason && <p className="mt-2 text-slate-300">{event.reason}</p>}
        <p className="mt-2 text-slate-400">{event.autoApplied ? '근거가 확인된 기간·계약 상태를 계약표에 반영했습니다. 월 단위 날짜는 월초·월말로 표시하며 정확한 일자는 아닙니다. 미공개 일당은 평균에서 제외합니다.' : '별도 공지로 표시하며 계약표·가동률·평균 dayrate 계산에는 포함하지 않습니다.'} 계약 가치를 일당으로 환산하지 않습니다.</p>
        {event.llmReview && <p className="mt-2 text-xs text-slate-500">원문·기존 계약 대조: Codex · {event.llmReview.model} · {event.llmReview.reasoningEffort} · Fast {event.llmReview.fast ? 'on' : 'off'} · {new Date(event.llmReview.reviewedAt).toLocaleString('ko-KR')}</p>}
      </div>
    </article>
  );
}
