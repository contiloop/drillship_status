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
const { isStatisticalContract, isCurrentReportedRate, contractPeriod } = require('../utils/contractTimeline.ts');
const { dayrateObservations, dayrateSeries } = require('../utils/dayrateTrends.ts');
const { default: Gantt, ContractDetails } = require('../components/FleetGantt.tsx');
const { announcementDetails } = require('../components/OfficialAnnouncementCard.tsx');
const interpretation = patch => ({ sourceStartText: 'fourth quarter of 2026', sourceEndText: null,
  startPrecision: 'quarter', endPrecision: 'approximate', dateBasis: 'placement', placementBasis: '모델이 앞선 계약 종료일에 연결',
  awardType: 'signed-contract', termText: '880 days', optionText: 'up to 880 days', valueText: '$420 million',
  rateBasis: 'backlog-derived', reportedUsdPerDay: null, derivedUsdPerDay: 420e6 / 880,
  valueUsd: 420e6, baseDurationDays: 880, durationAssumption: '기본 880일', maximumTerm: false,
  includes: ['동원 비용'], excludes: ['추가 서비스'], ...patch });
const row = patch => ({ id: 'model-row', vesselId: 'ship', startDate: '2026-10-28', endDate: '2029-03-25',
  dayRate: 0, client: 'Client', region: 'Brazil', status: 'Firm', sourceUrl: 'https://example.com/award',
  interpretation: interpretation(), ...patch });
const ship = contracts => ({ id: 'ship', name: 'Ship', company: 'Seadrill', generation: '7G+', status: 'Idle', statusAsOf: '2026-08-01', contracts });
const event = decision => ({ company: 'Seadrill', vessel: 'Ship', classification: 'official-news-signal', autoApplied: true,
  url: 'https://example.com/award', readingDecision: decision });

test('canonical model dates and one bar survive duplicate news without frontend placement', () => {
  const canonical = row();
  const notice = event({ action: 'upsert', row: canonical, facts: { interpretation: canonical.interpretation } });
  const before = JSON.stringify({ canonical, notice });
  const rows = timelineContracts(ship([canonical]), [notice, notice]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].startDate, '2026-10-28');
  assert.equal(rows[0].endDate, '2029-03-25');
  assert.equal(rows[0].economics.rates[0].usdPerDay, 420e6 / 880);
  assert.equal(JSON.stringify({ canonical, notice }), before);
  assert.equal(timelineContracts(ship([]), [notice]).length, 0);
});

test('legacy text/evidence never creates a period or a converted rate', () => {
  const legacy = { ...event(), autoApplied: false, reviewStatus: 'reviewed', llmReview: { model: 'stored' },
    facts: { expectedStart: 'Q1 2027', awardType: 'signed-contract', announcedValueUsdApprox: 420e6, termText: '880 days' },
    evidence: [{ field: 'termText', quote: '880 days for $420 million, direct continuation' }] };
  assert.equal(newsEconomics(legacy), undefined);
  assert.deepEqual(timelineContracts(ship([]), [legacy]), []);
  const legacyRow = row({ interpretation: undefined, dayRate: 400000 });
  assert.equal(timelineContracts(ship([legacyRow]), [legacy])[0].dayRate, 400000);
});

test('maximum-only, missing, nonfinite and inconsistent conversions fail closed', () => {
  for (const patch of [{ maximumTerm: true }, { baseDurationDays: null }, { valueUsd: null },
    { derivedUsdPerDay: Infinity }, { derivedUsdPerDay: 500000 }, { rateBasis: 'undisclosed' }]) {
    const r = row({ interpretation: interpretation(patch) });
    assert.equal(timelineContracts(ship([r]), [])[0].economics, undefined);
  }
});

test('placement stays out of utilization and reported averages; Dayrates retains its own rate basis', () => {
  const placed = row();
  const today = new Date('2026-10-29T00:00:00Z');
  assert.equal(isStatisticalContract(placed), false);
  assert.equal(isCurrentReportedRate({ ...placed, dayRate: 500000 }, '2026-10-29'), false);
  assert.equal(getComputedStatus(ship([placed]), today), 'Idle');
  assert.equal(countFirmCoveredDays(ship([placed]), today, today), 0);
  const observations = dayrateObservations([ship([placed])], []);
  assert.equal(observations.length, 1);
  assert.equal(observations[0].basis, 'backlog');
  const reported = row({ dayRate: 400000, interpretation: interpretation({ dateBasis: 'source', startPrecision: 'month', endPrecision: 'month', rateBasis: 'reported', reportedUsdPerDay: 400000, derivedUsdPerDay: null }) });
  assert.equal(isStatisticalContract(reported), true);
  assert.equal(isCurrentReportedRate(reported, '2026-10-29'), true);
  assert.equal(getComputedStatus(ship([reported]), today), 'Active');
  assert.equal(countFirmCoveredDays(ship([reported]), today, today), 1);
  const placedReported = { ...reported, interpretation: interpretation({ rateBasis: 'reported', reportedUsdPerDay: 400000 }) };
  assert.equal(dayrateObservations([ship([placedReported])], [])[0].basis, 'reported');
  assert.equal(isCurrentReportedRate(placedReported, '2026-10-29'), false);
  assert.equal(contractPeriod(reported), '2026-10 ~ 2029-03');
});

test('options and contingent awards never enter firm rate statistics or copy a preceding rate', () => {
  for (const status of ['Option', 'Contingent']) {
    assert.equal(dayrateObservations([ship([row({ status })])], []).length, 0);
    assert.equal(countFirmCoveredDays(ship([row({ status })]), new Date('2026-10-28'), new Date('2026-10-29')), 0);
  }
  const undisclosed = row({ interpretation: interpretation({ rateBasis: 'undisclosed', derivedUsdPerDay: null }) });
  assert.equal(timelineContracts(ship([row({ id: 'previous', dayRate: 500000, interpretation: undefined }), undisclosed]), [])[1].economics, undefined);
});

test('bar design and details retain original expressions, calculation, placement, costs and source', () => {
  const canonical = row();
  const displayed = timelineContracts(ship([canonical]), [])[0];
  const html = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([canonical])] }));
  assert.match(html, /Client • \$477k/);
  assert.match(html, /height:30px;top:4px/);
  assert.match(html, /bg-blue-600 border-black\/20/);
  const detail = renderToStaticMarkup(React.createElement(ContractDetails, { selected: displayed, shipName: 'Ship', onClose() {} }));
  for (const value of ['fourth quarter of 2026', '모델이 앞선 계약 종료일에 연결', '420,000,000', '880일', '동원 비용', '추가 서비스', 'https://example.com/award']) assert(detail.includes(value), value);
  assert.match(economicsDetails(displayed.economics), /공시 dayrate 아님/);
  assert.equal(contractPeriod(canonical), '2026-10-28 ~ 2029-03-25');
});

test('held decisions preserve model facts without inventing a bar or converting source text', () => {
  const held = event({ action: 'hold', row: null, facts: { client: 'Client', region: 'Brazil', interpretation: interpretation({ dateBasis: 'unresolved' }) }, reasonKo: '시작 근거 부족', evidence: [] });
  assert.equal(timelineContracts(ship([]), [held]).length, 0);
  const details = Object.fromEntries(announcementDetails(held));
  assert.match(details['작업 시작'], /fourth quarter of 2026/);
  assert.equal(details['기본 기간'], '880 days');
});


test('each extension tranche uses only its own model-linked amount and base period', () => {
  const extensions = [[41e6, '2027-01-01', '2027-04-30'], [53e6, '2027-05-01', '2027-08-28']].map(([amount, startDate, endDate], index) => row({
    id: `extension-${index}`, startDate, endDate,
    interpretation: interpretation({ awardType: 'extension', valueUsd: amount, baseDurationDays: 120, derivedUsdPerDay: amount / 120 }),
  }));
  const legacy = row({ id: 'legacy', dayRate: 0, interpretation: undefined });
  const rows = timelineContracts(ship([legacy, ...extensions]), [event()]);
  assert.equal(rows[0].economics, undefined);
  assert.deepEqual(rows.slice(1).map(row => row.economics.rates[0].usdPerDay), [41e6 / 120, 53e6 / 120]);
  const observations = dayrateObservations([ship([legacy, ...extensions])], []);
  assert.equal(observations.length, 2);
  assert.equal(dayrateSeries(observations, '7G+', false).length, 0);
  assert.deepEqual(observations.map(row => row.rate), [41e6 / 120, 53e6 / 120]);
  assert.equal(timelineContracts(ship([]), [event({ facts: { interpretation: interpretation({ valueUsd: 94e6 }) } })]).length, 0);
});

test('invalid ISO dates and unresolved metadata never become bars or observations', () => {
  const { contractSpan } = require('../utils/contractTimeline.ts');
  for (const patch of [{ startDate: '2026-02-30' }, { endDate: 'tomorrow' }, { interpretation: interpretation({ dateBasis: 'unresolved' }) }]) {
    const invalid = row(patch);
    assert.equal(contractSpan(invalid, 2026, 2030), null);
    assert.equal(dayrateObservations([ship([invalid])], []).length, 0);
    assert.equal(isStatisticalContract(invalid), false);
  }
});

test('stored-result provenance stays in selected details and preserves original time and URL', () => {
  const storedReading = { legacyLedger: 'old-reviewed-news', recordSha256: 'a'.repeat(64), originalSourceSha256: null, reviewedAt: '2026-08-20T09:10:00Z' };
  const canonical = row({ inputMode: 'stored-result', storedReading });
  const displayed = timelineContracts(ship([canonical]), [])[0];
  const html = renderToStaticMarkup(React.createElement(Gantt, { ships: [ship([canonical])] }));
  assert.doesNotMatch(html, /저장된 읽기 결과로 보완|원래 읽기 시각/);
  const detail = renderToStaticMarkup(React.createElement(ContractDetails, { selected: displayed, shipName: 'Ship', onClose() {} }));
  assert.match(detail, /저장된 읽기 결과로 보완/);
  assert.match(detail, /원래 읽기 시각: 2026-08-20T09:10:00Z/);
  assert.match(detail, /원문 URL/);
  assert.match(detail, /href="https:\/\/example.com\/award"/);
  assert.equal(displayed.startDate, canonical.startDate);
  assert.equal(displayed.economics.rates[0].usdPerDay, canonical.interpretation.derivedUsdPerDay);
  const plain = renderToStaticMarkup(React.createElement(ContractDetails, { selected: row(), shipName: 'Ship', onClose() {} }));
  assert.doesNotMatch(plain, /저장된 읽기 결과로 보완|원래 읽기 시각/);
});
