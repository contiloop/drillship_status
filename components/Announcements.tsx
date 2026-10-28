import React from 'react';
import type { FleetSource, OfficialEvent } from '../types';
import { contractSource, isIsoDate } from '../utils/contractTimeline';
import OfficialAnnouncementCard from './OfficialAnnouncementCard';

const publicationTime = (event: OfficialEvent) => {
  const date = event.publishedAt?.slice(0, 10) || '';
  return isIsoDate(date) ? Date.parse(`${date}T00:00:00Z`) : 0;
};

const isContractItem = (event: OfficialEvent) =>
  !['superseded', 'covered-by-report', 'not-contract-news'].includes(event.reviewStatus || '')
  && (Boolean((event.llmReview || event.readingDecision) && event.vessel) || (event.pendingReview !== false && !event.autoApplied));

/** Every event is listed: contract items as cards, everything else as title + link. */
export function splitAnnouncements(events: OfficialEvent[]) {
  const sorted = [...events].sort((a, b) => publicationTime(b) - publicationTime(a));
  const relevant = sorted.filter(isContractItem);
  return {
    unresolved: relevant.filter(e => !e.autoApplied),
    applied: relevant.filter(e => e.autoApplied),
    others: sorted.filter(event => !isContractItem(event)),
  };
}

export default function Announcements({ events, sources }: { events: OfficialEvent[]; sources: FleetSource[] }) {
  const { unresolved, applied, others } = splitAnnouncements(events);
  const cards = (rows: OfficialEvent[]) => rows.map((event, index) => (
    <OfficialAnnouncementCard key={`${event.url}-${event.vessel || index}`} event={event}
      source={sources.find(source => source.company === event.company)} />
  ));
  return (
    <section aria-label="뉴스·공시 목록" className="max-w-5xl space-y-4">
      <p className="text-sm text-slate-400">계약 조건과 계산 근거를 보관합니다. 기간·요율은 Fleet Status에서도 확인할 수 있습니다.</p>
      <h3 className="text-sm font-bold text-slate-300">미확정 조건 {unresolved.length}건</h3>
      {cards(unresolved)}
      {unresolved.length === 0 && <p className="py-8 text-sm text-slate-500">Fleet Status에 반영하지 못한 발표가 없습니다.</p>}
      {applied.length > 0 && (
        <details className="border-t border-slate-800 pt-4">
          <summary className="cursor-pointer py-2 text-sm text-slate-500">Fleet Status 반영 이력 {applied.length}건</summary>
          <div className="mt-3 space-y-3">{cards(applied)}</div>
        </details>
      )}
      {others.length > 0 && (
        <details className="border-t border-slate-800 pt-4">
          <summary className="cursor-pointer py-2 text-sm text-slate-500">기타 발표 {others.length}건</summary>
          <ul className="mt-3 space-y-2 text-sm text-slate-300">
            {others.map((event, index) => {
              const title = event.title || event.vessel || event.vessels?.join(', ') || `${event.company} 공식 발표`;
              const href = contractSource(event.url);
              return <li key={`${event.url}-${index}`} className="break-words">
                {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-300 underline underline-offset-4 hover:text-blue-200">{title} ↗</a> : title}
                <span className="ml-2 text-xs text-slate-500">{event.company}{event.publishedAt ? ` · ${event.publishedAt.slice(0, 10)}` : ''}</span>
              </li>;
            })}
          </ul>
        </details>
      )}
    </section>
  );
}
