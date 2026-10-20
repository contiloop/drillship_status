
import React, { useMemo, useState } from 'react';
import { Drillship, OfficialEvent } from '../types';
import { getComputedStatus } from '../utils/shipStatus';
import { contractPeriod, contractSource, contractSpan } from '../utils/contractTimeline';
import { economicsDetails, backlogRateLabel, timelineContracts, TimelineContract } from '../utils/newsEconomics';

interface FleetGanttProps {
  ships: Drillship[];
  events?: OfficialEvent[];
}

const periodLabel = (row: TimelineContract) => row.forecast?.windowLabel || contractPeriod(row);
const rateLabel = (row: TimelineContract, compact = false) => row.dayRate > 0
  ? (compact ? `$${Math.round(row.dayRate / 1000)}k` : `$${row.dayRate.toLocaleString()} / 일 (공시)`)
  : row.economics ? `${backlogRateLabel(row.economics, compact)}${compact ? '' : ' (백로그 환산)'}` : '일당 미공개';

export function ContractDetails({ selected, shipName, onClose }: { selected: TimelineContract; shipName: string; onClose: () => void }) {
  return (
    <div role="status" className="fixed bottom-3 left-[4.25rem] right-3 md:left-[14.5rem] md:right-6 z-[60] flex max-h-[45vh] overflow-y-auto flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-3 text-xs text-slate-300 shadow-2xl">
      <strong className="text-white">{shipName}</strong>
      <span>{selected.forecast?.awardLabel || selected.status} · {selected.client} · {selected.region}</span>
      <span>{periodLabel(selected)}{!selected.forecast && (selected.datePrecision?.start !== 'day' || selected.datePrecision?.end !== 'day') ? ' (월 단위)' : ''}</span>
      <span>{rateLabel(selected)}</span>
      {contractSource(selected.sourceUrl) && <a className="text-blue-300 underline" href={contractSource(selected.sourceUrl)} target="_blank" rel="noopener noreferrer">원문 ↗</a>}
      <button className="ml-auto rounded px-3 py-2 text-slate-400 hover:text-white" onClick={onClose} aria-label="계약 상세 닫기">닫기</button>
      {selected.economics && <p className="w-full text-slate-400">{economicsDetails(selected.economics)}</p>}
      {selected.forecast && <p className="w-full text-slate-400">점선은 공개된 개시 구간부터 종료까지의 일정 범위이며, 전체 폭이 작업 기간은 아닙니다. Firm은 계약 체결, LOA는 수주 통지를 뜻합니다. 정확한 일자가 없는 일정과 환산 요율은 가동률·평균 공시 dayrate 집계에서 제외합니다.</p>}
    </div>
  );
}

const FleetGantt: React.FC<FleetGanttProps> = ({ ships, events = [] }) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const timelineShips = useMemo(() => ships.map(s => ({ ...s, contracts: timelineContracts(s, events) })), [ships, events]);
  const selectedShip = timelineShips.find(s => s.contracts.some(c => c.id === selectedId));
  const selected = selectedShip?.contracts.find(c => c.id === selectedId);
  const now = new Date();
  const yearStart = new Date(now.getFullYear(), 0, 1);
  
  let maxEndDate = new Date(now.getFullYear() + 2, 11, 31);
  timelineShips.forEach(ship => {
    ship.contracts.forEach(contract => {
      const d = new Date(contract.endDate);
      if (d > maxEndDate) maxEndDate = d;
    });
  });

  const yearEnd = new Date(maxEndDate.getFullYear(), 11, 31);
  const totalMonths = (yearEnd.getFullYear() - yearStart.getFullYear() + 1) * 12;

  const getGenColor = (gen: string) => {
    switch(gen) {
      case '8G': return 'bg-fuchsia-600 shadow-fuchsia-900/20';
      case '7G+': return 'bg-violet-600 shadow-violet-900/20';
      case '7G': return 'bg-blue-600 shadow-blue-900/20';
      default: return 'bg-slate-600';
    }
  };

  const quarterMarkers = [];
  for (let i = 0; i <= totalMonths; i += 3) {
    const date = new Date(yearStart.getFullYear(), yearStart.getMonth() + i, 1);
    const quarter = Math.floor(date.getMonth() / 3) + 1;
    const isYearStart = quarter === 1;
    quarterMarkers.push({
      percent: (i / totalMonths) * 100,
      label: isYearStart ? `${date.getFullYear()} Q1` : `Q${quarter}`,
      isYear: isYearStart
    });
  }

  const minWidth = Math.max(1200, (totalMonths / 3) * 120);

  return (
    <section aria-label="계약표" onKeyDown={event => { if (event.key === 'Escape') setSelectedId(null); }} className="bg-slate-900/50 rounded-2xl border border-slate-800 overflow-hidden relative z-10">
      <div className="p-4 border-b border-slate-800 bg-slate-900/80 flex flex-col md:flex-row justify-between gap-3 items-start md:items-center">
        <div>
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            Contracts & Availability
          </h3>
          <p className="text-[10px] text-slate-500 mt-1">{ships.length}척 · {yearStart.getFullYear()}–{yearEnd.getFullYear()} · 바를 선택하면 계약 조건을 확인할 수 있습니다.</p>
        </div>
        <div className="flex flex-wrap gap-4 text-[10px] uppercase font-black tracking-widest text-slate-400">
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-blue-600 rounded-sm"></div> Firm</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-sky-400/20 border border-sky-400 rounded-sm"></div> Option</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-amber-400/20 border border-amber-400 rounded-sm"></div> Contingent</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 border border-dashed border-slate-300 rounded-sm"></div> 개시 구간 공개</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-slate-950 border border-slate-800 rounded-sm"></div> Idle/Gap</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-slate-800 border border-slate-700 rounded-sm cold-stacked-legend"></div> Cold-Stacked</span>
        </div>
      </div>

      {selected && selectedShip && (
        <ContractDetails selected={selected} shipName={selectedShip.name} onClose={() => setSelectedId(null)} />
      )}
      
      <div role="region" aria-label="선박별 계약 타임라인, 좌우 스크롤" tabIndex={0} className="p-3 md:p-4 overflow-x-auto custom-scrollbar relative focus-visible:outline focus-visible:outline-blue-400">
        <div className="relative" style={{ minWidth: `${minWidth}px` }}>
          
          {/* Header Timeline - Sticky Top */}
          <div className="flex mb-6 border-b border-slate-800 pb-2 sticky top-0 bg-slate-900 z-40">
            <div className="w-40 md:w-64 shrink-0 text-[10px] font-black text-slate-500 uppercase tracking-widest sticky left-0 bg-slate-900 z-50 px-2">Vessel / Spec</div>
            <div className="flex-1 relative h-6">
              {quarterMarkers.map((marker, idx) => (
                <div key={idx} className={`absolute top-0 text-[9px] flex flex-col ${idx === 0 ? 'items-start' : 'items-center -translate-x-1/2'} ${marker.isYear ? 'text-slate-200 font-bold' : 'text-slate-500'}`} style={{ left: `${marker.percent}%` }}>
                  <span>{marker.label}</span>
                  <div className={`w-px mt-1 ${marker.isYear ? 'h-3 bg-slate-500' : 'h-1.5 bg-slate-700'}`}></div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-4 relative">
            {/* Background Grid Lines */}
            <div className="absolute inset-0 left-40 md:left-64 pointer-events-none z-0">
              {quarterMarkers.map((marker, idx) => (
                <div key={`grid-${idx}`} className={`absolute top-0 bottom-0 w-px ${marker.isYear ? 'bg-slate-800/40' : 'bg-slate-800/10'}`} style={{ left: `${marker.percent}%` }}></div>
              ))}
            </div>

            {[...timelineShips]
              .sort((a,b) => {
                const genOrder = { '8G': 4, '7G+': 3, '7G': 2, '6G': 1 };
                const aVal = genOrder[a.generation as keyof typeof genOrder] || 0;
                const bVal = genOrder[b.generation as keyof typeof genOrder] || 0;
                if (bVal !== aVal) return bVal - aVal;
                return a.company.localeCompare(b.company);
              })
              .map((ship) => {
                // View-only forecasts must never change the vessel's active status.
                const canonicalShip = ships.find(s => s.id === ship.id)!;
                const computedStatus = getComputedStatus(canonicalShip, now);
                const isColdStacked = computedStatus === 'Cold-Stacked';
                const forecasts = ship.contracts.filter(c => c.forecast);
                return (
                <div key={ship.id} className="flex items-center group transition-all relative">
                  {/* Sticky Vessel Information - Opaque Background */}
                  <div className="w-40 md:w-64 shrink-0 flex items-center gap-2 pr-3 sticky left-0 bg-slate-900 z-30 py-2 border-r border-slate-800/50 shadow-[4px_0_12px_rgba(0,0,0,0.8)]">
                    <span className={`w-8 h-8 flex items-center justify-center text-[10px] rounded-xl font-black shrink-0 text-white shadow-lg ${getGenColor(ship.generation)}`}>
                      {ship.generation}
                    </span>
                    <div className="flex flex-col truncate">
                      <span className="text-xs font-black text-slate-100 group-hover:text-blue-400 transition-colors truncate">{ship.name}</span>
                      <span className="text-[9px] text-slate-500 font-bold uppercase tracking-tight">{ship.company}</span>
                    </div>
                  </div>

                  {/* Timeline Row Section */}
                  <div style={{ height: 40 + forecasts.length * 36 }} className={`flex-1 relative rounded-xl overflow-hidden border z-10 mx-2 ${
                    isColdStacked
                      ? 'bg-slate-800 border-slate-700'
                      : 'bg-slate-950/60 border-slate-800'
                  }`}>
                    {/* Cold-Stacked: 전체 회색 배경 + 빗금 패턴 */}
                    {isColdStacked && (
                      <div className="absolute inset-0 cold-stacked-pattern flex items-center justify-center">
                        <span className="text-[9px] font-black text-slate-400 tracking-[0.3em] italic uppercase bg-slate-800/80 px-2 rounded">COLD-STACKED</span>
                      </div>
                    )}

                    {/* Idle/Available (계약 없음) */}
                    {!isColdStacked && ship.contracts.length === 0 && (
                      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                        <span className="text-[9px] font-black text-slate-700 tracking-[0.4em]">AVAILABLE / IDLE</span>
                      </div>
                    )}

                    {ship.contracts.map((c) => {
                      const span = contractSpan(c, yearStart.getFullYear(), yearEnd.getFullYear());
                      if (!span) return null;
                      
                      const kind = c.forecast?.awardLabel || c.status;
                      return (
                        <button type="button" key={c.id} aria-pressed={selectedId === c.id} onClick={() => setSelectedId(c.id)} aria-label={`${ship.name} · ${kind} · ${c.client} · ${periodLabel(c)} · ${rateLabel(c)}`} className={`absolute text-left rounded-md border hover:brightness-125 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white ${selectedId === c.id ? 'ring-2 ring-white' : ''} ${c.forecast ? (c.status === 'Firm' ? 'bg-blue-600/70 border-dashed border-blue-300' : 'bg-amber-400/10 border-dashed border-amber-300/80') : c.status === 'Firm' ? 'bg-blue-600 border-black/20' : c.status === 'Contingent' ? 'bg-amber-400/20 border-dashed border-amber-400/70' : 'bg-sky-400/20 border-dashed border-sky-400/60'}`} style={{ left: `${span.left}%`, width: `${span.width}%`, height: 30, top: c.forecast ? 42 + forecasts.indexOf(c) * 36 : 4 }} title={`${kind} · ${c.client}: ${periodLabel(c)} · ${rateLabel(c)}`}>
                          <div className={`h-full w-full flex items-center ${c.forecast ? 'justify-center' : ''} px-3 overflow-hidden whitespace-nowrap`}>
                            <span className="text-[10px] font-black text-white/95 truncate drop-shadow-sm">
                              {c.forecast ? `${c.forecast.awardLabel} · ${c.forecast.startLabel} · ${c.forecast.duration.label}` : c.client} {(c.dayRate > 0 || c.economics) ? `• ${rateLabel(c, true)}` : ''}
                            </span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="px-4 py-3 bg-slate-900 border-t border-slate-800">
        <p className="text-[10px] text-slate-500">Firm: 체결 계약 · LOA: 수주 통지 · 점선: 공개된 개시 구간 기준 일정 범위 · 요율은 공시값 또는 계약 가치÷기본 작업 기간. 계산 근거는 바를 선택해 확인합니다.</p>
      </div>
      <style>{`
        .custom-scrollbar::-webkit-scrollbar { height: 8px; width: 8px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: #0f172a; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; border: 2px solid #0f172a; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #475569; }
        .cold-stacked-pattern {
          background: repeating-linear-gradient(
            -45deg,
            transparent,
            transparent 4px,
            rgba(71, 85, 105, 0.3) 4px,
            rgba(71, 85, 105, 0.3) 8px
          );
        }
        .cold-stacked-legend {
          background: repeating-linear-gradient(
            -45deg,
            #1e293b,
            #1e293b 1px,
            #475569 1px,
            #475569 2px
          );
        }
      `}</style>
    </section>
  );
};

export default FleetGantt;
