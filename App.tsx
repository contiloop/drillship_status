
import React, { useState, useEffect } from 'react';
import {
  LayoutGrid, Ship, Download, FileJson, Database, BarChart3,
  AlertTriangle, TrendingUp, Code
} from 'lucide-react';
import { Analytics } from '@vercel/analytics/react';
import { Drillship, FleetManifest, OfficialEvent } from './types';
import MarketChart from './components/MarketChart';
import FleetGantt from './components/FleetGantt';
import OfficialAnnouncementCard from './components/OfficialAnnouncementCard';
import defaultData from './data/data_as_of_26_01_07.json';
import { countFirmCoveredDays, getComputedStatus } from './utils/shipStatus';

const BASE_URL = import.meta.env.BASE_URL || '/';
const MANIFEST_URL = `${BASE_URL}data/manifest.json`;

type TabType = 'overview' | 'transocean' | 'valaris' | 'noble' | 'seadrill' | 'readme';

export default function App() {
  const [ships, setShips] = useState<Drillship[]>([]);
  const [dataSource, setDataSource] = useState<'live' | 'custom' | 'bundled'>('bundled');
  const [manifest, setManifest] = useState<FleetManifest | null>(null);
  const [officialEvents, setOfficialEvents] = useState<OfficialEvent[]>([]);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>('overview');
  const [utilizationMode, setUtilizationMode] = useState<'fleet' | 'market' | 'economic'>('market');

  useEffect(() => {
    const loadRemoteData = async () => {
      try {
        const manifestResponse = await fetch(MANIFEST_URL, { cache: 'no-store' });
        if (!manifestResponse.ok) throw new Error(`manifest HTTP ${manifestResponse.status}`);
        const nextManifest = await manifestResponse.json() as FleetManifest;
        if (!nextManifest.fleetFile || nextManifest.shipCount !== 62 || nextManifest.sourceCount !== 4) {
          throw new Error('manifest validation failed');
        }
        const response = await fetch(`${BASE_URL}data/${nextManifest.fleetFile}`, { cache: 'force-cache' });
        if (!response.ok) throw new Error(`remote data HTTP ${response.status}`);

        const remoteShips = await response.json();
        if (Array.isArray(remoteShips) && remoteShips.length === nextManifest.shipCount) {
          if (nextManifest.eventsFile) {
            const eventsResponse = await fetch(`${BASE_URL}data/${nextManifest.eventsFile}`, { cache: 'force-cache' });
            if (eventsResponse.ok) {
              const feed = await eventsResponse.json();
              setOfficialEvents(Array.isArray(feed.events) ? feed.events : []);
            }
          }
          setShips(remoteShips as Drillship[]);
          setManifest(nextManifest);
          setDataSource('live');
          setSyncError(null);
          return;
        }
        throw new Error('fleet payload validation failed');
      } catch (error) {
        console.warn('Remote fleet data unavailable, falling back to bundled data.', error);
        setSyncError(error instanceof Error ? error.message : 'unknown sync error');
      }

      setShips(defaultData as Drillship[]);
      setDataSource('bundled');
    };

    loadRemoteData();
  }, []);

  const exportData = () => {
    const blob = new Blob([JSON.stringify(ships, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `drillship_fleet_${new Date().toISOString().split('T')[0]}.json`;
    a.click();
  };

  const importData = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string);
        if (!Array.isArray(data) || data.length === 0) throw new Error('JSON array required');
        setShips(data as Drillship[]);
        setDataSource('custom');
        alert("데이터를 성공적으로 불러왔습니다.");
        setActiveTab('overview');
      } catch (e) {
        alert("유효하지 않은 파일입니다. JSON 형식을 확인해주세요.");
      }
    };
    reader.readAsText(file);
  };

  const reloadLiveData = () => {
    window.location.reload();
  };

  // 회사별 필터링
  const getFilteredShips = () => {
    if (activeTab === 'transocean') return ships.filter(s => s.company === 'Transocean');
    if (activeTab === 'valaris') return ships.filter(s => s.company === 'Valaris');
    if (activeTab === 'noble') return ships.filter(s => s.company === 'Noble');
    if (activeTab === 'seadrill') return ships.filter(s => s.company === 'Seadrill');
    return ships;
  };

  const filteredShips = getFilteredShips();

  const totalShips = filteredShips.length;
  const today = new Date();
  const activeShips = filteredShips.filter(s => getComputedStatus(s, today) === 'Active').length;
  const todayStr = today.toISOString().slice(0, 10);
  const allContracts = filteredShips.flatMap(s => s.contracts).filter(c =>
    c.status === 'Firm' && c.startDate <= todayStr && c.endDate >= todayStr
  );
  const avgDayrate = allContracts.filter(c => c.dayRate > 0).reduce((a, b, _, arr) => a + (b.dayRate / arr.length), 0);
  const coldStacked = filteredShips.filter(s => getComputedStatus(s, today) === 'Cold-Stacked').length;

  // Utilization 계산
  const marketedFleet = totalShips - coldStacked;
  const fleetUtilization = totalShips > 0 ? Math.round((activeShips / totalShips) * 100) : 0;
  const marketUtilization = marketedFleet > 0 ? Math.round((activeShips / marketedFleet) * 100) : 0;

  // Economic Utilization: 유상 계약 일수 기준 (간단 계산)
  const yearStart = new Date(Date.UTC(today.getUTCFullYear(), 0, 1));
  const todayUtc = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const daysSinceYearStart = Math.floor((todayUtc.getTime() - yearStart.getTime()) / 86_400_000) + 1;
  const totalPossibleDays = marketedFleet * daysSinceYearStart;
  const totalContractDays = filteredShips
    .filter(ship => getComputedStatus(ship, today) !== 'Cold-Stacked')
    .reduce((sum, ship) => sum + countFirmCoveredDays(ship, yearStart, todayUtc), 0);

  const economicUtilization = totalPossibleDays > 0 ? Math.round((totalContractDays / totalPossibleDays) * 100) : 0;
  const recentEvents = [...officialEvents]
    .filter(event => event.pendingReview !== false && !event.autoApplied)
    .sort((a, b) => (Date.parse(b.publishedAt || '') || 0) - (Date.parse(a.publishedAt || '') || 0))
    .slice(0, 5);
  const reportSources = [...(manifest?.sources ?? [])]
    .sort((a, b) => a.company.localeCompare(b.company));
  const reportDates = reportSources.map(source => source.reportDate).sort();
  const reportDateRange = reportDates.length === 0
    ? 'bundled fallback'
    : reportDates[0] === reportDates[reportDates.length - 1]
      ? `report date ${reportDates[0]}`
      : `report dates ${reportDates[0]}–${reportDates[reportDates.length - 1]}`;

  return (
    <>
      <Analytics />
      <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans selection:bg-blue-500/30">
        <div className="fixed left-0 top-0 bottom-0 w-20 md:w-64 bg-slate-900 border-r border-slate-800 z-50 flex flex-col items-center md:items-stretch py-8 px-4">
        <div className="flex items-center gap-3 px-2 mb-12">
          <div className="bg-blue-600 p-2 rounded-xl shadow-lg shadow-blue-900/40">
            <Ship className="text-white w-6 h-6" />
          </div>
          <span className="hidden md:block font-black text-lg tracking-tighter">Drillship Status</span>
        </div>
        
        <nav className="flex-1 space-y-2 overflow-y-auto">
          <button onClick={() => setActiveTab('overview')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'overview' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <LayoutGrid size={20} />
            <span className="hidden md:block font-bold">Dashboard</span>
          </button>

          <div className="hidden md:block pt-2 pb-1 px-4">
            <p className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Companies</p>
          </div>

          <button onClick={() => setActiveTab('transocean')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'transocean' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Ship size={18} />
            <span className="hidden md:block font-bold text-sm">Transocean</span>
          </button>
          <button onClick={() => setActiveTab('valaris')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'valaris' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Ship size={18} />
            <span className="hidden md:block font-bold text-sm">Valaris</span>
          </button>
          <button onClick={() => setActiveTab('noble')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'noble' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Ship size={18} />
            <span className="hidden md:block font-bold text-sm">Noble</span>
          </button>
          <button onClick={() => setActiveTab('seadrill')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'seadrill' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Ship size={18} />
            <span className="hidden md:block font-bold text-sm">Seadrill</span>
          </button>

          <div className="hidden md:block pt-4 pb-1 px-4">
            <div className="border-t border-slate-800"></div>
          </div>

          <button onClick={() => setActiveTab('readme')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'readme' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Database size={20} />
            <span className="hidden md:block font-bold">README</span>
          </button>
        </nav>

        <div className="pt-8 border-t border-slate-800 opacity-50">
          <div className="hidden md:block p-4 bg-slate-800/30 rounded-2xl">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1">Auto Sync</p>
            <p className="text-[10px] text-slate-400">Official FSR, SEC and IR sources are checked every six hours.</p>
          </div>
        </div>
      </div>

      <main className="flex-1 ml-20 md:ml-64 p-6 md:p-10">
        <header className="mb-10 flex flex-col md:flex-row md:items-end justify-between gap-6">
          <div>
            <h2 className="text-3xl font-black text-white tracking-tight">
              {activeTab === 'overview' && 'Market Overview'}
              {activeTab === 'transocean' && 'Transocean Fleet'}
              {activeTab === 'valaris' && 'Valaris Fleet'}
              {activeTab === 'noble' && 'Noble Fleet'}
              {activeTab === 'seadrill' && 'Seadrill Fleet'}
              {activeTab === 'readme' && 'Database Management'}
            </h2>
            <p className="text-slate-500 mt-1 font-medium">
              {activeTab === 'readme' ? 'Offshore Drillship Fleet Status & Pricing Intelligence' :
               activeTab === 'overview' ? 'Offshore Drillship Fleet Status & Pricing Intelligence' :
               `${activeTab.charAt(0).toUpperCase() + activeTab.slice(1)} Drillship Fleet Analysis`}
            </p>
            <p className="mt-3 inline-flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/70 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.22em] text-slate-400">
              Official sync
              <span className="text-slate-200">{manifest?.sourceCount ?? 0}/4 verified</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-500">
                {reportDateRange}
              </span>
            </p>
            {reportSources.length > 0 && (
              <p className="text-[11px] text-slate-500 mt-2">
                Sources: {reportSources.map(source => `${source.company} ${source.reportDate}`).join(' · ')}
              </p>
            )}
            {manifest?.generatedAt && (
              <p className="text-[11px] text-slate-600 mt-2">
                Generated {new Date(manifest.generatedAt).toLocaleString()} · {manifest.contractCount} contract records · 상세 조건 미확정 공지 {manifest.pendingEventCount}건
              </p>
            )}
            {syncError && <p className="text-[11px] text-amber-500/80 mt-2">Live sync fallback: {syncError}</p>}
            <p className="text-[10px] font-bold text-slate-600 uppercase tracking-[0.24em] mt-2">
              Data source: {dataSource}
            </p>
          </div>
          {(activeTab === 'overview' || activeTab === 'transocean' || activeTab === 'valaris' || activeTab === 'noble' || activeTab === 'seadrill') && (
            <div className="flex gap-3">
               <button onClick={exportData} className="p-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg text-slate-400 hover:text-white transition-all flex items-center gap-2 px-3">
                 <Download size={18} /> <span className="text-xs font-bold">Export JSON</span>
               </button>
               <label className="p-2 bg-blue-600 hover:bg-blue-500 border border-blue-500 rounded-lg text-white transition-all cursor-pointer flex items-center gap-2 px-3 shadow-lg shadow-blue-900/20">
                 <FileJson size={18} /> <span className="text-xs font-bold">Upload JSON</span>
                 <input type="file" onChange={importData} className="hidden" />
               </label>
               <button onClick={reloadLiveData} className="p-2 bg-orange-900/50 hover:bg-orange-800 border border-orange-800 rounded-lg text-orange-400 hover:text-orange-300 transition-all flex items-center gap-2 px-3">
                 <Database size={18} /> <span className="text-xs font-bold">Reload Live</span>
               </button>
            </div>
          )}
        </header>

        {(activeTab === 'overview' || activeTab === 'transocean' || activeTab === 'valaris' || activeTab === 'noble' || activeTab === 'seadrill') ? (
          <div className="space-y-10 max-w-7xl">
            {activeTab === 'overview' && recentEvents.length > 0 && (
              <section className="bg-slate-900/60 border border-slate-800 rounded-3xl p-6 shadow-xl">
                <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-2 mb-5">
                  <div>
                    <h3 className="text-lg font-black text-white">공식 발표 · 상세 조건 미확정</h3>
                    <p className="text-sm leading-relaxed text-slate-400 mt-2">공식 자료에서 확인된 조건을 먼저 보여줍니다. 승인 대기가 아니라, 상세 조건이 추가로 공개되기를 기다리는 항목입니다.</p>
                  </div>
                </div>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                  {recentEvents.map((event, index) => (
                    <div key={`${event.company}-${event.url || event.vessel || index}`} className="min-w-0">
                      <OfficialAnnouncementCard
                        event={event}
                        source={reportSources.find(source => source.company === event.company)}
                      />
                    </div>
                  ))}
                </div>
              </section>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
              {[
                { label: 'Total Fleet', value: totalShips, icon: Ship },
                { label: 'Avg Dayrate', value: `$${Math.round(avgDayrate/1000)}k`, icon: TrendingUp },
                { label: 'Cold Stacked', value: coldStacked, icon: AlertTriangle },
              ].map((kpi, idx) => (
                <div key={idx} className="bg-slate-900/50 p-6 rounded-3xl border border-slate-800 shadow-xl group hover:border-slate-700 transition-all">
                  <div className={`p-3 rounded-2xl bg-blue-500/10 text-blue-500 w-fit mb-4`}>
                    <kpi.icon size={24} />
                  </div>
                  <p className="text-slate-500 text-xs font-bold uppercase tracking-widest">{kpi.label}</p>
                  <p className="text-3xl font-black text-white mt-1">{kpi.value}</p>
                </div>
              ))}

              {/* Utilization Card with Tabs */}
              <div className="bg-slate-900/50 p-6 rounded-3xl border border-slate-800 shadow-xl group hover:border-slate-700 transition-all">
                <div className="p-3 rounded-2xl bg-blue-500/10 text-blue-500 w-fit mb-4">
                  <BarChart3 size={24} />
                </div>

                {/* Tab Buttons */}
                <div className="flex gap-1 mb-3">
                  {(['fleet', 'market', 'economic'] as const).map(mode => (
                    <button
                      key={mode}
                      onClick={() => setUtilizationMode(mode)}
                      className={`px-2 py-1 text-[9px] font-bold uppercase tracking-wider rounded transition-all ${
                        utilizationMode === mode
                          ? 'bg-blue-600 text-white'
                          : 'text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      {mode}
                    </button>
                  ))}
                </div>

                {/* Display Current Utilization */}
                <p className="text-slate-500 text-xs font-bold uppercase tracking-widest">
                  {utilizationMode === 'fleet' && 'Fleet Utilization'}
                  {utilizationMode === 'market' && 'Market Utilization'}
                  {utilizationMode === 'economic' && 'Economic Utilization'}
                </p>
                <p className="text-3xl font-black text-white mt-1">
                  {utilizationMode === 'fleet' && `${fleetUtilization}%`}
                  {utilizationMode === 'market' && `${marketUtilization}%`}
                  {utilizationMode === 'economic' && `${economicUtilization}%`}
                </p>
                <p className="text-[10px] text-slate-600 mt-2 font-bold">
                  {utilizationMode === 'fleet' && `${activeShips} active / ${totalShips} total`}
                  {utilizationMode === 'market' && `${activeShips} active / ${marketedFleet} marketed`}
                  {utilizationMode === 'economic' && `${totalContractDays} days / ${totalPossibleDays} possible`}
                </p>
              </div>
            </div>

            {filteredShips.length > 0 ? (
              <>
                <MarketChart ships={filteredShips} />
                <FleetGantt ships={filteredShips} />
              </>
            ) : (
              <div className="text-center py-24 bg-slate-900/30 rounded-3xl border border-dashed border-slate-800">
                <Database className="mx-auto text-slate-700 mb-4" size={48} />
                <h3 className="text-xl font-bold text-slate-400">
                  {ships.length === 0 ? 'Empty Database' : `No ${activeTab.charAt(0).toUpperCase() + activeTab.slice(1)} Ships`}
                </h3>
                <p className="text-slate-600 mt-2 max-w-xs mx-auto">
                  {ships.length === 0 ? 'Upload a valid JSON file to visualize the offshore drilling fleet market.' :
                   `No ships found for ${activeTab.charAt(0).toUpperCase() + activeTab.slice(1)} in the current database.`}
                </p>
                {ships.length === 0 && (
                  <button onClick={() => setActiveTab('readme')} className="mt-6 bg-blue-600 hover:bg-blue-500 text-white px-8 py-3 rounded-2xl font-bold transition-all">Go to Upload Guide</button>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="max-w-5xl grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-8">
              {/* Utilization Definitions */}
              <div className="bg-slate-900 border border-slate-800 p-8 rounded-3xl shadow-2xl">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-10 h-10 bg-blue-500/10 text-blue-500 rounded-xl flex items-center justify-center">
                    <BarChart3 size={24} />
                  </div>
                  <h3 className="text-xl font-bold">Utilization Metrics Guide</h3>
                </div>

                <div className="space-y-6">
                  <p className="text-slate-400 text-sm leading-relaxed">
                    본 시스템은 3가지 가동률 지표를 제공합니다. 각 지표는 서로 다른 관점에서 시추선 함대의 활용도를 측정합니다.
                  </p>

                  <div className="space-y-4">
                    {/* Fleet Utilization */}
                    <div className="bg-slate-800/30 rounded-xl p-5 border border-slate-800">
                      <div className="flex items-center gap-2 mb-3">
                        <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                        <h4 className="text-sm font-bold text-white">① Fleet Utilization (함대 가동률)</h4>
                      </div>
                      <p className="text-xs text-slate-400 mb-2">
                        <span className="font-mono bg-slate-950 px-2 py-1 rounded text-blue-300">가동 중인 시추선 수 ÷ 전체 보유 시추선 수</span>
                      </p>
                      <p className="text-xs text-slate-500 leading-relaxed mb-2">
                        가장 단순한 방식으로, 전체 보유 자산 대비 실제 가동 중인 시추선의 비율을 나타냅니다.
                      </p>
                      <p className="text-xs text-orange-400/80 leading-relaxed">
                        ⚠️ 주의: Cold-Stacked 시추선도 분모에 포함되어 실제 영업 가능한 자산과 괴리가 있을 수 있습니다.
                      </p>
                    </div>

                    {/* Market Utilization */}
                    <div className="bg-slate-800/30 rounded-xl p-5 border border-slate-800 border-l-4 border-l-blue-500">
                      <div className="flex items-center gap-2 mb-3">
                        <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                        <h4 className="text-sm font-bold text-white">② Market Utilization (시장 가동률) ⭐</h4>
                      </div>
                      <p className="text-xs text-slate-400 mb-2">
                        <span className="font-mono bg-slate-950 px-2 py-1 rounded text-blue-300">가동 중 ÷ (가동 중 + 유휴 but 시장 출시)</span>
                      </p>
                      <p className="text-xs text-slate-500 leading-relaxed mb-2">
                        실제로 시장에 나와 있는 시추선만을 기준으로 계산합니다. Cold-Stacked, 폐선 예정, 장기 조선소 입고 자산은 제외됩니다.
                      </p>
                      <p className="text-xs text-green-400/80 leading-relaxed">
                        👉 드릴링 시장 수급 판단 시 가장 많이 사용되는 핵심 지표입니다.
                      </p>
                    </div>

                    {/* Economic Utilization */}
                    <div className="bg-slate-800/30 rounded-xl p-5 border border-slate-800">
                      <div className="flex items-center gap-2 mb-3">
                        <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                        <h4 className="text-sm font-bold text-white">③ Economic Utilization (경제적 가동률)</h4>
                      </div>
                      <p className="text-xs text-slate-400 mb-2">
                        <span className="font-mono bg-slate-950 px-2 py-1 rounded text-blue-300">유상 계약 일수 ÷ 전체 가능 일수</span>
                      </p>
                      <p className="text-xs text-slate-500 leading-relaxed">
                        실제로 수익을 창출하는 날짜를 기준으로 계산합니다. 가장 실무적이고 재무적 관점의 지표입니다.
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 p-8 rounded-3xl shadow-2xl">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-10 h-10 bg-blue-500/10 text-blue-500 rounded-xl flex items-center justify-center">
                    <Code size={24} />
                  </div>
                  <h3 className="text-xl font-bold">JSON Data Schema Guide</h3>
                </div>

                <div className="space-y-6">
                  <p className="text-slate-400 text-sm leading-relaxed">
                    본 시스템은 특정 구조의 JSON 파일을 필요로 합니다. 외부 AI 도구를 사용하여 데이터를 구조화할 때 아래 스키마를 참고하십시오.
                  </p>
                  
                  <div className="bg-slate-950 rounded-2xl p-6 border border-slate-800 font-mono text-[11px] overflow-x-auto text-blue-300">
                    <pre>{`[
  {
    "id": "deepwater-titan",
    "name": "Deepwater Titan",
    "company": "Transocean",
    "generation": "8G",
    "status": "Active",
    "statusAsOf": "2026-08-05",
    "yearBuilt": 2023,
    "contracts": [
      {
        "id": "deepwater-titan-1",
        "vesselId": "deepwater-titan",
        "startDate": "2023-04-01",
        "endDate": "2028-04-01",
        "dayRate": 462000,
        "client": "Chevron",
        "region": "USGOM",
        "status": "Firm"
      }
    ]
  }
]`}</pre>
                  </div>

                  <div className="bg-slate-800/30 rounded-xl border border-slate-800 overflow-hidden">
                    <p className="text-[10px] font-black text-slate-500 uppercase tracking-widest p-4 pb-2">Field Reference</p>
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-slate-700 text-slate-400">
                          <th className="text-left p-3 font-bold">Field</th>
                          <th className="text-left p-3 font-bold">Type</th>
                          <th className="text-left p-3 font-bold">Allowed Values</th>
                        </tr>
                      </thead>
                      <tbody className="text-slate-300">
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">company</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">Transocean, Valaris, Noble, Seadrill</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">generation</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">6G, 7G, 7G+, 8G</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">status (ship)</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">Active, Idle, Warm-Stacked, Cold-Stacked</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">statusAsOf (optional)</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">Official status snapshot date (YYYY-MM-DD)</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">status (contract)</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">Firm, Option, Contingent</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">dayRate</td>
                          <td className="p-3 text-slate-500">number</td>
                          <td className="p-3">Integer (e.g. 462000)</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">client</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">e.g. Chevron, Petrobras, Shell, bp</td>
                        </tr>
                        <tr className="border-b border-slate-800">
                          <td className="p-3 font-medium">region</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">e.g. USGOM, Brazil, India, Australia</td>
                        </tr>
                        <tr>
                          <td className="p-3 font-medium">dates</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">YYYY-MM-DD format</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-6">
              <div className="bg-slate-900 border border-slate-800 p-8 rounded-3xl shadow-2xl">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 bg-amber-500/10 text-amber-500 rounded-xl flex items-center justify-center">
                    <AlertTriangle size={24} />
                  </div>
                  <h4 className="text-lg font-bold">Notes & Tips</h4>
                </div>
                <ul className="text-sm text-slate-400 space-y-3 leading-relaxed">
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    공식 FSR/IR/SEC 소스는 GitHub Actions가 6시간마다 확인하고, 검증을 모두 통과한 경우에만 공개 JSON을 갱신합니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    JSON 파일은 Dashboard 탭에서 업로드/다운로드 가능합니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    기본 데이터는 브라우저 저장소가 아니라 빌드에 포함된 최신 공개 JSON을 사용합니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    중요한 데이터는 Export JSON으로 백업해두세요.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    Dayrate 단위는 USD/일 기준입니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    차트에서 BY GENERATION / BY COMPANY 버튼으로 뷰 전환 가능합니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    차트 바에 커서를 올리면 상세 정보(회사별 Utilization, 최고/최저 Dayrate 선박)를 확인할 수 있습니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    Utilization 색상 기준:
                  </li>
                  <li className="flex gap-2 ml-4">
                    <span className="text-fuchsia-400">■</span>
                    95%+ (Super Cycle) - 초호황, 공급 부족
                  </li>
                  <li className="flex gap-2 ml-4">
                    <span className="text-emerald-400">■</span>
                    85-95% (Seller's Market) - 공급자 우위
                  </li>
                  <li className="flex gap-2 ml-4">
                    <span className="text-amber-400">■</span>
                    75-85% (Balanced) - 균형/전환기
                  </li>
                  <li className="flex gap-2 ml-4">
                    <span className="text-red-400">■</span>
                    75% 미만 (Buyer's Market) - 수요자 우위, 불황
                  </li>
                  <li className="flex gap-2 mt-3">
                    <span className="text-amber-500">•</span>
                    <span><strong className="text-slate-300">Noble Guyana 4척</strong> (Tom Madden, Sam Croft, Don Taylor, Bob Douglas): ExxonMobil 계약으로, 매년 3월과 9월에 시장 상황에 따라 dayRate가 조정됨</span>
                  </li>
                </ul>
              </div>

              <button 
                onClick={reloadLiveData}
                className="w-full py-4 bg-slate-900 border border-slate-800 text-red-500/50 hover:text-red-500 hover:border-red-500/30 rounded-2xl text-xs font-bold uppercase tracking-widest flex items-center justify-center gap-2 transition-all"
              >
                <Database size={16} /> Reload Verified Live Data
              </button>
            </div>
          </div>
        )}
      </main>
      </div>
    </>
  );
}
