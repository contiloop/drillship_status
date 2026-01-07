
import React, { useState, useEffect, useRef } from 'react';
import {
  LayoutGrid, Ship, Download, FileJson, Database, BarChart3,
  AlertTriangle, TrendingUp, Code, Trash2
} from 'lucide-react';
import { Drillship, Company, Generation } from './types';
import MarketChart from './components/MarketChart';
import FleetGantt from './components/FleetGantt';

const STORAGE_KEY = 'drillship_fleet_data_v2';

export default function App() {
  const [ships, setShips] = useState<Drillship[]>([]);
  const [activeTab, setActiveTab] = useState<'overview' | 'input'>('overview');
  const isInitialMount = useRef(true);
  const isLoaded = useRef(false);

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        let parsed: Drillship[] = JSON.parse(saved);
        setShips(parsed);
      } catch (e) {
        console.error("Failed to load local data", e);
      }
    }
    isLoaded.current = true;
  }, []);

  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    if (isLoaded.current) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(ships));
    }
  }, [ships]);

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
        setShips(data);
        alert("데이터를 성공적으로 불러왔습니다.");
        setActiveTab('overview');
      } catch (e) {
        alert("유효하지 않은 파일입니다. JSON 형식을 확인해주세요.");
      }
    };
    reader.readAsText(file);
  };

  const totalShips = ships.length;
  const activeShips = ships.filter(s => s.status === 'Active').length;
  const allContracts = ships.flatMap(s => s.contracts);
  const avgDayrate = allContracts.filter(c => c.dayRate > 0).reduce((a, b, _, arr) => a + (b.dayRate / arr.length), 0);
  const warmStacked = ships.filter(s => s.status === 'Warm-Stacked').length;
  const coldStacked = ships.filter(s => s.status === 'Cold-Stacked').length;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans selection:bg-blue-500/30">
      <div className="fixed left-0 top-0 bottom-0 w-20 md:w-64 bg-slate-900 border-r border-slate-800 z-50 flex flex-col items-center md:items-stretch py-8 px-4">
        <div className="flex items-center gap-3 px-2 mb-12">
          <div className="bg-blue-600 p-2 rounded-xl shadow-lg shadow-blue-900/40">
            <Ship className="text-white w-6 h-6" />
          </div>
          <span className="hidden md:block font-black text-lg tracking-tighter">Drillship Status</span>
        </div>
        
        <nav className="flex-1 space-y-2">
          <button onClick={() => setActiveTab('overview')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'overview' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <LayoutGrid size={20} />
            <span className="hidden md:block font-bold">Dashboard</span>
          </button>
          <button onClick={() => setActiveTab('input')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === 'input' ? 'bg-blue-600 text-white shadow-lg' : 'text-slate-400 hover:bg-slate-800'}`}>
            <Database size={20} />
            <span className="hidden md:block font-bold">README</span>
          </button>
        </nav>

        <div className="pt-8 border-t border-slate-800 opacity-50">
          <div className="hidden md:block p-4 bg-slate-800/30 rounded-2xl">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1">Manual Mode</p>
            <p className="text-[10px] text-slate-400">Database is strictly controlled by JSON imports.</p>
          </div>
        </div>
      </div>

      <main className="flex-1 ml-20 md:ml-64 p-6 md:p-10">
        <header className="mb-10 flex flex-col md:flex-row md:items-end justify-between gap-6">
          <div>
            <h2 className="text-3xl font-black text-white tracking-tight">{activeTab === 'overview' ? 'Market Overview' : 'Database Management'}</h2>
            <p className="text-slate-500 mt-1 font-medium">Offshore Drillship Fleet Status & Pricing Intelligence</p>
          </div>
          {activeTab === 'overview' && (
            <div className="flex gap-3">
               <button onClick={exportData} className="p-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg text-slate-400 hover:text-white transition-all flex items-center gap-2 px-3">
                 <Download size={18} /> <span className="text-xs font-bold">Export JSON</span>
               </button>
               <label className="p-2 bg-blue-600 hover:bg-blue-500 border border-blue-500 rounded-lg text-white transition-all cursor-pointer flex items-center gap-2 px-3 shadow-lg shadow-blue-900/20">
                 <FileJson size={18} /> <span className="text-xs font-bold">Upload JSON</span>
                 <input type="file" onChange={importData} className="hidden" />
               </label>
            </div>
          )}
        </header>

        {activeTab === 'overview' ? (
          <div className="space-y-10 max-w-7xl">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
              {[
                { label: 'Total Fleet', value: totalShips, icon: Ship },
                { label: 'Utilization', value: `${totalShips > 0 ? Math.round((activeShips/totalShips)*100) : 0}%`, icon: BarChart3 },
                { label: 'Avg Dayrate', value: `$${Math.round(avgDayrate/1000)}k`, icon: TrendingUp },
                { label: 'Warm / Cold Stacked', value: `${warmStacked} / ${coldStacked}`, icon: AlertTriangle },
              ].map((kpi, idx) => (
                <div key={idx} className="bg-slate-900/50 p-6 rounded-3xl border border-slate-800 shadow-xl group hover:border-slate-700 transition-all">
                  <div className={`p-3 rounded-2xl bg-blue-500/10 text-blue-500 w-fit mb-4`}>
                    <kpi.icon size={24} />
                  </div>
                  <p className="text-slate-500 text-xs font-bold uppercase tracking-widest">{kpi.label}</p>
                  <p className="text-3xl font-black text-white mt-1">{kpi.value}</p>
                </div>
              ))}
            </div>

            {ships.length > 0 ? (
              <>
                <MarketChart ships={ships} />
                <FleetGantt ships={ships} />
              </>
            ) : (
              <div className="text-center py-24 bg-slate-900/30 rounded-3xl border border-dashed border-slate-800">
                <Database className="mx-auto text-slate-700 mb-4" size={48} />
                <h3 className="text-xl font-bold text-slate-400">Empty Database</h3>
                <p className="text-slate-600 mt-2 max-w-xs mx-auto">Upload a valid JSON file to visualize the offshore drilling fleet market.</p>
                <button onClick={() => setActiveTab('input')} className="mt-6 bg-blue-600 hover:bg-blue-500 text-white px-8 py-3 rounded-2xl font-bold transition-all">Go to Upload Guide</button>
              </div>
            )}
          </div>
        ) : (
          <div className="max-w-5xl grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-8">
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
                          <td className="p-3 font-medium">status (contract)</td>
                          <td className="p-3 text-slate-500">string</td>
                          <td className="p-3">Firm, Option</td>
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
                    JSON 파일은 Dashboard 탭에서 업로드/다운로드 가능합니다.
                  </li>
                  <li className="flex gap-2">
                    <span className="text-amber-500">•</span>
                    데이터는 로컬 브라우저에 저장되며, 다른 기기와 동기화되지 않습니다.
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
                onClick={() => { if(confirm("초기화하시겠습니까? 로컬 데이터가 모두 삭제됩니다.")) { localStorage.removeItem(STORAGE_KEY); setShips([]); } }} 
                className="w-full py-4 bg-slate-900 border border-slate-800 text-red-500/50 hover:text-red-500 hover:border-red-500/30 rounded-2xl text-xs font-bold uppercase tracking-widest flex items-center justify-center gap-2 transition-all"
              >
                <Trash2 size={16} /> Reset Local Database
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
