"""Local reading run (Mac): collect, triage titles, read pending documents with Codex.

``python -m automation.review_news`` writes the results into this checkout only.
Production publishing goes through ``python -m automation.update_news``, which
runs this module inside a temporary clone of main.

``--stored-results`` additionally queues, once, every saved review in
data/news/reviews.json as a stored-result job (completed records prevent reruns).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fleet_sync.reading_pipeline import execute


def review_news(root: Path, reader=None, *, cache_path: Path | None = None, stored_results: bool = False) -> dict:
    service = None
    if reader is not None:
        from .fleet_sync.reading_service import ReadingService
        service = ReadingService(reader)
    return execute(root, mode="local", write=True, service=service, cache_path=cache_path, stored_results=stored_results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--cache-path", type=Path, help="response cache kept outside a temporary clone")
    parser.add_argument("--stored-results", action="store_true", help="one-time recovery from data/news/reviews.json")
    args = parser.parse_args(argv)
    result = review_news(args.root.resolve(), cache_path=args.cache_path, stored_results=args.stored_results)
    result.pop("manifest", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
