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
const { default: Card, announcementDetails } = require('../components/OfficialAnnouncementCard.tsx');
const { default: Announcements, splitAnnouncements } = require('../components/Announcements.tsx');
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
  assert.match(html, /배치에 의존하는 일정은 가동률·평균 공시 dayrate 집계에서 제외/);
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
  assert.match(applied, /원문 날짜 표현·정밀도와 표시 날짜를 구분/);
  assert.match(applied, /미공개·환산 요율도 평균 공시 dayrate에 포함하지 않습니다/);
  assert.doesNotMatch(applied, /월초·월말|계약표/);
  assert.match(render({ ...reviewed, reviewStatus: 'conflict' }), /조건 충돌/);
  assert.match(render({ ...kg2, llmReview: undefined }), /확인 대기/);
  assert.match(html, /^<details[^>]*><summary/);
  assert.doesNotMatch(html, /<details[^>]*\sopen(?:\s|=|>)/);
});

test('applied announcements are archived and every detail stays collapsed', () => {
  const grouped = splitAnnouncements(events);
  assert.equal(grouped.applied.length, 2);
  assert.equal(grouped.unresolved.length, 6);
  assert(grouped.unresolved.every(e => !e.autoApplied));
  const html = renderToStaticMarkup(React.createElement(Announcements, {events, sources: manifest.sources}));
  assert.match(html, /미확정 조건 6건/);
  assert.match(html, /Fleet Status 반영 이력 2건/);
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
    assert.match(html, /배치에 의존하는 일정은 가동률·평균 공시 dayrate 집계에서 제외/);
  }
  assert.equal(JSON.stringify({ fleet, events }), before);
});
