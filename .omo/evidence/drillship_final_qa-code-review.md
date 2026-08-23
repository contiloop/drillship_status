# Drillship Final QA Code Review

Review snapshot: `/Users/inertia/Desktop/drillship`, current uncommitted worktree on 2026-08-24 KST.

## Skill Perspective Check

- `remove-ai-slops`: unavailable in the session skill list and not found under `/Users/inertia/.codex/skills`; applied the prompt criteria manually.
- `programming`: unavailable in the session skill list and not found under `/Users/inertia/.codex/skills`; applied the prompt criteria manually.
- Result: violations found. The diff contains a CI-skipped golden parser test path that creates false confidence, and production fallback logic that can validate stale documents when official discovery fails.

## CRITICAL

None.

## HIGH

1. `automation/fleet_sync/sources.py:115` silently falls back to hard-coded August 2026 documents when official discovery fails.

   The outer `except` in `discover_document` returns `spec.fallback_document_url` with a `fallback-after-*` discovery label (`sources.py:131-134`). That means a changed official index, network block, or parser discovery bug can still make the scheduled job pass by re-parsing an old known-good PDF. `_build_outputs` only rejects report dates older than the previous manifest (`pipeline.py:244-247`), so a stale same-date fallback remains valid forever. This violates the goal of automatically checking official sources for current updates; the job should fail closed or require an explicit offline/degraded mode before publishing or reporting success.

2. End-to-end deployment is not currently verified; production still does not serve the generated manifest.

   The old GitHub Pages deployment workflow is deleted, and the new refresh workflow only commits `public/data` and `data/provenance` back to `main` (`.github/workflows/refresh-fleet-data.yml:73-86`). README says Vercel Git integration will deploy (`README.md:13-14`), but there is no local `.vercel`/`vercel.json` evidence in the repo, and my direct check of `https://offshore-drillship-fleet-analyst.vercel.app/data/manifest.json` returned 404 while `/` returned 200 from Vercel. Until the generated `public/data/manifest.json` is actually deployed, the live app falls back to bundled data instead of the official sync feed.

3. Parser golden tests are skipped in fresh CI because their fixtures live under ignored `.cache`.

   `automation/tests/test_current_reports.py:15-20` skips all four official PDF parser golden-count cases if `.cache/reports` is missing. `.gitignore:29` ignores `.cache/`, and `git ls-files .cache/reports automation/tests/test_current_reports.py` showed no tracked fixture files. A fresh GitHub Actions checkout will therefore skip the strongest parser tests. This is a `remove-ai-slops` false-confidence issue: the tests look like coverage of real PDFs, but they do not run unless the local ignored cache happens to exist.

## MEDIUM

1. README local commands create a venv but npm scripts ignore it.

   README instructs `python3 -m venv .venv` and `.venv/bin/pip install -r automation/requirements.txt`, then `npm run data:check` (`README.md:29-34`). But `package.json:9-12` calls global `python3`, not `.venv/bin/python` or `python`. On this machine, `npm run test` failed with `/opt/homebrew/opt/python@3.14/bin/python3.14: No module named pytest`, and `python3 -m automation.fleet_sync --check` failed with `ModuleNotFoundError: No module named 'pdfplumber'`. This blocks the documented local workflow.

## LOW

1. README references the old event artifact path.

   README says ambiguous signals remain in `public/data/events.json` (`README.md:16`), but the current manifest points to content-addressed `public/data/events.<hash>.json`. The generated artifact section later uses the correct content-addressed form (`README.md:48`).

## Verification Run

- `.venv/bin/python -m pytest automation/tests -q`: passed, `14 passed in 0.85s` with local ignored PDF fixtures present.
- `.venv/bin/python -m automation.fleet_sync --check`: passed against live sources; returned `ok: true`, `changed: false`, `shipCount: 62`, `contractCount: 116`, `pendingEventCount: 28`.
- `npm run build`: passed; Vite emitted the existing large chunk warning.
- Generated artifact integrity check: `fleet_count=62`, `contract_count=116`, `source_count=4`, `fleet_hash_ok=True`, `events_hash_ok=True`.
- `npm run test`: failed locally because the package script uses global `python3` without installed pytest.
- Production check: `https://offshore-drillship-fleet-analyst.vercel.app/data/manifest.json` returned 404 during review.

## Recommendation

REQUEST_CHANGES. The code has a working live parse path in the reviewed environment, but the stale fallback path, skipped CI parser tests, and missing deployed manifest block approval for the stated end-to-end automation goal.
