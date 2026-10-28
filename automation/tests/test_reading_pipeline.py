"""Controlled integration checks; no network, Codex, git push or live data writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.fleet_sync.core import load_json
from automation.fleet_sync.reading_contracts import context_sha256, source_sha256
from automation.fleet_sync.reading_pipeline import execute

ROOT = Path(__file__).resolve().parents[2]


def setup_root(tmp_path):
    from automation.fleet_sync.core import dump_json
    dump_json(tmp_path / 'data/data_as_of_26_01_07.json', load_json(ROOT / 'data/data_as_of_26_01_07.json'))
    return tmp_path


def collector_for(*names, revision='a', extraction='text'):
    def collect(client, **kwargs):
        materials, sources = [], []
        for name in names:
            pages = [{'locator': 'html:article', 'text': f'Complete official body {name} {extraction}'}]
            req = dict(sourceId=name, sourceSha256=source_sha256(pages), company='Noble', kind='official-news',
                       url=f'https://noblecorp.com/{name}', publishedAt='2026-10-01', pages=pages,
                       baseline=deepcopy(kwargs['baseline']), priorEvidence=deepcopy(kwargs['prior_evidence']),
                       contextSha256=context_sha256(kwargs['baseline'], kwargs['prior_evidence']),
                       readerPolicySha256=kwargs['reader_policy_sha256'])
            raw = revision * 64
            materials.append(dict(readingInput=req, documentSha256=raw, retrievedAt='2026-10-01T00:00:00Z', extractionVersion='test'))
            sources.append(dict(sourceId=name, company='Noble', kind='official-news', url=req['url'],
                                documentSha256=raw, sourceSha256=req['sourceSha256'], bodyRead=True, lastSuccessAt='2026-10-01T00:00:00Z'))
        return dict(materials=materials, sources=sources, errors=[])
    return collect


class Service:
    def __init__(self, fail=(), hold=False):
        self.calls, self.fail, self.hold = [], set(fail), hold

    def read(self, request):
        from automation.fleet_sync.reading_contracts import validate_reading_response
        self.calls.append(request['sourceId'])
        if request['sourceId'] in self.fail:
            raise RuntimeError('controlled reader failure')
        response = dict(schemaVersion=2, sourceId=request['sourceId'], sourceSha256=request['sourceSha256'],
                        company=request['company'], reportDate=None, readLocators=[p['locator'] for p in request['pages']],
                        coverage=[], vesselUpdates=[], decisions=[])
        return SimpleNamespace(response=response, validation=validate_reading_response(response, request), attempts=1)


def run(root, service, collector, **kwargs):
    return execute(root, mode='local', write=True, service=service, collector=collector,
                   recheck=lambda material: material['documentSha256'], base_revision='b'*40, **kwargs)


def test_server_only_detects_and_never_reads(tmp_path):
    root = setup_root(tmp_path)
    service = Service()
    result = execute(root, mode='server', write=True, service=service, collector=collector_for('a'))
    assert result['manifest']['newsQueue']['schemaVersion'] == 2
    assert result['manifest']['newsQueue']['pendingCount'] == 1
    assert service.calls == []


def test_batch_completion_and_platform_extraction_variance_do_not_requeue(tmp_path):
    root, service = setup_root(tmp_path), Service()
    first = run(root, service, collector_for('a', 'b'))
    assert len(first['completedJobIds']) == 2
    run(root, service, collector_for('a', 'b', extraction='different OS renderer'))
    assert service.calls == ['a', 'b']
    assert load_json(root / 'data/provenance/readings.json')['jobs'][0]['status'] == 'completed'


def test_partial_failure_is_published_and_only_failure_retries(tmp_path):
    root, service = setup_root(tmp_path), Service(fail={'b'})
    first = run(root, service, collector_for('a', 'b'), now=100)
    assert len(first['completedJobIds']) == 1 and len(first['failedJobIds']) == 1
    assert first['ok'] and first['manifest']['newsQueue']['pendingCount'] == 1
    assert run(root, service, collector_for('a', 'b'), now=200)['modelCalls'] == 0
    service.fail.clear()
    run(root, service, collector_for('a', 'b'), now=400)
    assert service.calls == ['a', 'b', 'b']


def test_source_change_during_read_never_completes_old_job(tmp_path):
    root, service = setup_root(tmp_path), Service()
    result = execute(root, mode='local', write=True, service=service, collector=collector_for('a'),
                     recheck=lambda _: 'f'*64, base_revision='b'*40)
    assert result['completedJobIds'] == []
    assert result['manifest']['newsQueue']['pendingCount'] == 1
    assert load_json(root / 'data/provenance/readings.json')['jobs'][0]['status'] == 'pending'


def test_recovery_cache_avoids_repeat_after_checkout_is_lost(tmp_path):
    root, service = setup_root(tmp_path / 'first'), Service()
    cache = tmp_path / 'recovery.json'
    run(root, service, collector_for('a'), recovery_path=cache)
    other = setup_root(tmp_path / 'fresh')
    run(other, service, collector_for('a'), recovery_path=cache)
    assert service.calls == ['a']


def empty_interpretation():
    return dict(sourceStartText=None, sourceEndText=None, startPrecision='unknown', endPrecision='unknown',
                dateBasis='unresolved', placementBasis=None, awardType='unreported', termText=None, optionText=None,
                valueText=None, rateBasis='undisclosed', reportedUsdPerDay=None, derivedUsdPerDay=None, valueUsd=None,
                baseDurationDays=None, durationAssumption=None, maximumTerm=False, includes=[], excludes=[])


class DecisionService(Service):
    def __init__(self, action='hold', invalid=False):
        super().__init__()
        self.action, self.invalid = action, invalid

    def read(self, request):
        outcome = super().read(request)
        vessel = next(v for v in request['baseline'] if v['company'] == 'Noble')
        interpretation = empty_interpretation()
        decision = dict(vesselId=vessel['id'], contractId=None, action=self.action, row=None,
                        facts=dict(client=None, region=None, interpretation=deepcopy(interpretation)),
                        reasonKo='전체 원문을 확인했지만 이 계약의 조건은 충분하지 않습니다.', evidence=[])
        if self.action == 'upsert':
            interpretation.update(sourceStartText='2027-01-01', sourceEndText='2027-12-31', startPrecision='day',
                                  endPrecision='day', dateBasis='source', awardType='signed-contract', rateBasis='reported',
                                  reportedUsdPerDay=123456.75)
            decision['facts']['interpretation'] = deepcopy(interpretation)
            decision['row'] = dict(id=None, vesselId=vessel['id'], startDate='2027-01-01', endDate='2027-12-31',
                                   client='Client', region='Brazil', status='Firm', dayRate=123456.75,
                                   interpretation=interpretation, sourceUrl=None)
            decision['evidence'] = [dict(sourceId=request['sourceId'], locator=request['pages'][0]['locator'],
                                          quote=request['pages'][0]['text'], fields=['row.startDate', 'row.endDate',
                                          'row.client', 'row.region', 'row.status', 'row.dayRate', 'row.interpretation', 'facts.interpretation'])]
        outcome.response['decisions'] = [decision]
        if self.invalid:
            outcome.response['decisions'].append({**deepcopy(decision), 'vesselId': 'foreign-vessel'})
        return outcome


def test_held_same_input_is_completed_and_new_source_context_rereads(tmp_path):
    root, service = setup_root(tmp_path), DecisionService()
    run(root, service, collector_for('a'))
    ledger = load_json(root / 'data/provenance/readings.json')
    assert ledger['jobs'][0]['status'] == 'completed' and ledger['jobs'][0]['outcome'] == 'held'
    run(root, service, collector_for('a'))
    assert service.calls == ['a']
    run(root, service, collector_for('a', 'new-evidence'))
    assert service.calls == ['a', 'a', 'new-evidence']


def test_partial_invalid_decision_preserves_valid_upsert_and_canonical_event_id(tmp_path):
    root, service = setup_root(tmp_path), DecisionService(action='upsert', invalid=True)
    before = load_json(root / 'data/data_as_of_26_01_07.json')
    first = run(root, service, collector_for('a'))
    manifest = first['manifest']
    fleet = load_json(root / 'public/data' / manifest['fleetFile'])
    assert sum(len(v['contracts']) for v in fleet) == sum(len(v['contracts']) for v in before) + 1
    row = fleet[0]['contracts'][-1]
    assert row['dayRate'] == 123456.75 and row['sourceUrl'] == 'https://noblecorp.com/a'
    event = load_json(root / 'public/data' / manifest['eventsFile'])['events'][0]
    assert event['readingDecision']['contractId'] == event['readingDecision']['row']['id'] == row['id']
    assert event['autoApplied']
    run(root, service, collector_for('a'))
    assert service.calls == ['a']
    journal = load_json(root / 'data/provenance/readings.json')
    assert journal['jobs'][0]['before'][0]['row'] is None and journal['jobs'][0]['issues']


def test_direct_run_lock_prevents_duplicate_reader(tmp_path):
    import fcntl
    root, service = setup_root(tmp_path), Service()
    path = root / '.cache/reading-pipeline.lock'
    path.parent.mkdir()
    with path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(root, service, collector_for('a'))['skipped']
    assert service.calls == []


def test_active_cli_has_no_legacy_parser_fallback(tmp_path, monkeypatch):
    from automation.fleet_sync import pipeline, news_review
    monkeypatch.setattr(pipeline, '_build_outputs', lambda *a, **k: pytest.fail('legacy parser active'))
    monkeypatch.setattr(news_review, 'reconcile_reviews', lambda *a, **k: pytest.fail('legacy reconciliation active'))
    monkeypatch.setattr('automation.fleet_sync.reading_pipeline.execute',
                        lambda root, **kwargs: execute(root, collector=collector_for(), **kwargs))
    result = pipeline.run(setup_root(tmp_path), write=False)
    assert result['modelCalls'] == 0
    with pytest.raises(pipeline.PipelineError, match='retired'):
        pipeline.run(tmp_path, write=True, offline=True)


def test_public_manifest_keeps_four_report_sources_when_news_or_history_grows(tmp_path):
    from automation.fleet_sync.core import dump_json
    from automation.fleet_sync.source_materials import source_id
    root = setup_root(tmp_path)
    companies = ('Noble', 'Seadrill', 'Transocean', 'Valaris')
    reports = [dict(company=c, documentUrl=f'https://example.com/{c}.pdf', reportDate='2026-08-01',
                    indexUrl='https://example.com', sha256='0'*64, discovery='official-index') for c in companies]
    dump_json(root / 'public/data/manifest.json', {'sources': reports, 'sourceCount': 4})
    result = execute(root, mode='server', write=True, collector=collector_for('news-a', 'news-b'))
    assert result['manifest']['sourceCount'] == 4
    assert {s['company'] for s in result['manifest']['sources']} == set(companies)
    assert len(result['manifest']['sourceHealth']) == 4
    assert result['manifest']['newsQueue']['pendingCount'] == 2


def test_user_baseline_edit_reopens_held_context_and_missing_source_preserves_contracts(tmp_path):
    from automation.fleet_sync.core import dump_json
    root, service = setup_root(tmp_path), DecisionService()
    first = run(root, service, collector_for('a'))
    path = root / 'public/data' / first['manifest']['fleetFile']
    fleet = load_json(path)
    old_count = sum(len(v['contracts']) for v in fleet)
    fleet[0]['contracts'][0]['client'] = 'User verified customer'
    from automation.fleet_sync.core import content_hash
    manifest = first['manifest']
    manifest['dataHash'] = content_hash(fleet, length=20)
    manifest['fleetFile'] = 'fleet.' + manifest['dataHash'] + '.json'
    dump_json(root / 'public/data' / manifest['fleetFile'], fleet)
    dump_json(root / 'public/data/manifest.json', manifest)
    second = run(root, service, collector_for('a'))
    assert service.calls == ['a', 'a']
    run(root, service, collector_for())
    fleet = load_json(root / 'public/data' / second['manifest']['fleetFile'])
    assert sum(len(v['contracts']) for v in fleet) == old_count


def test_nonfinite_rejected_item_is_diagnostic_json_and_visible_hold(tmp_path):
    import json
    class NonFiniteService(DecisionService):
        def read(self, request):
            outcome = super().read(request)
            invalid = deepcopy(outcome.response['decisions'][0])
            invalid['row']['dayRate'] = float('nan')
            invalid['row']['interpretation']['reportedUsdPerDay'] = float('nan')
            outcome.response['decisions'].append(invalid)
            return outcome
    root = setup_root(tmp_path)
    result = run(root, NonFiniteService(action='upsert'), collector_for('a'))
    journal_text = (root / 'data/provenance/readings.json').read_text()
    journal = json.loads(journal_text, parse_constant=lambda x: pytest.fail(f'non-JSON constant {x}'))
    assert journal['jobs'][0]['output']['decisions'][1]['row']['dayRate'] == {'invalidNumber': 'nan'}
    events = load_json(root / 'public/data' / result['manifest']['eventsFile'])['events']
    issue = next(e for e in events if 'readingIssue' in e)
    assert issue['pendingReview'] and issue['reviewStatus'] == 'conflict'
    assert '검증을 통과하지 못하여 기존 값을 보존' in issue['reason']
    assert issue['evidence'][0]['quote'] == 'Complete official body a text'
    assert 'readingDecision' not in issue and 'facts' not in issue
    assert next(e for e in events if 'readingDecision' in e)['autoApplied']


def test_explicit_withdraw_preserves_before_and_other_history(tmp_path):
    class Withdraw(Service):
        def read(self, request):
            outcome = super().read(request)
            vessel = next(v for v in request['baseline'] if v['company'] == 'Noble')
            outcome.response['decisions'] = [dict(vesselId=vessel['id'], contractId=vessel['contracts'][0]['id'], action='withdraw', row=None,
                facts=dict(client=None, region=None, interpretation=empty_interpretation()), reasonKo='공식 취소 근거 확인',
                evidence=[dict(fields=['action'], sourceId=request['sourceId'], locator=request['pages'][0]['locator'], quote=request['pages'][0]['text'])])]
            return outcome
    root, service = setup_root(tmp_path), Withdraw()
    old = load_json(root / 'data/data_as_of_26_01_07.json')
    result = run(root, service, collector_for('a'))
    fleet = load_json(root / 'public/data' / result['manifest']['fleetFile'])
    assert sum(len(v['contracts']) for v in fleet) == sum(len(v['contracts']) for v in old) - 1
    journal = load_json(root / 'data/provenance/readings.json')
    assert journal['jobs'][0]['before'][0]['row'] == old[0]['contracts'][0]
    run(root, service, collector_for('a'))
    assert service.calls == ['a']


def test_corrupted_recovery_cache_is_discarded_without_losing_job(tmp_path):
    root, service = setup_root(tmp_path), Service()
    cache = tmp_path / 'recovery.json'
    cache.write_text('{interrupted')
    result = run(root, service, collector_for('a'), recovery_path=cache)
    assert len(result['completedJobIds']) == 1 and service.calls == ['a']


def test_symlink_ledger_is_rejected_before_reading_source_or_model(tmp_path):
    root, service = setup_root(tmp_path / 'project'), Service()
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    ledger = root / 'data/provenance/readings.json'
    ledger.parent.mkdir()
    ledger.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        run(root, service, collector_for('a'))
    assert service.calls == [] and outside.read_text() == '{}'


def test_long_reader_failure_backoff_starts_when_failure_happens(tmp_path, monkeypatch):
    clock_value = [100]
    monkeypatch.setattr('automation.fleet_sync.reading_pipeline.time.time', lambda: clock_value[0])
    class SlowFailure(Service):
        def read(self, request):
            clock_value[0] = 10000
            raise RuntimeError('failed after a long model run')
    root = setup_root(tmp_path)
    run(root, SlowFailure(), collector_for('a'))
    job = load_json(root / 'data/provenance/readings.json')['jobs'][0]
    assert job['nextAttemptAfter'] == 10300
    assert job['lastAttemptAt'] == '1970-01-01T02:46:40+00:00'


def test_context_references_reconstruct_full_evidence_without_quadratic_copies(tmp_path):
    import json
    from automation.fleet_sync.core import dump_json
    from automation.fleet_sync.reading_pipeline import reconstruct_context
    root, service = setup_root(tmp_path), Service()
    legacy_text = 'Large retained official evidence. ' * 4000
    dump_json(root / 'data/news/reviews.json', {'reviews': [{'fullEvidence': legacy_text}]})
    run(root, service, collector_for(*(f'news-{i}' for i in range(40))))
    path = root / 'data/provenance/readings.json'
    ledger = load_json(path)
    # The large legacy object is present once, rather than once per input.
    assert path.read_text().count(legacy_text) == 1
    assert path.stat().st_size < 2_000_000
    context = reconstruct_context(ledger, ledger['jobs'][-1]['modelInputContextSha256'])
    assert context['priorEvidence'][0]['content']['reviews'][0]['fullEvidence'] == legacy_text
    assert len([e for e in context['priorEvidence'] if 'readingJobId' in e]) == 39
    digest = ledger['contexts'][ledger['jobs'][-1]['modelInputContextSha256']]['baselineRef']
    ledger['objects'][digest].append({'tampered': True})
    with pytest.raises(ValueError, match='hash mismatch'):
        reconstruct_context(ledger, ledger['jobs'][-1]['modelInputContextSha256'])


@pytest.mark.parametrize('failed', [False, True])
def test_report_collection_reading_and_unknown_date_have_distinct_public_health(tmp_path, failed):
    from automation.fleet_sync.core import dump_json
    root = setup_root(tmp_path)
    dump_json(root / 'public/data/manifest.json', {'sources': [dict(company='Noble', documentUrl='https://noblecorp.com/old', reportDate='2026-08-01')]})
    base_collector = collector_for('report')
    def collect(client, **kwargs):
        result = base_collector(client, **kwargs)
        result['materials'][0]['readingInput']['kind'] = 'fleet-report'
        result['sources'][0]['kind'] = 'fleet-report'
        return result
    class Report:
        def read(self, request):
            if failed:
                raise RuntimeError('controlled report reading failed')
            response = dict(schemaVersion=2, sourceId=request['sourceId'], sourceSha256=request['sourceSha256'], company='Noble',
                            reportDate=None, readLocators=[p['locator'] for p in request['pages']], vesselUpdates=[], decisions=[],
                            coverage=[dict(vesselId=v['id'], state='not-mentioned') for v in request['baseline'] if v['company'] == 'Noble'])
            return SimpleNamespace(response=response, validation=None, attempts=1)
    result = run(root, Report(), collect, now=100)
    manifest = result['manifest']
    assert manifest['sources'][0]['reportDate'] == '2026-08-01'
    assert manifest['sourceHealth'][0]['fleetReport'] != 'healthy'
    stages = manifest['readingStatus']['reports'][0]
    assert stages['readingStatus'] == ('failed' if failed else 'date-unverified')
    assert manifest['warningsCount'] > 0
