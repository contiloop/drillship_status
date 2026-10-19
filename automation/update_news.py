"""Reproducible local Codex scheduler entry point, isolated from user edits.

Only generated data and the source-bound review ledger may be published.
GitHub-hosted PDF polling stays independent; credentials never leave this Mac.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

PRODUCTION = "https://offshore-drillship-fleet-analyst.vercel.app"
PUBLISH_PATHS = ("public/data", "data/provenance", "data/news")


def command(args: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def publish(root: Path, *, deploy_timeout: int = 600) -> dict:
    # One lock for this repository, not a lock inside a disposable checkout.
    lock_path = root / ".cache/codex-news-update.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "skipped": "another news update is running"}
        remote = command(["git", "remote", "get-url", "origin"], root, capture=True)
        with tempfile.TemporaryDirectory(prefix="drillship-publish-") as temp:
            checkout = Path(temp) / "checkout"
            command(["git", "clone", "--quiet", "--branch", "main", "--single-branch", remote, str(checkout)], root)
            # Use this interpreter's hash-locked environment, not host Python.
            command([sys.executable, "-m", "automation.review_news"], checkout)
            command([sys.executable, "-m", "pytest", "automation/tests", "-q"], checkout)
            command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], checkout)
            command(["npm", "run", "test:ui"], checkout)
            command(["npm", "run", "build"], checkout)
            changed = command(["git", "status", "--porcelain"], checkout, capture=True)
            if not changed:
                return {"ok": True, "changed": False}
            command(["git", "add", "--", *PUBLISH_PATHS], checkout)
            staged = command(["git", "diff", "--cached", "--name-only"], checkout, capture=True).splitlines()
            if not staged or any(not any(p.startswith(prefix + "/") for prefix in PUBLISH_PATHS) for p in staged):
                raise RuntimeError("unsafe or empty data publication")
            command(["git", "-c", "user.name=contiloop", "-c", "user.email=contiloop@users.noreply.github.com",
                     "commit", "-m", "data: reconcile official news with Codex"], checkout)
            # Never force push. A concurrent PDF refresh causes a safe failure;
            # next scheduled run reclones current main and retries from evidence.
            command(["git", "push", "origin", "HEAD:main"], checkout)
            expected = json.loads((checkout / "public/data/manifest.json").read_text())
            deadline = time.monotonic() + deploy_timeout
            while time.monotonic() < deadline:
                try:
                    with urlopen(PRODUCTION + "/data/manifest.json?codex=" + expected["stateFingerprint"], timeout=20) as response:
                        actual = json.load(response)
                    if actual["stateFingerprint"] == expected["stateFingerprint"]:
                        for key in ("fleetFile", "eventsFile"):
                            with urlopen(PRODUCTION + "/data/" + actual[key], timeout=20) as response:
                                json.load(response)
                        return {"ok": True, "changed": True, "stateFingerprint": actual["stateFingerprint"], "production": PRODUCTION}
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(10)
            raise RuntimeError("data pushed but production deployment was not confirmed before timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.root.resolve()), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
