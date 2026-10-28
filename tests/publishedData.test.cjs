const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// The changing public dataset is checked for integrity. Specific ships/articles
// and counts belong in the immutable legacy fixture, not in live-data tests.
const directory = path.join(__dirname, '../public/data');
const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.json'), 'utf8'));
const fleet = JSON.parse(fs.readFileSync(path.join(directory, manifest.fleetFile), 'utf8'));
const { events } = JSON.parse(fs.readFileSync(path.join(directory, manifest.eventsFile), 'utf8'));
const iso = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

test('published manifest references content-named files and accurate current counts', () => {
  assert.equal(path.basename(manifest.fleetFile), `fleet.${manifest.dataHash}.json`);
  assert.equal(path.basename(manifest.eventsFile), `events.${manifest.eventsHash}.json`);
  assert.equal(manifest.shipCount, fleet.length);
  assert.equal(manifest.contractCount, fleet.reduce((total, ship) => total + ship.contracts.length, 0));
  assert.equal(manifest.sourceCount, manifest.sources.length);
  assert(Array.isArray(events));
});

test('current canonical rows retain valid dates, identities, statuses and rate separation', () => {
  const ids = new Set();
  for (const ship of fleet) {
    assert(!ids.has(ship.id));
    ids.add(ship.id);
    const contracts = new Set();
    for (const row of ship.contracts) {
      assert(!contracts.has(row.id));
      contracts.add(row.id);
      assert.equal(row.vesselId, ship.id);
      assert(iso(row.startDate) && iso(row.endDate) && row.endDate >= row.startDate);
      assert(['Firm', 'Option', 'Contingent'].includes(row.status));
      assert(Number.isFinite(row.dayRate) && row.dayRate >= 0);
      const value = row.interpretation;
      if (!value) continue; // Retained validated legacy rows remain readable.
      assert(['source', 'placement'].includes(value.dateBasis));
      assert(['reported', 'backlog-derived', 'undisclosed'].includes(value.rateBasis));
      assert(Array.isArray(value.includes) && Array.isArray(value.excludes));
      assert.equal(row.dayRate, value.rateBasis === 'reported' ? value.reportedUsdPerDay : 0);
      if (value.rateBasis === 'backlog-derived') {
        assert(!value.maximumTerm && value.valueUsd > 0 && value.baseDurationDays > 0 && value.derivedUsdPerDay > 0);
        assert(Math.abs(value.valueUsd / value.baseDurationDays - value.derivedUsdPerDay) <= Math.max(1, value.derivedUsdPerDay * 0.00001));
      }
    }
  }
});

test('normalized news retains model facts, evidence and hold decisions as details', () => {
  for (const event of events) {
    const decision = event.readingDecision;
    if (!decision) continue;
    assert(['upsert', 'withdraw', 'no-change', 'hold'].includes(decision.action));
    assert(decision.facts.interpretation);
    assert(Array.isArray(decision.evidence));
    assert.equal(typeof decision.reasonKo, 'string');
    if (decision.action === 'hold') assert.equal(decision.row, null);
    if (decision.row) assert(iso(decision.row.startDate) && iso(decision.row.endDate));
  }
});
