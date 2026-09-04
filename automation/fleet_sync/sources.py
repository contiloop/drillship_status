from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .core import clean_text
from .model import Company


ALLOWED_HOSTS = {
    "www.deepwater.com",
    "investor.deepwater.com",
    "www.valaris.com",
    "s23.q4cdn.com",
    "noblecorp.com",
    "www.seadrill.com",
    "ir.seadrill.com",
    "data.sec.gov",
    "www.sec.gov",
}


@dataclass(frozen=True)
class SourceSpec:
    company: Company
    index_url: str
    fallback_document_url: str
    news_url: str


SOURCE_SPECS = (
    SourceSpec(
        company="Transocean",
        index_url="https://www.deepwater.com/investors/fleet-status-report",
        fallback_document_url="https://www.deepwater.com/documents/FleetStatusReport/2026/August%202026%20Fleet%20Status%20Report.pdf",
        news_url="https://investor.deepwater.com/press-releases",
    ),
    SourceSpec(
        company="Valaris",
        index_url="https://www.valaris.com/investors/",
        fallback_document_url="https://s23.q4cdn.com/956522167/files/doc_financials/2026/q2/08052026-Fleet-Status-Report_FINAL.pdf",
        news_url="https://www.valaris.com/news/default.aspx",
    ),
    SourceSpec(
        company="Noble",
        index_url="https://noblecorp.com/our-investors/reports-filings/fleet-status-report/",
        fallback_document_url="https://noblecorp.com/download/noble-corporation-fleet-status-report-3/?wpdmdl=3701",
        news_url="https://noblecorp.com/our-investors/investor-news/",
    ),
    SourceSpec(
        company="Seadrill",
        index_url="https://www.seadrill.com/wp-json/wp/v2/pages/987508015",
        fallback_document_url="https://www.seadrill.com/wp-content/uploads/2026/08/Seadrill-Fleet-Status-Report-August-2026-vF.pdf",
        news_url="https://ir.seadrill.com/news/",
    ),
)


class HttpClient:
    MAX_REDIRECTS = 5

    def __init__(self) -> None:
        user_agent = os.environ.get(
            "SEC_USER_AGENT",
            "DrillshipStatus/1.0 contiloop@users.noreply.github.com",
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/json,application/pdf;q=0.9,*/*;q=0.5",
            }
        )
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"source URL is not allowlisted: {url}")

    def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: int,
        byte_limit: int,
        json_payload: dict | None = None,
    ) -> requests.Response:
        current_method = method
        current_url = url
        current_payload = json_payload
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_url(current_url)
            response = self.session.request(
                current_method,
                current_url,
                json=current_payload,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            self._validate_url(response.url)
            if response.is_redirect or response.is_permanent_redirect:
                if redirect_count >= self.MAX_REDIRECTS:
                    response.close()
                    raise ValueError(f"source exceeded {self.MAX_REDIRECTS} redirects: {url}")
                location = response.headers.get("location")
                if not location:
                    response.close()
                    raise ValueError(f"source redirect has no Location header: {response.url}")
                next_url = urljoin(response.url, location)
                # Validate before the next request so an allowlisted host cannot
                # bounce credentials or traffic to an arbitrary destination.
                self._validate_url(next_url)
                if response.status_code == 303 or (
                    current_method == "POST" and response.status_code in {301, 302}
                ):
                    current_method = "GET"
                    current_payload = None
                response.close()
                current_url = next_url
                continue

            try:
                response.raise_for_status()
            except Exception:
                response.close()
                raise
            declared_length = response.headers.get("content-length")
            if declared_length:
                try:
                    declared_bytes = int(declared_length)
                except ValueError:
                    declared_bytes = None
                if declared_bytes is not None and declared_bytes > byte_limit:
                    response.close()
                    raise ValueError(
                        f"source response exceeds {byte_limit} bytes: {response.url}"
                    )

            chunks: list[bytes] = []
            received = 0
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > byte_limit:
                        raise ValueError(
                            f"source response exceeds {byte_limit} bytes: {response.url}"
                        )
                    chunks.append(chunk)
            finally:
                response.close()
            response._content = b"".join(chunks)
            response._content_consumed = True
            return response
        raise AssertionError("redirect loop escaped its bound")

    def get(self, url: str, *, timeout: int = 45) -> requests.Response:
        return self._request(
            "GET",
            url,
            timeout=timeout,
            byte_limit=25 * 1024 * 1024,
        )

    def post_json(self, url: str, payload: dict, *, timeout: int = 45) -> requests.Response:
        return self._request(
            "POST",
            url,
            timeout=timeout,
            byte_limit=5 * 1024 * 1024,
            json_payload=payload,
        )


def discover_document(client: HttpClient, spec: SourceSpec) -> tuple[str, str]:
    try:
        if spec.company == "Transocean":
            return _discover_transocean(client, spec), "official-index"
        if spec.company == "Valaris":
            try:
                return _discover_valaris_official(client, spec), "official-index"
            except Exception as official_error:
                return (
                    _discover_valaris_sec(client),
                    f"sec-submissions-after-official-{type(official_error).__name__}",
                )
        if spec.company == "Noble":
            return _discover_noble(client, spec), "official-index"
        if spec.company == "Seadrill":
            return _discover_seadrill(client, spec), "official-wordpress-api"
    except Exception as error:
        # The known-good official document is intentionally a last-resort safety
        # net. Parsing and freshness validation still run before publication.
        return spec.fallback_document_url, f"fallback-after-{type(error).__name__}"
    raise AssertionError(spec.company)


def _error_summary(error: Exception) -> str:
    """Return a stable, non-secret-bearing monitor error classification."""

    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__}({error.response.status_code})"
    return type(error).__name__


def _discover_transocean(client: HttpClient, spec: SourceSpec) -> str:
    response = client.get(spec.index_url)
    soup = BeautifulSoup(response.text, "lxml")
    candidates: list[tuple[datetime, str]] = []
    for row in soup.select("tr"):
        text = clean_text(row.get_text(" "))
        date_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
        link = row.find("a", href=re.compile(r"FleetStatusReport.*\.pdf", re.I))
        if not date_match or link is None:
            continue
        candidates.append(
            (
                datetime.strptime(date_match.group(1), "%m/%d/%Y"),
                urljoin(response.url, link["href"]),
            )
        )
    if not candidates:
        raise ValueError("Transocean index did not expose a fleet report PDF")
    return max(candidates, key=lambda item: item[0])[1]


def _discover_noble(client: HttpClient, spec: SourceSpec) -> str:
    response = client.get(spec.index_url)
    soup = BeautifulSoup(response.text, "lxml")
    candidates: list[tuple[datetime, str]] = []
    for link in soup.find_all("a", href=re.compile(r"noble-corporation-fleet-status-report", re.I)):
        container = link.find_parent(["article", "section", "div"]) or link.parent
        text = clean_text(container.get_text(" "))
        date_match = re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
            text,
        )
        if date_match:
            parsed = datetime.strptime(date_match.group(0), "%B %d, %Y")
        else:
            # The official page is newest-first; a missing adjacent date is
            # accepted only behind any dated candidate.
            parsed = datetime.min
        resolved = urljoin(response.url, link["href"])
        parsed_url = urlparse(resolved)
        stable_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed_url.query)
                if key.casefold() != "refresh"
            ]
        )
        candidates.append((parsed, urlunparse(parsed_url._replace(query=stable_query))))
    if not candidates:
        raise ValueError("Noble index did not expose a fleet report PDF")
    return max(candidates, key=lambda item: item[0])[1]


def _discover_valaris_official(client: HttpClient, spec: SourceSpec) -> str:
    response = client.get(spec.index_url)
    soup = BeautifulSoup(response.text, "lxml")
    links = [
        urljoin(response.url, link["href"])
        for link in soup.find_all("a", href=True)
        if re.search(r"Fleet[-_ ]Status[-_ ]Report.*\.pdf", link["href"], re.I)
    ]
    if not links:
        raise ValueError("Valaris investor page did not expose a fleet report PDF")
    return links[0]


def _discover_seadrill(client: HttpClient, spec: SourceSpec) -> str:
    payload = client.get(spec.index_url).json()
    html = payload.get("content", {}).get("rendered", "")
    soup = BeautifulSoup(html, "lxml")
    links = [
        urljoin("https://www.seadrill.com/", link.get("href", ""))
        for link in soup.find_all("a")
        if re.search(r"Seadrill[-_ ]Fleet[-_ ]Status[-_ ]Report.*\.pdf", link.get("href", ""), re.I)
    ]
    if not links:
        rendered = unescape(html)
        links = [
            urljoin("https://www.seadrill.com/", match.lstrip("\"'“”‘’"))
            for match in re.findall(
                r"link_option_url=[\"']?([^\s\"'\]]*Seadrill-Fleet-Status-Report[^\s\"'\]]*\.pdf)",
                rendered,
                re.I,
            )
        ]
    if not links:
        raise ValueError("Seadrill WordPress page did not expose a fleet report PDF")
    return links[0]


def _discover_valaris_sec(client: HttpClient) -> str:
    submissions = client.get("https://data.sec.gov/submissions/CIK0000314808.json").json()
    recent = submissions["filings"]["recent"]
    filings = []
    for index, form in enumerate(recent["form"]):
        if form == "8-K":
            filings.append(
                (
                    recent["filingDate"][index],
                    recent["accessionNumber"][index],
                    recent.get("items", [""] * len(recent["form"]))[index],
                )
            )
    for _, accession, items in sorted(filings, reverse=True)[:16]:
        if items and "7.01" not in items:
            continue
        accession_compact = accession.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/314808/{accession_compact}/index.json"
        try:
            directory = client.get(index_url).json()["directory"]["item"]
        except Exception:
            continue
        names = [item["name"] for item in directory if item["name"].lower().endswith((".htm", ".html"))]
        for name in names:
            exhibit_url = urljoin(index_url, name)
            try:
                body = client.get(exhibit_url).text
            except Exception:
                continue
            normalized = clean_text(BeautifulSoup(body, "lxml").get_text(" "))
            if "Fleet Status Report" in normalized and "Asset Category / Rig" in normalized:
                return exhibit_url
        time.sleep(0.12)
    raise ValueError("SEC submissions did not expose a Valaris fleet-status exhibit")


def collect_official_news(
    client: HttpClient,
    spec: SourceSpec,
    vessel_names: list[str],
    *,
    limit: int = 8,
) -> tuple[list[dict], str | None]:
    q4_warning: str | None = None
    if spec.company in {"Valaris", "Seadrill"}:
        try:
            q4_events = _collect_q4_news(client, spec, vessel_names, limit=limit)
            if q4_events:
                return q4_events, None
            q4_warning = "Q4 service returned no matching monitored items; rendered-page fallback used"
        except Exception as error:
            # Fall through to the rendered official page. Some Q4 tenants
            # intermittently disable the service endpoint during publishing.
            q4_warning = f"Q4 service {_error_summary(error)}; rendered-page fallback used"
    try:
        response = client.get(spec.news_url)
    except Exception as error:
        detail = _error_summary(error)
        if q4_warning:
            detail = f"{q4_warning}; rendered page {detail}"
        return [], detail

    soup = BeautifulSoup(response.text, "lxml")
    keywords = re.compile(r"\b(contract|fleet|award|extension|option|backlog|drillship|rig|results)\b", re.I)
    events: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        title = clean_text(unescape(link.get_text(" ")))
        if len(title) < 12 or not keywords.search(title):
            continue
        url = urljoin(response.url, link["href"])
        parsed_url = urlparse(url)
        if url in seen or parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_HOSTS:
            continue
        seen.add(url)
        parent_text = clean_text((link.find_parent(["article", "li", "div"]) or link).get_text(" "))
        detail_text = parent_text
        date_match = re.search(
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
            parent_text,
            re.I,
        )
        matched_vessels = [name for name in vessel_names if name.casefold() in parent_text.casefold()]
        needs_detail_facts = (
            spec.company == "Transocean"
            and "300 Million Contract" in title
        )
        if (not matched_vessels or needs_detail_facts) and parsed_url.path not in {"", "/"}:
            try:
                detail_text = clean_text(
                    BeautifulSoup(client.get(url).text, "lxml").get_text(" ")
                )
                matched_vessels = [
                    name for name in vessel_names if name.casefold() in detail_text.casefold()
                ]
                if date_match is None:
                    date_match = re.search(
                        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
                        detail_text,
                        re.I,
                    )
            except Exception:
                # A listing item is still a useful review signal when the
                # detail page temporarily rejects automated access.
                pass
        event = {
            "company": spec.company,
            "title": title,
            "url": url,
            "publishedAt": date_match.group(0) if date_match else None,
            "vessels": matched_vessels,
            "classification": "official-news-signal",
            "autoApplied": False,
        }
        facts = _extract_review_facts(spec.company, title, detail_text)
        if facts:
            event["facts"] = facts
        events.append(event)
        if len(events) >= limit:
            break
    if not events:
        detail = "official news page returned no matching monitored items"
        if q4_warning:
            detail = f"{q4_warning}; {detail}"
        return [], detail
    return events, q4_warning


def _extract_review_facts(company: str, title: str, detail_text: str) -> dict | None:
    """Extract only the bounded, deterministic facts needed for manual review."""

    if not (
        company == "Transocean"
        and "300 Million Contract" in title
        and re.search(r"\b(?:ONGC|Oil and Natural Gas Corporation)\b", detail_text, re.I)
        and re.search(r"\bIndia\b", detail_text, re.I)
        and re.search(r"\b(?:first quarter(?: of)?|Q1)\s+2027\b", detail_text, re.I)
        and re.search(r"\b(?:two|2)[- ]year\b", detail_text, re.I)
        and re.search(r"binding\s+Letter of Award", detail_text, re.I)
        and re.search(r"two years of priced options", detail_text, re.I)
        and re.search(r"\$?300\s+million", detail_text, re.I)
        and re.search(r"inclusive of additional services and mobilization fees", detail_text, re.I)
    ):
        return None
    facts = {
        "counterparty": "ONGC",
        "location": "India",
        "expectedStart": "Q1 2027",
        "startPrecision": "quarter",
        "awardType": "binding-letter-of-award",
        "awardTermYears": 2,
        "optionTermYears": 2,
        "announcedValueUsdApprox": 300_000_000,
        "valueIncludes": ["additional services", "mobilization fee"],
        "exactDatesInferred": False,
        "dayRateInferred": False,
    }
    # This remains a bounded rule for this release, not a general news reader.
    # Preserve the source's approximate option horizon, never synthesize a day.
    option_end = re.search(r"into\s+early\s+(\d{4})", detail_text, re.I)
    if option_end:
        facts["optionEndIfFullyExercised"] = f"early {option_end.group(1)}"
    facts["dayRateDisclosure"] = (
        "not-extracted"
        if re.search(r"\b(?:day[ -]?rate|daily rate|per day)\b", detail_text, re.I)
        else "undisclosed"
    )
    return facts


def _collect_q4_news(
    client: HttpClient,
    spec: SourceSpec,
    vessel_names: list[str],
    *,
    limit: int,
) -> list[dict]:
    host = urlparse(spec.news_url)
    endpoint = f"https://{host.hostname}/Services/PressReleaseService.svc/GetPressReleaseList"
    year = datetime.now(timezone.utc).year
    payload = {
        "serviceDto": {
            "ViewType": "2",
            "ViewDate": "",
            "RevisionNumber": "1",
            "LanguageId": "1",
            "Signature": "",
            "ItemCount": 40,
            "StartIndex": 0,
            "TagList": [],
            "IncludeTags": True,
        },
        "excludeSelection": 1,
        "year": year,
        "pressReleaseBodyType": 0,
        "pressReleaseSelection": 3,
        "pressReleaseCategoryWorkflowId": "00000000-0000-0000-0000-000000000000",
    }
    items = client.post_json(endpoint, payload).json().get("GetPressReleaseListResult", [])
    keywords = re.compile(r"\b(contract|fleet|award|extension|option|backlog|drillship|rig|results)\b", re.I)
    events: list[dict] = []
    for item in items:
        title = clean_text(item.get("Headline"))
        if not keywords.search(title):
            continue
        if re.search(r"\b(voting|annual general meeting)\b", title, re.I):
            continue
        url = urljoin(spec.news_url, item.get("LinkToDetailPage") or item.get("LinkToUrl") or "")
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_HOSTS:
            continue
        text = clean_text(
            " ".join(
                str(item.get(key) or "")
                for key in ("Headline", "ShortDescription", "ShortBody", "Body")
            )
        )
        matched_vessels = [name for name in vessel_names if name.casefold() in text.casefold()]
        if not matched_vessels:
            try:
                detail = clean_text(BeautifulSoup(client.get(url).text, "lxml").get_text(" "))
                matched_vessels = [
                    name for name in vessel_names if name.casefold() in detail.casefold()
                ]
            except Exception:
                pass
        raw_date = clean_text(item.get("PressReleaseDate"))
        try:
            published_at = datetime.strptime(raw_date, "%m/%d/%Y %H:%M:%S").date().isoformat()
        except ValueError:
            published_at = raw_date or None
        events.append(
            {
                "company": spec.company,
                "title": title,
                "url": url,
                "publishedAt": published_at,
                "vessels": matched_vessels,
                "classification": "official-news-signal",
                "autoApplied": False,
            }
        )
        if len(events) >= limit:
            break
    return events


def retrieved_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
