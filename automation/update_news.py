"""Isolated local publisher; only generated JSON and the readings journal leave it."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from .fleet_sync.core import content_hash

PRODUCTION = 'https://offshore-drillship-fleet-analyst.vercel.app'
PUBLISH_PATHS = ('public/data', 'data/provenance', 'data/news')


class StaleBase(RuntimeError):
    pass


def command(args: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ''


def validate_publication_paths(checkout: Path, paths: list[str]) -> None:
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not name.endswith('.json') or not any(name.startswith(prefix + '/') for prefix in PUBLISH_PATHS):
            raise ValueError(f'unsafe publication path: {name}')
        target = checkout / name
        if any(p.is_symlink() for p in [target, *target.parents] if p != checkout.parent):
            raise ValueError(f'symlink publication path: {name}')
        if target.exists() and not target.is_file():
            raise ValueError(f'non-file publication path: {name}')


def verify_deployment(checkout: Path, expected: dict, *, opener=urlopen) -> bool:
    """Check the actual payload content, not merely a matching manifest label."""
    suffix = '?codex=' + expected['stateFingerprint']
    with opener(PRODUCTION + '/data/manifest.json' + suffix, timeout=20) as response:
        actual = json.load(response)
    if actual.get('stateFingerprint') != expected['stateFingerprint']:
        return False
    for key, hash_key in (('fleetFile', 'dataHash'), ('eventsFile', 'eventsHash')):
        filename = actual.get(key)
        if filename != expected.get(key) or not isinstance(filename, str) or PurePosixPath(filename).name != filename:
            return False
        local = json.loads((checkout / 'public/data' / filename).read_text())
        with opener(PRODUCTION + '/data/' + filename + suffix, timeout=20) as response:
            remote = json.load(response)
        if remote != local:
            return False
        payload = remote if key == 'fleetFile' else remote.get('events')
        if content_hash(payload, length=20) != expected[hash_key]:
            return False
    return True


def verify_publication_sources(checkout: Path, base: str, *, recheck=None) -> None:
    """Recheck original bytes after tests/build, immediately before committing."""
    from .fleet_sync.reading_pipeline import _verify_source
    journal = json.loads((checkout / 'data/provenance/readings.json').read_text())
    recheck = recheck or _verify_source
    checked = set()
    for job in journal['jobs']:
        if job['status'] != 'completed' or job.get('baseRevision') != base:
            continue
        identity = (job['sourceId'], job['documentSha256'])
        if identity in checked:
            continue
        source = journal['sources'][job['sourceId']]
        material = {'readingInput': {'company': source['company'], 'url': source.get('finalUrl') or source['url']}}
        if recheck(material) != job['documentSha256']:
            raise StaleBase('source changed after reading/build; retry against current original bytes')
        checked.add(identity)


def _publish_checkout(root: Path, remote: str, recovery: Path, *, deploy_timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix='drillship-publish-') as temp:
        checkout = (Path(temp) / 'checkout').resolve()
        command(['git', 'clone', '--quiet', '--branch', 'main', '--single-branch', remote, str(checkout)], root)
        base = command(['git', 'rev-parse', 'HEAD'], checkout, capture=True)
        raw = command([sys.executable, '-m', 'automation.review_news', '--recovery-path', str(recovery)], checkout, capture=True)
        review = json.loads(raw)
        if review.get('skipped'):
            return review
        command([sys.executable, '-m', 'pytest', 'automation/tests', '-q'], checkout)
        command(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], checkout)
        command(['npm', 'run', 'test:ui'], checkout)
        command(['npm', 'run', 'build'], checkout)
        tracked = command(['git', 'diff', '--name-only', '--no-renames', '-z', 'HEAD'], checkout, capture=True).split('\0')
        untracked = command(['git', 'ls-files', '--others', '--exclude-standard', '-z'], checkout, capture=True).split('\0')
        changed = [p for p in [*tracked, *untracked] if p]
        validate_publication_paths(checkout, changed)
        receipts = {'completedJobIds': review.get('handledJobIds', review.get('completedJobIds', [])),
                    'failedJobIds': review.get('failedJobIds', []), 'pendingJobIds': review.get('pendingJobIds', [])}
        expected = json.loads((checkout / 'public/data/manifest.json').read_text())
        if changed:
            verify_publication_sources(checkout, base)
            command(['git', 'add', '--', *PUBLISH_PATHS], checkout)
            staged = command(['git', 'diff', '--cached', '--name-only', '-z'], checkout, capture=True).split('\0')
            validate_publication_paths(checkout, [p for p in staged if p])
            command(['git', 'diff', '--cached', '--check'], checkout)
            latest = command(['git', 'ls-remote', 'origin', 'refs/heads/main'], checkout, capture=True).split()[0]
            if latest != base:
                raise StaleBase('main changed during reading; retry against current main')
            command(['git', '-c', 'user.name=contiloop', '-c', 'user.email=contiloop@users.noreply.github.com',
                     'commit', '-m', 'data: apply validated official document readings'], checkout)
            try:
                command(['git', 'push', 'origin', 'HEAD:main'], checkout)
            except subprocess.CalledProcessError:
                latest = command(['git', 'ls-remote', 'origin', 'refs/heads/main'], checkout, capture=True).split()[0]
                if latest != base:
                    raise StaleBase('main raced the non-force publication')
                raise
        deadline = time.monotonic() + deploy_timeout
        while time.monotonic() < deadline:
            try:
                if verify_deployment(checkout, expected):
                    return {'ok': True, 'changed': bool(changed), 'deploymentVerified': True,
                            'stateFingerprint': expected['stateFingerprint'], 'production': PRODUCTION, **receipts}
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(10)
        # No receipts escape this function until payload verification succeeds.
        raise RuntimeError('production manifest/payload verification timed out; jobs are not acknowledged')


def publish(root: Path, *, deploy_timeout: int = 600) -> dict:
    root = root.resolve()
    lock_path = root / '.cache/codex-news-update.lock'
    from .fleet_sync.reading_pipeline import _assert_no_links
    _assert_no_links(lock_path)
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': True, 'skipped': 'another document publication is running'}
        remote = command(['git', 'remote', 'get-url', 'origin'], root, capture=True)
        recovery = root / '.cache/readings-recovery.json'
        for attempt in range(3):
            try:
                return _publish_checkout(root, remote, recovery, deploy_timeout=deploy_timeout)
            except StaleBase:
                if attempt == 2:
                    raise
        raise AssertionError('unreachable')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.root.resolve()), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
