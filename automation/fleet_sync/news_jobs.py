"""Deterministic server-side change detection, shared with the local reader.

This module never invokes Codex. Jobs are source/context hashes, not commands.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .codex_reader import PROMPT, SCHEMA
from .core import content_hash
from .news_review import REVIEW_VERSION, event_date, reconcile_reviews


def eligible_events(events: list[dict], dates: dict[str, str]) -> list[dict]:
    unique = {}
    for event in events:
        published = event_date(event.get("publishedAt"))
        if event.get("classification") == "official-news-signal" and published and published > dates[event["company"]]:
            unique.setdefault(event["url"], event)
    return sorted(unique.values(), key=lambda e: (event_date(e.get("publishedAt")), e["url"]))


def reading_context(root: Path, baseline: list[dict], events: list[dict], dates: dict[str, str], reviews: list[dict], event: dict) -> tuple[list[dict], list[dict], str]:
    published = event_date(event["publishedAt"])
    earlier = sorted((r for r in reviews if (r["publishedAt"], r["url"]) < (published, event["url"])
                      and any(e.get("url") == r["url"] and e.get("articleSha256") == r["sourceSha256"] for e in events)),
                     key=lambda r: (r["publishedAt"], r["url"]))
    fleet = copy.deepcopy(baseline)
    reconcile_reviews(root, fleet, copy.deepcopy(events), dates, reviews=earlier)
    company_fleet = [s for s in fleet if s["company"] == event["company"]]
    announcements = [{"publishedAt": r["publishedAt"], "url": r["url"], "result": r["result"]}
                     for r in earlier if r["company"] == event["company"] and r["publishedAt"] > dates[event["company"]]]
    context_hash = content_hash({"fleet": company_fleet, "announcements": announcements,
                                 "readerPolicy": content_hash({"prompt": PROMPT, "schema": SCHEMA})}, length=64)
    return company_fleet, announcements, context_hash


def reading_needed(event: dict, prior: dict | None, context_hash: str) -> bool:
    return not (prior and prior["sourceSha256"] == event.get("articleSha256")
                and prior.get("contextSha256") == context_hash and prior["version"] == REVIEW_VERSION)


def plan_news_jobs(root: Path, baseline: list[dict], events: list[dict], dates: dict[str, str], reviews: list[dict]) -> list[dict]:
    jobs = []
    for event in eligible_events(events, dates):
        # HTTP recovery belongs to the server's next collection, not an LLM.
        if event.get("articleReadError") or not event.get("articleSha256"):
            continue
        _, _, context_hash = reading_context(root, baseline, events, dates, reviews, event)
        prior = next((r for r in reviews if r["url"] == event["url"]), None)
        if reading_needed(event, prior, context_hash):
            jobs.append({"company": event["company"], "url": event["url"],
                         "sourceSha256": event["articleSha256"], "contextSha256": context_hash})
    return jobs


def queue_from_events(events: list[dict]) -> dict:
    jobs = sorted({e["readingJob"]["url"]: e["readingJob"] for e in events if e.get("readingJob")}.values(),
                  key=lambda job: job["url"])
    return {"schemaVersion": 1, "pendingCount": len(jobs), "fingerprint": content_hash(jobs, length=64), "jobs": jobs}
