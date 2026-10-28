const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// Compile just this component for Node tests with the already-installed compiler.
// No browser, extra test runner, or production loader is needed.
const compile = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  module._compile(outputText, filename);
};
require.extensions['.tsx'] = compile;
require.extensions['.ts'] = compile;
const { manifest, events, fleet } = require('./fixtures/legacy-fleet/load.cjs');
const { default: Card, announcementDetails, decisionDetails } = require('../components/OfficialAnnouncementCard.tsx');
const { default: Announcements, orderedAnnouncements, newsCategory } = require('../components/Announcements.tsx');
const { default: Gantt } = require('../components/FleetGantt.tsx');
const { contractSpan, contractPeriod, quarterScrollOffset } = require('../utils/contractTimeline.ts');
const kg2 = {
  company: 'Transocean', vessels: ['Dhirubhai Deepwater KG2'],
  classification: 'official-news-signal', autoApplied: false, pendingReview: true,
  publishedAt: 'August 20, 2026', url: 'https://investor.deepwater.com/news-releases/example',
  facts: {
    counterparty: 'ONGC', location: 'India', awardType: 'binding-letter-of-award',
    expectedStart: 'Q1 2027', startPrecision: 'quarter', awardTermYears: 2,
    optionTermYears: 2, optionEndIfFullyExercised: 'early 2031',
    announcedValueUsdApprox: 300000000, valueIncludes: ['additional services', 'mobilization fee'],
    dayRateDisclosure: 'undisclosed', dayRateInferred: false,
  },
};
const render = (event, source) => renderToStaticMarkup(React.createElement(Card, { event, source }));

test('initial viewport follows the current calendar quarter, not the current month', () => {
  for (let month = 0; month < 12; month++) {
    assert.equal(quarterScrollOffset(month, 120, 4800), Math.floor(month / 3) * 120);
  }
  assert.equal(quarterScrollOffset(9, 120, 4800), 360); // October is Q4.
  assert.equal(quarterScrollOffset(0, 120, 4800), 0); // January resets to Q1.
});

test('initial viewport uses measured timeline width on desktop and narrow layouts', () => {
  assert.equal(quarterScrollOffset(9, 72, 2624), 328);
  assert.equal(quarterScrollOffset(9, 72, 2720), 340);
  assert.equal(quarterScrollOffset(9, 36, 1440), 360);
});

test('KG2 renders all announced terms without inventing exact dates or a dayrate', () => {
  const details = Object.fromEntries(announcementDetails(kg2));
  assert.equal(details['고객·지역'], 'ONGC · 인도');
  assert.match(details['수주 형태'], /구속력 있는 LOA/);
  assert.equal(details['작업 시작'], 'Q1 2027 예정 · 정확한 날짜 미공개');
  assert.equal(details['기본 기간'], '2년');
  assert.equal(details['옵션'], '추가 2년 · 모두 행사 시 early 2031까지 작업 예정');
  assert.equal(details['기본 계약 가치'], '약 3억 달러 · 추가 서비스·이동 비용 포함');
  assert.equal(details.Dayrate, '미공개');
  const html = render(kg2);
  assert.equal((html.match(/<dt /g) || []).length, 7);
  assert.match(html, /Dhirubhai Deepwater KG2/);
  assert.match(html, /August 20, 2026/);
  assert.match(html, /미공개·환산 요율은 평균 공시 dayrate에 포함하지 않습니다/);
  assert.doesNotMatch(html, /집계에서 제외/);
  assert.doesNotMatch(html, /2027-01-01|2029-01-01|411,|\$0/);
});

test('generic news distinguishes missing extracted facts from disclosed absence', () => {
  const event = { company: 'Noble', classification: 'official-news-signal', autoApplied: false, title: 'New fleet news' };
  const html = render(event);
  assert.match(html, /확인된 정보 없음/);
  assert.match(html, /발표일 확인되지 않음/);
  assert.doesNotMatch(JSON.stringify(announcementDetails(event)), /미공개|구속력|2031/);
});

test('LOA retains month precision and falls back only to report provenance', () => {
  const source = { company: 'Valaris', documentUrl: 'https://www.valaris.com/report.pdf', reportDate: '2026-08-05' };
  const event = { company: 'Valaris', vessel: 'VALARIS DS-18', classification: 'letter-of-award', autoApplied: false, start: 'Nov 26', end: 'May 27' };
  const html = render(event, source);
  assert.match(html, /Nov 26/);
  assert.match(html, /Nov 26–May 27 \(원문 기간\)/);
  assert.match(html, /공식 보고서.*2026-08-05/);
  assert.match(html, /href="https:\/\/www.valaris.com\/report.pdf"/);
  assert.doesNotMatch(html, /구속력|7개월/);
  assert.doesNotMatch(render({ ...event, classification: 'official-news-signal' }, source), /href=/);
  assert.equal(Object.fromEntries(announcementDetails({ ...event, facts: { counterparty: 'Undisclosed', location: 'Undisclosed' } }))['고객·지역'], '미공개 · 미공개');
});

test('source links reject unsafe schemes and title text is escaped', () => {
  const html = render({ ...kg2, url: 'javascript:alert(1)', title: '<script>alert(1)</script>' });
  assert.doesNotMatch(html, /href=|<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test('a missing option horizon is not computed and reported rates remain separate', () => {
  const facts = { ...kg2.facts, optionEndIfFullyExercised: undefined, dayRateDisclosure: 'reported', dayRateUsd: 400000 };
  const details = Object.fromEntries(announcementDetails({ ...kg2, facts }));
  assert.equal(details.Dayrate, '$400,000 / 일');
  assert.equal(details['옵션'], '추가 2년 · 종료 시점 확인되지 않음');
  assert.equal(Object.fromEntries(announcementDetails({ ...kg2, facts: { ...facts, dayRateUsd: 0 } })).Dayrate, '확인된 정보 없음');
});

test('Codex-reviewed incomplete terms are distinct from unread articles', () => {
  const reviewed = { ...kg2, pendingReview: false, reviewStatus: 'details-incomplete',
    llmReview: { model: 'gpt-6-luna', reasoningEffort: 'medium', fast: false, reviewedAt: '2026-10-20T00:00:00Z' },
    facts: { ...kg2.facts, expectedStart: 'H1 2027', startPrecision: 'half-year', termText: '기본 880일', optionText: '추가 최대 880일', valueText: '약 4.2억 달러 · 이동 포함 · 서비스 제외' } };
  const html = render(reviewed);
  assert.match(html, /상세 조건 미공개/);
  assert.match(html, /H1 2027 예정/);
  assert.match(html, /기본 880일|추가 최대 880일/);
  assert.doesNotMatch(html, /Codex|gpt-6-luna|Fast off/);
  assert.match(render({ ...reviewed, facts: { ...reviewed.facts, expectedStart: 'fourth quarter of 2026' } }), /fourth quarter of 2026/);
  const applied = render({ ...reviewed, autoApplied: true });
  assert.match(applied, /Fleet Status 반영/);
  assert.match(applied, /원문 날짜 표현과 표시 날짜를 구분/);
  assert.match(applied, /미공개·환산 요율은 평균 공시 dayrate에 포함하지 않습니다/);
  assert.doesNotMatch(applied, /월초·월말|계약표/);
  assert.match(render({ ...reviewed, reviewStatus: 'conflict' }), /조건 충돌/);
  assert.match(render({ ...kg2, llmReview: undefined }), /확인 대기/);
  assert.match(html, /^<details[^>]*><summary/);
  assert.doesNotMatch(html, /<details[^>]*\sopen(?:\s|=|>)/);
});

test('every event is listed newest first in one list with a category tag and details stay collapsed', () => {
  const ordered = orderedAnnouncements(events);
  assert.equal(ordered.length, events.length);
  const html = renderToStaticMarkup(React.createElement(Announcements, {events, sources: manifest.sources}));
  assert.doesNotMatch(html, /미확정 조건|반영 이력|기타 발표 \d+건/);
  assert.equal((html.match(/rounded border border-slate-700 px-1.5/g) || []).length, events.length);
  assert.doesNotMatch(html, /계약표/);
  assert.doesNotMatch(html, /<details[^>]*\sopen(?:\s|=|>)|Codex|gpt-6-luna/);
});

test('contract bars include month ends and exclude completed or out-of-range contracts', () => {
  const row = {startDate: '2028-02-01', endDate: '2028-02-29'};
  assert.deepEqual(contractSpan(row, 2028, 2028), {left: 31 / 366 * 100, width: 29 / 366 * 100});
  assert.equal(contractSpan({startDate:'2025-01-01',endDate:'2025-12-31'}, 2026, 2028), null);
  assert.equal(contractSpan({startDate:'2030-01-01',endDate:'2030-12-31'}, 2026, 2028), null);
  assert.equal(contractSpan({startDate:'2026-02-03',endDate:'2026-02-02'}, 2026, 2028), null);
  assert.equal(contractPeriod(row), '2028-02 ~ 2028-02');
  assert.equal(contractPeriod({...row,datePrecision:{start:'day',end:'day'}}), '2028-02-01 ~ 2028-02-29');
});

test('real applied contracts render as accessible selectable bars without a news feed', () => {
  const html = renderToStaticMarkup(React.createElement(Gantt, {ships:fleet}));
  assert.match(html, /West Capella · Firm · PTTEP · 2026-03 ~ 2028-02/);
  assert.match(html, /Sonangol Libongos · Firm · Undisclosed · 2027-03 ~ 2030-01/);
  assert.match(html, /aria-pressed="false"/);
  assert.match(html, /aria-label="Fleet Status"/);
  assert.doesNotMatch(html, /계약표/);
  assert.doesNotMatch(html, /Codex|공식 발표|<article/);
  const app = fs.readFileSync('App.tsx', 'utf8');
  assert(app.indexOf('<FleetGantt') < app.indexOf('<MarketChart'));
  assert.doesNotMatch(app, /Codex 계약 대조|Codex 원문 대조|recentEvents/);
});

test('generated feed renders separately without mutating fleet or announcement data', () => {
  const before = JSON.stringify({ fleet, events });
  const pending = events.filter(event => event.pendingReview && !event.autoApplied);
  for (const event of pending) {
    const html = render(event, manifest.sources.find(source => source.company === event.company));
    assert.equal((html.match(/<dt /g) || []).length, 7);
    assert.doesNotMatch(html, /집계에서 제외|undefined/);
  }
  assert.equal(JSON.stringify({ fleet, events }), before);
});

test('stored-result announcement distinguishes original reading time and source only inside collapsed details', () => {
  const stored = { ...kg2, inputMode: 'stored-result',
    storedReading: { legacyLedger: 'legacy-review', recordSha256: 'b'.repeat(64), originalSourceSha256: 'c'.repeat(64), reviewedAt: '2026-08-20T09:10:00Z' },
    llmReview: { reviewedAt: '2026-10-28T10:00:00Z' } };
  const html = render(stored);
  assert.match(html, /저장된 읽기 결과로 보완/);
  assert.match(html, /원래 읽은 시각 2026-08-20T09:10:00Z/);
  assert.match(html, /원문 URL/);
  assert.match(html, /href="https:\/\/investor.deepwater.com\/news-releases\/example"/);
  assert.doesNotMatch(html.slice(0, html.indexOf('</summary>')), /저장된 읽기 결과로 보완|원래 읽은 시각/);
  assert.doesNotMatch(html, /2026-10-28T10:00:00Z|<details[^>]*\sopen(?:\s|=|>)/);
  assert.match(render({ ...stored, storedReading: { ...stored.storedReading, reviewedAt: null } }), /원래 읽은 시각 확인되지 않음/);
  assert.doesNotMatch(render(kg2), /저장된 읽기 결과로 보완|원래 읽은 시각/);
});

test('non-contract items appear in date order as tagged title + link rows', () => {
  const items = [
    { company: 'Transocean', classification: 'official-news-signal', autoApplied: false, pendingReview: false, title: 'Quarterly results', url: 'https://example.com/q3', publishedAt: '2026-10-01', newsCategory: '실적' },
    { company: 'Noble', classification: 'official-news-signal', autoApplied: false, pendingReview: false, title: 'Conference call', url: 'javascript:alert(1)', publishedAt: '2026-10-05' },
    { company: 'Noble', classification: 'official-report-reading', autoApplied: false, title: 'Noble Fleet Status Report', url: 'https://example.com/fsr', publishedAt: '2026-09-01' },
  ];
  assert.deepEqual(orderedAnnouncements(items).map(e => e.title), ['Conference call', 'Quarterly results', 'Noble Fleet Status Report']);
  assert.deepEqual(items.map(newsCategory), ['실적', '기타', 'Fleet Status']);
  const html = renderToStaticMarkup(React.createElement(Announcements, { events: items, sources: [] }));
  assert(html.indexOf('Conference call') < html.indexOf('Quarterly results'));
  assert.match(html, />실적<\/span><a href="https:\/\/example.com\/q3"[^>]*>Quarterly results ↗/);
  assert.doesNotMatch(html, /javascript:|<dt /);
});
const newInterpretation = { sourceStartText: 'mid-2027', placementBasis: '앞선 계약 뒤에 배치', termText: '2 years',
  valueText: '$300 million', valueUsd: 300e6, baseDurationDays: 730, derivedUsdPerDay: 300e6 / 730 };

test('new readingDecision shape renders action, row, reason, quote, locator and hold reason', () => {
  const upsert = { company: 'Seadrill', vessel: 'West Saturn', classification: 'official-news-signal', autoApplied: true, url: 'https://example.com/a',
    readingDecision: { action: 'upsert', contractId: 'c1', reasonKo: '신규 계약', quote: 'awarded a two-year contract', locator: 'p1',
      row: { startDate: '2027-07-01', endDate: '2029-06-30', dayRate: 0, client: 'Petrobras', region: 'Brazil', status: 'Firm', interpretation: newInterpretation } } };
  const details = Object.fromEntries(announcementDetails(upsert));
  assert.equal(details['고객·지역'], 'Petrobras · Brazil');
  assert.equal(details['작업 시작'], 'mid-2027');
  assert.equal(details['기본 기간'], '2 years');
  assert.equal(details['기본 계약 가치'], '$300 million');
  assert.equal(details.Dayrate, '미공개');
  const { notes, quotes } = decisionDetails(upsert);
  assert(notes.includes('판단: Fleet Status 반영'));
  assert(notes.includes('신규 계약'));
  assert(notes.includes('표시 구간: 2027-07-01 – 2029-06-30'));
  assert(notes.some(n => n.includes('일정 배치: 앞선 계약 뒤에 배치')));
  assert(notes.some(n => n.includes('$300,000,000 ÷ 730일 = $410,959 / 일')));
  assert.deepEqual(quotes, ['awarded a two-year contract · p1']);
  const reported = { ...upsert, readingDecision: { ...upsert.readingDecision, row: { ...upsert.readingDecision.row, dayRate: 415000 } } };
  assert.equal(Object.fromEntries(announcementDetails(reported)).Dayrate, '$415,000 / 일');

  const hold = { ...upsert, autoApplied: false, readingDecision: { action: 'hold', contractId: null, row: null, holdReason: 'end date before start date' } };
  const held = decisionDetails(hold);
  assert(held.notes.includes('판단: 보류'));
  assert(held.notes.includes('보류 사유: end date before start date'));
  assert.deepEqual(held.quotes, []);
  const html = render(hold);
  assert.match(html, /보류 사유: end date before start date/);
  assert.doesNotMatch(html, /undefined|null/);

  const bare = render({ ...upsert, readingDecision: { action: 'no-change', contractId: null, row: null } });
  assert.match(bare, /판단: 변경 없음/);
  assert.doesNotMatch(bare, /undefined|null/);
});

test('legacy readingDecision facts/evidence shape still renders', () => {
  const legacy = { company: 'Seadrill', vessel: 'Ship', classification: 'official-news-signal', autoApplied: false, url: 'https://example.com/b',
    readingDecision: { action: 'hold', contractId: null, row: null, reasonKo: '시작 근거 부족',
      facts: { client: 'Client', region: 'Brazil', interpretation: { ...newInterpretation, sourceStartText: 'fourth quarter of 2026', dateBasis: 'unresolved', startPrecision: 'quarter', rateBasis: 'backlog-derived', includes: ['동원 비용'], excludes: [] } },
      evidence: [{ fields: ['termText'], quote: '880 days', locator: 'p2' }, { quote: 'no locator' }] } };
  const details = Object.fromEntries(announcementDetails(legacy));
  assert.equal(details['고객·지역'], 'Client · Brazil');
  assert.equal(details['작업 시작'], 'fourth quarter of 2026');
  const { notes, quotes } = decisionDetails(legacy);
  assert(notes.includes('시작 근거 부족'));
  assert(notes.some(n => n.includes('포함: 동원 비용')));
  assert.deepEqual(quotes, ['880 days · p2', 'no locator']);
  const html = render(legacy);
  assert.doesNotMatch(html, /undefined|\(quarter\)|배치 보류/);
});
