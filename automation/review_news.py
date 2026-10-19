"""Fetch -> Codex read/compare -> validate -> replay -> publish-ready artifacts.

Run with the project's Python environment and Codex ChatGPT login. No API keys.
The scheduler calls this command; the model has no write or deployment tools.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fleet_sync.codex_reader import CodexReader
from .fleet_sync.core import dump_json
from .fleet_sync.news_jobs import eligible_events, reading_context, reading_needed
from .fleet_sync.news_review import MODEL, REVIEW_PATH, REVIEW_VERSION, event_date, read_reviews, validate_reading
from .fleet_sync.pipeline import _build_outputs, run
from .fleet_sync.sources import HttpClient, fetch_article, retrieved_at


def review_news(root: Path, reader=None, *, reread_urls: set[str] | None = None) -> dict:
    baseline, sources, _, events, _, _ = _build_outputs(root, offline=False, include_news_reviews=False)
    dates = {s.company: s.report_date for s in sources}
    reviews = read_reviews(root)
    client, reader = HttpClient(), reader or CodexReader()
    reviewed, failures = [], []
    for event in eligible_events(events, dates):
        published = event_date(event.get("publishedAt"))
        company_fleet, announcements, context_hash = reading_context(root, baseline, events, dates, reviews, event)
        prior = next((r for r in reviews if r["url"] == event["url"]), None)
        if not reading_needed(event, prior, context_hash) and not event.get("articleReadError") and event["url"] not in (reread_urls or set()):
            continue
        try:
            print(f"Reading with {MODEL} / medium / fast off: {event['title']}", flush=True)
            text, sha = fetch_article(client, event["url"])
            article = {"title": event["title"], "url": event["url"], "publishedAt": published, "text": text,
                       "previousAnnouncements": announcements}
            for attempt in range(3):
                result = reader.read(article, company_fleet)
                try:
                    validate_reading(result, text, company_fleet)
                    break
                except ValueError as exc:
                    if attempt == 2:
                        raise
                    article["validationFeedback"] = {"error": str(exc), "previousOutput": result,
                                                       "instruction": "Correct this validation failure using the original verbatim source, not invented quotes."}
            record = {"version": REVIEW_VERSION, "company": event["company"], "title": event["title"],
                      "url": event["url"], "publishedAt": published, "sourceSha256": sha, "contextSha256": context_hash,
                      "reader": {"engine": "codex-cli", "model": MODEL, "reasoningEffort": "medium", "fast": False,
                                 "reviewedAt": retrieved_at()}, "result": result}
            reviews = [r for r in reviews if r["url"] != record["url"]] + [record]
            dump_json(root / REVIEW_PATH, {"version": REVIEW_VERSION, "reviews": reviews})
            reviewed.append(event["url"])
        except Exception as exc:
            failures.append({"url": event["url"], "error": str(exc)})
            print(json.dumps(failures[-1], ensure_ascii=False), flush=True)
    final = run(root, write=True)
    return {"ok": not failures, "reviewed": reviewed, "failures": failures, "manifest": final["manifest"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--reread-url", action="append", default=[], help="re-read a discovered official article even if cached")
    args = parser.parse_args()
    result = review_news(args.root.resolve(), reread_urls=set(args.reread_url))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
