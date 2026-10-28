"""Local full-document Codex reading entry point (reports, SEC HTML and news).

Partial failures are durable jobs; successful independent changes remain ready
for publication. Common artifact/build/deployment failures stop the publisher.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fleet_sync.reading_pipeline import execute


def review_news(root: Path, reader=None, *, recovery_path: Path | None = None) -> dict:
    service = None
    if reader is not None:
        from .fleet_sync.reading_service import ReadingService, build_reader_policy
        from .fleet_sync.source_materials import EXTRACTION_VERSION
        policy = build_reader_policy(extraction_version=EXTRACTION_VERSION)
        service = ReadingService(reader, policy=policy, max_attempts=3)
    return execute(root, mode='local', write=True, service=service, recovery_path=recovery_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--recovery-path', type=Path)
    args = parser.parse_args()
    result = review_news(args.root.resolve(), recovery_path=args.recovery_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
