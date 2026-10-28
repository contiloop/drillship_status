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
    # Issuer investor index links directly to this official release host.
    "noble.gcs-web.com",
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
        # Corporate mirror carries the same issuer releases without the IR
        # host's Akamai denial of automated requests. No anti-bot bypass.
        news_url="https://www.deepwater.com/news",
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
    ) -> requests.Response:
        current_url = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_url(current_url)
            response = self.session.request(
                method,
                current_url,
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


def discover_document(
    client: HttpClient, spec: SourceSpec, *, diagnostics: list[dict] | None = None,
) -> tuple[str, str]:
    """Keep the legacy URL/mode result and optionally retain attempt failures."""
    attempt_url = spec.index_url
    try:
        if spec.company == "Transocean":
            return _discover_transocean(client, spec), "official-index"
        if spec.company == "Valaris":
            try:
                return _discover_valaris_official(client, spec), "official-index"
            except Exception as official_error:
                if diagnostics is not None:
                    diagnostics.append(_discovery_diagnostic(official_error, spec.index_url, "official-report-index"))
                attempt_url = "https://data.sec.gov/submissions/CIK0000314808.json"
                return (
                    _discover_valaris_sec(client),
                    f"sec-submissions-after-official-{type(official_error).__name__}",
                )
        if spec.company == "Noble":
            return _discover_noble(client, spec), "official-index"
        if spec.company == "Seadrill":
            return _discover_seadrill(client, spec), "official-wordpress-api"
    except Exception as error:
        # Keep the failed discovery attempt separate from its official fallback.
        if diagnostics is not None:
            diagnostics.append(_discovery_diagnostic(error, attempt_url, "official-report-discovery"))
        return spec.fallback_document_url, f"fallback-after-{type(error).__name__}"
    raise AssertionError(spec.company)


def _error_summary(error: Exception) -> str:
    """Return a stable, non-secret-bearing monitor error classification."""

    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"{type(error).__name__}({error.response.status_code})"
    return type(error).__name__


def _discovery_diagnostic(error: Exception, url: str, stage: str, **metadata: object) -> dict:
    """Structured attempt evidence; consumers never parse human warning text."""
    response = getattr(error, "response", None)
    return {
        "url": getattr(response, "url", None) or url,
        "stage": stage, "errorType": type(error).__name__,
        "error": f"{type(error).__name__}: {error}",
        "httpStatus": getattr(response, "status_code", None), **metadata,
    }


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


def retrieved_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def publication_date(value: object) -> str | None:
    """Normalize explicit publisher metadata, never dates in contract prose."""
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.isoformat() if "T" in text else parsed.date().isoformat()
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y %H:%M:%S", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def discover_official_news(
    client: HttpClient, spec: SourceSpec, *, limit: int = 20,
    diagnostics: list[dict] | None = None,
) -> tuple[list[dict], str | None]:
    """Discover official articles without judging headline/vessel relevance.

    Each listing and Q4 item is an independent attempt. Optional structured
    diagnostics preserve HTTP status/item identity; warnings remain compatible
    with existing callers. Full article acquisition belongs to source_materials.
    """
    if limit <= 0:
        return [], None
    events: list[dict] = []
    seen: set[str] = set()
    warnings: list[str] = []

    def failed(error: Exception, url: str, stage: str, **metadata: object) -> None:
        if diagnostics is not None:
            diagnostics.append(_discovery_diagnostic(error, url, stage, **metadata))
        warnings.append(f"{stage} {_error_summary(error)}")

    if spec.company in {"Valaris", "Seadrill"}:
        host = urlparse(spec.news_url).hostname
        query = urlencode({
            "LanguageId": 1, "bodyType": 0, "pressReleaseDateFilter": 3,
            "categoryId": "00000000-0000-0000-0000-000000000000",
            "pageSize": limit, "pageNumber": 0, "tagList": "",
            "includeTags": "true", "year": -1, "excludeSelection": 1,
        })
        endpoint = f"https://{host}/feed/PressRelease.svc/GetPressReleaseList?{query}"
        try:
            items = client.get(endpoint).json()["GetPressReleaseListResult"]
            if not isinstance(items, list):
                raise ValueError("Q4 list result is not an array")
            for index, item in enumerate(items):
                try:
                    if not isinstance(item, dict):
                        raise ValueError("Q4 list item is not an object")
                    href = item.get("LinkToDetailPage") or item.get("LinkToUrl")
                    if not isinstance(href, str) or not href:
                        raise ValueError("Q4 article has no detail URL")
                    url = urljoin(spec.news_url, href)
                    if url in seen:
                        continue
                    seen.add(url)
                    events.append({
                        "company": spec.company, "url": url,
                        "title": clean_text(item.get("Headline")),
                        "publishedAt": publication_date(item.get("PressReleaseDate")),
                        "discoveryMode": "official-q4-public-feed",
                    })
                    if len(events) >= limit:
                        break
                except Exception as error:
                    failed(error, endpoint, f"Q4 item[{index}]", itemIndex=index)
            if events:
                return events, "; ".join(warnings) or None
            failed(ValueError("Q4 public feed returned no valid article URLs"), endpoint, "official-q4-public-feed")
        except Exception as error:
            failed(error, endpoint, "official-q4-public-feed")

    # The primary listing and the Noble PDF listing are deliberately sibling
    # attempts: an HTTP failure in either cannot prevent the other being read.
    try:
        response = client.get(spec.news_url)
        soup = BeautifulSoup(response.text, "lxml")
        for index, link in enumerate(soup.find_all("a", href=True)):
            try:
                url = urljoin(response.url, link["href"])
                parsed = urlparse(url)
                # Route shapes and dates are collection metadata, not relevance.
                is_article = (
                    bool(re.search(r"/news/detail/?$", parsed.path, re.I))
                    and bool(dict(parse_qsl(parsed.query)).get("ID"))
                ) or bool(re.search(r"/news/news-details/|/news-releases/news-release-details/", parsed.path, re.I))
                if not is_article or url in seen:
                    continue
                seen.add(url)
                container = link.find_parent(["tr", "article", "li"]) or link.parent
                time_node = container.find("time") if container else None
                published = publication_date(time_node.get("datetime") or time_node.get_text()) if time_node else None
                if published is None and container:
                    date_match = re.search(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b|\b\d{2}/\d{2}/\d{4}\b", container.get_text(" "))
                    published = publication_date(date_match.group(0)) if date_match else None
                events.append({
                    "company": spec.company, "url": url,
                    "title": clean_text(unescape(link.get_text(" "))),
                    "publishedAt": published, "discoveryMode": "official-news-index",
                })
                if len(events) >= limit:
                    break
            except Exception as error:
                failed(error, spec.news_url, "official-news-index-item", itemIndex=index)
    except Exception as error:
        failed(error, spec.news_url, "official-news-index")

    if spec.company == "Noble":
        releases_url = "https://noblecorp.com/our-investors/reports-filings/quarterly-results/"
        try:
            releases = client.get(releases_url)
            release_soup = BeautifulSoup(releases.text, "lxml")
            count = 0
            for link in release_soup.find_all("a", href=True):
                if clean_text(link.get_text(" ")).casefold() != "earnings release":
                    continue
                url = urljoin(releases.url, link["href"])
                if url in seen:
                    continue
                seen.add(url)
                events.append({"company": spec.company, "url": url, "title": "Earnings Release",
                               "publishedAt": None, "discoveryMode": "official-quarterly-release-pdf"})
                count += 1
                if count >= limit:
                    break
        except Exception as error:
            failed(error, releases_url, "official-quarterly-release-index")
    if not events:
        failed(ValueError("official indices did not expose article URLs"), spec.news_url, "official-news-discovery")
    return events, "; ".join(warnings) or None
