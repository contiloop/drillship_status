"""Reading pipeline with injected collector/reader/recheck/clock; no network, Codex or live data."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
from pathlib import Path

import pytest

from automation.fleet_sync import reading_pipeline, reading_service
from automation.fleet_sync.core import dump_json, load_json
from automation.fleet_sync.news_jobs import job_id, load_records, new_contract_id
from automation.fleet_sync.pipeline import IntegrityError
from automation.fleet_sync.reading_contracts import validate_news_queue
from automation.fleet_sync.reading_pipeline import execute
from automation.fleet_sync.reading_service import ReaderUsageLimited, ReadingService
from automation.fleet_sync.source_materials import source_id

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000
VESSEL = 'noble-faye-kozack'
CONTRACT = 'noble-faye-kozack-1'
OLD_NOBLE_REPORT = 'd' * 63 + '1'
OLD_ARTICLE_URL = 'https://noblecorp.com/press-release/old-award/'
OLD_ARTICLE_HASH = 'e' * 63 + '1'
REVIEWS = [
    {'company': 'Transocean', 'title': 'Transocean award', 'url': 'https://www.deepwater.com/news/detail?ID=1',
     'publishedAt': '2026-08-20', 'sourceSha256': 'f' * 64, 'reader': {'reviewedAt': '2026-10-19T16:32:53Z'},
     'result': {'vessels': [{'vessel': 'Deepwater Atlas', 'facts': {'counterparty': 'Client'}}]}},
    {'company': 'Noble', 'title': 'Noble award', 'url': 'https://noblecorp.com/press-release/reviewed/',
     'publishedAt': '2026-09-01', 'sourceSha256': 'f' * 63 + '0', 'reader': {'reviewedAt': '2026-10-19T16:40:00Z'},
     'result': {'vessels': []}},
]


@pytest.fixture
def root(tmp_path):
    """Synthetic published state on the static seed fleet (never the live generated data)."""
    return make_root(tmp_path / 'root')


def make_root(path):
    from automation.fleet_sync.core import content_hash
    fleet = load_json(ROOT / 'data/data_as_of_26_01_07.json')
    events = [{'company': 'Noble', 'title': 'Old award', 'url': OLD_ARTICLE_URL, 'publishedAt': '2026-08-01',
               'vessels': [], 'classification': 'official-news-signal', 'autoApplied': False,
               'pendingReview': False, 'reviewStatus': 'acknowledged', 'articleSha256': OLD_ARTICLE_HASH,
               'newsCategory': '계약'}]
    fleet_hash, events_hash = content_hash(fleet, length=20), content_hash(events, length=20)
    manifest = {'schemaVersion': 1, 'fleetFile': f'fleet.{fleet_hash}.json', 'eventsFile': f'events.{events_hash}.json',
                'dataHash': fleet_hash, 'eventsHash': events_hash, 'stateFingerprint': 'old',
                'sources': [{'company': 'Noble', 'indexUrl': 'https://noblecorp.com/fsr/', 'documentUrl': 'https://noblecorp.com/download/old/',
                             'reportDate': '2026-07-27', 'sha256': OLD_NOBLE_REPORT, 'discovery': 'official-index'}]}
    dump_json(path / 'public/data/manifest.json', manifest)
    dump_json(path / 'public/data' / manifest['fleetFile'], fleet)
    dump_json(path / 'public/data' / manifest['eventsFile'], {'events': events})
    dump_json(path / 'data/provenance/sources.json', {'documents': [{'company': 'Noble', 'sha256': OLD_NOBLE_REPORT}]})
    dump_json(path / 'data/news/reviews.json', {'version': 'old', 'reviews': REVIEWS})
    return path


def material(name, *, company='Noble', kind='official-news', sha='a', url=None, title=None, legacy=None, published='2026-10-20'):
    url = url or f'https://noblecorp.com/press-release/{name}/'
    digest = sha if len(sha) == 64 else sha * 64
    return dict(sourceId=source_id(company, kind, url), company=company, kind=kind, url=url, finalUrl=url,
                title=title or f'Noble announces {name}', publishedAt=published,
                pages=[{'locator': 'html:article', 'text': f'Official body of {name}'}], documentSha256=digest,
                legacyArticleSha256=legacy, retrievedAt='2026-10-28T00:00:00Z', discoveryMode='official-index',
                extractionWarnings=[])


def collector(*materials, errors=()):
    def collect(**kwargs):
        sources = [dict(sourceId=m['sourceId'], company=m['company'], kind=m['kind'], url=m['url'],
                        documentSha256=m['documentSha256'], lastSuccessAt='2026-10-28T00:00:00Z',
                        discoveryMode=m['discoveryMode'], httpStatus=200, error=None) for m in materials]
        return dict(materials=deepcopy(list(materials)), sources=sources + [dict(e) for e in errors], errors=[dict(e) for e in errors])
    return collect


def row(**changes):
    value = dict(startDate='2027-01-01', endDate='2027-12-31', dayRate=400000, client='Petrobras', region='Brazil',
                 status='Firm', interpretation={})
    value.update(changes)
    return value


def decision(action='upsert', contract=None, vessel=VESSEL, **changes):
    item = dict(vesselId=vessel, contractId=contract, action=action, row=row() if action == 'upsert' else None,
                reasonKo='근거', quote='quote', locator='html:article')
    item.update(changes)
    return item


class FakeReader:
    """Reader adapter double: ``decide(request) -> decisions`` and a fixed title selection."""

    def __init__(self, decide=lambda request: [], select=None, fail=(), usage_limit=False):
        self.decide, self.select, self.fail, self.usage_limit = decide, select, set(fail), usage_limit
        self.reads, self.triages = [], []

    def read(self, request):
        self.reads.append(deepcopy(request))
        if self.usage_limit:
            raise ReaderUsageLimited('usage limit', retry_at=datetime.fromtimestamp(NOW + 7200, timezone.utc))
        if request['url'] in self.fail:
            raise RuntimeError('controlled technical failure')
        return {'reportDate': None, 'vesselUpdates': [], 'decisions': self.decide(request)}

    def triage(self, items, *, correction=None):
        self.triages.append(deepcopy(items))
        categories = [{'id': item['id'], 'category': '실적' if 'Results' in item['title'] else '계약'} for item in items]
        if self.select is None:
            return {'selected': [item['id'] for item in items], 'categories': categories}
        return {'selected': [item['id'] for item in items if any(word in item['title'] for word in self.select)],
                'categories': categories}


def run(root, reader, collect, *, mode='local', now=NOW, recheck=None, **kwargs):
    return execute(root, mode=mode, write=True, service=ReadingService(reader), collector=collect,
                   recheck=recheck or (lambda record: record['documentSha256']), now=now,
                   cache_path=kwargs.pop('cache_path', root / '.cache/reading-responses.json'), **kwargs)


def published(root):
    manifest = load_json(root / 'public/data/manifest.json')
    fleet = load_json(root / 'public/data' / manifest['fleetFile'])
    events = load_json(root / 'public/data' / manifest['eventsFile'])['events']
    return manifest, fleet, events


def documents(root):
    return load_json(root / 'data/provenance/readings.json')['documents']


def contracts(fleet, vessel=VESSEL):
    return next(ship for ship in fleet if ship['id'] == vessel)['contracts']


# --- server mode -----------------------------------------------------------------

def test_server_queues_new_documents_without_reading_or_changing_contracts(root):
    reader = FakeReader()
    before_manifest, before_fleet, before_events = published(root)
    collect = collector(material('award'), material('report', kind='fleet-report', sha='c',
                                                    url='https://noblecorp.com/download/report/'))
    result = run(root, reader, collect, mode='server')
    manifest, fleet, events = published(root)
    queue = manifest['newsQueue']
    validate_news_queue(queue)
    assert queue['schemaVersion'] == 2 and queue['pendingCount'] == 2
    assert {job['kind'] for job in queue['jobs']} == {'official-news', 'fleet-report'}
    assert reader.reads == [] and reader.triages == [] and result['modelCalls'] == 0
    assert fleet == before_fleet and events == before_events
    assert {r['status'] for r in documents(root).values()} == {'pending'}


def test_server_records_collection_error_with_status_and_keeps_existing_report(root):
    before, _, _ = published(root)
    failure = dict(sourceId='x', company='Noble', kind='fleet-report', url='https://noblecorp.com/download/report/',
                   phase='collection', error='HTTPError: 403', httpStatus=403, lastSuccessAt='2026-10-01T00:00:00Z')
    run(root, FakeReader(), collector(errors=[failure]), mode='server')
    manifest, _, _ = published(root)
    noble = next(row for row in manifest['sources'] if row['company'] == 'Noble')
    assert noble['httpStatus'] == 403 and noble['health'] == 'degraded-preserved'
    assert noble['sha256'] == OLD_NOBLE_REPORT and noble['reportDate'] == '2026-07-27'
    assert manifest['readingStatus']['collectionErrors'][0]['lastSuccessAt'] == '2026-10-01T00:00:00Z'
    assert manifest['dataHash'] == before['dataHash']


# --- identity and re-read rule ------------------------------------------------------

def test_same_document_rerun_makes_no_model_call_and_no_duplicate(root):
    reader = FakeReader(lambda request: [decision()])
    collect = collector(material('award'))
    first = run(root, reader, collect)
    assert first['modelCalls'] == 2  # one triage + one read
    count = len(contracts(published(root)[1]))
    second = run(root, reader, collect, now=NOW + 60)
    assert second['modelCalls'] == 0 and len(reader.reads) == 1 and len(reader.triages) == 1
    assert len(contracts(published(root)[1])) == count
    assert published(root)[0]['newsQueue']['pendingCount'] == 0


def test_model_calls_count_every_format_retry(root):
    class Malformed(FakeReader):
        def read(self, request):
            output = super().read(request)
            return {'bad': True} if len(self.reads) == 1 else output
    reader = Malformed(lambda request: [decision()])
    result = run(root, reader, collector(material('award')))
    assert result['modelCalls'] == 3  # one triage + two reads
    assert run(root, reader, collector(material('award')), now=NOW + 60)['modelCalls'] == 0


def test_model_calls_count_attempts_of_a_failed_reading(root):
    class AlwaysMalformed(FakeReader):
        def read(self, request):
            super().read(request)
            return {'bad': True}
    reader = AlwaysMalformed()
    result = run(root, reader, collector(material('award')))
    assert len(reader.reads) == 3 and result['modelCalls'] == 4  # one triage + three reads
    assert len(result['failedJobIds']) == 1


def test_prompt_or_baseline_change_does_not_requeue(root, monkeypatch):
    reader = FakeReader(lambda request: [decision()])
    run(root, reader, collector(material('award')))
    monkeypatch.setattr(reading_service, 'NEWS_PROMPT', 'a different prompt')
    # A second document changes the baseline; the first is still not re-read.
    result = run(root, reader, collector(material('award'), material('second', sha='b')), now=NOW + 60)
    assert [r['url'] for r in reader.reads] == ['https://noblecorp.com/press-release/award/',
                                                'https://noblecorp.com/press-release/second/']
    assert result['modelCalls'] == 2


def test_new_document_hash_creates_a_job(root):
    reader = FakeReader()
    run(root, reader, collector(material('award')))
    result = run(root, reader, collector(material('award', sha='d')), mode='server', now=NOW + 60)
    assert result['manifest']['newsQueue']['pendingCount'] == 1
    assert result['manifest']['newsQueue']['jobs'][0]['documentSha256'] == 'd' * 64


def test_deterministic_new_contract_id_across_runs(tmp_path, root):
    reader = FakeReader(lambda request: [decision()])
    m = material('award')
    run(root, reader, collector(m))
    other = make_root(tmp_path / 'other')
    run(other, FakeReader(lambda request: [decision()]), collector(m))
    expected = new_contract_id(VESSEL, m['sourceId'], m['documentSha256'], 0)
    assert expected in {c['id'] for c in contracts(published(root)[1])}
    assert expected in {c['id'] for c in contracts(published(other)[1])}


# --- title triage ------------------------------------------------------------------

def test_unselected_article_is_excluded_without_reading_and_never_retriaged(root):
    reader = FakeReader(select=['contract'])
    collect = collector(material('agm', title='Noble announces AGM results'))
    result = run(root, reader, collect)
    assert reader.reads == [] and len(reader.triages) == 1
    record = next(iter(documents(root).values()))
    assert record['status'] == 'completed' and record['outcome'] == 'excluded'
    events = published(root)[2]
    listing = next(e for e in events if e.get('sourceId') == record['sourceId'])
    assert listing['reviewStatus'] == 'acknowledged' and listing['classification'] == 'official-news-signal'
    assert listing['title'] == 'Noble announces AGM results' and 'readingDecision' not in listing
    assert result['completedJobIds'] == [record['jobId']]
    # Rerun and even a changed body never triage it again.
    run(root, reader, collector(material('agm', title='Noble announces AGM results', sha='e')), now=NOW + 60)
    assert len(reader.triages) == 1 and reader.reads == []


def test_triage_batches_companies_in_one_call(root):
    reader = FakeReader(select=[])
    run(root, reader, collector(material('one'), material('two', company='Transocean',
                                                          url='https://www.deepwater.com/news/detail?ID=2')))
    assert len(reader.triages) == 1 and len(reader.triages[0]) == 2


# --- failures ----------------------------------------------------------------------

def test_partial_failure_saves_successes_and_backs_off_failed_job(root):
    reader = FakeReader(lambda request: [decision()], fail={'https://noblecorp.com/press-release/bad/'})
    collect = collector(material('good'), material('bad', sha='b'))
    result = run(root, reader, collect)
    docs = {r['url'].rsplit('/', 2)[-2]: r for r in documents(root).values()}
    assert docs['good']['outcome'] == 'applied' and docs['bad']['status'] == 'failed'
    assert docs['bad']['failureCount'] == 1 and docs['bad']['nextAttemptAfter'] == NOW + 300
    assert len(result['completedJobIds']) == 1 and len(result['failedJobIds']) == 1
    assert len(contracts(published(root)[1])) == 2
    assert run(root, reader, collect, now=NOW + 100)['modelCalls'] == 0
    reader.fail.clear()
    run(root, reader, collect, now=NOW + 400)
    assert sorted(r['url'].rsplit('/', 2)[-2] for r in reader.reads) == ['bad', 'bad', 'good']


def test_usage_limit_waits_without_counting_failure_and_stops_calls(root):
    reader = FakeReader(usage_limit=True)
    collect = collector(material('one'), material('two', sha='b'))
    result = run(root, reader, collect)
    assert len(reader.reads) == 1
    assert result['usageLimitedUntil'] == NOW + 7200 and result['retryAfter'] == NOW + 7200
    docs = list(documents(root).values())
    limited = [r for r in docs if r['nextAttemptAfter']]
    assert len(limited) == 1 and limited[0]['nextAttemptAfter'] == NOW + 7200
    assert all(r['failureCount'] == 0 and r['status'] == 'pending' for r in docs)
    assert published(root)[0]['readingStatus']['usageLimitedUntil'] == NOW + 7200


def test_usage_limit_during_triage_defers_all_candidates(root):
    class Limited(FakeReader):
        def triage(self, items, *, correction=None):
            raise ReaderUsageLimited('limit', retry_at=None)
    reader = Limited()
    result = run(root, reader, collector(material('one'), material('two', sha='b')))
    assert reader.reads == [] and result['usageLimitedUntil'] == NOW + 3600
    assert all(r['failureCount'] == 0 for r in documents(root).values())


def test_source_changed_during_read_is_not_applied_and_requeued(root):
    reader = FakeReader(lambda request: [decision()])
    before = len(contracts(published(root)[1]))
    m = material('award')
    run(root, reader, collector(m), recheck=lambda record: 'f' * 64)
    docs = list(documents(root).values())
    assert len(docs) == 1 and docs[0]['documentSha256'] == 'f' * 64 and docs[0]['status'] == 'pending'
    assert len(contracts(published(root)[1])) == before
    assert published(root)[0]['newsQueue']['jobs'][0]['jobId'] == job_id(m['sourceId'], 'f' * 64)


def test_integrity_failure_discards_the_whole_document(root, monkeypatch):
    real = reading_pipeline.validate_fleet
    def strict(fleet, registered):
        if any(c.get('client') == 'BREAK' for ship in fleet for c in ship['contracts']):
            raise IntegrityError('controlled integrity failure')
        return real(fleet, registered)
    monkeypatch.setattr(reading_pipeline, 'validate_fleet', strict)
    reader = FakeReader(lambda request: [decision(), decision(row=row(client='BREAK'))])
    before = published(root)[1]
    result = run(root, reader, collector(material('award')))
    assert published(root)[1] == before
    record = next(iter(documents(root).values()))
    assert record['status'] == 'failed' and 'integrity' in record['lastError']
    assert result['failedJobIds'] == [record['jobId']]


# --- applying and events -------------------------------------------------------------

def test_validation_issue_holds_only_that_item_with_conflict_event_and_open_hold(root):
    bad = decision(row=row(startDate='2027-13-01'))
    reader = FakeReader(lambda request: [decision(), bad])
    run(root, reader, collector(material('award')))
    _, fleet, events = published(root)
    assert len(contracts(fleet)) == 2
    record = next(iter(documents(root).values()))
    assert record['outcome'] == 'held' and len(record['openHolds']) == 1
    assert record['openHolds'][0]['holdReason']
    conflict = next(e for e in events if e.get('reviewStatus') == 'conflict' and 'readingDecision' in e)
    assert conflict['readingDecision']['action'] == 'hold' and conflict['readingDecision']['holdReason']
    assert conflict['readingDecision']['contractId'] is None and conflict['pendingReview'] is True
    applied = next(e for e in events if e.get('reviewStatus') == 'applied' and 'readingDecision' in e)
    assert applied['readingDecision']['contractId'] == new_contract_id(VESSEL, record['sourceId'], record['documentSha256'], 0)


def test_open_holds_go_to_next_same_company_read_then_close(root):
    reader = FakeReader(lambda request: [decision(action='hold', row=None)])
    run(root, reader, collector(material('first')))
    first = next(iter(documents(root).values()))
    assert len(first['openHolds']) == 1
    reader.decide = lambda request: []
    run(root, reader, collector(material('first'), material('second', sha='b')), now=NOW + 60)
    assert len(reader.reads[-1]['openHolds']) == 1
    assert reader.reads[-1]['openHolds'][0]['sourceId'] == first['sourceId']
    assert documents(root)[first['jobId']]['openHolds'] == []


def test_withdraw_removes_contract_and_keeps_before_value(root):
    old = next(c for c in contracts(published(root)[1]) if c['id'] == CONTRACT)
    reader = FakeReader(lambda request: [decision(action='withdraw', contract=CONTRACT, row=None)])
    run(root, reader, collector(material('cancel')))
    assert CONTRACT not in {c['id'] for c in contracts(published(root)[1])}
    record = next(iter(documents(root).values()))
    assert record['changes'] == [{'contractId': CONTRACT, 'before': old, 'reasonKo': '근거', 'quote': 'quote'}]


def test_repeated_withdraw_of_the_same_contract_is_a_no_op(root):
    old = next(c for c in contracts(published(root)[1]) if c['id'] == CONTRACT)
    withdraw = decision(action='withdraw', contract=CONTRACT, row=None)
    reader = FakeReader(lambda request: [withdraw, withdraw])
    result = run(root, reader, collector(material('cancel')))
    assert result['failedJobIds'] == [] and len(result['completedJobIds']) == 1
    assert CONTRACT not in {c['id'] for c in contracts(published(root)[1])}
    record = next(iter(documents(root).values()))
    assert record['status'] == 'completed' and record['outcome'] == 'applied'
    assert record['changes'] == [{'contractId': CONTRACT, 'before': old, 'reasonKo': '근거', 'quote': 'quote'}]


def test_response_that_fails_to_apply_is_not_replayed(root, monkeypatch):
    real = reading_pipeline.apply_reading
    def broken(*args):
        raise RuntimeError('controlled apply failure')
    monkeypatch.setattr(reading_pipeline, 'apply_reading', broken)
    reader = FakeReader(lambda request: [decision()])
    collect = collector(material('award'))
    run(root, reader, collect)
    record = next(iter(documents(root).values()))
    assert record['status'] == 'failed' and 'controlled apply failure' in record['lastError']
    assert load_json(root / '.cache/reading-responses.json')['responses'] == {}
    monkeypatch.setattr(reading_pipeline, 'apply_reading', real)
    result = run(root, reader, collect, now=NOW + 400)
    assert len(reader.reads) == 2 and result['failedJobIds'] == []


def test_upsert_existing_replaces_fields_keeps_id_and_sets_source_fields(root):
    reader = FakeReader(lambda request: [decision(contract=CONTRACT, row=row(dayRate=410000))])
    run(root, reader, collector(material('extension')))
    updated = next(c for c in contracts(published(root)[1]) if c['id'] == CONTRACT)
    assert updated['dayRate'] == 410000 and updated['vesselId'] == VESSEL
    assert updated['sourceUrl'] == 'https://noblecorp.com/press-release/extension/'
    assert updated['sourcePublishedAt'] == '2026-10-20' and updated['newsReviewed'] is True
    assert updated['interpretation']['derivedUsdPerDay'] is None


def test_event_shape_per_decision(root):
    reader = FakeReader(lambda request: [decision(), decision(action='no-change', contract=CONTRACT, row=None)])
    run(root, reader, collector(material('award')))
    events = [e for e in published(root)[2] if 'readingDecision' in e]
    assert [e['reviewStatus'] for e in events] == ['applied', 'acknowledged']
    required = {'id', 'company', 'vessel', 'vessels', 'vesselId', 'classification', 'url', 'publishedAt', 'title',
                'sourceId', 'autoApplied', 'pendingReview', 'reviewStatus', 'readingDecision'}
    assert all(required <= set(e) for e in events)
    assert events[0]['autoApplied'] and not events[1]['autoApplied']
    assert set(events[0]['readingDecision']) >= {'action', 'contractId', 'row', 'reasonKo', 'quote', 'locator'}
    assert events[1]['readingDecision'] == {'action': 'no-change', 'contractId': CONTRACT, 'row': None,
                                            'reasonKo': '근거', 'quote': 'quote', 'locator': 'html:article'}
    assert not any('facts' in e or 'evidence' in e for e in events)


def test_report_date_confirmed_only_by_the_reading(root):
    report = material('report', kind='fleet-report', sha='c', url='https://noblecorp.com/download/report/')
    class Dated(FakeReader):
        def read(self, request):
            return {**super().read(request), 'reportDate': '2026-10-27'}
    run(root, FakeReader(), collector(report), mode='server')
    assert next(r for r in published(root)[0]['sources'] if r['company'] == 'Noble')['reportDate'] == '2026-07-27'
    run(root, Dated(), collector(report), now=NOW + 60)
    noble = next(r for r in published(root)[0]['sources'] if r['company'] == 'Noble')
    assert noble['reportDate'] == '2026-10-27' and noble['health'] == 'healthy'


# --- migration and stored results ---------------------------------------------------

def test_migration_marks_previously_handled_documents_without_model_calls(root):
    old_report = material('old-report', kind='fleet-report', sha=OLD_NOBLE_REPORT, url='https://noblecorp.com/download/old/')
    old_url = material('old-url', url=OLD_ARTICLE_URL)
    reviewed = material('reviewed', url=REVIEWS[1]['url'])
    old_hash = material('moved', url='https://noblecorp.com/press-release/moved/', legacy=OLD_ARTICLE_HASH)
    reader = FakeReader(select=[])
    result = run(root, reader, collector(old_report, old_url, reviewed, old_hash))
    assert result['modelCalls'] == 0 and reader.reads == [] and reader.triages == []
    assert {(r['status'], r['outcome']) for r in documents(root).values()} == {('completed', 'migrated')}
    # Never-processed articles and changed reports still become jobs.
    new_report = material('new-report', kind='fleet-report', sha='9', url='https://noblecorp.com/download/new/')
    result = run(root, reader, collector(old_report, old_url, reviewed, old_hash, new_report, material('fresh', sha='8')), now=NOW + 60)
    assert len(reader.triages) == 1 and [i['url'] for i in reader.triages[0]] == ['https://noblecorp.com/press-release/fresh/']
    assert [r['url'] for r in reader.reads] == ['https://noblecorp.com/download/new/']


def test_html_report_migrates_by_its_old_raw_byte_hash(root):
    html_report = material('old-html-report', kind='fleet-report', sha='7', url='https://noblecorp.com/download/old/')
    html_report['rawSha256'] = OLD_NOBLE_REPORT
    reader = FakeReader()
    result = run(root, reader, collector(html_report))
    assert result['modelCalls'] == 0 and reader.reads == []
    assert [(r['status'], r['outcome']) for r in documents(root).values()] == [('completed', 'migrated')]
    changed = material('changed-html-report', kind='fleet-report', sha='6', url='https://noblecorp.com/download/changed/')
    changed['rawSha256'] = '5' * 64
    run(root, reader, collector(html_report, changed), now=NOW + 60)
    assert [r['url'] for r in reader.reads] == ['https://noblecorp.com/download/changed/']


def test_old_schema_record_file_keeps_completed_hashes(root):
    m = material('award')
    old = {'schemaVersion': 2, 'jobs': [{'jobId': 'reading-x', 'sourceId': m['sourceId'], 'company': 'Noble',
                                         'kind': 'official-news', 'documentSha256': m['documentSha256'],
                                         'status': 'completed', 'outcome': 'applied'},
                                        {'jobId': 'reading-y', 'sourceId': 'other', 'company': 'Noble',
                                         'kind': 'official-news', 'documentSha256': 'b' * 64, 'status': 'pending'}],
           'sources': {m['sourceId']: {'url': m['url'], 'title': 'Award'}}, 'contexts': {}, 'objects': {}}
    dump_json(root / 'data/provenance/readings.json', old)
    reader = FakeReader()
    assert run(root, reader, collector(m))['modelCalls'] == 0
    assert load_json(root / 'data/provenance/readings.json')['schemaVersion'] == 3
    with pytest.raises(ValueError):
        load_records({'schemaVersion': 9})


def test_stored_results_run_once_with_stored_input(root):
    reviews = REVIEWS
    transocean = next(s for s in published(root)[1] if s['company'] == 'Transocean')
    def decide(request):
        return [decision(vessel=transocean['id'])] if request['company'] == 'Transocean' else []
    reader = FakeReader(decide)
    result = run(root, reader, collector(), stored_results=True)
    assert len(reader.reads) == len(reviews) and reader.triages == []
    assert all(r['inputMode'] == 'stored-result' and [p['locator'] for p in r['pages']] == ['stored-review'] for r in reader.reads)
    records = [r for r in documents(root).values() if r['sourceId'].startswith('stored:')]
    assert len(records) == len(reviews) and {r['outcome'] for r in records} == {'stored-result'}
    rows = [c for c in contracts(published(root)[1], transocean['id']) if c.get('inputMode') == 'stored-result']
    assert rows and all(c['storedReading']['reviewedAt'] for c in rows)
    assert result['modelCalls'] == len(reviews)
    assert run(root, reader, collector(), stored_results=True, now=NOW + 60)['modelCalls'] == 0


# --- operational safety -------------------------------------------------------------

def test_response_cache_survives_a_discarded_clone(tmp_path, root):
    cache = tmp_path / 'cache.json'
    reader = FakeReader(lambda request: [decision()])
    run(root, reader, collector(material('award')), cache_path=cache)
    fresh = make_root(tmp_path / 'fresh')
    result = run(fresh, reader, collector(material('award')), cache_path=cache)
    assert result['modelCalls'] == 0 and len(reader.reads) == 1


def test_pipeline_lock_skips_a_second_run(root):
    lock_path = root / '.cache/reading-pipeline.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(root, FakeReader(), collector(material('a')))['skipped']


def test_symlinked_record_file_is_refused(tmp_path, root):
    target = tmp_path / 'elsewhere.json'
    dump_json(target, {})
    (root / 'data/provenance/readings.json').symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        run(root, FakeReader(), collector(material('a')))


def test_old_record_files_are_never_written(root):
    paths = [root / 'data/provenance/sources.json', root / 'data/news/reviews.json']
    before = [p.read_bytes() for p in paths]
    run(root, FakeReader(lambda request: [decision()]), collector(material('a')))
    assert [p.read_bytes() for p in paths] == before
    assert not (root / 'data/provenance/observations.json').exists()


# --- news-list categories (R5) --------------------------------------------------------

def test_events_carry_categories_and_untagged_old_events_are_tagged_once_from_titles(root):

    manifest, fleet, events = published(root)
    events.append({'company': 'Noble', 'title': 'Noble Reports Third Quarter Results', 'url': 'https://noblecorp.com/r3',
                   'publishedAt': '2026-10-27', 'vessels': [], 'classification': 'official-news-signal',
                   'autoApplied': False, 'pendingReview': False, 'reviewStatus': 'acknowledged'})
    events.append({'company': 'Noble', 'title': 'Fleet report', 'url': 'https://noblecorp.com/fsr', 'publishedAt': None,
                   'vessels': [], 'classification': 'official-report-reading', 'autoApplied': False,
                   'pendingReview': False, 'reviewStatus': 'acknowledged'})
    from automation.fleet_sync.core import content_hash, dump_json
    events_hash = content_hash(events, length=20)
    (root / 'public/data' / manifest['eventsFile']).unlink()
    manifest.update(eventsFile=f'events.{events_hash}.json', eventsHash=events_hash)
    dump_json(root / 'public/data' / manifest['eventsFile'], {'events': events})
    dump_json(root / 'public/data/manifest.json', manifest)
    reader = FakeReader()
    run(root, reader, lambda **_: {'materials': [], 'sources': [], 'errors': []})
    assert len(reader.triages) == 1 and [item['title'] for item in reader.triages[0]] == ['Noble Reports Third Quarter Results']
    assert all('body' not in item and 'text' not in item for item in reader.triages[0])
    tagged = {e['url']: e['newsCategory'] for e in published(root)[2]}
    assert tagged['https://noblecorp.com/r3'] == '실적' and tagged['https://noblecorp.com/fsr'] == 'Fleet Status'
    rerun = FakeReader()
    assert run(root, rerun, lambda **_: {'materials': [], 'sources': [], 'errors': []})['modelCalls'] == 0
    assert rerun.triages == []


def test_triage_category_outside_the_tag_set_becomes_other():
    from automation.fleet_sync.reading_contracts import triage_categories
    response = {'selected': [], 'categories': [{'id': 'a', 'category': 'Weird'}, {'id': 'b', 'category': '일정'}]}
    assert triage_categories(response, ['a', 'b', 'c']) == {'a': '기타', 'b': '일정', 'c': '기타'}
