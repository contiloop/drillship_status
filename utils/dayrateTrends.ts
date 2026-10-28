import { regressionLoess } from 'd3-regression';
import type { Company, Drillship, FleetSource, Generation, OfficialEvent } from '../types';
import { contractSource, isIsoDate, interpretationDetails, reportedRate } from './contractTimeline';
import { economicsDetails, timelineContracts } from './newsEconomics';

const DAY = 86_400_000;
export const GENERATIONS: Generation[] = ['6G', '7G', '7G+', '8G'];
export const DAYRATE_START_YEAR: Partial<Record<Generation, number>> = { '7G+': 2025 };

export interface DayrateObservation {
  id: string;
  vessel: string;
  company: Company;
  generation: Generation;
  client: string;
  time: number;
  start: string;
  end: string;
  days: number;
  rate: number;
  basis: 'reported' | 'backlog';
  sourceUrl?: string;
  detail: string;
}

/** Only supported firm rate-periods, not an invented historical award database. */
export function dayrateObservations(ships: Drillship[], events: OfficialEvent[], sources: FleetSource[] = []): DayrateObservation[] {
  const observations: DayrateObservation[] = [];
  for (const ship of ships) {
    for (const row of timelineContracts(ship, events)) {
      if (row.status !== 'Firm' || row.interpretation?.dateBasis === 'unresolved' || !isIsoDate(row.startDate) || !isIsoDate(row.endDate)) continue;
      const time = Date.parse(`${row.startDate}T00:00:00Z`);
      const end = Date.parse(`${row.endDate}T00:00:00Z`);
      if (!Number.isFinite(time + end) || end < time) continue;
      const reported = reportedRate(row) > 0;
      // Each canonical model row represents its own value/term tranche.
      const conversion = row.economics?.rates.length === 1 ? row.economics.rates[0] : undefined;
      if (!reported && !conversion) continue;
      const rate = reported ? row.dayRate : conversion!.usdPerDay;
      if (!Number.isFinite(rate) || rate <= 0) continue;
      observations.push({
        id: row.id, vessel: ship.name, company: ship.company, generation: ship.generation, client: row.client,
        time, start: row.startDate, end: row.endDate, days: (end - time) / DAY + 1, rate,
        basis: reported ? 'reported' : 'backlog',
        sourceUrl: contractSource(row.sourceUrl) || contractSource(sources.find(s => s.company === ship.company)?.documentUrl),
        detail: [reported ? '공식 Fleet Status Report에 공개된 일당' : economicsDetails(row.economics!),
          row.interpretation ? interpretationDetails(row) : '월 단위 계약 구간은 월초·월말로 배치'].join(' · '),
      });
    }
  }
  return observations.sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

export interface TrendPoint { time: number; rate: number; band: [number, number] }
export interface DayrateTrend { points: TrendPoint[]; bandwidth: number; reason?: string }

/** Apply the display window before fitting so hidden history cannot influence the curve. */
export function dayrateSeries(observations: DayrateObservation[], generation: Generation, includeBacklog = true) {
  const fromYear = DAYRATE_START_YEAR[generation];
  const start = fromYear === undefined ? -Infinity : Date.UTC(fromYear, 0, 1);
  return observations.filter(p => p.generation === generation && p.time >= start && (includeBacklog || p.basis === 'reported'));
}

/** Robust LOESS via d3-regression. Band = local sample SD of residuals, NOT a confidence interval. */
export function dayrateTrend(observations: DayrateObservation[]): DayrateTrend {
  const rows = observations.filter(p => Number.isFinite(p.time + p.rate) && p.rate > 0).sort((a, b) => a.time - b.time);
  const bandwidth = Math.max(0.3, Math.min(1, 4 / rows.length));
  if (rows.length < 5 || new Set(rows.map(p => p.time)).size < 4) {
    return { points: [], bandwidth, reason: '표본이 부족해 추세선과 음영 밴드는 표시하지 않습니다.' };
  }
  const fit = regressionLoess<DayrateObservation>().x(p => p.time / DAY).y(p => p.rate).bandwidth(bandwidth)(rows);
  if (fit.some(p => !Number.isFinite(p[0] + p[1]))) return { points: [], bandwidth, reason: '유효한 추세선을 계산할 수 없습니다.' };
  const residuals = rows.map(p => ({ time: p.time, value: p.rate - fit.predict(p.time / DAY) }));
  const count = Math.max(4, Math.ceil(rows.length * bandwidth));
  const points = fit.map(([day, rate]) => {
    const time = day * DAY;
    const local = [...residuals].sort((a, b) => Math.abs(a.time - time) - Math.abs(b.time - time)).slice(0, count);
    const mean = local.reduce((sum, p) => sum + p.value, 0) / local.length;
    const sd = Math.sqrt(local.reduce((sum, p) => sum + (p.value - mean) ** 2, 0) / (local.length - 1));
    return { time, rate, band: [Math.max(0, rate - sd), rate + sd] as [number, number] };
  });
  return { points, bandwidth };
}

/** Diamond area, rather than radius, scales with duration; cap at the 4y+ legend mark. */
export const durationRadius = (days: number) => 4 * Math.sqrt(Math.max(1, Math.min(1460, days)) / 90);

export function dayrateDomain(rows: DayrateObservation[], trend: TrendPoint[], fromYear?: number) {
  if (!rows.length) return { x: [Date.UTC(fromYear ?? 2021, 0, 1), Date.UTC(fromYear === undefined ? 2027 : fromYear + 1, 0, 1)] as [number, number], y: [200_000, 550_000] as [number, number], years: [] as number[], ticks: [] as number[] };
  const first = fromYear ?? new Date(Math.min(...rows.map(p => p.time))).getUTCFullYear();
  const last = Math.max(first, new Date(Math.max(...rows.map(p => p.time))).getUTCFullYear());
  const low = Math.max(0, Math.floor((Math.min(...rows.map(p => p.rate), ...trend.map(p => p.band[0])) - 20_000) / 50_000) * 50_000);
  const high = Math.ceil((Math.max(...rows.map(p => p.rate), ...trend.map(p => p.band[1])) + 20_000) / 50_000) * 50_000;
  const years = Array.from({ length: last - first + 1 }, (_, i) => Date.UTC(first + i, 0, 1));
  const ticks = Array.from({ length: Math.round((high - low) / 50_000) + 1 }, (_, i) => low + i * 50_000);
  return { x: [Date.UTC(first, 0, 1) - (fromYear === undefined ? 60 * DAY : 0), Date.UTC(last + 1, 0, 1)] as [number, number], y: [low, high] as [number, number], years, ticks };
}
