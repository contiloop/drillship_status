
import React from 'react';
import { Drillship, Contract } from '../types';
import { getComputedStatus } from '../utils/shipStatus';

interface FleetGanttProps {
  ships: Drillship[];
}

const FleetGantt: React.FC<FleetGanttProps> = ({ ships }) => {
  const now = new Date();
  const yearStart = new Date(now.getFullYear(), 0, 1);
  
  let maxEndDate = new Date(now.getFullYear() + 2, 11, 31);
  ships.forEach(ship => {
    ship.contracts.forEach(contract => {
      const d = new Date(contract.endDate);
      if (d > maxEndDate) maxEndDate = d;
    });
  });

  const yearEnd = new Date(maxEndDate.getFullYear(), 11, 31);
  const totalMonths = (yearEnd.getFullYear() - yearStart.getFullYear() + 1) * 12;

  const getPos = (dateStr: string) => {
    const d = new Date(dateStr);
    const months = (d.getFullYear() - yearStart.getFullYear()) * 12 + (d.getMonth() - yearStart.getMonth());
    return Math.max(0, Math.min(100, (months / totalMonths) * 100));
  };

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
    <div className="bg-slate-900/50 backdrop-blur-sm rounded-3xl border border-slate-800 shadow-2xl overflow-hidden relative z-10">
      <div className="p-6 border-b border-slate-800 bg-slate-900/80 flex flex-col md:flex-row justify-between gap-4 items-start md:items-center">
        <div>
          <h3 className="text-xl font-bold text-white flex items-center gap-2">
            <span className="w-2 h-8 bg-purple-500 rounded-full"></span>
            Contract Pipeline & Future Availability
          </h3>
          <p className="text-xs text-slate-500 mt-1 uppercase tracking-tighter font-bold">Vessel-specific commitment through {yearEnd.getFullYear()}</p>
        </div>
        <div className="flex flex-wrap gap-4 text-[10px] uppercase font-black tracking-widest text-slate-400">
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-blue-600 rounded-sm"></div> Firm</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-sky-400/20 border border-sky-400 rounded-sm"></div> Option</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-slate-950 border border-slate-800 rounded-sm"></div> Idle/Gap</span>
          <span className="flex items-center gap-2"><div className="w-3 h-3 bg-slate-800 border border-slate-700 rounded-sm cold-stacked-legend"></div> Cold-Stacked</span>
        </div>
      </div>
      
      <div className="p-6 overflow-x-auto custom-scrollbar relative">
        <div className="relative" style={{ minWidth: `${minWidth}px` }}>
          
          {/* Header Timeline - Sticky Top */}
          <div className="flex mb-6 border-b border-slate-800 pb-2 sticky top-0 bg-slate-900 z-40">
            <div className="w-64 shrink-0 text-[10px] font-black text-slate-500 uppercase tracking-widest sticky left-0 bg-slate-900 z-50 px-2">Vessel / Spec</div>
            <div className="flex-1 relative h-6">
              {quarterMarkers.map((marker, idx) => (
                <div key={idx} className={`absolute top-0 text-[9px] flex flex-col items-center -translate-x-1/2 ${marker.isYear ? 'text-slate-200 font-bold' : 'text-slate-500'}`} style={{ left: `${marker.percent}%` }}>
                  <span>{marker.label}</span>
                  <div className={`w-px mt-1 ${marker.isYear ? 'h-3 bg-slate-500' : 'h-1.5 bg-slate-700'}`}></div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-4 relative">
            {/* Background Grid Lines */}
            <div className="absolute inset-0 left-64 pointer-events-none z-0">
              {quarterMarkers.map((marker, idx) => (
                <div key={`grid-${idx}`} className={`absolute top-0 bottom-0 w-px ${marker.isYear ? 'bg-slate-800/40' : 'bg-slate-800/10'}`} style={{ left: `${marker.percent}%` }}></div>
              ))}
            </div>

            {ships
              .sort((a,b) => {
                const genOrder = { '8G': 4, '7G+': 3, '7G': 2, '6G': 1 };
                const aVal = genOrder[a.generation as keyof typeof genOrder] || 0;
                const bVal = genOrder[b.generation as keyof typeof genOrder] || 0;
                if (bVal !== aVal) return bVal - aVal;
                return a.company.localeCompare(b.company);
              })
              .map((ship) => {
                const computedStatus = getComputedStatus(ship, now);
                const isColdStacked = computedStatus === 'Cold-Stacked';
                return (
                <div key={ship.id} className="flex items-center group transition-all relative">
                  {/* Sticky Vessel Information - Opaque Background */}
                  <div className="w-64 shrink-0 flex items-center gap-3 pr-4 sticky left-0 bg-slate-900 z-30 py-2 border-r border-slate-800/50 shadow-[4px_0_12px_rgba(0,0,0,0.8)]">
                    <span className={`w-8 h-8 flex items-center justify-center text-[10px] rounded-xl font-black shrink-0 text-white shadow-lg ${getGenColor(ship.generation)}`}>
                      {ship.generation}
                    </span>
                    <div className="flex flex-col truncate">
                      <span className="text-xs font-black text-slate-100 group-hover:text-blue-400 transition-colors truncate">{ship.name}</span>
                      <span className="text-[9px] text-slate-500 font-bold uppercase tracking-tight">{ship.company}</span>
                    </div>
                  </div>

                  {/* Timeline Row Section */}
                  <div className={`flex-1 relative h-10 rounded-xl overflow-hidden border z-10 mx-2 ${
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
                      const start = getPos(c.startDate);
                      const end = getPos(c.endDate);
                      const width = Math.max(0.2, end - start);
                      if (width <= 0) return null;
                      
                      return (
                        <div key={c.id} className={`absolute top-1.5 bottom-1.5 rounded-lg border border-black/20 transition-all hover:scale-y-110 hover:z-50 cursor-help ${c.status === 'Firm' ? 'bg-blue-600 shadow-[0_0_15px_rgba(37,99,235,0.4)]' : 'bg-sky-400/20 border-dashed border-sky-400/60 shadow-inner'}`} style={{ left: `${start}%`, width: `${width}%` }} title={`${c.client}: ${c.startDate} ~ ${c.endDate} ($${Math.round(c.dayRate/1000)}k)`}>
                          <div className="h-full w-full flex items-center px-3 overflow-hidden whitespace-nowrap">
                            <span className="text-[10px] font-black text-white/95 truncate drop-shadow-sm">
                              {c.client} {c.dayRate > 0 ? `• $${Math.round(c.dayRate/1000)}k` : ''}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="px-6 py-5 bg-slate-900 border-t border-slate-800 text-center">
        <p className="text-[10px] text-slate-500 font-bold tracking-widest uppercase opacity-70 italic">Contract data visualized from quarterly reports. Horizontal scroll enabled for future projections.</p>
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
    </div>
  );
};

export default FleetGantt;
