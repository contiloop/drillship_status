"""Discovery, durable per-document jobs and the sole contract-writing use case.

The reader returns data. Only this module applies mechanically validated items.
Raw document identity is independent of platform-specific text/PNG extraction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any

from .core import content_hash, load_json
from .reading_contracts import DocumentValidationError, context_sha256, policy_sha256, validate_reading_response, validate_news_queue
from .reading_service import ReaderConfigurationError, ReadingService, build_reader_policy
from .source_materials import EXTRACTION_VERSION, PRESERVED_MATERIAL_DISCOVERY, collect_reading_materials, document_sha256, validate_official_source
from .sources import HttpClient

LEDGER_PATH = Path('data/provenance/readings.json')


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'refusing symlink output: {path}')
    temporary = path.with_name(path.name + '.tmp')
    if temporary.is_symlink():
        raise ValueError('refusing symlink temporary output')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def _diagnostic_json(value: Any) -> Any:
    """Preserve rejected nonfinite values as explicit JSON diagnostic data."""
    if isinstance(value, float) and not math.isfinite(value):
        return {'invalidNumber': repr(value)}
    if isinstance(value, dict):
        return {key: _diagnostic_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_diagnostic_json(item) for item in value]
    return value


def _issue_events(request: dict, validation: Any, response: dict, job: dict, events: list[dict]) -> None:
    """Expose validation holds with real source excerpts, never rejected facts."""
    registry = {v['id']: v for v in request['baseline'] if v['company'] == request['company']}
    for issue in validation.issues:
        raw_items = response.get(issue.collection, [])
        original = raw_items[issue.index] if issue.index < len(raw_items) else {}
        vessel = registry.get(original.get('vesselId')) if isinstance(original, dict) else None
        identity = 'reading-issue-' + content_hash({'job': job['jobId'], 'collection': issue.collection, 'index': issue.index}, length=20)
        reason = '원문 읽기는 완료했지만 이 항목은 검증을 통과하지 못하여 기존 값을 보존했습니다. ' + issue.message
        excerpts = []
        for page in request['pages']:
            if page['text'].strip():
                excerpts = [{'field': 'sourceExcerpt', 'quote': page['text'][:500]}]
                break
        event = {'id': identity, 'company': request['company'], 'classification': 'official-reading-hold',
                 'url': request['url'], 'publishedAt': request['publishedAt'], 'title': job.get('title') or '공식 자료 판단 보류',
                 'autoApplied': False, 'pendingReview': True, 'reviewStatus': 'conflict', 'needsDetails': True,
                 'reason': reason, 'evidence': excerpts,
                 'readingIssue': {'collection': issue.collection, 'index': issue.index, 'message': issue.message}}
        if vessel:
            event.update(vessel=vessel['name'], vesselId=vessel['id'])
        events[:] = [old for old in events if old.get('id') != identity]
        events.append(event)


def _assert_no_links(path: Path) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'refusing symlink input: {path}')


def _read(path: Path, default: Any) -> Any:
    _assert_no_links(path)
    return load_json(path) if path.exists() else deepcopy(default)


def _payload(root: Path, manifest: dict, key: str, default: Any) -> Any:
    filename = manifest.get(key)
    if filename is None:
        return deepcopy(default)
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.json'):
        raise ValueError(f'unsafe {key}')
    path = root / 'public/data' / filename
    _assert_no_links(path)
    payload = load_json(path)
    hash_key = 'dataHash' if key == 'fleetFile' else 'eventsHash'
    data = payload if key == 'fleetFile' else payload.get('events')
    if manifest.get(hash_key) and content_hash(data, length=20) != manifest[hash_key]:
        raise ValueError(f'{key} content hash does not match manifest')
    return payload


def _legacy_evidence(root: Path) -> list[dict]:
    """Old ledgers are comparison evidence, never executable reconciliation."""
    evidence = []
    for name in ('data/news/reviews.json', 'data/provenance/observations.json'):
        path = root / name
        _assert_no_links(path)
        if path.exists():
            evidence.append({'legacyLedger': name, 'content': _diagnostic_json(load_json(path))})
    return evidence


def _prior_entry(job: dict, ledger: dict) -> dict:
    source = job.get('inputSource') or ledger['sources'].get(job['sourceId'], {})
    return {'sourceId': job['sourceId'], 'company': job['company'], 'kind': job['kind'],
            'url': source.get('finalUrl') or source.get('url') or job['url'], 'publishedAt': source.get('publishedAt'),
            'documentSha256': job['documentSha256'], 'inputContextSha256': job['contextSha256'], 'result': job['output']}


def _prior(ledger: dict, legacy: list[dict]) -> list[dict]:
    return [*deepcopy(legacy), *[_prior_entry(j, ledger) for j in ledger['jobs'] if j['status'] == 'completed' and 'output' in j]]


def _save_context(ledger: dict, request: dict) -> None:
    """Merkle-like references avoid copying all prior outputs into every context."""
    objects = ledger.setdefault('objects', {})
    def intern(value):
        digest = content_hash(value, length=64)
        objects.setdefault(digest, deepcopy(value))
        return digest
    baseline = [{'vesselRef': intern({k: v for k, v in ship.items() if k != 'contracts'}),
                 'contractRefs': [intern(row) for row in ship['contracts']]} for ship in request['baseline']]
    # Job identities stay in audit references, outside the model's evidence.
    prior_jobs = {content_hash(_prior_entry(job, ledger), length=64): job['jobId']
                  for job in ledger['jobs'] if job['status'] == 'completed' and 'output' in job}
    evidence_refs = []
    for item in request['priorEvidence']:
        digest = content_hash(item, length=64)
        if digest in prior_jobs:
            evidence_refs.append({'readingJobId': prior_jobs[digest], 'sha256': digest})
        else:
            evidence_refs.append({'objectRef': intern(item)})
    ledger.setdefault('contexts', {})[request['contextSha256']] = {
        'baselineRef': intern(baseline), 'priorEvidenceRefs': evidence_refs}


def reconstruct_context(ledger: dict, context_hash: str) -> dict:
    """Rebuild the exact model context and verify every persisted reference."""
    objects = ledger['objects']
    def resolve(digest):
        value = deepcopy(objects[digest])
        if content_hash(value, length=64) != digest:
            raise ValueError('context object hash mismatch')
        return value
    context = ledger['contexts'][context_hash]
    baseline = [{**resolve(s['vesselRef']), 'contracts': [resolve(r) for r in s['contractRefs']]}
                for s in resolve(context['baselineRef'])]
    jobs = {j['jobId']: j for j in ledger['jobs']}
    evidence = []
    for ref in context['priorEvidenceRefs']:
        item = resolve(ref['objectRef']) if 'objectRef' in ref else deepcopy(_prior_entry(jobs[ref['readingJobId']], ledger))
        if 'sha256' in ref and content_hash(item, length=64) != ref['sha256']:
            raise ValueError('prior reading output hash mismatch')
        evidence.append(item)
    if context_sha256(baseline, evidence) != context_hash:
        raise ValueError('model context hash mismatch')
    return {'baseline': baseline, 'priorEvidence': evidence}


def _preserved_materials(ledger: dict) -> list[dict]:
    retained = {}
    for job in ledger['jobs']:
        if job['status'] == 'completed' and job.get('outcome') != 'held':
            continue
        source = ledger['sources'].get(job['sourceId'], {})
        item = {'company': job['company'], 'kind': job['kind'], 'url': source.get('url') or job['url'],
                'title': source.get('title') or job.get('title'),
                'publishedAt': source.get('publishedAt') or job.get('inputSource', {}).get('publishedAt')}
        retained[(item['company'], item['kind'], item['url'])] = item
    for source in ledger['sources'].values():
        if source.get('error') or not source.get('bodyRead', True):
            item = {k: source.get(k) for k in ('company', 'kind', 'url', 'title', 'publishedAt')}
            retained[(item['company'], item['kind'], item['url'])] = item
    return list(retained.values())


def _cache_identity(request: dict) -> dict:
    # Operational epochs/job IDs do not identify model input. Envelope metadata
    # is included because publication time or URL can change without new pages.
    return {key: request[key] for key in ('sourceId', 'sourceSha256', 'contextSha256', 'readerPolicySha256',
                                          'company', 'kind', 'url', 'publishedAt')}


def _state(company: str, fleet: list, sources: dict, evidence: list, policy_hash: str) -> str:
    # Times, retry counters and extraction hashes are deliberately absent.
    versions = sorted((key, s.get('lastSuccessfulDocumentSha256') or s.get('documentSha256'), s.get('publishedAt'), s.get('finalUrl') or s.get('url'))
                      for key, s in sources.items() if s.get('company') == company)
    return content_hash({'baseline': [s for s in fleet if s['company'] == company],
                         'evidence': evidence, 'sourceVersions': versions, 'policy': policy_hash}, length=64)


def _job(material: dict, epoch: str) -> dict:
    request = material['readingInput']
    identity = {'sourceId': request['sourceId'], 'documentSha256': material['documentSha256'],
                'comparisonEpochSha256': epoch, 'readerPolicySha256': request['readerPolicySha256']}
    return {'jobId': 'reading-' + content_hash(identity, length=64), **identity,
            'contextSha256': request['contextSha256'], 'serverContextSha256': request['contextSha256'],
            'sourceSha256': request['sourceSha256'], 'kind': request['kind'], 'company': request['company'],
            'url': request['url'], 'status': 'pending', 'attempts': 0}


def queue(jobs: list[dict]) -> dict:
    fields = ('jobId', 'kind', 'company', 'url', 'sourceSha256', 'contextSha256', 'readerPolicySha256')
    items = sorted(({key: j[key] for key in fields} for j in jobs if j['status'] != 'completed'), key=lambda j: j['jobId'])
    result = {'schemaVersion': 2, 'pendingCount': len(items), 'fingerprint': content_hash(items, length=64), 'jobs': items}
    validate_news_queue(result)
    return result


def _verify_source(material: dict) -> str:
    request = material['readingInput']
    validate_official_source(request['company'], request['url'])
    response = HttpClient().get(request['url'])
    validate_official_source(request['company'], response.url)
    return document_sha256(response.content)


def _apply(fleet: list[dict], request: dict, validation: Any, job: dict, events: list[dict]) -> list[dict]:
    before = []
    ships = {ship['id']: ship for ship in fleet}
    for update in validation.vessel_updates:
        vessel = ships[update['vesselId']]
        before.append({'vesselId': vessel['id'], 'status': vessel['status'], 'statusAsOf': vessel.get('statusAsOf')})
        vessel.update(status=update['status'], statusAsOf=update['statusAsOf'])
    for index, original in enumerate(validation.decisions):
        decision = deepcopy(original)
        vessel = ships[decision['vesselId']]
        old = next((row for row in vessel['contracts'] if row['id'] == decision['contractId']), None)
        action = decision['action']
        if action in {'upsert', 'withdraw'}:
            before.append({'vesselId': vessel['id'], 'contractId': decision['contractId'], 'row': deepcopy(old)})
        if action == 'upsert':
            proposed = decision['row']
            # Null provenance means omitted, not a request to erase old metadata.
            row = {**(old or {}), **{k: v for k, v in proposed.items() if v is not None}}
            row['id'] = decision['contractId'] or proposed.get('id') or (
                vessel['id'] + '-reading-' + content_hash({'modelInput': content_hash(_cache_identity(request), length=64), 'index': index}, length=20))
            row['vesselId'] = vessel['id']
            row['sourceUrl'] = request['url']
            row['sourceSha256'] = request['sourceSha256']
            if request.get('publishedAt'):
                row['sourcePublishedAt'] = request['publishedAt']
            elif not old or (old.get('sourceUrl'), old.get('sourceSha256')) != (request['url'], request['sourceSha256']):
                # A new citation must not inherit the old citation's date. The
                # old verified pair remains available in the before history.
                row.pop('sourcePublishedAt', None)
            if request['kind'] == 'official-news':
                row['newsReviewed'] = True
            row['datePrecision'] = {'start': row['interpretation']['startPrecision'], 'end': row['interpretation']['endPrecision']}
            if old:
                vessel['contracts'][vessel['contracts'].index(old)] = row
            else:
                vessel['contracts'].append(row)
            decision['contractId'] = row['id']
            decision['row'] = deepcopy(row)
        elif action == 'withdraw':
            # Historical values remain in this job's before/output and event.
            vessel['contracts'] = [row for row in vessel['contracts'] if row['id'] != decision['contractId']]
        event_id = 'reading-event-' + content_hash({'job': job['jobId'], 'index': index}, length=20)
        event = {'id': event_id, 'company': request['company'], 'vessel': vessel['name'], 'vessels': [vessel['name']],
                 'vesselId': vessel['id'], 'classification': 'official-news-signal' if request['kind'] == 'official-news' else 'official-report-reading',
                 'url': request['url'], 'publishedAt': request['publishedAt'], 'title': job.get('title') or f"{request['company']} 공식 자료",
                 'sourceId': request['sourceId'], 'sourceSha256': request['sourceSha256'], 'readingDecision': decision,
                 'autoApplied': action in {'upsert', 'withdraw'}, 'pendingReview': False,
                 'needsDetails': action == 'hold', 'reviewStatus': 'conflict' if action == 'hold' else 'applied' if action in {'upsert', 'withdraw'} else 'acknowledged'}
        events[:] = [old_event for old_event in events if old_event.get('id') != event_id]
        events.append(event)
    return before


def _validate_common(fleet: list[dict], baseline: list[dict]) -> None:
    from .pipeline import COMPANY_COUNTS, ALLOWED_GENERATIONS, ALLOWED_VESSEL_STATUSES, ALLOWED_CONTRACT_STATUSES
    from collections import Counter
    if len(fleet) != 62 or dict(Counter(s.get('company') for s in fleet)) != COMPANY_COUNTS:
        raise ValueError('registered company/vessel counts changed')
    if {(s['id'], s['name'], s['company'], s['generation']) for s in fleet} != {(s['id'], s['name'], s['company'], s['generation']) for s in baseline}:
        raise ValueError('registered fleet changed')
    json.dumps(fleet, allow_nan=False)
    ids = set()
    for ship in fleet:
        if (ship.get('generation') not in ALLOWED_GENERATIONS or ship.get('status') not in ALLOWED_VESSEL_STATUSES
                or isinstance(ship.get('yearBuilt'), bool) or not isinstance(ship.get('yearBuilt'), int)):
            raise ValueError('vessel schema integrity failure')
        if 'statusAsOf' in ship and datetime.strptime(ship['statusAsOf'], '%Y-%m-%d').date().isoformat() != ship['statusAsOf']:
            raise ValueError('vessel status date integrity failure')
        for row in ship['contracts']:
            if not isinstance(row.get('id'), str) or not row['id'] or row['id'] in ids or row['vesselId'] != ship['id']:
                raise ValueError('contract reference/ID integrity failure')
            ids.add(row['id'])
            if (row.get('status') not in ALLOWED_CONTRACT_STATUSES or
                    any(not isinstance(row.get(key), str) or not row[key] for key in ('client', 'region'))):
                raise ValueError('contract schema integrity failure')
            for key in ('startDate', 'endDate'):
                if datetime.strptime(row[key], '%Y-%m-%d').date().isoformat() != row[key]:
                    raise ValueError('contract ISO date integrity failure')
            if (row['startDate'] > row['endDate'] or isinstance(row['dayRate'], bool)
                    or not isinstance(row['dayRate'], (int, float)) or not math.isfinite(row['dayRate']) or row['dayRate'] < 0):
                raise ValueError('contract date/numeric integrity failure')


def execute(root: Path, *, mode: str, write: bool, service=None, collector=collect_reading_materials,
            recheck=_verify_source, base_revision: str | None = None, now: int | None = None,
            recovery_path: Path | None = None) -> dict:
    if mode not in {'server', 'local'}:
        raise ValueError('unknown pipeline mode')
    root = root.resolve()
    if mode == 'local' and recovery_path is None:
        recovery_path = root / '.cache/readings-recovery.json'
    # A direct CLI invocation has the same exclusion as the scheduler.
    lock_path = root / '.cache/reading-pipeline.lock'
    _assert_no_links(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': True, 'skipped': 'reading pipeline is locked'}
        return _execute(root, mode=mode, write=write, service=service, collector=collector, recheck=recheck,
                        base_revision=base_revision, now=now, recovery_path=recovery_path)


def _execute(root: Path, *, mode, write, service, collector, recheck, base_revision, now, recovery_path):
    def stamp():
        value = int(time.time() if now is None else now)
        return value, datetime.fromtimestamp(value, timezone.utc).isoformat()
    timestamp, at = stamp()
    manifest = _read(root / 'public/data/manifest.json', {})
    fleet = _payload(root, manifest, 'fleetFile', _read(root / 'data/data_as_of_26_01_07.json', []))
    baseline = deepcopy(fleet)
    events = _payload(root, manifest, 'eventsFile', {'events': []})['events']
    ledger = _read(root / LEDGER_PATH, {'schemaVersion': 2, 'jobs': [], 'sources': {}, 'acknowledgements': {}})
    if ledger.get('schemaVersion') != 2 or not isinstance(ledger.get('jobs'), list):
        raise ValueError('unsupported readings journal; preserve it for diagnosis')
    legacy = _legacy_evidence(root)
    evidence = _prior(ledger, legacy)
    policy = build_reader_policy(extraction_version=EXTRACTION_VERSION)
    policy_hash = policy_sha256(policy)
    preserved = [{'company': e['company'], 'url': ledger['sources'].get(e.get('sourceId'), {}).get('url', e['url']), 'title': e.get('title'), 'publishedAt': e.get('publishedAt')}
                 for e in events if e.get('classification') == 'official-news-signal' and e.get('url')]
    collection = collector(HttpClient(), baseline=fleet, prior_evidence=evidence, reader_policy_sha256=policy_hash,
                           previous_sources=ledger['sources'], preserved_news=preserved,
                           preserved_materials=_preserved_materials(ledger), news_limit=20)
    timestamp, at = stamp()
    sources = {**ledger['sources'], **{s['sourceId']: {**ledger['sources'].get(s['sourceId'], {}), **s} for s in collection['sources']}}
    initial_evidence = deepcopy(evidence)
    acknowledgements = ledger.setdefault('acknowledgements', {})
    epochs = {}
    for company in {ship['company'] for ship in fleet}:
        state_hash = _state(company, fleet, sources, initial_evidence, policy_hash)
        previous = acknowledgements.get(company, {})
        epochs[company] = previous['comparisonEpochSha256'] if previous.get('stateSha256') == state_hash else state_hash
    jobs = {j['jobId']: j for j in ledger['jobs']}
    active = []
    for material in collection['materials']:
        job = _job(material, epochs[material['readingInput']['company']])
        job = jobs.setdefault(job['jobId'], job)
        job['title'] = sources.get(job['sourceId'], {}).get('title')
        active.append((material, job))
    # An unreadable previously queued source remains pending, not silently lost.
    current_source_ids = {m['readingInput']['sourceId'] for m, _ in active}
    active_jobs = [j for _, j in active] + [j for j in jobs.values() if j['status'] != 'completed' and j['sourceId'] not in current_source_ids]
    ledger.update(sources=sources, jobs=list(jobs.values()), lastCollectedAt=at, collectionErrors=collection['errors'])
    ledger.setdefault('policies', {})[policy_hash] = json.loads(policy)
    try:
        cache = _read(recovery_path, {}) if recovery_path else {}
    except (ValueError, OSError):
        cache = {}
    # v1 records omit source identity/policy and cannot be safely migrated.
    if not isinstance(cache, dict) or cache.get('schemaVersion') != 2 or not isinstance(cache.get('entries'), dict):
        cache = {'schemaVersion': 2, 'entries': {}}
    cache_entries = cache['entries']
    model_calls, completed, failed = 0, [], []
    if base_revision is None:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, capture_output=True)
        base_revision = result.stdout.strip() if result.returncode == 0 else None
    if mode == 'local':
        if service is None:
            from .codex_reader import CodexReader
            service = ReadingService(CodexReader(policy=policy), policy=policy, max_attempts=3)
        for material, job in active:
            timestamp, at = stamp()
            if job['status'] == 'completed' or job.get('nextAttemptAfter', 0) > timestamp:
                continue
            request = deepcopy(material['readingInput'])
            # Each reader sees preceding successful decisions in the same batch.
            request['baseline'], request['priorEvidence'] = deepcopy(fleet), _prior(ledger, legacy)
            request['contextSha256'] = context_sha256(request['baseline'], request['priorEvidence'])
            job.update(baseRevision=base_revision, lastAttemptAt=at, inputSource={
                       'url': request['url'], 'publishedAt': request['publishedAt'], 'kind': request['kind']},
                       contextSha256=request['contextSha256'],
                       modelInputContextSha256=request['contextSha256'])
            _save_context(ledger, request)
            try:
                identity = _cache_identity(request)
                job['modelInputSha256'] = content_hash(identity, length=64)
                cache_key = 'input-' + job['modelInputSha256']
                cached = cache_entries.get(cache_key)
                reusable = False
                if isinstance(cached, dict) and cached.get('inputIdentity') == identity:
                    try:
                        response = cached['output']
                        if content_hash(response, length=64) != cached['outputSha256']:
                            raise ValueError('recovery output hash mismatch')
                        validation = validate_reading_response(response, request)
                        attempts = cached['attempts']
                        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
                            raise ValueError('invalid recovery attempt count')
                        reusable = True
                    except (DocumentValidationError, KeyError, TypeError, ValueError):
                        cache_entries.pop(cache_key, None)
                if not reusable:
                    model_calls += 1
                    outcome = service.read(request)
                    response, validation, attempts = outcome.response, outcome.validation, outcome.attempts
                    # Even injectable ports cannot bypass the validator.
                    validation = validate_reading_response(response, request)
                    stored_output = _diagnostic_json(response)
                    cache_entries[cache_key] = {'inputIdentity': identity, 'output': stored_output,
                                                'outputSha256': content_hash(stored_output, length=64), 'attempts': attempts}
                    if recovery_path:
                        atomic_json(recovery_path, cache)
                fresh_hash = recheck(material)
                timestamp, at = stamp()
                if fresh_hash != material['documentSha256']:
                    job.update(status='pending', lastError='source changed during reading', detectedDocumentSha256=fresh_hash, lastAttemptAt=at,
                               rejectedOutput=_diagnostic_json(response), localSourceSha256=request['sourceSha256'],
                               modelInputContextSha256=request['contextSha256'], baseRevision=base_revision)
                    continue
                safe = deepcopy(validation)
                candidate, candidate_events = deepcopy(fleet), deepcopy(events)
                before = _apply(candidate, request, safe, job, candidate_events)
                _issue_events(request, validation, response, job, candidate_events)
                _validate_common(candidate, baseline)
                fleet[:], events[:] = candidate, candidate_events
                job.update(status='completed', outcome='held' if validation.issues or any(d['action'] == 'hold' for d in safe.decisions) else
                           'applied' if before else 'no-change', output=_diagnostic_json(response), appliedDecisions=safe.decisions,
                           issues=[asdict(issue) for issue in validation.issues], before=before, baseRevision=base_revision,
                           modelInputContextSha256=request['contextSha256'], localSourceSha256=request['sourceSha256'],
                           inputPages=[{'locator': p['locator'], 'textSha256': hashlib.sha256(p['text'].encode()).hexdigest(),
                                        'imageSha256': p.get('image', {}).get('sha256')} for p in request['pages']],
                           completedAt=at, lastReadAt=at, attempts=attempts, lastError=None, nextAttemptAfter=0)
                if response.get('reportDate') and request['kind'] == 'fleet-report':
                    sources[request['sourceId']]['reportDate'] = response['reportDate']
                elif request['kind'] == 'fleet-report':
                    job['diagnostics'] = ['보고서 기준일 확인 불가: 마지막 확인값을 유지합니다.']
                completed.append(job['jobId'])
                for event in events:
                    if event.get('url') == request['url'] and event.get('company') == request['company'] and 'readingDecision' not in event and 'readingIssue' not in event:
                        event.update(pendingReview=False, reviewStatus='acknowledged', sourceId=request['sourceId'])
                ledger['lastReadAt'] = at
                if before:
                    job['lastAppliedAt'] = at
                    ledger['lastAppliedAt'] = at
            except Exception as error:
                timestamp, at = stamp()
                if getattr(error, 'last_response', None) is not None:
                    job['rejectedOutput'] = _diagnostic_json(error.last_response)
                    job['attempts'] = getattr(error, 'attempts', 0)
                retries = job.get('failureCount', 0) + 1
                job.update(status='failed', failureCount=retries, lastAttemptAt=at, lastError=f'{type(error).__name__}: {error}',
                           nextAttemptAfter=timestamp + min(21600, 300 * 2 ** min(retries - 1, 7)))
                job.setdefault('attemptHistory', []).append({
                    'status': 'failed', 'at': at, 'error': job['lastError'], 'baseRevision': base_revision,
                    'contextSha256': request['contextSha256'], 'sourceSha256': request['sourceSha256']})
                failed.append(job['jobId'])
                if isinstance(error, ReaderConfigurationError):
                    # A shared decoder/schema failure cannot be repaired by
                    # sending the next document. Persist this failure/backoff;
                    # keep prior successes/cache and later pending jobs intact.
                    break
    # Acknowledge our own resulting baseline/evidence in the same publication.
    # A subsequent external baseline/source/evidence change opens a new epoch.
    for company, epoch in epochs.items():
        acknowledgements[company] = {'comparisonEpochSha256': epoch, 'stateSha256': _state(company, fleet, sources, _prior(ledger, legacy), policy_hash)}
    _validate_common(fleet, baseline)
    pending_queue = queue(active_jobs)
    fleet_hash = content_hash(fleet, length=20)
    events_hash = content_hash(events, length=20)
    old_sources = {s['company']: s for s in manifest.get('sources', [])}
    # Public sources remain exactly one report per existing company. Historical
    # documents and news belong to the journal, not the UI's four-report count.
    source_by_company = deepcopy(old_sources)
    health_by_company = {s['company']: deepcopy(s) for s in manifest.get('sourceHealth', [])}
    for collected in collection['sources']:
        if collected.get('kind') != 'fleet-report' or collected.get('discoveryMode') == PRESERVED_MATERIAL_DISCOVERY:
            continue
        s = sources[collected['sourceId']]
        previous = source_by_company.get(s['company'], {})
        source_by_company[s['company']] = {
            **previous, 'company': s['company'], 'documentUrl': s['url'],
            'indexUrl': previous.get('indexUrl', s['url']), 'discovery': s.get('discoveryMode', 'official-source'),
            'sha256': s.get('documentSha256'), 'reportDate': s.get('reportDate') or previous.get('reportDate'),
            'lastSuccessAt': s.get('lastSuccessAt'), 'error': s.get('error'), 'httpStatus': s.get('httpStatus'),
            'health': 'degraded-preserved' if s.get('error') else 'healthy',
        }
    for company, source in source_by_company.items():
        errors = [e for e in collection['errors'] if e.get('company') == company and e.get('discoveryMode') != PRESERVED_MATERIAL_DISCOVERY]
        health_by_company[company] = {
            **health_by_company.get(company, {}), 'company': company,
            'fleetReport': 'degraded-preserved' if any(e.get('kind') == 'fleet-report' for e in errors) else 'healthy',
            'newsMonitor': 'degraded-preserved' if any(e.get('kind') == 'official-news' for e in errors) else 'healthy',
            'reportDate': source.get('reportDate'), 'documentUrl': source['documentUrl'], 'sha256': source.get('sha256'),
        }
    source_rows = [source_by_company[c] for c in sorted(source_by_company)]
    warnings = [str(e.get('error', 'collection failed')) for e in collection['errors']]
    report_stages = []
    for company, source in source_by_company.items():
        current_ids = {s['sourceId'] for s in collection['sources'] if s.get('company') == company and s.get('kind') == 'fleet-report' and s.get('discoveryMode') != PRESERVED_MATERIAL_DISCOVERY}
        report_job = next((j for j in active_jobs if j['kind'] == 'fleet-report' and j['sourceId'] in current_ids), None)
        status = 'pending'
        diagnostics = []
        if report_job:
            status = report_job['status']
            diagnostics = list(report_job.get('diagnostics', []))
            if status == 'failed':
                warnings.append(company + ': ' + report_job.get('lastError', 'report reading failed'))
            elif status == 'completed' and report_job.get('output', {}).get('reportDate') is None:
                status = 'date-unverified'
            elif status == 'completed' and report_job.get('outcome') == 'held':
                status = 'held'
            warnings.extend(company + ': ' + message for message in diagnostics)
        collection_failed = any(e.get('company') == company and e.get('kind') == 'fleet-report' and e.get('discoveryMode') != PRESERVED_MATERIAL_DISCOVERY for e in collection['errors'])
        stages = {'company': company, 'url': source['documentUrl'],
                  'collectionStatus': 'failed' if collection_failed else 'collected', 'readingStatus': status,
                  'lastCollectedAt': ledger['lastCollectedAt'],
                  'lastReadAt': (report_job or {}).get('lastReadAt', source.get('lastReadAt')),
                  'lastAppliedAt': (report_job or {}).get('lastAppliedAt', source.get('lastAppliedAt')),
                  'diagnostics': diagnostics}
        source.update(stages)
        if collection_failed:
            health = 'degraded-preserved'
        elif status == 'completed':
            health = 'healthy'
        else:
            health = 'reading-pending' if status == 'pending' else 'degraded-' + status
        source['health'] = health
        health_by_company[company].update(fleetReport=health, **stages)
        report_stages.append(stages)
    timestamp, at = stamp()
    next_manifest = {**manifest, 'schemaVersion': 1, 'generatedAt': at, 'fleetFile': f'fleet.{fleet_hash}.json',
                     'eventsFile': f'events.{events_hash}.json', 'dataHash': fleet_hash, 'eventsHash': events_hash,
                     'shipCount': len(fleet), 'contractCount': sum(len(s['contracts']) for s in fleet),
                     'sources': source_rows, 'sourceCount': len(source_rows), 'eventCount': len(events),
                     'pendingEventCount': sum(bool(e.get('pendingReview')) for e in events), 'warningsCount': len(warnings),
                     'sourceHealth': [health_by_company[c] for c in sorted(health_by_company)], 'newsQueue': pending_queue,
                     'readingStatus': {'reports': report_stages, 'lastCollectedAt': ledger['lastCollectedAt'], 'lastReadAt': ledger.get('lastReadAt'),
                                       'lastAppliedAt': ledger.get('lastAppliedAt'), 'collectionErrors': collection['errors'],
                                       'readingErrors': [{'jobId': j['jobId'], 'error': j.get('lastError')} for j in active_jobs if j['status'] == 'failed']}}
    dates = [s['reportDate'] for s in source_rows if s.get('reportDate')]
    if dates:
        next_manifest['updatedAsOf'] = max(dates)
    next_manifest['stateFingerprint'] = content_hash({k: v for k, v in next_manifest.items() if k not in {'generatedAt', 'stateFingerprint'}}, length=20)
    if write:
        public = root / 'public/data'
        atomic_json(public / next_manifest['fleetFile'], fleet)
        atomic_json(public / next_manifest['eventsFile'], {'generatedAt': at, 'events': events})
        from .pipeline import _semantic_changes
        atomic_json(public / 'changes.json', {'generatedAt': at, 'fromDataHash': manifest.get('dataHash'),
                                            'toDataHash': fleet_hash, **_semantic_changes(baseline, fleet)})
        atomic_json(root / 'data/provenance/sources.json', {'generatedAt': at, 'documents': list(sources.values()), 'warnings': warnings})
        atomic_json(root / LEDGER_PATH, ledger)
        atomic_json(public / 'manifest.json', next_manifest)
        from .pipeline import _prune_obsolete_payloads
        _prune_obsolete_payloads(public, {next_manifest['fleetFile'], next_manifest['eventsFile']})
    return {'ok': True, 'changed': manifest.get('stateFingerprint') != next_manifest['stateFingerprint'], 'manifest': next_manifest,
            'modelCalls': model_calls, 'completedJobIds': completed, 'failedJobIds': failed,
            'handledJobIds': [j['jobId'] for _, j in active if j['status'] == 'completed'],
            'pendingJobIds': [j['jobId'] for j in active_jobs if j['status'] != 'completed']}
