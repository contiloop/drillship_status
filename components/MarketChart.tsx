
import React, { useState } from 'react';
import { 
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, BarChart, Bar, Cell
} from 'recharts';
import { Drillship, Company, Generation } from '../types';
import { getComputedStatus } from '../utils/shipStatus';

interface MarketChartProps {
  ships: Drillship[];
}

const MarketChart: React.FC<MarketChartProps> = ({ ships }) => {
  const [view, setView] = useState<'generation' | 'company'>('generation');
  const today = new Date().toISOString().slice(0, 10);
  const isCurrentReportedRate = (contract: Drillship['contracts'][number]) =>
    contract.status === 'Firm' && contract.dayRate > 0 && contract.startDate <= today && contract.endDate >= today;

  const generations: Generation[] = ['6G', '7G', '7G+', '8G'];
  const companies: Company[] = ['Transocean', 'Valaris', 'Noble', 'Seadrill'];

  // Market Benchmark
  const allRates = ships.flatMap(s => s.contracts).filter(isCurrentReportedRate).map(c => c.dayRate);
  const marketAvg = allRates.length > 0 ? allRates.reduce((a, b) => a + b, 0) / allRates.length : 0;

  // Generation Stats
  const genStats = generations.map(gen => {
    const genShips = ships.filter(s => s.generation === gen);
    const rates = genShips.flatMap(s => s.contracts).filter(isCurrentReportedRate).map(c => c.dayRate);
    const avgRate = rates.length > 0 ? Math.round(rates.reduce((a, b) => a + b, 0) / rates.length) : 0;
    return { name: gen, avgRate, count: genShips.length };
  });

  // Company Stats
  const companyStats = companies.map(comp => {
    const compShips = ships.filter(s => s.company === comp);
    const data: any = { name: comp };
    
    generations.forEach(gen => {
      const filtered = compShips.filter(s => s.generation === gen);
      data[`${gen}_count`] = filtered.length;
      const rates = filtered.flatMap(s => s.contracts).filter(isCurrentReportedRate).map(c => c.dayRate);
      data[`${gen}_avgRate`] = rates.length > 0 ? Math.round(rates.reduce((a, b) => a + b, 0) / rates.length) : 0;
    });

    const compRates = compShips.flatMap(s => s.contracts).filter(isCurrentReportedRate).map(c => c.dayRate);
    data.overallAvgRate = compRates.length > 0 ? Math.round(compRates.reduce((a, b) => a + b, 0) / compRates.length) : 0;
    data.totalCount = compShips.length;
    data.premium = marketAvg > 0 ? ((data.overallAvgRate - marketAvg) / marketAvg) * 100 : 0;

    // Utilization 계산: Active 선박 수 / 전체 선박 수
    const activeCount = compShips.filter(s => getComputedStatus(s) === 'Active').length;
    data.utilization = compShips.length > 0 ? Math.round((activeCount / compShips.length) * 100) : 0;
    
    const sortedVessels = compShips.map(s => {
      const vRates = s.contracts.filter(isCurrentReportedRate).map(c => c.dayRate);
      const vAvg = vRates.length > 0 ? Math.round(vRates.reduce((a, b) => a + b, 0) / vRates.length) : 0;
      return { name: s.name, avg: vAvg };
    }).filter(v => v.avg > 0).sort((a, b) => b.avg - a.avg);

    data.topVessel = sortedVessels[0];
    data.bottomVessel = sortedVessels[sortedVessels.length - 1];
    
    return data;
  });

  const genColors: Record<string, string> = {
    '6G': '#64748b', '7G': '#3b82f6', '7G+': '#8b5cf6', '8G': '#d946ef'
  };

  const PricingTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const item = payload[0].payload;
      return (
        <div className="bg-slate-950 border border-slate-700 p-5 rounded-2xl shadow-2xl min-w-[280px] z-[9999] ring-1 ring-white/10 opacity-100 !bg-opacity-100 pointer-events-none">
          <div className="flex justify-between items-center mb-3 border-b border-slate-800 pb-3">
            <p className="font-black text-white text-lg">{label}</p>
            {view === 'company' && (
              <span className={`text-[10px] font-bold px-2 py-1 rounded ${item.utilization >= 95 ? 'bg-fuchsia-500/20 text-fuchsia-400' : item.utilization >= 85 ? 'bg-emerald-500/20 text-emerald-400' : item.utilization >= 75 ? 'bg-amber-500/20 text-amber-400' : 'bg-red-500/20 text-red-400'}`}>
                {item.utilization}% Utilization
              </span>
            )}
          </div>
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <span className="text-slate-400 text-xs font-medium">Avg Dayrate</span>
              <span className="text-blue-400 font-black text-lg">${Math.round((payload[0].value)/1000)}k</span>
            </div>
            {view === 'company' && (
              <div className="space-y-2 pt-2 border-t border-slate-800">
                <p className="text-[9px] font-black text-slate-500 uppercase tracking-widest">Yield Highlights</p>
                {item.topVessel && (
                  <div className="flex justify-between items-center text-[11px]">
                    <span className="text-slate-300 truncate max-w-[150px]">Highest: {item.topVessel.name}</span>
                    <span className="text-emerald-400 font-bold">${Math.round(item.topVessel.avg/1000)}k</span>
                  </div>
                )}
                {item.bottomVessel && item.bottomVessel !== item.topVessel && (
                  <div className="flex justify-between items-center text-[11px]">
                    <span className="text-slate-300 truncate max-w-[150px]">Lowest: {item.bottomVessel.name}</span>
                    <span className="text-orange-400 font-bold">${Math.round(item.bottomVessel.avg/1000)}k</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      );
    }
    return null;
  };

  const CompositionTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const item = payload[0].payload;
      return (
        <div className="bg-slate-950 border border-slate-700 p-5 rounded-2xl shadow-2xl min-w-[300px] z-[9999] ring-1 ring-white/10 opacity-100 !bg-opacity-100 pointer-events-none">
          <div className="flex justify-between items-center mb-4 border-b border-slate-800 pb-3">
            <p className="font-black text-white text-lg">{label} Fleet</p>
            {view === 'company' && <span className="bg-slate-800 text-slate-300 px-2 py-1 rounded text-[10px] font-bold">{item.totalCount} Units</span>}
          </div>
          <div className="space-y-2">
            <p className="text-[9px] font-black text-slate-500 uppercase tracking-widest mb-2">Generation Breakdown</p>
            {view === 'company' ? (
              generations.slice().reverse().map(gen => {
                const count = item[`${gen}_count`];
                const avg = item[`${gen}_avgRate`];
                if (count === 0) return null;
                return (
                  <div key={gen} className="flex items-center justify-between bg-white/5 p-2 rounded-lg">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-4 rounded-full" style={{ backgroundColor: genColors[gen] }}></div>
                      <span className="text-white font-bold text-xs">{gen}</span>
                      <span className="text-slate-500 text-[10px]">{count} units</span>
                    </div>
                    <span className="text-blue-300 font-mono font-bold text-xs">{avg > 0 ? `$${Math.round(avg/1000)}k` : 'N/A'}</span>
                  </div>
                );
              })
            ) : (
               <div className="flex items-center justify-between bg-white/5 p-2 rounded-lg">
                  <span className="text-slate-400 text-xs">Total Capacity</span>
                  <span className="text-emerald-400 font-bold">{payload[0].value} units</span>
               </div>
            )}
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="space-y-6 mb-8 relative z-20">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-bold text-white flex items-center gap-2">
          <span className="w-2 h-8 bg-blue-500 rounded-full"></span>
          Market Analysis Charts
        </h3>
        <div className="bg-slate-900 p-1 rounded-lg border border-slate-800 flex gap-1">
          {['generation', 'company'].map((t) => (
            <button key={t} onClick={() => setView(t as any)} className={`px-4 py-1.5 rounded-md text-xs font-bold transition-all uppercase ${view === t ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:text-white'}`}>BY {t}</button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-slate-900/50 backdrop-blur-sm p-6 rounded-3xl border border-slate-800 shadow-xl h-[420px] flex flex-col">
          <h4 className="text-xs font-black text-slate-500 mb-6 uppercase tracking-widest flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-blue-500"></div> Average Dayrate
          </h4>
          <div className="flex-1 w-full min-h-0">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 500, height: 320 }}>
              <BarChart data={view === 'generation' ? genStats : companyStats} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="name" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} dy={10} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `$${v/1000}k`} />
                <Tooltip content={<PricingTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} wrapperStyle={{ zIndex: 9999 }} />
                <Bar dataKey={view === 'generation' ? "avgRate" : "overallAvgRate"} radius={[6, 6, 0, 0]} barSize={32}>
                  {(view === 'generation' ? genStats : companyStats).map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={view === 'generation' ? (genColors[entry.name] || '#3b82f6') : '#3b82f6'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-slate-900/50 backdrop-blur-sm p-6 rounded-3xl border border-slate-800 shadow-xl h-[420px] flex flex-col">
          <h4 className="text-xs font-black text-slate-500 mb-6 uppercase tracking-widest flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div> Asset Composition
          </h4>
          <div className="flex-1 w-full min-h-0">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 500, height: 320 }}>
              <BarChart data={view === 'generation' ? genStats : companyStats} margin={{ top: 10, right: 10, left: 0, bottom: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="name" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} dy={10} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip content={<CompositionTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} wrapperStyle={{ zIndex: 9999 }} />
                {view === 'generation' ? (
                  <Bar dataKey="count" radius={[6, 6, 0, 0]} barSize={32}>
                    {genStats.map((entry, index) => (
                      <Cell key={`cell- composição-${index}`} fill={genColors[entry.name] || '#10b981'} />
                    ))}
                  </Bar>
                ) : (
                  generations.map(gen => (
                    <Bar key={gen} dataKey={`${gen}_count`} stackId="a" fill={genColors[gen]} barSize={32} name={gen} />
                  ))
                )}
                {view === 'company' && <Legend iconType="circle" verticalAlign="bottom" wrapperStyle={{ paddingTop: '20px', fontSize: '10px', fontWeight: 'bold' }} />}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MarketChart;
