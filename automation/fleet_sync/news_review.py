"""Pure reconciliation rules. Codex and HTTP are replaceable adapters.

Reviews are immutable source-hash-bound evidence, not an executable patch.
Only sufficiently precise, source-supported contract rows reach fleet metrics.
"""
from __future__ import annotations

import calendar
import copy
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .core import assign_contract_ids, clean_text, load_json

REVIEW_VERSION = "2026-10-20.1"
REVIEW_PATH = Path("data/news/reviews.json")
MODEL = "gpt-6-luna"


def event_date(value: str | None) -> str | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:10] if pattern == "%Y-%m-%d" else value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def read_reviews(root: Path) -> list[dict]:
    path = root / REVIEW_PATH
    if not path.exists():
        return []
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("version") != REVIEW_VERSION:
        raise ValueError("unsupported news review ledger")
    return payload["reviews"]


def validate_reading(result: dict, text: str, fleet: list[dict]) -> None:
    """Reject fabricated citations, foreign vessels and ungrounded fields."""
    names = {ship["name"] for ship in fleet}
    # BeautifulSoup inserts spaces around inline <i>/<b> nodes, e.g. "Rig A ,".
    # Ignore only that rendering whitespace; words/order/punctuation must match.
    quote_key = lambda value: re.sub(r"\s+([,.;:!?])", r"\1", clean_text(value)).casefold()
    normalized = quote_key(text)
    seen = set()
    for item in result["vessels"]:
        if item["vessel"] not in names or item["vessel"] in seen:
            raise ValueError("unknown or duplicate vessel in Codex reading")
        seen.add(item["vessel"])
        evidence = {row["field"]: row["quote"] for row in item["evidence"]}
        quotes = [*evidence.values(), *(q for c in item["changes"] for q in c["quotes"])]
        if not quotes:
            raise ValueError("Codex reading has no source evidence")
        for quote in quotes:
            if len(clean_text(quote)) < 4 or quote_key(quote) not in normalized:
                raise ValueError(f"Not a verbatim contiguous article quote (copy exactly, no ellipsis): {quote!r}")
        for key, value in item["facts"].items():
            if value not in (None, [], "undisclosed") and key not in {"dayRateDisclosure", "startPrecision"} and key not in evidence:
                raise ValueError(f"missing field evidence: {key}")
        facts = item["facts"]
        party_quote = evidence.get("counterparty", "")
        if facts.get("counterparty") and re.search(r"joint venture|subsidiary", party_quote, re.I) and re.search(r"has been awarded", party_quote, re.I):
            raise ValueError("Counterparty means the CUSTOMER/operator purchasing drilling services, not the drilling contractor, its joint venture or owner receiving the award. This quote identifies a contractor-side recipient. Use null/undisclosed unless the article explicitly names the buying customer; keep existing canonical customer unchanged for a continuation.")
        narratives = [item["comparison"], *(facts.get(k) for k in ("termText", "optionText", "valueText")),
                      *(change["reason"] for change in item["changes"])]
        if any(value and not re.search(r"[가-힣]", value) for value in narratives):
            raise ValueError("Korean UI language required: comparison, termText, optionText, valueText and change reasons must be written in Korean, not Chinese or English. Keep source quotations verbatim in their original language.")
        if facts["dayRateDisclosure"] != "reported" and facts["dayRateUsd"] is not None:
            raise ValueError("inferred dayrate is forbidden")
        if facts["dayRateDisclosure"] == "reported":
            quote = evidence.get("dayRateUsd", "")
            digits = re.sub(r"[, ]", "", quote)
            if not re.search(r"day.?rate|per day|daily rate", quote, re.I) or str(facts["dayRateUsd"]) not in digits:
                raise ValueError("dayrate has no direct disclosure")
        if "letter-of-award" in (facts["awardType"] or "") and item["changes"]:
            raise ValueError("LOA cannot be promoted to a signed contract")
        if not item["changes"] and facts.get("awardType") in {"option-exercised", "extension"}:
            # Missing economics must not suppress otherwise explicit committed
            # coverage. Zero already means unknown, not a uniform known rate.
            target = next(s for s in fleet if s["name"] == item["vessel"])
            firm = [c for c in target["contracts"] if c["status"] == "Firm"]
            try:
                horizon = datetime.strptime(facts.get("expectedEnd") or "", "%B %Y").date()
            except ValueError:
                horizon = None
            continuation = any("direct continuation" in q.casefold() and item["vessel"].casefold() in q.casefold() for q in evidence.values())
            if horizon and continuation and len(firm) == 1 and firm[0]["dayRate"] == 0 and firm[0]["endDate"][:7] < horizon.isoformat()[:7]:
                raise ValueError("Reconciliation incomplete: source explicitly confirms direct continuation through a new end month and the existing Firm rate is already unknown (0). Unknown tranche rates do not prevent extending committed coverage with dayRate=0, preserving the existing start/client/region. Propose the source-supported month-precision end-date update; zero does not assert one uniform rate.")


def _date_supported(value: str, precision: str, quotes: list[str], old: str | None, *, end: bool) -> bool:
    parsed = date.fromisoformat(value)
    if value == old:
        return True
    text = clean_text(" ".join(quotes))
    if precision == "day":
        forms = (value, f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}", f"{parsed.day} {parsed.strftime('%B')} {parsed.year}")
        return any(token.casefold() in text.casefold() for token in forms)
    if precision != "month":
        return False
    boundary = calendar.monthrange(parsed.year, parsed.month)[1] if end else 1
    if parsed.day != boundary:
        return False
    return bool(re.search(rf"\b(?:{parsed.strftime('%B')}|{parsed.strftime('%b')})\s+{parsed.year}\b", text, re.I))


def apply_changes(ship: dict, item: dict, review: dict) -> list[str]:
    """All-or-nothing per vessel; never turn a coarse forecast into exact days."""
    candidate = copy.deepcopy(ship["contracts"])
    applied = []
    for change in item["changes"]:
        old = next((c for c in candidate if c.get("id") == change["contractId"]), None) if change["contractId"] else None
        if change["contractId"] and old is None:
            raise ValueError("contract target no longer exists")
        for field in ("client", "region"):
            words = lambda s: re.sub(r"[^a-z0-9]", "", s.casefold())
            if not change[field] or (old and change[field] == old[field]):
                continue
            if words(change[field]) not in words(" ".join(change["quotes"])) and change[field].casefold() != "undisclosed":
                raise ValueError(f"new {field} is not supported by a source quote")
        for key, precision, end in (("startDate", "startPrecision", False), ("endDate", "endPrecision", True)):
            if not _date_supported(change[key], change[precision], change["quotes"], old.get(key) if old else None, end=end):
                raise ValueError(f"contract {key} lacks month/day source evidence")
        if change["startDate"] > change["endDate"]:
            raise ValueError("reversed contract interval")
        rate = change["dayRate"]
        if rate < 0 or (rate > 0 and (item["facts"]["dayRateDisclosure"] != "reported" or rate != item["facts"]["dayRateUsd"])):
            raise ValueError("contract dayrate is not disclosed in this award")
        if old:
            candidate.remove(old)
        row = {key: change[key] for key in ("startDate", "endDate", "dayRate", "client", "region", "status")}
        precision = {"start": change["startPrecision"], "end": change["endPrecision"]}
        if old:
            for side, key in (("start", "startDate"), ("end", "endDate")):
                if change[key] == old[key]:
                    precision[side] = old.get("datePrecision", {}).get(side, "month")
        row.update({"sourceUrl": review["url"], "sourcePublishedAt": review["publishedAt"],
                    "sourceSha256": review["sourceSha256"],
                    "datePrecision": precision,
                    "newsReviewed": True})
        candidate.append(row)
        applied.append(change["reason"])
    keys = [(c["startDate"], c["endDate"], c["client"], c["region"], c["status"]) for c in candidate]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate contract proposed by news reading")
    ship["contracts"] = assign_contract_ids(ship["id"], sorted(candidate, key=lambda c: (c["startDate"], c["endDate"], c["status"])))
    return applied


def reconcile_reviews(root: Path, fleet: list[dict], events: list[dict], report_dates: dict[str, str], *, reviews: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    reviews = sorted(read_reviews(root) if reviews is None else reviews, key=lambda r: (r["publishedAt"], r["url"]))
    warnings = []
    output = [dict(e) for e in events]
    for review in reviews:
        matches = [e for e in output if e.get("company") == review["company"] and
                   (e.get("url") == review["url"] or e.get("title") == review["title"])]
        if not matches:
            continue
        event = matches[-1]
        # A changed/unreadable source must not silently reuse old conclusions.
        if event.get("articleSha256") != review["sourceSha256"]:
            warnings.append(f"{review['company']} news needs re-reading: {review['title']}")
            for match in matches:
                match.update({"pendingReview": True, "reviewStatus": "pending", "autoApplied": False})
                for key in ("llmReview", "facts", "needsDetails", "reason"):
                    match.pop(key, None)
            continue
        for match in matches:
            output.remove(match)
        if review["publishedAt"] <= report_dates[review["company"]]:
            output.append({**event, "pendingReview": False, "reviewStatus": "covered-by-report"})
            continue
        if not review["result"]["vessels"]:
            output.append({**event, "pendingReview": False, "reviewStatus": "not-contract-news", "llmReview": review["reader"]})
        for item in review["result"]["vessels"]:
            ship = next(s for s in fleet if s["company"] == review["company"] and s["name"] == item["vessel"])
            try:
                applied = apply_changes(ship, item, review)
                status = "applied" if applied else "details-incomplete" if item["needsDetails"] else "reviewed"
                error = None
            except ValueError as exc:
                applied, status, error = [], "conflict", str(exc)
                warnings.append(f"{ship['name']} news reconciliation conflict: {exc}")
            output.append({**event, "vessel": item["vessel"], "vessels": [item["vessel"]],
                           "facts": {k: v for k, v in item["facts"].items() if v is not None},
                           "reviewStatus": status, "pendingReview": status == "conflict",
                           "autoApplied": bool(applied), "needsDetails": item["needsDetails"],
                           "reason": error or item["comparison"], "llmReview": review["reader"],
                           "evidence": item["evidence"], "sourceSha256": review["sourceSha256"]})
            # A later signed award supersedes a report's provisional LOA card.
            if item["facts"]["awardType"] in {"signed-contract", "option-exercised", "extension"}:
                for prior in output:
                    if prior.get("classification") == "letter-of-award" and prior.get("company") == review["company"] and prior.get("vessel") == item["vessel"]:
                        prior.update({"pendingReview": False, "reviewStatus": "superseded", "supersededBy": review["url"]})
    return output, warnings
