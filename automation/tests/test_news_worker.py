"""Worker trigger rules with injected manifest fetch, publisher and clock."""
from pathlib import Path

import pytest

from automation.fleet_sync.core import content_hash, dump_json, load_json
from automation.install_news_worker import LABEL, launch_agent
from automation.news_worker import check_once


def manifest(*ids, version=2):
    jobs = [dict(jobId=i, kind='official-news', company='Noble', url='https://noblecorp.com/' + i,
                 documentSha256='a' * 64) for i in ids]
    return {'newsQueue': dict(schemaVersion=version, jobs=jobs, pendingCount=len(jobs), fingerprint=content_hash(jobs, length=64))}


def never(_):
    pytest.fail('publisher/Codex must not run')


def ok(**extra):
    return lambda root: dict(ok=True, deploymentVerified=True, queueFingerprint='q', **extra)


def state(root):
    return load_json(root / '.cache/news-worker-state.json')


def test_empty_queue_never_starts_a_run(tmp_path):
    assert check_once(tmp_path, fetch=manifest, publisher=never, now=100) == {'ok': True, 'status': 'idle', 'modelInvoked': False}


def test_pending_jobs_start_one_publication(tmp_path):
    calls = []
    publisher = lambda root: calls.append(root) or ok()(root)
    assert check_once(tmp_path, fetch=lambda: manifest('one'), publisher=publisher, now=100)['status'] == 'processed'
    assert calls == [tmp_path]


def test_v1_queue_is_rejected_without_running(tmp_path):
    assert not check_once(tmp_path, fetch=lambda: manifest('one', version=1), publisher=never, now=100)['ok']


def test_tampered_fingerprint_is_rejected(tmp_path):
    payload = manifest('one')
    payload['newsQueue']['fingerprint'] = 'b' * 64
    assert not check_once(tmp_path, fetch=lambda: payload, publisher=never, now=100)['ok']


def test_usage_limit_wait_is_respected_even_for_new_work(tmp_path):
    check_once(tmp_path, fetch=lambda: manifest('one'), publisher=ok(usageLimitedUntil=5000, retryAfter=5000), now=100)
    assert check_once(tmp_path, fetch=lambda: manifest('one', 'two'), publisher=never, now=200)['status'] == 'waiting'
    assert check_once(tmp_path, fetch=lambda: manifest('one', 'two'), publisher=ok(), now=5001)['status'] == 'processed'


def test_job_backoff_waits_until_the_server_queues_different_work(tmp_path):
    payload = manifest('one')
    fingerprint = payload['newsQueue']['fingerprint']
    publisher = lambda root: dict(ok=True, deploymentVerified=True, queueFingerprint=fingerprint, retryAfter=1000)
    check_once(tmp_path, fetch=lambda: payload, publisher=publisher, now=100)
    assert check_once(tmp_path, fetch=lambda: payload, publisher=never, now=200)['status'] == 'waiting'
    assert check_once(tmp_path, fetch=lambda: manifest('one', 'two'), publisher=ok(), now=300)['status'] == 'processed'


def test_failure_backs_off_and_unverified_deployment_is_a_failure(tmp_path):
    payload = manifest('one')
    result = check_once(tmp_path, fetch=lambda: payload, publisher=lambda root: dict(ok=True), now=100)
    assert not result['ok'] and state(tmp_path)['nextAttemptAfter'] == 400
    assert check_once(tmp_path, fetch=lambda: payload, publisher=never, now=200)['status'] == 'waiting'
    assert check_once(tmp_path, fetch=lambda: payload, publisher=ok(), now=400)['status'] == 'processed'
    assert state(tmp_path)['lastError'] is None


def test_busy_publisher_keeps_work_pending(tmp_path):
    assert check_once(tmp_path, fetch=lambda: manifest('one'), publisher=lambda _: {'ok': True, 'skipped': 'locked'})['status'] == 'busy'


def test_job_url_is_never_executed(tmp_path):
    payload = manifest('one')
    payload['newsQueue']['jobs'][0]['url'] = 'https://noblecorp.com/$(touch should-not-exist)'
    payload['newsQueue']['fingerprint'] = content_hash(payload['newsQueue']['jobs'], length=64)
    assert check_once(tmp_path, fetch=lambda: payload, publisher=ok(), now=100)['ok']
    assert not (tmp_path / 'should-not-exist').exists()


def test_damaged_checkpoint_is_replaced(tmp_path):
    dump_json(tmp_path / '.cache/news-worker-state.json', [])
    assert check_once(tmp_path, fetch=manifest, publisher=never)['status'] == 'idle'
    assert state(tmp_path)['status'] == 'idle'


def test_launch_agent_runs_the_worker_every_five_minutes():
    agent = launch_agent(Path('/project'), '/project/.venv/bin/python', ['/bin', '/bin'])
    assert agent['Label'] == LABEL and agent['StartInterval'] == 300 and agent['RunAtLoad']
    assert agent['ProgramArguments'] == ['/project/.venv/bin/python', '-u', '-m', 'automation.news_worker', '--root', '/project']
    assert agent['EnvironmentVariables'] == {'PATH': '/bin'}
