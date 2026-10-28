import React from 'react';
import type { FleetSource, OfficialEvent } from '../types';
import { interpretationDetails, reportedRate, storedReadingDetails } from '../utils/contractTimeline';
import { economicsDetails, backlogRateLabel, eventInterpretation, newsEconomics } from '../utils/newsEconomics';

const UNKNOWN = '확인된 정보 없음';
function publicationLabel(value?: string): string {
  if (!value) return '발표일 확인되지 않음';
  // Legacy source labels stay verbatim; only ISO timestamp presentation is trimmed.
  return /^\d{4}-\d{2}-\d{2}T/.test(value) ? value.slice(0, 10) : value;
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
  return value === 'Undisclosed' ? '미공개' : value;
}

const ACTIONS: Record<string, string> = { upsert: 'Fleet Status 반영', withdraw: '계약 철회', 'no-change': '변경 없음', hold: '보류' };

export function announcementDetails(event: OfficialEvent): [string, string][] {
  const decision = event.readingDecision;
  const interpretation = eventInterpretation(event);
  const facts = event.facts;
  const unknown = event.llmReview || decision ? '원문 미공개' : UNKNOWN;
  const awardType = interpretation?.awardType || facts?.awardType || event.classification;
  const award = awardType === 'binding-letter-of-award'
    ? '구속력 있는 LOA (수주 통지)'
    : awardType === 'letter-of-award' ? 'LOA (수주 통지) · 확정 계약으로 미분류'
      : ({ 'signed-contract': '확정 계약 수주', 'option-exercised': '옵션 행사 · 확정 계약', extension: '계약 연장' } as Record<string, string>)[awardType] || UNKNOWN;
  // Reader text is shown verbatim; only legacy event facts carry a precision hint.
  const start = interpretation ? interpretation.sourceStartText || undefined : facts?.expectedStart || event.start;
  const approximateStart = !interpretation && ['quarter', 'month', 'half-year', 'year', 'approximate'].includes(facts?.startPrecision || '');
  const term = interpretation ? interpretation.termText || unknown : facts?.termText || (facts?.awardTermYears
    ? `${facts.awardTermYears}년`
    : start && (facts?.expectedEnd || event.end)
      ? `${start}–${facts?.expectedEnd || event.end} (원문 기간)`
      : unknown);
  const option = interpretation ? interpretation.optionText || unknown : facts?.optionText || (facts?.optionTermYears
    ? `추가 ${facts.optionTermYears}년${facts.optionEndIfFullyExercised ? ` · 모두 행사 시 ${facts.optionEndIfFullyExercised}까지 작업 예정` : ' · 종료 시점 확인되지 않음'}`
    : unknown);
  const included = (facts?.valueIncludes || []).map(value => ({
    'additional services': '추가 서비스',
    'mobilization fee': '이동 비용',
  } as Record<string, string>)[value] || value);
  const valueUsd = interpretation?.valueUsd;
  const value = interpretation ? interpretation.valueText || (valueUsd && Number.isFinite(valueUsd) && valueUsd > 0 ? `$${valueUsd.toLocaleString('en-US')}` : unknown) : facts?.valueText || (facts?.announcedValueUsdApprox && facts.announcedValueUsdApprox > 0
    ? `약 ${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(facts.announcedValueUsdApprox / 100_000_000)}억 달러${included.length ? ` · ${included.join('·')} 포함` : ''}`
    : unknown);
  const rowRate = decision?.row ? reportedRate(decision.row) : 0;
  const rate = rowRate > 0 ? `$${rowRate.toLocaleString('en-US')} / 일`
    : decision ? '미공개'
      : facts?.dayRateDisclosure === 'reported' && (facts.dayRateUsd ?? 0) > 0
        ? `$${new Intl.NumberFormat('en-US').format(facts.dayRateUsd!)} / 일`
        : facts?.dayRateDisclosure === 'undisclosed' ? '미공개' : UNKNOWN;
  const client = decision ? decision.row?.client || decision.facts?.client || undefined : facts?.counterparty;
  const region = decision ? decision.row?.region || decision.facts?.region || undefined : facts?.location;
  return [
    ['고객·지역', `${disclosedText(client, unknown === UNKNOWN ? '고객 확인되지 않음' : unknown)} · ${!decision && region === 'India' ? '인도' : disclosedText(region, unknown === UNKNOWN ? '지역 확인되지 않음' : unknown)}`],
    ['수주 형태', award],
    ['작업 시작', start ? `${start}${approximateStart ? ' 예정 · 정확한 날짜 미공개' : ''}` : unknown],
    ['기본 기간', term],
    ['옵션', option],
    ['기본 계약 가치', value],
    ['Dayrate', rate],
  ];
}

/** Optional reader notes and quotes for the collapsed event body; every field may be absent. */
export function decisionDetails(event: OfficialEvent): { notes: string[]; quotes: string[] } {
  const decision = event.readingDecision;
  const economics = newsEconomics(event);
  const quote = (text?: string | null, locator?: string | null) => text ? (locator ? `${text} · ${locator}` : text) : '';
  const notes = [
    decision?.action ? `판단: ${ACTIONS[decision.action] || decision.action}` : '',
    decision?.reasonKo || event.reason || '',
    decision?.holdReason ? `보류 사유: ${decision.holdReason}` : '',
    decision?.row ? `표시 구간: ${decision.row.startDate} – ${decision.row.endDate}` : '',
    interpretationDetails(eventInterpretation(event)),
    economics ? `${backlogRateLabel(economics)} (백로그 환산) · ${economicsDetails(economics)}` : '',
  ].filter(Boolean);
  const evidence = decision
    ? [quote(decision.quote, decision.locator), ...(Array.isArray(decision.evidence) ? decision.evidence.map(e => quote(e?.quote, e?.locator)) : [])]
    : (event.evidence || []).map(e => quote(e?.quote));
  return { notes, quotes: evidence.filter(Boolean) };
}

export default function OfficialAnnouncementCard({ event, source }: { event: OfficialEvent; source?: FleetSource }) {
  const vessel = event.vessel || event.vessels?.join(', ');
  const isReport = event.classification === 'letter-of-award';
  // Only report-derived events may fall back to the company's FSR provenance.
  const href = sourceHref(event.url || (isReport ? source?.documentUrl : undefined));
  const publishedAt = publicationLabel(event.publishedAt || (isReport ? source?.reportDate : undefined));
  const { notes, quotes } = decisionDetails(event);
  return (
    <details className="min-w-0 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <summary className="cursor-pointer rounded text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
        <span className="ml-2 font-bold">{vessel || event.title || `${event.company} 공식 발표`}</span>
        <span className="ml-3 text-xs text-slate-400">{event.company} · {publishedAt}</span>
        <span className="ml-3 inline-block text-xs text-amber-200">{event.reviewStatus === 'conflict' ? '조건 충돌 · 확인 필요' : event.autoApplied ? 'Fleet Status 반영' : (event.llmReview || event.readingDecision) ? (event.needsDetails === false ? '확인 완료' : '상세 조건 미공개') : '확인 대기'}</span>
      </summary>
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
            {event.inputMode === 'stored-result' ? '원문 URL:' : '출처:'} {event.company} {isReport ? '공식 보고서' : '공식 발표'} · {publishedAt} ↗
          </a>
        ) : <p>출처 원문 링크 확인되지 않음 · {publishedAt}</p>}
        {storedReadingDetails(event) && <p className="mt-2 text-slate-400">{storedReadingDetails(event)}</p>}
        {notes.map((note, index) => <p key={index} className="mt-2 text-slate-400">{note}</p>)}
        {quotes.map((quote, index) => <blockquote key={index} className="mt-2 text-xs text-slate-400">{quote}</blockquote>)}
        <p className="mt-2 text-slate-400">원문 날짜 표현과 표시 날짜를 구분합니다. 미공개·환산 요율은 평균 공시 dayrate에 포함하지 않습니다.</p>
      </div>
    </details>
  );
}
