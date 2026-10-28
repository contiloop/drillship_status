"""Publisher boundaries with controlled adapters; never pushes or contacts production."""
import io
import json
from pathlib import Path

import pytest

from automation.fleet_sync.core import content_hash, dump_json, load_json
from automation.update_news import StaleBase, publish, validate_publication_paths, verify_deployment


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


@pytest.mark.parametrize('changed', [{'sourceCount': 1}, {'newsQueue': {'pendingCount': 999}}])
def test_deployment_rejects_modified_manifest_even_when_label_and_payloads_match(tmp_path, changed):
    fleet, events = [{'id': 'rig'}], {'events': []}
    expected = dict(stateFingerprint='fingerprint', fleetFile='fleet.json', eventsFile='events.json', sourceCount=4,
                    newsQueue={'pendingCount': 0}, dataHash=content_hash(fleet, length=20), eventsHash=content_hash([], length=20))
    dump_json(tmp_path / 'public/data/fleet.json', fleet)
    dump_json(tmp_path / 'public/data/events.json', events)
    values = [{**expected, **changed}, fleet, events]
    assert not verify_deployment(tmp_path, expected, opener=lambda *a, **k: io.StringIO(json.dumps(values.pop(0))))


def test_publisher_passes_stored_results_flag_and_returns_receipts(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr('automation.update_news.command', lambda *a, **k: 'trusted-remote')
    def attempt(root, remote, cache, **kwargs):
        seen.append(kwargs['stored_results'])
        return dict(ok=True, deploymentVerified=True)
    monkeypatch.setattr('automation.update_news._publish_checkout', attempt)
    publish(tmp_path, stored_results=True)
    assert seen == [True]


def test_checkout_refuses_to_commit_when_main_moved_and_never_forces(tmp_path, monkeypatch):
    from automation.update_news import _publish_checkout
    calls = []
    def fake(args, cwd, *, capture=False):
        calls.append(args)
        if 'clone' in args:
            dump_json(Path(args[-1]) / 'public/data/manifest.json', {'stateFingerprint': 'x', 'newsQueue': {'fingerprint': 'q'}})
            return ''
        if 'automation.review_news' in args:
            assert '--cache-path' in args
            return json.dumps({'ok': True, 'completedJobIds': ['one']})
        if args[:2] == ['git', 'rev-parse']:
            return 'base'
        if args[:3] == ['git', 'diff', '--name-only'] or args[:3] == ['git', 'diff', '--cached']:
            return 'public/data/manifest.json'
        if args[:2] == ['git', 'ls-remote']:
            return 'newer\trefs/heads/main'
        if args[:2] in (['git', 'commit'], ['git', 'push']) or 'commit' in args:
            pytest.fail('must not commit or push on a stale base')
        return ''
    monkeypatch.setattr('automation.update_news.command', fake)
    with pytest.raises(StaleBase):
        _publish_checkout(tmp_path, 'remote', tmp_path / 'cache.json', deploy_timeout=1)
    assert not any('--force' in args or '-f' in args for args in calls)
