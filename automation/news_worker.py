"""Consume server-detected jobs without waking a chat or polling with an LLM."""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

from .fleet_sync.core import content_hash, dump_json, load_json
from .update_news import PRODUCTION, publish


def fetch_manifest() -> dict:
    request = Request(f"{PRODUCTION}/data/manifest.json?worker={int(time.time())}",
                      headers={"Cache-Control": "no-cache", "User-Agent": "DrillshipNewsWorker/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def check_once(root: Path, *, fetch=fetch_manifest, publisher=publish, now=None) -> dict:
    """External adapters are injectable; server payloads can never supply commands."""
    timestamp = int(time.time() if now is None else now)
    state_path = root / ".cache/news-worker-state.json"
    try:
        state = load_json(state_path) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except ValueError:
        # A damaged local checkpoint cannot permanently strand server jobs.
        # Reprocessing is safe because publication rechecks the source ledger.
        state = {}
    state["lastCheckedAt"] = timestamp
    try:
        manifest = fetch()
        queue = manifest.get("newsQueue")
        if not isinstance(queue, dict) or queue.get("schemaVersion") != 1:
            raise ValueError("server manifest does not expose a supported news queue")
        jobs = queue.get("jobs")
        fingerprint = queue.get("fingerprint", "")
        if not isinstance(jobs, list) or queue.get("pendingCount") != len(jobs) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint) or content_hash(jobs, length=64) != fingerprint:
            raise ValueError("invalid server news queue")
        state["serverQueue"] = fingerprint
        state["pendingCount"] = len(jobs)
        if not jobs:
            state.update({"failures": 0, "nextAttemptAfter": 0, "lastError": None})
            result = {"ok": True, "status": "idle", "modelInvoked": False}
        elif state.get("lastHandledQueue") == fingerprint:
            result = {"ok": True, "status": "already-handled", "modelInvoked": False}
        elif state.get("failedQueue") == fingerprint and state.get("nextAttemptAfter", 0) > timestamp:
            result = {"ok": True, "status": "retry-backoff", "modelInvoked": False}
        else:
            # The publisher reclones trusted main and independently rediscovers
            # sources; it never executes instructions from the public queue.
            publication = publisher(root)
            if not publication.get("ok"):
                raise RuntimeError(f"publication failed: {publication}")
            if publication.get("skipped"):
                result = {"ok": True, "status": "busy", "modelInvoked": False}
            else:
                state.update({"lastHandledQueue": fingerprint, "lastSuccessAt": timestamp,
                              "failures": 0, "nextAttemptAfter": 0, "lastError": None})
                result = {"ok": True, "status": "processed", "publication": publication}
        state["status"] = result["status"]
    except Exception as exc:
        failures = state.get("failures", 0) + 1 if state.get("failedQueue") == state.get("serverQueue") else 1
        state.update({"status": "failed", "failures": failures, "failedQueue": state.get("serverQueue"),
                      "nextAttemptAfter": timestamp + min(21600, 300 * 2 ** min(failures - 1, 7)),
                      "lastError": str(exc)})
        result = {"ok": False, "status": "failed", "error": str(exc)}
    temporary = state_path.with_suffix(".tmp")
    dump_json(temporary, state)
    temporary.replace(state_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
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
