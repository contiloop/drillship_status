"""Document records and the job queue (pure domain logic; no I/O).

A document is identified by ``sourceId`` (company + kind + URL) and its revision
by ``documentSha256`` (content fingerprint: PDF bytes, HTML article text). One record exists per
(sourceId, documentSha256); ``jobId`` is derived from that pair. A revision
whose record is completed (read, migrated or excluded by title triage) never
produces another job or model call. Prompt or baseline changes do not affect
identity, so they never re-queue anything.

Record file (``data/provenance/readings.json``)::

    {"schemaVersion": 3, "lastCollectedAt", "lastReadAt", "lastAppliedAt",
     "collectionErrors": [...],
     "documents": {jobId: {jobId, sourceId, company, kind, url, title?,
                           publishedAt?, documentSha256,
                           status: pending|failed|completed,
                           outcome: applied|no-change|held|excluded|migrated|stored-result|null,
                           lastReadAt, lastError?, failureCount, nextAttemptAfter,
                           openHolds: [...], changes: [{contractId|vesselId, before, reasonKo?, quote?}],
                           lastSuccessAt?, reportDate?, selected?, inputMode?}}}
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Iterable

from .core import content_hash
from .reading_contracts import validate_news_queue

RECORD_SCHEMA_VERSION = 3
OUTCOMES = ("applied", "no-change", "held", "excluded", "migrated", "stored-result")
BACKOFF_BASE_SECONDS = 300
BACKOFF_MAX_SECONDS = 6 * 3600
USAGE_LIMIT_DEFAULT_WAIT_SECONDS = 3600
STORED_PREFIX = "stored:"


def job_id(source_id: str, document_sha256: str) -> str:
    return "job-" + hashlib.sha256(f"{source_id}\n{document_sha256}".encode()).hexdigest()[:32]


def new_contract_id(vessel_id: str, source_id: str, document_sha256: str, index: int) -> str:
    """Deterministic id for a contract created by decision ``index`` of one document."""
    return f"{vessel_id}-r{content_hash([source_id, document_sha256, index], length=12)}"


def backoff_seconds(failure_count: int) -> int:
    """5 minutes doubling per consecutive failure, capped at 6 hours."""
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** max(0, min(failure_count - 1, 16)))


def empty_records() -> dict:
    return {"schemaVersion": RECORD_SCHEMA_VERSION, "lastCollectedAt": None, "lastReadAt": None,
            "lastAppliedAt": None, "collectionErrors": [], "documents": {}}


def new_record(*, source_id: str, company: str, kind: str, url: str, document_sha256: str,
               title: str | None = None, published_at: str | None = None, status: str = "pending",
               outcome: str | None = None) -> dict:
    return {"jobId": job_id(source_id, document_sha256), "sourceId": source_id, "company": company,
            "kind": kind, "url": url, "title": title, "publishedAt": published_at,
            "documentSha256": document_sha256, "status": status, "outcome": outcome,
            "lastReadAt": None, "lastError": None, "failureCount": 0, "nextAttemptAfter": 0,
            "openHolds": [], "changes": []}


def load_records(raw: Any) -> dict:
    """Accept the current schema, migrate the old schemaVersion 2 journal, else reject.

    The old journal ({jobs, sources, contexts, objects, ...}) was never
    published; only its completed document hashes are kept (as completed
    records with their old outcome), everything else is discarded.
    """
    if raw is None:
        return empty_records()
    if not isinstance(raw, dict):
        raise ValueError("record file must be a JSON object")
    if raw.get("schemaVersion") == RECORD_SCHEMA_VERSION and isinstance(raw.get("documents"), dict):
        records = {**empty_records(), **raw}
        for key, record in records["documents"].items():
            if not isinstance(record, dict) or record.get("jobId") != key:
                raise ValueError(f"record {key} is malformed")
        return records
    if raw.get("schemaVersion") == 2 and isinstance(raw.get("jobs"), list):
        records = empty_records()
        sources = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
        for job in raw["jobs"]:
            if not isinstance(job, dict) or job.get("status") != "completed":
                continue
            source = sources.get(job.get("sourceId"), {})
            if not all(isinstance(job.get(k), str) for k in ("sourceId", "company", "kind", "documentSha256")):
                continue
            outcome = job.get("outcome") if job.get("outcome") in OUTCOMES else "migrated"
            record = new_record(source_id=job["sourceId"], company=job["company"], kind=job["kind"],
                                url=source.get("url") or job.get("url") or "", document_sha256=job["documentSha256"],
                                title=source.get("title") or job.get("title"), published_at=source.get("publishedAt"),
                                status="completed", outcome=outcome)
            record["lastReadAt"] = job.get("completedAt")
            records["documents"][record["jobId"]] = record
        return records
    raise ValueError("unsupported record file schema; move it aside to start fresh")


def by_source(records: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for record in records["documents"].values():
        grouped.setdefault(record["sourceId"], []).append(record)
    return grouped


def previous_sources(records: dict) -> dict[str, dict]:
    """Last known fetch facts per sourceId, for the collector."""
    result: dict[str, dict] = {}
    for source, items in by_source(records).items():
        latest = max(items, key=lambda r: (r.get("lastSuccessAt") or "", r.get("lastReadAt") or ""))
        result[source] = {key: latest.get(key) for key in ("lastSuccessAt", "documentSha256", "title", "publishedAt")}
    return result


def open_jobs(records: dict) -> list[dict]:
    return [r for r in records["documents"].values() if r["status"] != "completed"]


def is_ready(record: dict, now: int) -> bool:
    return record["status"] != "completed" and (record.get("nextAttemptAfter") or 0) <= now


@dataclass
class LegacyIndex:
    """What the old pipeline already handled (read-only migration input)."""

    report_hashes: set[tuple[str, str]] = field(default_factory=set)  # (company, raw sha256)
    article_urls: set[str] = field(default_factory=set)
    article_hashes: set[str] = field(default_factory=set)  # old article text hashes

    @classmethod
    def build(cls, *, old_sources: Any, old_reviews: Any, events: Iterable[dict]) -> "LegacyIndex":
        index = cls()
        for document in (old_sources or {}).get("documents", []) if isinstance(old_sources, dict) else []:
            if isinstance(document, dict) and isinstance(document.get("sha256"), str):
                index.report_hashes.add((document.get("company"), document["sha256"]))
        reviews = old_reviews.get("reviews", []) if isinstance(old_reviews, dict) else []
        for review in reviews:
            if isinstance(review, dict) and isinstance(review.get("url"), str):
                index.article_urls.add(review["url"])
                if isinstance(review.get("sourceSha256"), str):
                    index.article_hashes.add(review["sourceSha256"])
        for event in events:
            # Events written by this pipeline carry sourceId; older ones do not.
            if not isinstance(event, dict) or "sourceId" in event or not isinstance(event.get("url"), str):
                continue
            index.article_urls.add(event["url"])
            if isinstance(event.get("articleSha256"), str):
                index.article_hashes.add(event["articleSha256"])
        return index

    def handled(self, material: dict) -> bool:
        if material["kind"] == "fleet-report":
            return (material["company"], material["documentSha256"]) in self.report_hashes
        urls = {material.get("url"), material.get("finalUrl")}
        return bool(urls & self.article_urls) or (material.get("legacyArticleSha256") in self.article_hashes)


def register_material(records: dict, material: dict, legacy: LegacyIndex, *, now_iso: str) -> dict | None:
    """Record one fetched document; returns its record, or None when nothing is queued.

    * same (sourceId, documentSha256) already recorded -> unchanged (no job)
    * news whose title triage excluded it before -> never re-triaged (R7)
    * first sighting that the old pipeline handled -> completed / migrated
    * otherwise -> pending job; older unfinished revisions of the same source
      are dropped (nothing was applied from them)
    """
    documents = records["documents"]
    identity = job_id(material["sourceId"], material["documentSha256"])
    existing = documents.get(identity)
    if existing is not None:
        existing["lastSuccessAt"] = now_iso
        for key, name in (("title", "title"), ("publishedAt", "publishedAt")):
            if material.get(name) and not existing.get(key):
                existing[key] = material[name]
        return existing
    siblings = [r for r in documents.values() if r["sourceId"] == material["sourceId"]]
    if material["kind"] == "official-news" and any(r.get("outcome") == "excluded" for r in siblings):
        return None
    record = new_record(source_id=material["sourceId"], company=material["company"], kind=material["kind"],
                        url=material["url"], document_sha256=material["documentSha256"],
                        title=material.get("title"), published_at=material.get("publishedAt"))
    record["lastSuccessAt"] = now_iso
    if not siblings and legacy.handled(material):
        record.update(status="completed", outcome="migrated")
    for sibling in siblings:
        if sibling["status"] != "completed":
            del documents[sibling["jobId"]]
    documents[identity] = record
    return record


def mark_completed(record: dict, *, outcome: str, now_iso: str) -> None:
    record.update(status="completed", outcome=outcome, lastReadAt=now_iso, lastError=None, nextAttemptAfter=0)


def mark_failed(record: dict, error: str, *, now: int) -> None:
    """Technical failure: count it and back off (5 min doubling, max 6 h)."""
    record["failureCount"] = int(record.get("failureCount") or 0) + 1
    record.update(status="failed", lastError=error, nextAttemptAfter=now + backoff_seconds(record["failureCount"]))


def mark_usage_limited(record: dict, error: str, *, retry_at: float | None, now: int) -> int:
    """Usage limit: not a failure; wait until the reset time. Returns that time."""
    until = int(retry_at) if retry_at else now + USAGE_LIMIT_DEFAULT_WAIT_SECONDS
    record.update(lastError=error, nextAttemptAfter=max(until, now))
    return record["nextAttemptAfter"]


def build_queue(records: dict) -> dict:
    jobs = sorted(({"jobId": r["jobId"], "kind": r["kind"], "company": r["company"], "url": r["url"],
                    "documentSha256": r["documentSha256"]} for r in open_jobs(records)), key=lambda j: j["jobId"])
    queue = {"schemaVersion": 2, "pendingCount": len(jobs), "fingerprint": content_hash(jobs, length=64), "jobs": jobs}
    validate_news_queue(queue)
    return queue
