"""Collection, job scheduling and the only use case that writes contract data.

``execute(root, mode="server"|"local", write=...)``

server: collect official sources (no model), register new document revisions
as jobs and publish the queue plus source status. Contract data is unchanged.

local (Mac): the same collection, then
  1. one title-triage call over untriaged pending news (unselected -> excluded,
     body never read by the model);
  2. read selected news and fleet-report jobs one at a time, each with the
     current fleet (after earlier applies in this run) as baseline and the
     company's open holds;
  3. re-fetch the source; if its bytes changed, do not apply and re-queue;
  4. apply validated items, check whole-dataset integrity (failure -> nothing
     from that document is applied, the job fails), write one event per
     decision and the document record.
A usage limit stops model calls for the rest of the run without counting a
failure. Successes are written even when other jobs fail.

External effects are ports: ``collector`` (HTTP collection), ``recheck``
(re-fetch hash), ``service`` (ReadingService over Codex) and ``now``.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import time
from typing import Any, Callable

from .core import content_hash, load_json
from .news_jobs import (
    STORED_PREFIX, LegacyIndex, build_queue, is_ready, job_id, load_records, mark_completed, mark_failed,
    mark_usage_limited, new_contract_id, new_record, open_jobs, previous_sources, register_material,
)
from .pipeline import IntegrityError, _prune_obsolete_payloads, _semantic_changes, validate_fleet
from .reading_contracts import DEFAULT_NEWS_CATEGORY, MalformedResponseError, stored_review_page, validate_reading_response

REPORT_CATEGORY = "Fleet Status"
from .reading_service import ReaderConfigurationError, ReaderUsageLimited, ReadingService
from .source_materials import PRESERVED_MATERIAL_DISCOVERY, collect_reading_materials, fetch_document_sha256
from .sources import SOURCE_SPECS, HttpClient

RECORDS_PATH = Path("data/provenance/readings.json")
OLD_SOURCES_PATH = Path("data/provenance/sources.json")
OLD_REVIEWS_PATH = Path("data/news/reviews.json")
SEED_FLEET_PATH = Path("data/data_as_of_26_01_07.json")
LOCK_PATH = Path(".cache/reading-pipeline.lock")
CACHE_PATH = Path(".cache/reading-responses.json")


# --- files -------------------------------------------------------------------

def _assert_no_links(path: Path) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"refusing symlink path: {path}")


def atomic_json(path: Path, value: Any) -> None:
    _assert_no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    _assert_no_links(temporary)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path: Path, default: Any = None) -> Any:
    _assert_no_links(path)
    return load_json(path) if path.exists() else deepcopy(default)


def _payload(root: Path, manifest: dict, key: str) -> Any:
    filename = manifest.get(key)
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
        raise ValueError(f"unsafe or missing {key}")
    payload = _read(root / "public/data" / filename)
    hash_key, data = ("dataHash", payload) if key == "fleetFile" else ("eventsHash", payload.get("events"))
    if manifest.get(hash_key) and content_hash(data, length=20) != manifest[hash_key]:
        raise ValueError(f"{key} content hash does not match manifest")
    return payload


def _json_safe(value: Any) -> Any:
    """Raw model items may hold NaN/Infinity; keep them as visible text."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalidNumber": repr(value)}
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _texts(source: dict, names=("reasonKo", "quote", "locator")) -> dict:
    return {name: source[name] for name in names if isinstance(source.get(name), str) and source[name].strip()}


# --- applying one document ---------------------------------------------------

def _contract_row(old: dict | None, proposed: dict, row_id: str, vessel_id: str, request: dict) -> dict:
    row = {**(old or {}), **deepcopy(proposed), "id": row_id, "vesselId": vessel_id, "sourceUrl": request["url"]}
    for legacy in ("datePrecision", "sourceSha256", "inputMode", "storedReading"):
        row.pop(legacy, None)
    if request.get("publishedAt"):
        row["sourcePublishedAt"] = request["publishedAt"]
    else:
        row.pop("sourcePublishedAt", None)
    if request["kind"] == "official-news":
        row["newsReviewed"] = True
    else:
        row.pop("newsReviewed", None)
    if request.get("inputMode") == "stored-result":
        row["inputMode"] = "stored-result"
        row["storedReading"] = deepcopy(request["storedReading"])
    return row


def _event(request: dict, record: dict, *, identity: str, ship: dict | None, decision: dict, status: str) -> dict:
    title = record.get("title") or (f"{request['company']} Fleet Status Report" if request["kind"] == "fleet-report"
                                    else f"{request['company']} 공식 발표")
    event = {"id": identity, "company": request["company"],
             "vessel": ship["name"] if ship else None, "vessels": [ship["name"]] if ship else [],
             "vesselId": ship["id"] if ship else None,
             "classification": "official-news-signal" if request["kind"] == "official-news" else "official-report-reading",
             "url": request["url"], "publishedAt": request.get("publishedAt"), "title": title,
             "sourceId": request["sourceId"], "autoApplied": status == "applied",
             "pendingReview": status == "conflict", "reviewStatus": status, "readingDecision": decision,
             "newsCategory": REPORT_CATEGORY if request["kind"] == "fleet-report" else record.get("newsCategory") or "계약"}
    if request.get("inputMode") == "stored-result":
        event["inputMode"] = "stored-result"
        event["storedReading"] = deepcopy(request["storedReading"])
    return event


def apply_reading(fleet: list[dict], request: dict, record: dict, response: dict, validation: Any) -> dict:
    """Apply validated items to ``fleet`` in place; return changes, holds and events.

    Held items (model "hold" or failed validation) keep existing values and
    become open holds plus a conflict event. Raises nothing itself; the caller
    checks dataset integrity afterwards on a copy.
    """
    ships = {ship["id"]: ship for ship in fleet}
    changes: list[dict] = []
    holds: list[dict] = []
    events: list[dict] = []
    applied = False
    for update in validation.vessel_updates:
        ship = ships[update["vesselId"]]
        before = {"status": ship.get("status"), "statusAsOf": ship.get("statusAsOf")}
        if before != {"status": update["status"], "statusAsOf": update["statusAsOf"]}:
            changes.append({"vesselId": ship["id"], "before": before, **_texts(update, ("reasonKo", "quote"))})
            ship.update(status=update["status"], statusAsOf=update["statusAsOf"])
            applied = True
    held_indexes = {issue.index for issue in validation.issues if issue.collection == "decisions"}
    indexes = [i for i in range(len(response["decisions"])) if i not in held_indexes]
    for index, decision in zip(indexes, validation.decisions):
        ship = ships[decision["vesselId"]]
        action, contract_id = decision["action"], decision["contractId"]
        if action == "upsert":
            contract_id = contract_id or new_contract_id(ship["id"], record["sourceId"], record["documentSha256"], index)
            old = next((row for row in ship["contracts"] if row["id"] == contract_id), None)
            row = _contract_row(old, decision["row"], contract_id, ship["id"], request)
            if old is None:
                ship["contracts"].append(row)
            else:
                ship["contracts"][ship["contracts"].index(old)] = row
            changes.append({"contractId": contract_id, "before": deepcopy(old), **_texts(decision, ("reasonKo", "quote"))})
            applied = True
        elif action == "withdraw":
            old = next(row for row in ship["contracts"] if row["id"] == contract_id)
            ship["contracts"].remove(old)
            changes.append({"contractId": contract_id, "before": deepcopy(old), **_texts(decision, ("reasonKo", "quote"))})
            applied = True
        elif action == "hold":
            holds.append({"vesselId": ship["id"], "contractId": contract_id, "sourceId": record["sourceId"],
                          "url": request["url"], **_texts(decision), **_hold_reason(decision)})
        status = {"upsert": "applied", "withdraw": "applied", "no-change": "acknowledged", "hold": "conflict"}[action]
        shown = {"action": action, "contractId": contract_id, "row": deepcopy(decision["row"]), **_texts(decision),
                 **(_hold_reason(decision) if action == "hold" else {})}
        events.append(_event(request, record, identity="reading-" + content_hash([record["jobId"], "decisions", index], 20),
                             ship=ship, decision=shown, status=status))
    for issue in validation.issues:
        if issue.collection == "reportDate":
            continue
        item = issue.item if isinstance(issue.item, dict) else {}
        ship = ships.get(item.get("vesselId"))
        if ship is not None and ship.get("company") != request["company"]:
            ship = None
        contract_id = item.get("contractId") if isinstance(item.get("contractId"), str) else None
        holds.append({"vesselId": ship["id"] if ship else None, "contractId": contract_id, "sourceId": record["sourceId"],
                      "url": request["url"], "holdReason": issue.message, "item": _json_safe(deepcopy(item)), **_texts(item)})
        shown = {"action": "hold", "contractId": contract_id, "row": None, **_texts(item), "holdReason": issue.message}
        events.append(_event(request, record, identity="reading-" + content_hash([record["jobId"], issue.collection, issue.index], 20),
                             ship=ship, decision=shown, status="conflict"))
    outcome = "held" if holds else "applied" if applied else "no-change"
    return {"changes": changes, "holds": holds, "events": events, "outcome": outcome}


def _hold_reason(decision: dict) -> dict:
    """A model hold shows its own Korean reason as the hold reason, when given."""
    return {"holdReason": decision["reasonKo"]} if decision.get("reasonKo") else {}


def _title_event(record: dict) -> dict:
    """Title + link listing for an article that produced no decision event."""
    return {"id": "news-" + content_hash(record["sourceId"], 20), "company": record["company"],
            "title": record.get("title") or record["url"], "url": record["url"], "publishedAt": record.get("publishedAt"),
            "vessels": [], "classification": "official-news-signal", "autoApplied": False,
            "pendingReview": False, "reviewStatus": "acknowledged", "sourceId": record["sourceId"],
            "newsCategory": record.get("newsCategory") or DEFAULT_NEWS_CATEGORY}


def _merge_events(events: list[dict], new: list[dict]) -> list[dict]:
    ids = {event["id"] for event in new}
    return [event for event in events if event.get("id") not in ids] + new


# --- the run -----------------------------------------------------------------

def execute(root: Path, *, mode: str, write: bool, service: ReadingService | None = None,
            collector: Callable[..., dict] | None = None, recheck: Callable[[dict], str] | None = None,
            now: Callable[[], float] | float | None = None, cache_path: Path | None = None,
            stored_results: bool = False) -> dict:
    """Run one server or local pass under the single pipeline lock."""
    if mode not in {"server", "local"}:
        raise ValueError("mode must be server or local")
    if stored_results and mode != "local":
        raise ValueError("stored-result recovery runs only in local mode")
    root = root.resolve()
    lock_path = root / LOCK_PATH
    _assert_no_links(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "skipped": "reading pipeline is locked"}
        clock = now if callable(now) else (lambda: time.time()) if now is None else (lambda: now)
        run = _Run(root, mode=mode, service=service, clock=clock,
                   collector=collector or (lambda **kw: collect_reading_materials(HttpClient(), **kw)),
                   recheck=recheck or (lambda record: fetch_document_sha256(record["company"], record["url"])),
                   cache_path=cache_path or root / CACHE_PATH)
        return run.execute(write=write, stored_results=stored_results)


class _Run:
    def __init__(self, root: Path, *, mode, service, clock, collector, recheck, cache_path):
        self.root, self.mode, self.service, self.clock = root, mode, service, clock
        self.collector, self.recheck, self.cache_path = collector, recheck, cache_path
        self.model_calls = 0
        self.completed: list[str] = []
        self.failed: list[str] = []
        self.usage_limited_until: int | None = None
        self.stop = False

    def now(self) -> int:
        return int(self.clock())

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self.now(), timezone.utc).isoformat().replace("+00:00", "Z")

    # -- load -----------------------------------------------------------------

    def _load(self) -> None:
        root = self.root
        self.manifest = _read(root / "public/data/manifest.json", {})
        if self.manifest.get("fleetFile"):
            self.fleet = _payload(root, self.manifest, "fleetFile")
            self.events = _payload(root, self.manifest, "eventsFile")["events"] if self.manifest.get("eventsFile") else []
        else:
            self.fleet, self.events = _read(root / SEED_FLEET_PATH), []
        self.registered = deepcopy(self.fleet)
        self.records = load_records(_read(root / RECORDS_PATH))
        self.old_reviews = _read(root / OLD_REVIEWS_PATH)
        self.legacy = LegacyIndex.build(old_sources=_read(root / OLD_SOURCES_PATH), old_reviews=self.old_reviews,
                                        events=self.events)

    def _load_cache(self) -> dict:
        try:
            cache = _read(self.cache_path, {})
        except (OSError, ValueError):
            cache = {}
        if not isinstance(cache, dict) or not isinstance(cache.get("responses"), dict) or not isinstance(cache.get("triage"), dict):
            cache = {"responses": {}, "triage": {}}
        if not isinstance(cache.get("categories"), dict):
            cache["categories"] = {}
        done = {key for key, record in self.records["documents"].items() if record["status"] == "completed"}
        for section in ("responses", "triage", "categories"):
            for key in [key for key in cache[section] if key in done or key not in self.records["documents"]]:
                del cache[section][key]
        return cache

    def _save_cache(self) -> None:
        atomic_json(self.cache_path, self.cache)

    # -- collection -------------------------------------------------------------

    def _collect(self) -> None:
        preserved = [{key: r.get(key) for key in ("company", "kind", "url", "title", "publishedAt")}
                     for r in open_jobs(self.records) if not r["sourceId"].startswith(STORED_PREFIX)]
        self.collection = self.collector(previous_sources=previous_sources(self.records), preserved_materials=preserved)
        now_iso = self.now_iso()
        self.materials = {m["sourceId"]: m for m in self.collection["materials"]}
        for material in self.collection["materials"]:
            register_material(self.records, material, self.legacy, now_iso=now_iso)
        self.records["lastCollectedAt"] = now_iso
        self.records["collectionErrors"] = [
            {key: error.get(key) for key in ("company", "kind", "url", "phase", "error", "httpStatus", "lastSuccessAt", "lastAttemptAt")}
            for error in self.collection["errors"]]

    def _enqueue_stored_results(self) -> None:
        reviews = self.old_reviews.get("reviews", []) if isinstance(self.old_reviews, dict) else []
        documents = self.records["documents"]
        for review in reviews:
            if not isinstance(review, dict) or not isinstance(review.get("url"), str) or review.get("company") not in {s.company for s in SOURCE_SPECS}:
                continue
            source = STORED_PREFIX + review["url"]
            if any(r["sourceId"] == source for r in documents.values()):
                continue
            digest = content_hash(stored_review_page(review)["text"], length=64)
            record = new_record(source_id=source, company=review["company"], kind="official-news", url=review["url"],
                                document_sha256=digest, title=review.get("title"), published_at=review.get("publishedAt"))
            record["inputMode"] = "stored-result"
            documents[record["jobId"]] = record

    # -- local reading --------------------------------------------------------------

    def _company_holds(self, company: str, exclude: str) -> list[dict]:
        return [deepcopy(hold) for r in self.records["documents"].values()
                if r["company"] == company and r["jobId"] != exclude for hold in r.get("openHolds") or []]

    def _triage(self) -> None:
        now = self.now()
        candidates = [r for r in self.records["documents"].values()
                      if r["kind"] == "official-news" and r.get("inputMode") != "stored-result"
                      and not r.get("selected") and is_ready(r, now)]
        if not candidates:
            return
        unknown = [r for r in candidates if r["jobId"] not in self.cache["triage"]]
        if unknown and not self.stop:
            items = [{"id": r["jobId"], "title": r.get("title") or "", "url": r["url"], "publishedAt": r.get("publishedAt")}
                     for r in unknown]
            try:
                self.model_calls += 1
                selected, categories = self.service.triage_with_categories(items)
                chosen = set(selected)
                for r in unknown:
                    self.cache["triage"][r["jobId"]] = r["jobId"] in chosen
                    self.cache["categories"][r["jobId"]] = categories.get(r["jobId"], DEFAULT_NEWS_CATEGORY)
                self._save_cache()
            except ReaderUsageLimited as error:
                self._usage_limited(unknown, error)
            except Exception as error:
                for r in unknown:
                    mark_failed(r, f"triage: {type(error).__name__}: {error}", now=self.now())
                    self.failed.append(r["jobId"])
                if isinstance(error, ReaderConfigurationError):
                    self.stop = True
        new_events = []
        for r in candidates:
            if r["jobId"] not in self.cache["triage"]:
                continue
            r["newsCategory"] = self.cache["categories"].get(r["jobId"], r.get("newsCategory") or DEFAULT_NEWS_CATEGORY)
            if self.cache["triage"][r["jobId"]]:
                r["selected"] = True
            else:
                mark_completed(r, outcome="excluded", now_iso=self.now_iso())
                self.completed.append(r["jobId"])
                if not any(e.get("url") == r["url"] and e.get("company") == r["company"] for e in self.events):
                    new_events.append(_title_event(r))
        self.events = _merge_events(self.events, new_events)

    def _tag_events(self) -> None:
        """Give every news-list event a category once, from its title only (R5)."""
        for event in self.events:
            if not event.get("newsCategory") and event.get("classification") == "official-report-reading":
                event["newsCategory"] = REPORT_CATEGORY
        untagged = [event for event in self.events if not event.get("newsCategory")]
        if not untagged or self.stop:
            return
        keys = {}
        for event in untagged:
            keys.setdefault("event-" + content_hash([event.get("company"), event.get("url"), event.get("title")], 20), []).append(event)
        items = [{"id": key, "title": group[0].get("title") or group[0].get("url") or "", "url": group[0].get("url") or "",
                  "publishedAt": group[0].get("publishedAt")} for key, group in keys.items()]
        try:
            self.model_calls += 1
            _, categories = self.service.triage_with_categories(items)
        except ReaderUsageLimited as error:
            self.usage_limited_until = max(self.usage_limited_until or 0, error.retry_at_epoch or 0)
            self.stop = True
            return
        except Exception:
            return  # untagged events still render (as 기타) and are retried next local run
        for key, group in keys.items():
            for event in group:
                event["newsCategory"] = categories.get(key, DEFAULT_NEWS_CATEGORY)

    def _usage_limited(self, records: list[dict], error: ReaderUsageLimited) -> None:
        for record in records:
            until = mark_usage_limited(record, f"usage limit: {error}", retry_at=error.retry_at_epoch, now=self.now())
            self.usage_limited_until = max(self.usage_limited_until or 0, until)
        self.stop = True

    def _request(self, record: dict) -> dict:
        request = {"sourceId": record["sourceId"], "company": record["company"], "kind": record["kind"],
                   "url": record["url"], "publishedAt": record.get("publishedAt"), "baseline": deepcopy(self.fleet),
                   "openHolds": self._company_holds(record["company"], record["jobId"])}
        if record.get("inputMode") == "stored-result":
            reviews = self.old_reviews.get("reviews", []) if isinstance(self.old_reviews, dict) else []
            review = next((r for r in reviews if isinstance(r, dict) and r.get("url") == record["url"]), None)
            if review is None:
                raise LookupError("stored review is no longer in data/news/reviews.json")
            request.update(pages=[stored_review_page(review)], inputMode="stored-result",
                           storedReading={"reviewedAt": (review.get("reader") or {}).get("reviewedAt")})
            return request
        material = self.materials.get(record["sourceId"])
        if material is None:
            error = next((e.get("error") for e in self.collection["sources"] if e.get("sourceId") == record["sourceId"]), None)
            raise LookupError(f"source could not be fetched this run: {error or 'not collected'}")
        if material["documentSha256"] != record["documentSha256"]:
            raise LookupError("fetched revision differs from the queued job")
        request["pages"] = material["pages"]
        return request

    def _response(self, record: dict, request: dict) -> Any:
        cached = self.cache["responses"].get(record["jobId"])
        if cached is not None:
            try:
                validate_reading_response(cached, request)
                return cached
            except MalformedResponseError:
                pass
        self.model_calls += 1
        outcome = self.service.read(request)
        self.cache["responses"][record["jobId"]] = _json_safe(outcome.response)
        self._save_cache()
        return outcome.response

    def _requeue_changed(self, record: dict, fresh: str) -> None:
        documents = self.records["documents"]
        del documents[record["jobId"]]
        self.cache["responses"].pop(record["jobId"], None)
        replacement = new_record(source_id=record["sourceId"], company=record["company"], kind=record["kind"],
                                 url=record["url"], document_sha256=fresh, title=record.get("title"),
                                 published_at=record.get("publishedAt"))
        replacement["lastError"] = "source changed during reading; queued as a new revision"
        documents.setdefault(replacement["jobId"], replacement)

    def _read_one(self, record: dict) -> None:
        request = self._request(record)
        response = self._response(record, request)
        # The service validated already; validate again so no port can bypass it.
        validation = validate_reading_response(response, request)
        if record.get("inputMode") != "stored-result":
            fresh = self.recheck(record)
            if fresh != record["documentSha256"]:
                self._requeue_changed(record, fresh)
                return
        candidate = deepcopy(self.fleet)
        result = apply_reading(candidate, request, record, response, validation)
        try:
            validate_fleet(candidate, self.registered)
        except IntegrityError as error:
            # Ask the model again next time instead of replaying this response.
            self.cache["responses"].pop(record["jobId"], None)
            self._save_cache()
            raise IntegrityError(f"dataset integrity failed; document not applied: {error}") from error
        self.fleet = candidate
        self.events = _merge_events(self.events, result["events"])
        if record["kind"] == "official-news" and not result["events"] and not any(
                e.get("url") == record["url"] and e.get("company") == record["company"] for e in self.events):
            self.events.append(_title_event(record))
        for other in self.records["documents"].values():
            if other["company"] == record["company"] and other is not record:
                other["openHolds"] = []
        now_iso = self.now_iso()
        outcome = "stored-result" if record.get("inputMode") == "stored-result" else result["outcome"]
        mark_completed(record, outcome=outcome, now_iso=now_iso)
        record.update(failureCount=0, openHolds=result["holds"], changes=_json_safe(result["changes"]))
        if record["kind"] == "fleet-report" and validation.report_date:
            record["reportDate"] = validation.report_date
        self.records["lastReadAt"] = now_iso
        if result["changes"]:
            self.records["lastAppliedAt"] = now_iso
        self.completed.append(record["jobId"])

    def _read_all(self) -> None:
        now = self.now()
        jobs = [r for r in self.records["documents"].values() if is_ready(r, now)
                and (r["kind"] == "fleet-report" or r.get("selected") or r.get("inputMode") == "stored-result")]
        jobs.sort(key=lambda r: (r["kind"] != "fleet-report", r.get("inputMode") == "stored-result",
                                 r.get("publishedAt") or "", r["url"]))
        for record in jobs:
            if self.stop:
                break
            try:
                self._read_one(record)
            except ReaderUsageLimited as error:
                self._usage_limited([record], error)
            except Exception as error:
                mark_failed(record, f"{type(error).__name__}: {error}", now=self.now())
                self.failed.append(record["jobId"])
                if isinstance(error, ReaderConfigurationError):
                    self.stop = True

    # -- publish ------------------------------------------------------------------

    def _report_rows(self) -> tuple[list[dict], list[dict], list[dict]]:
        previous = {row.get("company"): row for row in self.manifest.get("sources", []) if isinstance(row, dict)}
        index_urls = {spec.company: spec.index_url for spec in SOURCE_SPECS}
        rows, health, stages = [], [], []
        errors = self.collection["errors"]
        documents = self.records["documents"]
        for company in sorted({spec.company for spec in SOURCE_SPECS} | set(previous)):
            prior = previous.get(company, {})
            source = next((s for s in self.collection["sources"] if s.get("company") == company and s.get("kind") == "fleet-report"
                           and s.get("discoveryMode") != PRESERVED_MATERIAL_DISCOVERY), None)
            record = documents.get(job_id(source["sourceId"], source["documentSha256"])) if source and source.get("documentSha256") else None
            report_errors = [e for e in errors if e.get("company") == company and e.get("kind") == "fleet-report"
                             and e.get("discoveryMode") != PRESERVED_MATERIAL_DISCOVERY]
            confirmed = [r.get("reportDate") for r in documents.values()
                         if r["company"] == company and r["kind"] == "fleet-report" and r.get("reportDate")
                         and source and r["sourceId"] == source["sourceId"]]
            report_date = max(confirmed) if confirmed else prior.get("reportDate")
            if source is None or source.get("error"):
                state = "degraded-preserved"
            elif record is None or record["status"] == "pending":
                state = "reading-pending"
            elif record["status"] == "failed":
                state = "reading-failed"
            else:
                state = "held" if record.get("outcome") == "held" else "healthy"
            error = (source or {}).get("error") or next((e.get("error") for e in report_errors), None)
            http_status = (source or {}).get("httpStatus") or next((e.get("httpStatus") for e in report_errors if e.get("httpStatus")), None)
            row = {"company": company, "indexUrl": prior.get("indexUrl") or index_urls.get(company),
                   "documentUrl": (source or {}).get("url") or prior.get("documentUrl"),
                   "discovery": (source or {}).get("discoveryMode") or prior.get("discovery"),
                   "sha256": (source or {}).get("documentSha256") or prior.get("sha256"),
                   "reportDate": report_date, "lastSuccessAt": (source or {}).get("lastSuccessAt") or prior.get("lastSuccessAt"),
                   "httpStatus": http_status, "error": error, "health": state,
                   "readingStatus": record["status"] if record else "pending"}
            rows.append(row)
            news_failed = any(e.get("company") == company and e.get("kind") == "official-news" and e.get("phase") == "discovery"
                              for e in errors)
            health.append({"company": company, "fleetReport": state, "newsMonitor": "degraded-preserved" if news_failed else "healthy",
                           **{k: row[k] for k in ("reportDate", "documentUrl", "sha256", "lastSuccessAt", "httpStatus", "error")}})
            stages.append({"company": company, "url": row["documentUrl"], "collectionStatus": "failed" if state == "degraded-preserved" else "collected",
                           "readingStatus": row["readingStatus"], "lastReadAt": record.get("lastReadAt") if record else None})
        return rows, health, stages

    def _retry_after(self) -> int:
        if self.usage_limited_until:
            return self.usage_limited_until
        waiting = open_jobs(self.records)
        now = self.now()
        if waiting and all((r.get("nextAttemptAfter") or 0) > now for r in waiting):
            return min(r["nextAttemptAfter"] for r in waiting)
        return 0

    def execute(self, *, write: bool, stored_results: bool) -> dict:
        self._load()
        start_fleet = deepcopy(self.fleet)
        self._collect()
        if self.mode == "local":
            if self.service is None:
                from .codex_reader import CodexReader
                self.service = ReadingService(CodexReader())
            if stored_results:
                self._enqueue_stored_results()
            self.cache = self._load_cache()
            self._triage()
            self._read_all()
            self._tag_events()
        validate_fleet(self.fleet, self.registered)
        return self._publish(write=write, start_fleet=start_fleet)

    def _publish(self, *, write: bool, start_fleet: list[dict]) -> dict:
        at = self.now_iso()
        queue = build_queue(self.records)
        rows, health, stages = self._report_rows()
        reading_errors = [{key: r.get(key) for key in ("jobId", "company", "kind", "url", "lastError", "failureCount", "nextAttemptAfter")}
                          for r in open_jobs(self.records) if r.get("lastError")]
        fleet_hash, events_hash = content_hash(self.fleet, length=20), content_hash(self.events, length=20)
        manifest = {key: value for key, value in self.manifest.items() if key not in {"newsReview"}}
        manifest.update({
            "schemaVersion": 1, "generatedAt": at, "fleetFile": f"fleet.{fleet_hash}.json",
            "eventsFile": f"events.{events_hash}.json", "dataHash": fleet_hash, "eventsHash": events_hash,
            "shipCount": len(self.fleet), "contractCount": sum(len(s["contracts"]) for s in self.fleet),
            "sources": rows, "sourceCount": len(rows), "sourceHealth": health,
            "eventCount": len(self.events), "pendingEventCount": sum(bool(e.get("pendingReview")) for e in self.events),
            "warningsCount": len(self.records["collectionErrors"]) + len(reading_errors), "newsQueue": queue,
            "readingStatus": {"reports": stages, "lastCollectedAt": self.records["lastCollectedAt"],
                              "lastReadAt": self.records["lastReadAt"], "lastAppliedAt": self.records["lastAppliedAt"],
                              "usageLimitedUntil": self.usage_limited_until,
                              "collectionErrors": self.records["collectionErrors"], "readingErrors": reading_errors},
        })
        dates = [row["reportDate"] for row in rows if row.get("reportDate")]
        if dates:
            manifest["updatedAsOf"] = max(dates)
        manifest["stateFingerprint"] = content_hash({k: v for k, v in manifest.items() if k not in {"generatedAt", "stateFingerprint"}}, length=20)
        if write:
            public = self.root / "public/data"
            atomic_json(public / manifest["fleetFile"], self.fleet)
            atomic_json(public / manifest["eventsFile"], {"generatedAt": at, "events": self.events})
            atomic_json(public / "changes.json", {"generatedAt": at, "fromDataHash": self.manifest.get("dataHash"),
                                                  "toDataHash": fleet_hash, **_semantic_changes(start_fleet, self.fleet)})
            atomic_json(self.root / RECORDS_PATH, self.records)
            atomic_json(public / "manifest.json", manifest)
            _prune_obsolete_payloads(public, {manifest["fleetFile"], manifest["eventsFile"]})
        return {"ok": True, "mode": self.mode, "changed": self.manifest.get("stateFingerprint") != manifest["stateFingerprint"],
                "modelCalls": self.model_calls, "completedJobIds": self.completed, "failedJobIds": self.failed,
                "pendingJobIds": [job["jobId"] for job in queue["jobs"]], "usageLimitedUntil": self.usage_limited_until,
                "retryAfter": self._retry_after(), "manifest": manifest}
