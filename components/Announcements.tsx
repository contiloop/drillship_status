import React from 'react';
import type { FleetSource, OfficialEvent } from '../types';
import { contractSource, isIsoDate } from '../utils/contractTimeline';
import OfficialAnnouncementCard from './OfficialAnnouncementCard';

const publicationTime = (event: OfficialEvent) => {
  const date = event.publishedAt?.slice(0, 10) || '';
  return isIsoDate(date) ? Date.parse(`${date}T00:00:00Z`) : 0;
};

/** Items with contract conditions open as detail cards; the rest are title + link rows. */
export const hasContractDetails = (event: OfficialEvent) =>
  Boolean((event.llmReview || event.readingDecision) && event.vessel);

/** The tag shown on every news-list item; untagged legacy items fall back by kind. */
export function newsCategory(event: OfficialEvent): string {
  if (event.newsCategory) return event.newsCategory;
  if (event.classification === 'official-report-reading') return 'Fleet Status';
  return hasContractDetails(event) ? '계약' : '기타';
}

/** Every event, newest first, in one list. */
export function orderedAnnouncements(events: OfficialEvent[]) {
  return [...events].sort((a, b) => publicationTime(b) - publicationTime(a));
}

export function CategoryTag({ event }: { event: OfficialEvent }) {
  return <span className="mr-2 inline-block rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300">{newsCategory(event)}</span>;
}

export default function Announcements({ events, sources }: { events: OfficialEvent[]; sources: FleetSource[] }) {
  const rows = orderedAnnouncements(events);
  return (
    <section aria-label="뉴스·공시 목록" className="max-w-5xl space-y-3">
      <p className="text-sm text-slate-400">계약 조건과 계산 근거를 보관합니다. 기간·요율은 Fleet Status에서도 확인할 수 있습니다.</p>
      {rows.length === 0 && <p className="py-8 text-sm text-slate-500">표시할 발표가 없습니다.</p>}
      {rows.map((event, index) => {
        const key = `${event.url}-${event.vessel || ''}-${index}`;
        if (hasContractDetails(event)) {
          return <OfficialAnnouncementCard key={key} event={event} source={sources.find(source => source.company === event.company)} />;
        }
        const title = event.title || event.vessel || event.vessels?.join(', ') || `${event.company} 공식 발표`;
        const href = contractSource(event.url);
        return (
          <div key={key} className="break-words rounded-xl border border-slate-800 px-4 py-3 text-sm text-slate-300">
            <CategoryTag event={event} />
            {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-300 underline underline-offset-4 hover:text-blue-200">{title} ↗</a> : title}
            <span className="ml-2 text-xs text-slate-500">{event.company}{event.publishedAt ? ` · ${event.publishedAt.slice(0, 10)}` : ''}</span>
          </div>
        );
      })}
    </section>
  );
}
