import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const rootDir = process.cwd();
const sourcePath = path.join(rootDir, 'data', 'data_as_of_26_01_07.json');
const publicDir = path.join(rootDir, 'public');
const outputPath = path.join(publicDir, 'fleet-data.json');
const metaPath = path.join(publicDir, 'fleet-data.meta.json');
const sourcesPath = path.join(publicDir, 'fleet-sources.json');

const SOURCES = [
  {
    company: 'Transocean',
    pageUrl: 'https://www.deepwater.com/investors/fleet-status-report',
    match: /https:\/\/www\.deepwater\.com\/documents\/FleetStatusReport\/\d{4}\/[^"'<> ]+\.pdf/g,
    fallbackUrl: 'https://www.deepwater.com/documents/FleetStatusReport/2026/August%202026%20Fleet%20Status%20Report.pdf',
  },
  {
    company: 'Valaris',
    pageUrl: 'https://www.valaris.com/investors/default.aspx',
    match: /https:\/\/s23\.q4cdn\.com\/956522167\/files\/doc_financials\/\d{4}\/q\d\/[^"'<> ]+\.pdf/g,
    fallbackUrl: 'https://s23.q4cdn.com/956522167/files/doc_financials/2026/q2/08052026-Fleet-Status-Report_FINAL.pdf',
  },
  {
    company: 'Noble',
    pageUrl: 'https://noblecorp.com/our-fleet/',
    match: /https:\/\/noblecorp\.com\/download\/[^"'<> ]+wpdmdl=\d+/g,
  },
  {
    company: 'Seadrill',
    pageUrl: 'https://www.seadrill.com/fleet/',
    match: /https:\/\/www\.seadrill\.com\/wp-content\/uploads\/\d{4}\/\d{2}\/[^"'<> ]+\.pdf/g,
    fallbackUrl: 'https://www.seadrill.com/wp-content/uploads/2026/08/Seadrill-Fleet-Status-Report-August-2026-vF.pdf',
  },
];

async function extractLatestSource(source) {
  const response = await fetch(source.pageUrl, {
    headers: {
      'user-agent': 'Mozilla/5.0 drillship-status-sync',
      accept: 'text/html,application/xhtml+xml',
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to load ${source.company} page: ${response.status}`);
  }

  const html = await response.text();
  const matches = html.match(source.match) || [];
  const unique = [...new Set(matches)];
  const latestUrl = unique[0] || source.fallbackUrl || null;
  const dateMatch = html.match(/(\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b)|(\b\d{2}\/\d{2}\/\d{4}\b)/);

  return {
    company: source.company,
    pageUrl: source.pageUrl,
    latestUrl,
    reportedAt: dateMatch ? dateMatch[0] : null,
    found: unique.length,
  };
}

async function main() {
  const raw = await readFile(sourcePath, 'utf8');
  const ships = JSON.parse(raw);

  if (!Array.isArray(ships) || ships.length === 0) {
    throw new Error('Source fleet data must be a non-empty array.');
  }

  const normalized = ships.map((ship) => ({
    ...ship,
    contracts: Array.isArray(ship.contracts) ? ship.contracts : [],
  }));

  await mkdir(publicDir, { recursive: true });
  await writeFile(outputPath, JSON.stringify(normalized, null, 2) + '\n', 'utf8');
  const sourceStatus = [];

  for (const source of SOURCES) {
    try {
      sourceStatus.push(await extractLatestSource(source));
    } catch (error) {
      sourceStatus.push({
        company: source.company,
        pageUrl: source.pageUrl,
        latestUrl: source.fallbackUrl || null,
        reportedAt: null,
        found: 0,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const meta = {
    version: '26-01-07',
    updatedAt: new Date().toISOString(),
    source: 'curated-source-json',
    count: normalized.length,
    sources: sourceStatus,
  };

  await writeFile(metaPath, JSON.stringify(meta, null, 2) + '\n', 'utf8');
  await writeFile(sourcesPath, JSON.stringify(sourceStatus, null, 2) + '\n', 'utf8');

  console.log(`Wrote ${outputPath}`);
  console.log(`Wrote ${metaPath}`);
  console.log(`Wrote ${sourcesPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
