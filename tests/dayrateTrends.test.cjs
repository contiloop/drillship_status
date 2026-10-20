const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const compile = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
}).outputText, filename);
require.extensions['.tsx'] = compile;
require.extensions['.ts'] = compile;
const { DAYRATE_START_YEAR, dayrateObservations, dayrateSeries, dayrateTrend, durationRadius, dayrateDomain, GENERATIONS } = require('../utils/dayrateTrends.ts');
const { default: Dayrates } = require('../components/DayrateTrends.tsx');
const manifest = JSON.parse(fs.readFileSync('public/data/manifest.json', 'utf8'));
const events = JSON.parse(fs.readFileSync(`public/data/${manifest.eventsFile}`, 'utf8')).events;
const fleet = JSON.parse(fs.readFileSync(`public/data/${manifest.fleetFile}`, 'utf8'));
const observations = dayrateObservations(fleet, events, manifest.sources);
const DAY = 86400000;
const sample = (n, rate = i => 250000 + i * 10000) => Array.from({ length: n }, (_, i) => ({
  id: String(i), vessel: `Vessel ${i}`, company: 'Transocean', generation: '7G', client: 'Client',
  time: Date.UTC(2021, 0, 1) + i * 90 * DAY, start: '2021-01-01', end: '2021-03-31', days: 90,
  rate: rate(i), basis: 'reported', detail: 'test',
}));

test('real data contains only supported firm rate-periods by exact generation', () => {
  assert.deepEqual(GENERATIONS.map(gen => observations.filter(p => p.generation === gen && p.basis === 'reported').length), [13, 4, 8, 6]);
  assert.deepEqual(GENERATIONS.map(gen => observations.filter(p => p.generation === gen && p.basis === 'backlog').length), [0, 1, 3, 0]);
  assert.equal(observations.length, 35);
  assert(observations.every(p => p.rate > 0 && p.days > 0 && p.sourceUrl.startsWith('https://')));
  assert(!observations.some(p => p.vessel === 'Dhirubhai Deepwater KG2' && p.basis === 'backlog'));
  assert(!observations.some(p => p.vessel === 'West Capella' && p.basis === 'backlog'));
  assert(!observations.some(p => p.vessel === 'VALARIS DS-18' && p.basis === 'backlog'));
});

test('backlog rates retain amount/term/date provenance and do not overwrite public dayrates', () => {
  const before = JSON.stringify({ fleet, events });
  const carina = observations.find(p => p.vessel === 'West Carina' && p.basis === 'backlog');
  assert.equal(carina.rate, 420e6 / 880);
  assert.equal(carina.days, 880);
  assert.equal(carina.start, '2027-01-01');
  assert.match(carina.detail, /420,000,000.*880일/);
  assert.match(carina.detail, /정확한 일자 미공개/);
  assert.equal(observations.find(p => p.vessel === 'Deepwater Conqueror' && p.basis === 'reported').rate, 530000);
  assert.deepEqual(dayrateObservations(fleet, [...events].reverse(), manifest.sources), observations);
  assert.equal(JSON.stringify({ fleet, events }), before);
});

test('unknown/invalid rates, options, contingent periods, invalid dates, and unsafe links are excluded', () => {
  const base = fleet.find(s => s.contracts.length);
  const contract = { id: 'sample', client: 'Test', startDate: '2026-01-01', endDate: '2026-12-31', dayRate: 400000, status: 'Firm' };
  const ship = contracts => ({ ...base, contracts });
  assert.equal(dayrateObservations([ship([contract])], []).length, 1);
  for (const patch of [{ dayRate: 0 }, { dayRate: -1 }, { dayRate: Infinity }, { status: 'Option' }, { status: 'Contingent' }, { startDate: 'unknown' }, { endDate: '2025-12-31' }]) {
    assert.equal(dayrateObservations([ship([{ ...contract, ...patch }])], []).length, 0);
  }
  assert.equal(dayrateObservations([ship([{ ...contract, sourceUrl: 'javascript:alert(1)' }])], [])[0].sourceUrl, undefined);
});

test('all real generation curves are finite and stay within the observed time range', () => {
  for (const gen of GENERATIONS) {
    const rows = observations.filter(p => p.generation === gen);
    const trend = dayrateTrend(rows);
    assert(trend.points.length >= 4, gen);
    assert(trend.bandwidth >= 0.3 && trend.bandwidth <= 1);
    for (const p of trend.points) {
      assert(Number.isFinite(p.time + p.rate + p.band[0] + p.band[1]), gen);
      assert(p.time >= rows[0].time && p.time <= rows.at(-1).time);
      assert(p.band[0] <= p.rate && p.band[1] >= p.rate);
    }
  }
});

test('7G+ starts at 2025 and fits only visible contracts, without changing other generations or source data', () => {
  const before = JSON.stringify(observations);
  const start = Date.UTC(2025, 0, 1);
  const rows = dayrateSeries(observations, '7G+');
  assert.equal(rows.length, 9);
  assert(rows.every(p => p.generation === '7G+' && p.time >= start));
  assert(!rows.some(p => ['Deepwater Pontus', 'Deepwater Poseidon'].includes(p.vessel)));
  assert.equal(dayrateSeries(observations, '7G+', false).length, 6);
  const trend = dayrateTrend(rows);
  assert(trend.points.length >= 4);
  assert(trend.points.every(p => p.time >= start));
  const domain = dayrateDomain(rows, trend.points, DAYRATE_START_YEAR['7G+']);
  assert.equal(domain.x[0], start);
  assert.equal(domain.years[0], start);
  assert.equal(dayrateDomain([], [], 2025).x[0], start);
  for (const gen of ['6G', '7G', '8G']) {
    assert.deepEqual(dayrateSeries(observations, gen), observations.filter(p => p.generation === gen));
    assert.equal(DAYRATE_START_YEAR[gen], undefined);
  }
  const boundary = sample(2).map((p, i) => ({ ...p, generation: '7G+', time: start - (i ? 0 : 1) }));
  assert.deepEqual(dayrateSeries(boundary, '7G+').map(p => p.id), ['1']);
  assert.equal(JSON.stringify(observations), before);
});

test('insufficient or identical-date samples cannot produce an invented trend', () => {
  for (let n = 0; n < 5; n++) {
    assert.deepEqual(dayrateTrend(sample(n)).points, []);
    assert.match(dayrateTrend(sample(n)).reason, /표본이 부족/);
  }
  assert.equal(dayrateTrend(sample(20).map(p => ({ ...p, time: 1 }))).points.length, 0);
  assert.equal(dayrateTrend(observations.filter(p => p.generation === '7G' && p.basis === 'reported')).points.length, 0);
});

test('linear and constant samples fit without a decorative fixed-width band', () => {
  for (const rows of [sample(20), sample(20, () => 400000)]) {
    const trend = dayrateTrend(rows);
    for (const p of trend.points) {
      const original = rows.find(r => r.time === p.time);
      assert(Math.abs(p.rate - original.rate) < 0.001);
      assert(p.band[1] - p.band[0] < 0.001);
    }
  }
});

test('dispersion band equals the local residual sample SD, not SEM or confidence interval', () => {
  const rows = sample(20, i => 350000 + 1500 * i + [20000, -13000, 9000, -17000][i % 4]);
  const trend = dayrateTrend(rows);
  const p = trend.points[8];
  const nearest = [...rows].sort((a, b) => Math.abs(a.time - p.time) - Math.abs(b.time - p.time)).slice(0, 6);
  const residuals = nearest.map(r => r.rate - trend.points.find(t => t.time === r.time).rate);
  const mean = residuals.reduce((s, r) => s + r, 0) / residuals.length;
  const sd = Math.sqrt(residuals.reduce((s, r) => s + (r - mean) ** 2, 0) / (residuals.length - 1));
  assert(sd > 1000);
  assert(Math.abs((p.band[1] - p.rate) - sd) < 0.001);
  assert.deepEqual(dayrateTrend([...rows].reverse()), trend);
});

test('marker area scales with duration and caps at 4 years', () => {
  const area = days => 2 * durationRadius(days) ** 2;
  assert(Math.abs(area(730) / area(90) - 730 / 90) < 1e-12);
  assert.equal(durationRadius(2920), durationRadius(1460));
});

test('axis domains contain all real marks and band values with valid one-point and empty states', () => {
  for (const gen of GENERATIONS) {
    const rows = observations.filter(p => p.generation === gen);
    const trend = dayrateTrend(rows);
    const domain = dayrateDomain(rows, trend.points);
    for (const p of rows) assert(p.time >= domain.x[0] && p.time <= domain.x[1] && p.rate >= domain.y[0] && p.rate <= domain.y[1]);
    for (const p of trend.points) assert(p.band[0] >= domain.y[0] && p.band[1] <= domain.y[1]);
  }
  const domain = dayrateDomain(sample(1), []);
  assert(domain.x[1] > domain.x[0] && domain.y[1] > domain.y[0]);
  assert.deepEqual(dayrateDomain([], []).years, []);
});

test('chart exposes generation controls, sample count, duration legend, and source limitations', () => {
  const html = renderToStaticMarkup(React.createElement(Dayrates, { ships: fleet, events, sources: manifest.sources }));
  assert.match(html, /7G Drillship Dayrates/);
  assert.match(html, /n = 5 contract-rate periods/);
  assert.match(html, /Contract term/);
  assert.match(html, /LOESS trend/);
  assert.match(html, /±1 SD dispersion band/);
  assert.match(html, /not a confidence interval/);
  assert.match(html, /수주 발표일 아님/);
  assert.match(html, /모든 수주를 수집한 시장 시계열은 아닙니다/);
  assert.match(html, /7G\+<\/button>/);
  assert.doesNotMatch(html, /Deepwater Insight|n = 99/);
  const empty = renderToStaticMarkup(React.createElement(Dayrates, { ships: [], events: [], sources: [] }));
  assert.match(empty, /공개된 요율 데이터가 없습니다/);
  assert.doesNotMatch(empty, /LOESS trend/);
});
