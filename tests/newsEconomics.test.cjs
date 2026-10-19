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
const { awardDuration, newsEconomics, timelineContracts, estimatedRateLabel, economicsDetails } = require('../utils/newsEconomics.ts');
const { default: Gantt, ContractDetails } = require('../components/FleetGantt.tsx');
const manifest = JSON.parse(fs.readFileSync('public/data/manifest.json', 'utf8'));
const events = JSON.parse(fs.readFileSync(`public/data/${manifest.eventsFile}`, 'utf8')).events;
const fleet = JSON.parse(fs.readFileSync(`public/data/${manifest.fleetFile}`, 'utf8'));
const eventFor = (vessel, url) => events.find(e => e.vessel === vessel && e.llmReview && (!url || e.url.includes(url)));
const shipFor = name => fleet.find(s => s.name === name);

test('real award values divided only by their own firm durations', () => {
  for (const [vessel, url, amount, days, label] of [
    ['Deepwater Conqueror', '30626', 225e6, 450, '추정 $500k'],
    ['Deepwater Conqueror', '30551', 80e6, 170, '추정 $471k'],
    ['Deepwater Proteus', '', 325e6, 700, '추정 $464k'],
    ['West Carina', '', 420e6, 880, '추정 $477k'],
    ['Dhirubhai Deepwater KG2', '', 300e6, 730, '추정 $411k'],
  ]) {
    const economics = newsEconomics(eventFor(vessel, url));
    assert.equal(economics.scope, 'award');
    assert.equal(economics.rates.length, 1);
    assert.equal(economics.rates[0].usdPerDay, amount / days);
    assert.equal(economics.rates[0].duration.days, days);
    assert.equal(estimatedRateLabel(economics, true), label);
  }
});

test('KG2 includes base only, not the two-year option, and states conversion and fees', () => {
  const event = eventFor('Dhirubhai Deepwater KG2');
  const economics = newsEconomics(event);
  assert.equal(economics.rates[0].duration.days, 730);
  assert.match(economicsDetails(economics), /300,000,000.*730일.*1년=365일 가정/);
  assert.match(economicsDetails(economics), /포함: 추가 서비스·동원 비용/);
  assert.match(economicsDetails(economics), /공시 dayrate 아님/);
  assert.equal(event.facts.dayRateUsd, undefined);
});

test('Capella extension tranches remain separate and never rate the whole legacy row', () => {
  const economics = newsEconomics(eventFor('West Capella'));
  assert.equal(economics.scope, 'extension');
  assert.deepEqual(economics.rates.map(r => [r.valueUsd, r.duration.days]), [[41e6, 120], [53e6, 120]]);
  assert.equal(estimatedRateLabel(economics, true), '연장 추정 $342k / $442k');
  assert.match(economicsDetails(economics), /기존 계약 전체 요율 아님/);
  const rows = timelineContracts(shipFor('West Capella'), events);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].dayRate, 0);
  assert.equal(rows[0].economics.rates.length, 2);
});

test('missing backlog, missing term, conflicting evidence, maximum-only duration fail closed', () => {
  assert.equal(newsEconomics(eventFor('Sonangol Libongos')), undefined);
  assert.equal(newsEconomics(eventFor('VALARIS DS-18')), undefined);
  const event = eventFor('West Carina');
  assert.equal(newsEconomics({ ...event, reviewStatus: 'conflict' }), undefined);
  assert.equal(newsEconomics({ ...event, llmReview: undefined }), undefined);
  assert.equal(newsEconomics({ ...event, url: 'javascript:alert(1)' }), undefined);
  assert.equal(newsEconomics({ ...event, evidence: [] }), undefined);
  assert.equal(newsEconomics({ ...event, evidence: [{field:'termText',quote:'up to 880 days'}, ...event.evidence.filter(e => e.field !== 'termText')] }), undefined);
  assert.equal(awardDuration({ ...event, evidence: [{field:'termText',quote:'120 days and 180 days'}] }), undefined);
});

test('forecast envelope preserves source uncertainty, not fabricated exact dates', () => {
  const row = timelineContracts(shipFor('Dhirubhai Deepwater KG2'), events).find(c => c.forecast);
  assert.equal(row.forecast.startLabel, '2027년 1분기');
  assert.equal(row.forecast.awardLabel, 'LOA');
  assert.match(row.forecast.windowLabel, /730|2년/);
  assert.match(row.forecast.windowLabel, /종료 예상 2028-12–2029-03/);
  assert.equal(row.dayRate, 0);
  assert.equal(row.status, 'Contingent');
  const html = renderToStaticMarkup(React.createElement(ContractDetails, {selected: row, shipName:'KG2', onClose:()=>{}}));
  assert.match(html, /추정 \$410,959 \/ 일/);
  assert.match(html, /전체 폭이 작업 기간은 아닙니다/);
  assert.match(html, /1년=365일 가정/);
  assert.doesNotMatch(html, /2027-01-01|2029-03-30/);
});

test('all six reviewed unscheduled awards get separate forecast lanes, no guessed DS18 rate', () => {
  const before = JSON.stringify({fleet, events});
  const rows = fleet.flatMap(s => timelineContracts(s, events));
  assert.equal(rows.filter(c => c.forecast).length, 6);
  const conqueror = timelineContracts(shipFor('Deepwater Conqueror'), events);
  assert.equal(conqueror.filter(c => c.forecast).length, 2);
  assert.equal(conqueror.find(c => !c.forecast).dayRate, 530000);
  const ds18 = timelineContracts(shipFor('VALARIS DS-18'), events).find(c => c.forecast);
  assert.equal(ds18.forecast.duration.maximum, true);
  assert.equal(ds18.forecast.duration.label, '최대 7개월');
  assert.match(ds18.forecast.windowLabel, /최대 기간 기준 종료/);
  assert.equal(ds18.economics, undefined);
  assert.equal(JSON.stringify({fleet, events}), before);
});

test('forecast rendering exposes estimates without hover and distinguishes exact reported rates', () => {
  const html = renderToStaticMarkup(React.createElement(Gantt, { ships: fleet, events }));
  assert.match(html, /LOA · 2027년 1분기 · 2년.*추정 \$411k/);
  assert.match(html, /수주 · 2027년 상반기 · 880일.*추정 \$477k/);
  assert.match(html, /연장 추정 \$342k \/ \$442k/);
  assert.match(html, /Deepwater Conqueror · Firm.*\$530,000 \/ 일 \(공시\)/);
  assert.match(html, /예상 범위·추정 요율은 가동률·평균 dayrate에서 제외/);
  assert.doesNotMatch(html, /Codex 계약 대조/);
});

test('covered/superseded jobs and repeated events cannot duplicate or revive forecasts', () => {
  const ship = shipFor('West Carina');
  const event = eventFor(ship.name);
  assert.equal(timelineContracts(ship, [event, event]).filter(c => c.forecast).length, 1);
  for (const status of ['covered-by-report', 'superseded', 'conflict', 'pending', 'not-contract-news']) {
    assert.equal(timelineContracts(ship, [{...event, reviewStatus:status}]).filter(c => c.forecast).length, 0);
  }
  const other = { ...event, vessel: 'Different Vessel' };
  assert.equal(timelineContracts(ship, [other]).filter(c => c.forecast).length, 0);
});
