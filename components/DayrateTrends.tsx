import React, { useMemo, useState } from 'react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Scatter, XAxis, YAxis } from 'recharts';
import type { Drillship, FleetSource, Generation, OfficialEvent } from '../types';
import { DAYRATE_START_YEAR, dayrateDomain, dayrateObservations, dayrateSeries, dayrateTrend, durationRadius, GENERATIONS, type DayrateObservation } from '../utils/dayrateTrends';
import { contractSource } from '../utils/contractTimeline';

interface Props { ships: Drillship[]; events: OfficialEvent[]; sources: FleetSource[] }
const money = (rate: number) => `$${Math.round(rate).toLocaleString('en-US')}`;
const shortName = (name: string) => name.replace(/^(Deepwater|Dhirubhai Deepwater|VALARIS|Noble) /, '');
const ticker = { Transocean: 'RIG', Valaris: 'VAL', Noble: 'NE', Seadrill: 'SDRL' };
const tokens = { point: '#579bd6', line: '#8bcaff', band: '#4b8cc3', grid: '#242424', muted: '#a0a0a0' };
const setting = (key: string) => typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get(key);
function saveSetting(key: string, value: string) {
  const url = new URL(window.location.href);
  url.searchParams.set(key, value);
  window.history.replaceState(null, '', url);
}

export default function DayrateTrends({ ships, events, sources }: Props) {
  const [generation, setGeneration] = useState<Generation>(() => GENERATIONS.find(gen => gen === setting('dayrateGen')) || '7G');
  const [includeBacklog, setIncludeBacklog] = useState(() => setting('dayrateBasis') !== 'reported');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chartSize, setChartSize] = useState({ width: 0, height: 510 });
  const narrow = chartSize.width > 0 && chartSize.width < 600;
  const all = useMemo(() => dayrateObservations(ships, events, sources), [ships, events, sources]);
  const rows = useMemo(() => dayrateSeries(all, generation, includeBacklog), [all, generation, includeBacklog]);
  const trend = useMemo(() => dayrateTrend(rows), [rows]);
  const fromYear = DAYRATE_START_YEAR[generation];
  const domain = dayrateDomain(rows, trend.points, fromYear);
  const selected = rows.find(p => p.id === selectedId);
  const latest = [...rows].reverse().filter((p, index, list) => list.findIndex(other => other.vessel === p.vessel) === index).slice(0, narrow ? 1 : 3);
  const labels: { id: string; y: number; left: number; right: number }[] = [];
  const rightAligned = (p: DayrateObservation) => p.time > domain.x[1] - 230 * 86400000;
  for (const p of latest) {
    const x = (narrow ? 48 : 64) + (p.time - domain.x[0]) / (domain.x[1] - domain.x[0]) * (chartSize.width - (narrow ? 60 : 92));
    const width = Math.max(shortName(p.vessel).length, ticker[p.company].length + 2) * (narrow ? 10 : 12) * 0.65;
    const left = x - (rightAligned(p) ? width : width / 2);
    const right = left + width;
    let y = 52 + (domain.y[1] - p.rate) / (domain.y[1] - domain.y[0]) * (chartSize.height - 94) - durationRadius(p.days) - 15;
    const overlaps = (label: typeof labels[number]) => left < label.right + 8 && right > label.left - 8 && Math.abs(y - label.y) < 38;
    for (let attempt = 0; attempt < latest.length; attempt++) {
      const collision = labels.find(overlaps);
      if (!collision) break;
      y = collision.y - 38;
    }
    if (y >= 16) labels.push({ id: p.id, y, left, right });
  }

  const diamond = (props: unknown) => {
    const { cx, cy, payload } = props as { cx: number; cy: number; payload: DayrateObservation };
    if (!payload || !Number.isFinite(cx + cy)) return <g />;
    const radius = durationRadius(payload.days);
    const annotation = labels.find(label => label.id === payload.id);
    const active = selectedId === payload.id;
    return <g><g className="dayrate-mark" role="button" tabIndex={0} aria-pressed={active}
      aria-label={`${payload.vessel}, ${payload.start}, ${money(payload.rate)} / day, ${payload.days} days, ${payload.basis === 'reported' ? '공시 일당' : '백로그 환산'}`}
      onClick={() => setSelectedId(payload.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedId(payload.id); } }}>
      <title>{`${payload.vessel} · ${payload.start} – ${payload.end} · ${money(payload.rate)} / day`}</title>
      <circle cx={cx} cy={cy} r={Math.max(13, radius + 3)} fill="transparent" />
      <path className="dayrate-diamond" d={`M${cx},${cy - radius}L${cx + radius},${cy}L${cx},${cy + radius}L${cx - radius},${cy}Z`}
        fill={payload.basis === 'backlog' ? '#000' : tokens.point} fillOpacity={active ? 1 : 0.8}
        stroke={active ? '#fff' : tokens.point} strokeWidth={payload.basis === 'backlog' ? 2 : active ? 2 : 0} />
      </g>
      {annotation && <text x={cx} y={annotation.y} textAnchor={rightAligned(payload) ? 'end' : 'middle'} fill="#f5f5f5" fontSize={narrow ? 10 : 12} fontWeight={700} stroke="#000" strokeWidth={4} paintOrder="stroke" pointerEvents="none">
        <tspan x={cx}>{shortName(payload.vessel)}</tspan><tspan x={cx} dy={15}>({ticker[payload.company]})</tspan>
      </text>}
    </g>;
  };

  return <section aria-label="세대별 Dayrate 추이" className="dayrate-panel rounded-2xl border border-slate-800 bg-black p-4 sm:p-7 lg:p-10" onKeyDown={event => { if (event.key === 'Escape') setSelectedId(null); }}>
    <div className="mb-7 flex flex-wrap items-center justify-between gap-3">
      <div className="flex gap-1" aria-label="선박 세대">
        {GENERATIONS.map(gen => <button key={gen} aria-pressed={generation === gen} onClick={() => { setGeneration(gen); setSelectedId(null); saveSetting('dayrateGen', gen); }} className={`min-h-10 min-w-12 rounded-lg px-3 py-2 text-sm font-bold transition-colors ${generation === gen ? 'bg-[#579bd6] text-black' : 'text-neutral-400 hover:bg-neutral-900 hover:text-white'}`}>{gen}</button>)}
      </div>
      <label className="flex min-h-10 cursor-pointer items-center gap-2 text-xs text-neutral-400"><input type="checkbox" checked={includeBacklog} onChange={event => { setIncludeBacklog(event.target.checked); setSelectedId(null); saveSetting('dayrateBasis', event.target.checked ? 'all' : 'reported'); }} className="h-4 w-4 accent-[#579bd6]" />백로그 환산 포함</label>
    </div>
    <h3 className="text-2xl font-bold tracking-tight text-neutral-100 sm:text-4xl lg:text-[42px]">{generation} Drillship Dayrates</h3>
    <p className="mt-2 text-xs text-neutral-400 sm:text-base">Contract start dates{fromYear ? ` · ${fromYear} onwards` : ''} · Published dayrates{includeBacklog ? ' & backlog-derived values' : ''}</p>
    <figure className="mt-6" aria-label={`${generation} 계약 구간 ${rows.length}건의 요율 산점도`}>
      <p className="mb-2 text-xs font-semibold text-neutral-200">Dayrate (US$ thousands per day)</p>
      {rows.length ? <div className="h-[340px] min-w-0 sm:h-[430px] lg:h-[510px]">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} onResize={(width, height) => setChartSize(previous => previous.width === width && previous.height === height ? previous : { width, height })}>
          <ComposedChart data={trend.points} margin={{ top: 52, right: narrow ? 12 : 28, bottom: 12, left: 0 }} accessibilityLayer={false}>
            <CartesianGrid vertical={false} stroke={tokens.grid} strokeWidth={1} />
            <XAxis type="number" dataKey="time" domain={domain.x} ticks={domain.years.filter((_, i) => !narrow || i % Math.max(1, Math.ceil(domain.years.length / 4)) === 0)} scale="time" tickFormatter={time => String(new Date(time).getUTCFullYear())} tick={{ fill: tokens.muted, fontSize: narrow ? 10 : 13 }} stroke="#494949" tickLine={{ stroke: '#494949' }} tickMargin={12} />
            <YAxis type="number" dataKey="rate" domain={domain.y} ticks={domain.ticks.filter((_, i) => !narrow || i % 2 === 0)} tickFormatter={rate => `$${Math.round(rate / 1000)}K`} tick={{ fill: tokens.muted, fontSize: narrow ? 10 : 13 }} width={narrow ? 48 : 64} axisLine={false} tickLine={false} tickMargin={10} />
            <Area type="linear" dataKey="band" stroke="none" fill={tokens.band} fillOpacity={0.16} isAnimationActive={false} legendType="none" />
            <Line type="linear" dataKey="rate" stroke={tokens.line} strokeWidth={3} dot={false} activeDot={false} isAnimationActive={false} legendType="none" />
            <Scatter data={rows} shape={diamond} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div> : <div className="flex h-[340px] items-center justify-center text-sm text-neutral-400">이 세대에 공개된 요율 데이터가 없습니다.</div>}
      <figcaption className="mt-6 space-y-4">
        <div className="flex flex-wrap items-center gap-x-7 gap-y-3 text-xs text-neutral-300 sm:text-sm">
          <span className="flex items-center gap-2"><svg width="16" height="18" aria-hidden="true"><path d="M8,1L15,9L8,17L1,9Z" fill={tokens.point} /></svg>{generation} drillship contract</span>
          {includeBacklog && <span className="flex items-center gap-2"><svg width="16" height="18" aria-hidden="true"><path d="M8,1L15,9L8,17L1,9Z" fill="none" stroke={tokens.point} strokeWidth="2" /></svg>Backlog-derived</span>}
          {!!trend.points.length && <><span className="flex items-center gap-2"><i className="inline-block w-7 border-t-[3px] border-[#8bcaff]" />LOESS trend</span><span className="flex items-center gap-2"><i className="inline-block h-3 w-6 bg-[#4b8cc3]/20" />±1 SD dispersion band</span></>}
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-neutral-300 sm:text-sm"><span className="text-neutral-400">Contract term</span>{[[90, '90d'], [365, '1yr'], [730, '2yr'], [1460, '4yr+']].map(([days, label]) => {
          const r = durationRadius(Number(days));
          return <span key={label} className="flex items-center gap-2"><svg width={r * 2 + 3} height="36" aria-hidden="true"><path d={`M${r + 1},${18 - r}L${2 * r + 1},18L${r + 1},${18 + r}L1,18Z`} fill="#777" stroke="#aaa" /></svg>{label}</span>;
        })}</div>
        <p className="text-[11px] leading-relaxed text-neutral-500">n = {rows.length} contract-rate periods · Marker area scales with term (capped at 4 years){trend.points.length ? ` · LOESS span ${trend.bandwidth.toFixed(2)} · ±1 local residual SD, not a confidence interval` : ''}</p>
        {trend.reason && <p className="text-xs text-neutral-400">{trend.reason}</p>}
      </figcaption>
    </figure>
    {selected && <div role="status" className="mt-5 rounded-lg border border-neutral-700 bg-neutral-950 p-4 text-xs text-neutral-300">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2"><strong className="text-sm text-white">{selected.vessel}</strong><span>{selected.client}</span><span>{selected.start} – {selected.end} · {selected.days.toLocaleString()}일</span><strong className="text-[#8bcaff]">{money(selected.rate)} / day</strong><span>{selected.basis === 'reported' ? '공시 일당' : '백로그 환산'}</span>{selected.sourceUrl && <a href={selected.sourceUrl} target="_blank" rel="noopener noreferrer" className="text-[#8bcaff] underline">원문 ↗</a>}<button onClick={() => setSelectedId(null)} className="ml-auto min-h-8 px-2" aria-label="Dayrate 상세 닫기">닫기</button></div>
      <p className="mt-3 text-neutral-500">{selected.detail}</p>
    </div>}
    <details className="mt-5 text-xs text-neutral-500">
      <summary className="cursor-pointer py-2">Source & methodology</summary>
      <div className="mt-2 space-y-2 leading-relaxed">
        <p>Source: 회사별 공식 Fleet Status Reports 및 검토 완료된 공식 계약 발표. 현재 보유한 계약 구간의 요율이며, 2021년 이후 모든 수주를 수집한 시장 시계열은 아닙니다. 가로축은 계약 시작일(수주 발표일 아님), 미래 개시 계약도 포함합니다. 장기 계약의 일당은 최신 보고서 값이므로 최초 개시 당시의 일당을 뜻하지 않습니다.</p>
        <p>공시 일당은 추가 서비스 제외 여부를 일괄 보정하지 않았습니다. 빈 다이아몬드는 계약 가치÷기본 기간이며 서비스·동원 비용이 포함될 수 있습니다. 점을 선택하면 계산 근거를 확인할 수 있습니다. 옵션·조건부 계약, 미공개 요율, 개별 날짜가 없는 연장분은 제외합니다.</p>
        <p>LOESS는 각 계약 구간을 동일 가중치로 취급합니다. 표본 5건·서로 다른 시작일 4개 미만이면 추세선을 숨깁니다. 소표본에서는 최소 4개 이웃을 확보하도록 span을 늘립니다. 음영은 인접 구간 잔차의 표본 표준편차이며 예측 범위나 신뢰구간이 아닙니다.</p>
        <div className="flex flex-wrap gap-3">{sources.filter(source => contractSource(source.documentUrl)).map(source => <a key={source.company} href={source.documentUrl} target="_blank" rel="noopener noreferrer" className="underline">{source.company} ({source.reportDate}) ↗</a>)}</div>
      </div>
    </details>
    <style>{`.dayrate-mark { cursor: pointer; outline: none; } .dayrate-mark:focus-visible .dayrate-diamond { stroke: white; stroke-width: 3px; }`}</style>
  </section>;
}
