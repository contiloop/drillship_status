const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// Compile just this component for Node tests with the already-installed compiler.
// No browser, extra test runner, or production loader is needed.
require.extensions['.tsx'] = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  module._compile(outputText, filename);
};
const { default: Card, announcementDetails } = require('../components/OfficialAnnouncementCard.tsx');
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

test('KG2 renders all announced terms without inventing exact dates or a dayrate', () => {
  const details = Object.fromEntries(announcementDetails(kg2));
  assert.equal(details['고객·지역'], 'ONGC · 인도');
  assert.match(details['수주 형태'], /구속력 있는 LOA/);
  assert.equal(details['작업 시작'], '2027년 1분기 예정 · 정확한 날짜 미공개');
  assert.equal(details['기본 기간'], '2년');
  assert.equal(details['옵션'], '추가 2년 · 모두 행사 시 2031년 초까지 작업 예정');
  assert.equal(details['기본 계약 가치'], '약 3억 달러 · 추가 서비스·이동 비용 포함');
  assert.equal(details.Dayrate, '미공개');
  const html = render(kg2);
  assert.equal((html.match(/<dt /g) || []).length, 7);
  assert.match(html, /Dhirubhai Deepwater KG2/);
  assert.match(html, /2026-08-20/);
  assert.match(html, /계약표·가동률·평균 dayrate 계산에는 포함하지 않습니다/);
  assert.doesNotMatch(html, /2027-01-01|2029-01-01|411,|\$0/);
});

test('generic news distinguishes missing extracted facts from disclosed absence', () => {
  const event = { company: 'Noble', classification: 'official-news-signal', autoApplied: false, title: 'New fleet news' };
  const html = render(event);
  assert.match(html, /확인된 정보 없음/);
  assert.match(html, /발표일 확인되지 않음/);
  assert.doesNotMatch(html, /미공개|구속력|2031/);
});

test('LOA retains month precision and falls back only to report provenance', () => {
  const source = { company: 'Valaris', documentUrl: 'https://www.valaris.com/report.pdf', reportDate: '2026-08-05' };
  const event = { company: 'Valaris', vessel: 'VALARIS DS-18', classification: 'letter-of-award', autoApplied: false, start: 'Nov 26', end: 'May 27' };
  const html = render(event, source);
  assert.match(html, /2026년 11월 예정 · 정확한 날짜 미공개/);
  assert.match(html, /2026년 11월–2027년 5월 \(원문 기간\)/);
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

test('generated feed renders separately without mutating fleet or announcement data', () => {
  const manifest = JSON.parse(fs.readFileSync('public/data/manifest.json', 'utf8'));
  const { events } = JSON.parse(fs.readFileSync(`public/data/${manifest.eventsFile}`, 'utf8'));
  const fleet = JSON.parse(fs.readFileSync(`public/data/${manifest.fleetFile}`, 'utf8'));
  const before = JSON.stringify({ fleet, events });
  const pending = events.filter(event => event.pendingReview && !event.autoApplied);
  for (const event of pending) {
    const html = render(event, manifest.sources.find(source => source.company === event.company));
    assert.equal((html.match(/<dt /g) || []).length, 7);
    assert.match(html, /계약표·가동률·평균 dayrate 계산에는 포함하지 않습니다/);
  }
  assert.equal(JSON.stringify({ fleet, events }), before);
});
