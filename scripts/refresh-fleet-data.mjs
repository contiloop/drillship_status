import fs from 'node:fs/promises';
import path from 'node:path';
import pdf from 'pdf-parse';
import { COMPANY_SOURCES } from './fleet-sources.mjs';

const ROOT = process.cwd();
const SEED_FILES = [
  'data/data_as_of_26_01_07.json',
  'data/transocean_as_of_26-01-07.json',
  'data/valaris_as_of_26-01-07.json',
  'data/noble_as_of_26-01-07.json',
  'data/seadrill_as_of_26-01-07.json'
];

async function readSeedShips() {
  const all = [];
  for (const file of SEED_FILES) {
    const full = path.join(ROOT, file);
    const text = await fs.readFile(full, 'utf8');
    const parsed = JSON.parse(text);
    all.push(...parsed);
  }
  return all;
}

async function fetchText(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'drillship-status-bot/1.0' } });
  if (!res.ok) throw new Error(`failed to fetch ${url}: ${res.status}`);
  return res.text();
}

async function fetchPdfText(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'drillship-status-bot/1.0' } });
  if (!res.ok) throw new Error(`failed to fetch pdf ${url}: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const data = await pdf(buf);
  return data.text || '';
}

function latestDateMatch(text) {
  const dates = [...text.matchAll(/\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b/g)].map(m => m[0]);
  return dates.at(0) ?? null;
}

function normalizeDayRate(text) {
  const m = text.match(/\$([0-9]{3}(?:,[0-9]{3})+|\d{3,6})/);
  if (!m) return 0;
  return Number(m[1].replace(/,/g, ''));
}

function extractCompanySignals(company, text) {
  const lower = text.toLowerCase();
  const signals = [];
  for (const line of text.split(/\n+/)) {
    if (!line.toLowerCase().includes(company.toLowerCase())) continue;
    if (!/\b(?:rig|drillship|fleet|contract|dayrate|option)\b/i.test(line)) continue;
    signals.push(line.trim());
  }
  return signals.slice(0, 12);
}

async function collectSourceSnapshot() {
  const snapshots = [];
  for (const source of COMPANY_SOURCES) {
    let page = '';
    try {
      page = await fetchText(source.pageUrl);
    } catch {
      page = '';
    }
    const pdfUrl = (page.match(/https:\/\/[^"'\\s>]+\.pdf/i)?.[0]) ?? source.pdfUrl ?? null;
    let reportText = page;
    if (pdfUrl) {
      try {
        reportText = await fetchPdfText(pdfUrl);
      } catch {
        reportText = page;
      }
    }
    snapshots.push({
      company: source.company,
      source: source.pageUrl,
      pdfUrl,
      reportDate: latestDateMatch(reportText),
      signals: extractCompanySignals(source.company, reportText),
      sampleDayRate: normalizeDayRate(reportText)
    });
  }
  return snapshots;
}

async function main() {
  const ships = await readSeedShips();
  const snapshots = await collectSourceSnapshot();
  const byCompany = new Map(snapshots.map(s => [s.company, s]));

  const enriched = ships.map(ship => {
    const snapshot = byCompany.get(ship.company);
    return {
      ...ship,
      source: snapshot ? {
        pageUrl: snapshot.source,
        pdfUrl: snapshot.pdfUrl,
        reportDate: snapshot.reportDate,
        signals: snapshot.signals,
        sampleDayRate: snapshot.sampleDayRate
      } : undefined,
      updatedAt: new Date().toISOString()
    };
  });

  const meta = {
    version: snapshots.map(s => `${s.company}:${s.reportDate || 'unknown'}`).join('|'),
    collectedAt: new Date().toISOString(),
    sources: snapshots
  };

  await fs.writeFile(path.join(ROOT, 'public/fleet-data.json'), JSON.stringify(enriched, null, 2) + '\n');
  await fs.writeFile(path.join(ROOT, 'public/fleet-data.meta.json'), JSON.stringify(meta, null, 2) + '\n');
  console.log(`Wrote ${enriched.length} ships to public/fleet-data.json`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
