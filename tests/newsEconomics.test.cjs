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
const { newsEconomics, timelineContracts, economicsDetails } = require('../utils/newsEconomics.ts');
const { getComputedStatus, countFirmCoveredDays } = require('../utils/shipStatus.ts');
const { isStatisticalContract, isCurrentReportedRate, contractPeriod, contractSpan, reportedRate, interpretationDetails } = require('../utils/contractTimeline.ts');
const { dayrateObservations } = require('../utils/dayrateTrends.ts');
const { default: Gantt, ContractDetails } = require('../components/FleetGantt.tsx');

// New reader shape: no dateBasis/precision/rateBasis enums.
const interpretation = patch => ({ sourceStartText: 'fourth quarter of 2026', sourceEndText: null,
  placementBasis: '앞선 계약 종료일에 연결', awardType: 'signed-contract', termText: '880 days', optionText: 'up to 880 days',
  valueText: '$420 million', valueUsd: 420e6, baseDurationDays: 880, derivedUsdPerDay: 420e6 / 880,
  includes: ['동원 비용'], excludes: ['추가 서비스'], ...patch });
// Old published shape with legacy enum fields.
const legacyInterpretation = patch => interpretation({ startPrecision: 'quarter', endPrecision: 'approximate', dateBasis: 'placement',
  rateBasis: 'backlog-derived', reportedUsdPerDay: null, durationAssumption: '기본 880일', maximumTerm: false, ...patch });
const row = patch => ({ id: 'model-row', vesselId: 'ship', startDate: '2026-10-28', endDate: '2029-03-25',
  dayRate: 0, client: 'Client', region: 'Brazil', status: 'Firm', sourceUrl: 'https://example.com/award',
  interpretation: interpretation(), ...patch });
const ship = contracts => ({ id: 'ship', name: 'Ship', company: 'Seadrill', generation: '7G+', status: 'Idle', statusAsOf: '2026-08-01', contracts });
const event = decision => ({ company: 'Seadrill', vessel: 'Ship', classification: 'official-news-signal', autoApplied: true,
  url: 'https://example.com/award', readingDecision: decision });
const detail = selected => renderToStaticMarkup(React.createElement(ContractDetails, { selected, shipName: 'Ship', onClose() {} }));
const displayed = contract => timelineContracts(ship([contract]))[0];

test('bars come only from fleet contract rows; events never add or move a bar', () => {
  const canonical = row();
  const notice = event({ action: 'upsert', contractId: 'model-row', row: { ...canonical, startDate: '2027-01-01' } });
  const legacy = { ...event(), facts: { expectedStart: 'Q1 2027', announcedValueUsdApprox: 420e6, termText: '880 days' },
    evidence: [{ field: 'termText', quote: '880 days for $420 million' }] };
  assert.equal(timelineContracts.length, 1);
  const rows = timelineContracts(ship([canonical]));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].startDate, '2026-10-28');
  assert.deepEqual(timelineContracts(ship([])), []);
  const html = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([])], events: [notice, legacy] }));
  assert.doesNotMatch(html, /aria-pressed/);
  assert.equal(newsEconomics(legacy), undefined);
});

test('reported rate is dayRate > 0 regardless of interpretation fields', () => {
  const noBasis = row({ dayRate: 400000, interpretation: interpretation({ derivedUsdPerDay: null }) });
  assert.equal(reportedRate(noBasis), 400000);
  assert.equal(reportedRate(row({ dayRate: 400000, interpretation: legacyInterpretation({ rateBasis: 'undisclosed' }) })), 400000);
  assert.equal(reportedRate(row({ dayRate: 0 })), 0);
  assert.equal(reportedRate(row({ dayRate: Infinity })), 0);
  assert.equal(isCurrentReportedRate(noBasis, '2026-10-29'), true);
});

test('derived rate exists iff derivedUsdPerDay is a positive finite number', () => {
  assert.equal(displayed(row()).economics.usdPerDay, 420e6 / 880);
  assert.equal(displayed(row({ interpretation: interpretation({ rateBasis: undefined }) })).economics.usdPerDay, 420e6 / 880);
  assert.equal(displayed(row({ interpretation: legacyInterpretation({ rateBasis: 'undisclosed' }) })).economics.usdPerDay, 420e6 / 880);
  assert.equal(displayed(row({ interpretation: interpretation({ derivedUsdPerDay: 123456, valueUsd: null, baseDurationDays: null }) })).economics.usdPerDay, 123456);
  for (const derivedUsdPerDay of [null, undefined, 0, -1, Infinity, NaN, '477272']) {
    assert.equal(displayed(row({ interpretation: interpretation({ derivedUsdPerDay }) })).economics, undefined, String(derivedUsdPerDay));
  }
  assert.equal(displayed(row({ interpretation: undefined })).economics, undefined);
});

test('bar label shows reported rate first, derived rate otherwise, unchanged style', () => {
  const derived = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([row()])] }));
  assert.match(derived, /Client • \$477k/);
  assert.match(derived, /height:30px;top:4px/);
  assert.match(derived, /bg-blue-600 border-black\/20/);
  assert.doesNotMatch(derived, /추정|estimate/i);
  const reported = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([row({ dayRate: 400000 })])] }));
  assert.match(reported, /Client • \$400k/);
  assert.match(reported, /\$400,000 \/ 일 \(공시\)/);
  const none = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([row({ interpretation: interpretation({ derivedUsdPerDay: null }) })])] }));
  assert.match(none, /일당 미공개/);
});

test('placed/approximate dates count in utilization, status and bars like any contract', () => {
  const today = new Date('2026-10-29T00:00:00Z');
  for (const interp of [interpretation(), legacyInterpretation(), legacyInterpretation({ dateBasis: 'unresolved' })]) {
    const placed = row({ interpretation: interp });
    assert.equal(isStatisticalContract(placed), true);
    assert.equal(getComputedStatus(ship([placed]), today), 'Active');
    assert.equal(countFirmCoveredDays(ship([placed]), today, today), 1);
    assert.notEqual(contractSpan(placed, 2026, 2030), null);
  }
  for (const patch of [{ startDate: '2026-02-30' }, { endDate: 'tomorrow' }, { startDate: '2029-04-01' }]) {
    const invalid = row(patch);
    assert.equal(isStatisticalContract(invalid), false);
    assert.equal(dayrateObservations([ship([invalid])]).length, 0);
  }
  assert.equal(contractPeriod(row()), '2026-10-28 ~ 2029-03-25');
  assert.equal(contractPeriod({ startDate: '2026-10-01', endDate: '2029-03-31' }), '2026-10 ~ 2029-03');
});

test('reported and derived stay separate in Dayrates observations', () => {
  const [derived] = dayrateObservations([ship([row()])]);
  assert.equal(derived.basis, 'backlog');
  assert.equal(derived.rate, 420e6 / 880);
  const [reported] = dayrateObservations([ship([row({ dayRate: 400000 })])]);
  assert.equal(reported.basis, 'reported');
  assert.equal(reported.rate, 400000);
  for (const status of ['Option', 'Contingent']) assert.equal(dayrateObservations([ship([row({ status })])]).length, 0);
});

test('contract detail shows source wording, placement, terms, costs, formula and URL without undefined', () => {
  const html = detail(displayed(row()));
  for (const value of ['원문 시작: fourth quarter of 2026', '일정 배치: 앞선 계약 종료일에 연결', '880 days', 'up to 880 days', '$420 million',
    '포함: 동원 비용', '제외: 추가 서비스', '$420,000,000 ÷ 880일 = $477,273 / 일', 'href="https://example.com/award"']) assert(html.includes(value), value);
  assert.doesNotMatch(html, /undefined|null|\((quarter|approximate|day|month)\)|집계에서 제외|원문 종료/);
});

test('missing includes/excludes and sparse interpretations render without crashing', () => {
  const sparse = row({ interpretation: { derivedUsdPerDay: null } });
  const html = detail(displayed(sparse));
  assert.doesNotMatch(html, /undefined|포함:|제외:/);
  assert.equal(interpretationDetails(sparse), '');
  assert.equal(interpretationDetails({ sourceEndText: 'mid-2028' }), '원문 종료: mid-2028');
  const noLists = row({ interpretation: interpretation({ includes: undefined, excludes: undefined }) });
  assert.doesNotMatch(detail(displayed(noLists)), /undefined|포함:|제외:/);
});

test('legacy rows (old enum fields or no interpretation) render', () => {
  const legacy = row({ dayRate: 400000, interpretation: legacyInterpretation({ dateBasis: 'source', startPrecision: 'month', endPrecision: 'month', rateBasis: 'reported', reportedUsdPerDay: 400000, maximumTerm: true }) });
  const html = detail(displayed(legacy));
  assert.match(html, /\$400,000 \/ 일 \(공시\)/);
  assert.doesNotMatch(html, /undefined|\(month\)|집계에서 제외/);
  const plain = row({ interpretation: undefined, dayRate: 300000, datePrecision: { start: 'month', end: 'month' } });
  assert.match(detail(displayed(plain)), /\(월 단위\)/);
  assert.doesNotMatch(detail(displayed(plain)), /undefined/);
});

test('stored-result label shows original reading time only in the detail panel', () => {
  const stored = row({ inputMode: 'stored-result', storedReading: { reviewedAt: '2026-08-20T09:10:00Z' } });
  const gantt = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([stored])] }));
  assert.doesNotMatch(gantt, /저장된 읽기 결과로 보완/);
  const html = detail(displayed(stored));
  assert.match(html, /저장된 읽기 결과로 보완 · 원래 읽은 시각 2026-08-20T09:10:00Z/);
  assert.match(html, /원문 URL/);
  assert.match(detail(displayed(row({ inputMode: 'stored-result' }))), /원래 읽은 시각 확인되지 않음/);
  assert.doesNotMatch(detail(displayed(row())), /저장된 읽기 결과로 보완/);
});

test('economics details mark the derived value as not a disclosed dayrate', () => {
  assert.match(economicsDetails(displayed(row()).economics), /공시 dayrate 아님/);
  assert.equal(economicsDetails({ scope: 'award', usdPerDay: 123456 }), '$123,456 / 일 · 계약 가치의 일평균 환산액 · 공시 dayrate 아님');
});
