"""Publisher boundaries with controlled adapters; never pushes or contacts production."""
import io
import json
from pathlib import Path

import pytest

from automation.fleet_sync.core import content_hash, dump_json, load_json
from automation.news_worker import check_once
from automation.update_news import StaleBase, publish, validate_publication_paths, verify_deployment


def queue(*ids):
    jobs = [dict(jobId=i, kind='official-news', company='Noble', url='https://noblecorp.com/' + i,
                 sourceSha256='a'*64, contextSha256='b'*64, readerPolicySha256='c'*64) for i in ids]
    return dict(newsQueue=dict(schemaVersion=2, jobs=jobs, pendingCount=len(jobs), fingerprint=content_hash(jobs, length=64)))


def test_v2_per_job_receipts_keep_success_across_partial_failure_and_new_queue(tmp_path):
    payload = queue('one', 'two')
    first = lambda _: dict(ok=True, deploymentVerified=True, completedJobIds=['one'], failedJobIds=['two'])
    assert check_once(tmp_path, fetch=lambda: payload, publisher=first, now=100)['status'] == 'processed'
    state = load_json(tmp_path / '.cache/news-worker-state.json')
    assert state['jobs']['one']['status'] == 'completed' and state['jobs']['two']['status'] == 'failed'
    def no_call(_):
        pytest.fail('backoff should not publish')
    assert check_once(tmp_path, fetch=lambda: payload, publisher=no_call, now=200)['status'] == 'retry-backoff'
    payload = queue('one', 'two', 'three')
    completed = lambda _: dict(ok=True, deploymentVerified=True, completedJobIds=['two', 'three'])
    assert check_once(tmp_path, fetch=lambda: payload, publisher=completed, now=400)['status'] == 'processed'
    assert check_once(tmp_path, fetch=lambda: payload, publisher=no_call, now=500)['status'] == 'already-handled'


def test_unverified_deployment_never_acknowledges_any_v2_job(tmp_path):
    payload = queue('one')
    result = check_once(tmp_path, fetch=lambda: payload, publisher=lambda _: dict(ok=True, completedJobIds=['one']), now=100)
    assert not result['ok']
    assert load_json(tmp_path / '.cache/news-worker-state.json')['jobs']['one']['status'] == 'failed'


def test_v1_checkpoint_does_not_ack_v2_and_job_url_never_becomes_command(tmp_path):
    payload = queue('one')
    payload['newsQueue']['jobs'][0]['url'] = 'https://noblecorp.com/$(touch should-not-exist)'
    payload['newsQueue']['fingerprint'] = content_hash(payload['newsQueue']['jobs'], length=64)
    calls = []
    publisher = lambda root: calls.append(root) or dict(ok=True, deploymentVerified=True, completedJobIds=['one'])
    assert check_once(tmp_path, fetch=lambda: payload, publisher=publisher, now=100)['ok']
    assert calls == [tmp_path] and not (tmp_path / 'should-not-exist').exists()


@pytest.mark.parametrize('path', ['/tmp/evil.json', '../evil.json', 'public/data/../evil.json', 'package.json', 'data/news/commands.sh'])
def test_publication_allowlist_rejects_unsafe_paths(tmp_path, path):
    with pytest.raises(ValueError):
        validate_publication_paths(tmp_path, [path])


def test_publication_rejects_symlink_parent(tmp_path):
    (tmp_path / 'public').mkdir()
    (tmp_path / 'public/data').symlink_to(tmp_path / 'outside', target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        validate_publication_paths(tmp_path, ['public/data/fleet.json'])


def test_deployment_checks_payload_values_and_hashes(tmp_path):
    fleet, events = [{'id': 'rig'}], {'events': []}
    expected = dict(stateFingerprint='fingerprint', fleetFile='fleet.json', eventsFile='events.json',
                    dataHash=content_hash(fleet, length=20), eventsHash=content_hash([], length=20))
    dump_json(tmp_path / 'public/data/fleet.json', fleet)
    dump_json(tmp_path / 'public/data/events.json', events)
    values = [expected, fleet, events]
    def opener(url, timeout):
        return io.StringIO(json.dumps(values.pop(0)))
    assert verify_deployment(tmp_path, expected, opener=opener)
    values[:] = [expected, [{'id': 'tampered'}]]
    assert not verify_deployment(tmp_path, expected, opener=opener)


def test_main_race_retries_in_fresh_checkout_with_same_recovery_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('automation.update_news.command', lambda *a, **k: 'trusted-remote')
    def attempt(root, remote, cache, **kwargs):
        calls.append(cache)
        if len(calls) < 3:
            raise StaleBase('controlled main race')
        return dict(ok=True, deploymentVerified=True)
    monkeypatch.setattr('automation.update_news._publish_checkout', attempt)
    assert publish(tmp_path)['ok']
    assert len(calls) == 3 and len(set(calls)) == 1


def test_publisher_lock_prevents_second_checkout(tmp_path, monkeypatch):
    import fcntl
    path = tmp_path / '.cache/codex-news-update.lock'
    path.parent.mkdir()
    monkeypatch.setattr('automation.update_news.command', lambda *a, **k: pytest.fail('must not clone'))
    with path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert publish(tmp_path)['skipped']


def test_source_rechecked_after_build_blocks_a_late_changed_document(tmp_path):
    from automation.update_news import verify_publication_sources
    source = dict(sourceId='one', company='Noble', url='https://noblecorp.com/one', documentSha256='a'*64)
    journal = dict(sources={'one': source}, jobs=[dict(sourceId='one', documentSha256='a'*64, status='completed', baseRevision='base')])
    dump_json(tmp_path / 'data/provenance/readings.json', journal)
    with pytest.raises(StaleBase, match='source changed'):
        verify_publication_sources(tmp_path, 'base', recheck=lambda _: 'b'*64)


def test_worker_backoff_starts_after_slow_publisher_fails(tmp_path, monkeypatch):
    clock_value = [100]
    monkeypatch.setattr('automation.news_worker.time.time', lambda: clock_value[0])
    def fail(_):
        clock_value[0] = 10000
        raise RuntimeError('late deployment failure')
    check_once(tmp_path, fetch=lambda: queue('one'), publisher=fail)
    assert load_json(tmp_path / '.cache/news-worker-state.json')['jobs']['one']['nextAttemptAfter'] == 10300
