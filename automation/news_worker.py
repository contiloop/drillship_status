"""launchd entry (every 5 minutes): start a local reading run only when the server queued work.

Reads the production manifest's ``newsQueue`` (schemaVersion 2 only). When
``pendingCount > 0`` and no wait applies, it runs the publisher (temporary
clone, Codex reading, push, deployment check). Queue contents are never
executed; the publisher rediscovers sources itself. Waits: a Codex usage limit
(until its reset time), job backoff reported by the publisher (``retryAfter``,
skipped once the server queues different work) and this worker's own failure
backoff.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .fleet_sync.core import content_hash, load_json
from .fleet_sync.news_jobs import backoff_seconds
from .fleet_sync.reading_contracts import validate_news_queue
from .fleet_sync.reading_pipeline import atomic_json
from .update_news import PRODUCTION, publish

STATE_PATH = Path(".cache/news-worker-state.json")


def fetch_manifest() -> dict:
    request = Request(f"{PRODUCTION}/data/manifest.json?worker={int(time.time())}",
                      headers={"Cache-Control": "no-cache", "User-Agent": "DrillshipNewsWorker/2.0"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _load_state(path: Path) -> dict:
    try:
        state = load_json(path) if path.exists() else {}
    except ValueError:
        state = {}  # a damaged checkpoint must not strand server jobs
    return state if isinstance(state, dict) else {}


def check_once(root: Path, *, fetch=fetch_manifest, publisher=publish, now=None) -> dict:
    clock = (lambda: int(time.time())) if now is None else (lambda: int(now))
    state_path = root / STATE_PATH
    state = _load_state(state_path)
    state["lastCheckedAt"] = clock()
    queue = None
    try:
        queue = fetch().get("newsQueue")
        validate_news_queue(queue)
        if queue.get("fingerprint") != content_hash(queue["jobs"], length=64):
            raise ValueError("news queue fingerprint does not match its jobs")
        state["pendingCount"] = queue["pendingCount"]
        if queue["pendingCount"] == 0:
            state.update(failures=0, nextAttemptAfter=0, lastError=None)
            result = {"ok": True, "status": "idle", "modelInvoked": False}
        elif (state.get("usageLimitedUntil") or 0) > clock() or (
                (state.get("nextAttemptAfter") or 0) > clock() and state.get("waitQueue") == queue["fingerprint"]):
            # Usage limit: always wait. Backoff: wait unless the server queued new work.
            result = {"ok": True, "status": "waiting", "modelInvoked": False}
        else:
            publication = publisher(root)
            if not publication.get("ok"):
                raise RuntimeError(f"publication failed: {publication}")
            if publication.get("skipped"):
                result = {"ok": True, "status": "busy", "modelInvoked": False}
            else:
                if not publication.get("deploymentVerified"):
                    raise RuntimeError("publisher did not verify the deployment")
                state.update(failures=0, lastError=None, lastSuccessAt=clock(),
                             nextAttemptAfter=int(publication.get("retryAfter") or 0),
                             usageLimitedUntil=int(publication.get("usageLimitedUntil") or 0),
                             waitQueue=publication.get("queueFingerprint"))
                result = {"ok": True, "status": "processed", "publication": publication}
    except Exception as error:
        failures = int(state.get("failures") or 0) + 1
        state.update(failures=failures, lastError=f"{type(error).__name__}: {error}",
                     nextAttemptAfter=clock() + backoff_seconds(failures),
                     waitQueue=(queue or {}).get("fingerprint") if isinstance(queue, dict) else None)
        result = {"ok": False, "status": "failed", "error": state["lastError"]}
    state["status"] = result["status"]
    atomic_json(state_path, state)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    (root / ".cache").mkdir(exist_ok=True)
    with (root / ".cache/news-worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"ok":true,"status":"busy"}')
            return 0
        result = check_once(root)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
