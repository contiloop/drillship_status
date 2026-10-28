import React from 'react';
import type { FleetSource, OfficialEvent } from '../types';
import { interpretationDetails } from '../utils/contractTimeline';
import { economicsDetails, backlogRateLabel, newsEconomics } from '../utils/newsEconomics';

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

export function announcementDetails(event: OfficialEvent): [string, string][] {
  const model = event.readingDecision?.facts;
  const interpretation = model?.interpretation;
  const facts = event.facts;
  const unknown = event.llmReview ? '원문 미공개' : UNKNOWN;
  const awardType = interpretation?.awardType || facts?.awardType || event.classification;
  const award = awardType === 'binding-letter-of-award'
    ? '구속력 있는 LOA (수주 통지)'
    : awardType === 'letter-of-award' ? 'LOA (수주 통지) · 확정 계약으로 미분류'
      : ({ 'signed-contract': '확정 계약 수주', 'option-exercised': '옵션 행사 · 확정 계약', extension: '계약 연장' }[awardType] || UNKNOWN);
  const start = interpretation ? interpretation.sourceStartText : facts?.expectedStart || event.start;
  const approximateStart = ['quarter', 'month', 'half-year', 'year', 'approximate'].includes(interpretation?.startPrecision || facts?.startPrecision || '');
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
  }[value] || value));
  const value = interpretation ? interpretation.valueText || (interpretation.valueUsd ? `$${interpretation.valueUsd.toLocaleString('en-US')}` : unknown) : facts?.valueText || (facts?.announcedValueUsdApprox && facts.announcedValueUsdApprox > 0
    ? `약 ${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(facts.announcedValueUsdApprox / 100_000_000)}억 달러${included.length ? ` · ${included.join('·')} 포함` : ''}`
    : unknown);
  const modelRate = interpretation?.reportedUsdPerDay;
  const rate = interpretation ? (interpretation.rateBasis === 'reported' && modelRate && Number.isFinite(modelRate) && modelRate > 0 ? `$${modelRate.toLocaleString('en-US')} / 일` : '미공개') : facts?.dayRateDisclosure === 'reported' && (facts.dayRateUsd ?? 0) > 0
    ? `$${new Intl.NumberFormat('en-US').format(facts.dayRateUsd!)} / 일`
    : facts?.dayRateDisclosure === 'undisclosed' ? '미공개' : UNKNOWN;
  return [
    ['고객·지역', `${disclosedText(model ? model.client || undefined : facts?.counterparty, event.llmReview ? '원문 미공개' : '고객 확인되지 않음')} · ${model ? disclosedText(model.region || undefined, '원문 미공개') : facts?.location === 'India' ? '인도' : disclosedText(facts?.location, event.llmReview ? '원문 미공개' : '지역 확인되지 않음')}`],
    ['수주 형태', award],
    ['작업 시작', start ? `${start}${approximateStart ? ' 예정 · 정확한 날짜 미공개' : ''}` : unknown],
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
  const economics = newsEconomics(event);
  return (
    <details className="min-w-0 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <summary className="cursor-pointer rounded text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
        <span className="ml-2 font-bold">{vessel || event.title || `${event.company} 공식 발표`}</span>
        <span className="ml-3 text-xs text-slate-400">{event.company} · {publishedAt}</span>
        <span className="ml-3 inline-block text-xs text-amber-200">{event.reviewStatus === 'conflict' ? '조건 충돌 · 확인 필요' : event.autoApplied ? '계약표 반영' : (event.llmReview || event.readingDecision) ? (event.needsDetails === false ? '확인 완료' : '상세 조건 미공개') : '확인 대기'}</span>
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
      {economics && <p className="mt-4 text-sm text-slate-200">{backlogRateLabel(economics)} (백로그 환산)<span className="mt-1 block text-xs text-slate-400">{economicsDetails(economics)}</span></p>}
      <div className="mt-5 border-t border-slate-800 pt-4 text-sm leading-relaxed text-slate-300">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="inline-block rounded py-2 text-blue-300 underline underline-offset-4 hover:text-blue-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-400">
            출처: {event.company} {isReport ? '공식 보고서' : '공식 발표'} · {publishedAt} ↗
          </a>
        ) : <p>출처 원문 링크 확인되지 않음 · {publishedAt}</p>}
        {(event.readingDecision?.reasonKo || event.reason) && <p className="mt-2 text-slate-300">{event.readingDecision?.reasonKo || event.reason}</p>}
        {event.readingDecision && <p className="mt-2 text-slate-400">{interpretationDetails(event.readingDecision.facts.interpretation)}</p>}
        {event.readingDecision?.row && <p className="mt-2 text-slate-400">표시 구간: {event.readingDecision.row.startDate} – {event.readingDecision.row.endDate}</p>}
        {event.readingDecision?.evidence.map((evidence, index) => <blockquote key={index} className="mt-2 text-xs text-slate-400">{evidence.quote} · {evidence.locator}</blockquote>)}
        {!event.readingDecision && event.evidence?.map((evidence, index) => <blockquote key={index} className="mt-2 text-xs text-slate-400">{evidence.quote}</blockquote>)}
        <p className="mt-2 text-slate-400">{event.autoApplied ? '근거가 확인된 기간·계약 상태를 계약표에 반영했습니다. 월 단위 날짜는 월초·월말로 표시하며 정확한 일자는 아닙니다. 미공개 일당은 평균에서 제외합니다.' : '정확한 일자가 없는 일정은 가동률·평균 공시 dayrate 집계에서 제외합니다. 계약 체결 여부는 일자 정밀도와 별개로 Firm/LOA로 표시합니다.'} 환산 요율은 동일 수주의 금액·기본 기간만 사용하며 공시 dayrate와 구분합니다.</p>
      </div>
    </details>
  );
}
