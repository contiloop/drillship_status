"""Official source acquisition and content-change detection, without interpretation.

No completion decisions or semantic parsers live here. Every successfully fetched
material is returned, including unchanged material; scheduling owns job identity,
context matching and completion. Failed sources retain their last successful
hashes and timestamps and are retried on every call.
"""
from __future__ import annotations

from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup, Tag
import pdfplumber

from .document_ocr import PageOCR, TesseractPageOCR
from .reading_contracts import ReadingPage, context_sha256, source_sha256
from .sources import (
    HttpClient, SOURCE_SPECS, SourceSpec, discover_document,
    discover_official_news, publication_date, retrieved_at,
)

# Bump when extraction/locator behavior changes; the reader policy owner includes
# this in its policy text/hash so old completion records cannot hide new input.
EXTRACTION_VERSION = "official-materials-v4-dom-flow-whitespace-pdf-image144-ocr-tsv"
PRESERVED_MATERIAL_DISCOVERY = "preserved-reading-material"
PDF_IMAGE_DPI = 144
MAX_PAGE_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_IMAGE_BYTES = 40 * 1024 * 1024
MAX_PAGE_IMAGE_PIXELS = 10_000_000

_ISSUER_HOSTS = {
    "Transocean": {"www.deepwater.com", "investor.deepwater.com"},
    "Valaris": {"www.valaris.com"},
    "Noble": {"noblecorp.com", "noble.gcs-web.com"},
    "Seadrill": {"www.seadrill.com", "ir.seadrill.com"},
}
# Existing SEC discovery is validated for this issuer/CIK only; adding an
# issuer requires confirming its SEC identity rather than accepting any filing.
_SEC_CIKS = {"Valaris": {"314808"}}


def validate_official_source(company: str, url: str) -> None:
    """Check issuer ownership, including tenant/CIK on shared official stores."""
    HttpClient._validate_url(url)
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("official source must not contain credentials or a non-HTTPS port")
    if parsed.hostname in _ISSUER_HOSTS.get(company, set()):
        return
    if company == "Valaris" and parsed.hostname == "s23.q4cdn.com" and parsed.path.startswith("/956522167/"):
        return
    if parsed.hostname == "www.sec.gov":
        match = re.match(r"^/Archives/edgar/data/([0-9]+)/", parsed.path)
        if match and str(int(match.group(1))) in _SEC_CIKS.get(company, set()):
            return
    if parsed.hostname == "data.sec.gov":
        match = re.fullmatch(r"/submissions/CIK([0-9]+)\.json", parsed.path)
        if match and str(int(match.group(1))) in _SEC_CIKS.get(company, set()):
            return
    raise ValueError(f"URL is not an official source for {company}: {url}")


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment="", scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()))


def source_id(company: str, kind: str, url: str) -> str:
    """Stable document identity; sourceSha256 separately identifies body revision."""
    value = json.dumps([company, kind, _canonical_url(url)], separators=(",", ":"))
    return "source-" + sha256(value.encode()).hexdigest()


def document_sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


def render_page_image(page: Any, *, locator: str) -> dict:
    """Render every PDF page reproducibly; image/size failures are atomic."""
    pixels = (page.width * PDF_IMAGE_DPI / 72) * (page.height * PDF_IMAGE_DPI / 72)
    if pixels > MAX_PAGE_IMAGE_PIXELS:
        raise ValueError(f"PDF image exceeds {MAX_PAGE_IMAGE_PIXELS} pixels at {locator}")
    buffer = BytesIO()
    page.to_image(resolution=PDF_IMAGE_DPI).original.convert("RGB").save(buffer, format="PNG")
    content = buffer.getvalue()
    if len(content) > MAX_PAGE_IMAGE_BYTES:
        raise ValueError(f"PDF image exceeds {MAX_PAGE_IMAGE_BYTES} bytes at {locator}")
    return {"mimeType": "image/png", "sha256": document_sha256(content),
            "dataBase64": b64encode(content).decode("ascii")}


# Default HTML block boundaries, including table cells and explicit breaks.
# Inline/custom tags contribute only their own DOM text; their boundaries must
# never add a space to words, numbers or adjacent punctuation.
_HTML_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "legend", "li",
    "main", "menu", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul",
})


def _normalize_text_whitespace(text: str) -> str:
    """Change whitespace only; retain every non-whitespace character in order."""
    return " ".join(text.split())


def _html_text_flow(root: Tag) -> str:
    """Follow DOM text flow, separating only block boundaries before collapse.

    Joining stripped text nodes inserts punctuation/word breaks that a browser
    never displays. Preserve node text and existing inter-node whitespace first;
    a block/line-break contributes a separator on either side. get_text keeps
    comments out, and the caller has already removed script/style/template noise.
    """
    flow = deepcopy(root)
    for node in flow.find_all(True):
        if node.name in _HTML_BLOCK_TAGS:
            node.insert_before(" ")
            node.insert_after(" ")
    return _normalize_text_whitespace(flow.get_text(separator="", strip=False))


def extract_document_pages(
    content: bytes, content_type: str, *, kind: str, page_ocr: PageOCR | None = None,
    extraction_warnings: list[str] | None = None,
) -> list[ReadingPage]:
    """Return every PDF page with native text and original rendered page image.

    A page without text remains readable as its image. OCR is an optional
    mechanical aid for quotations; it never invents replacement descriptions.
    Rendering failures and limits fail the whole source, never drop a page.
    """
    if not content:
        raise ValueError("document is empty")
    if content.lstrip().startswith(b"%PDF-"):
        with pdfplumber.open(BytesIO(content)) as pdf:
            if not pdf.pages:
                raise ValueError("PDF contains no pages")
            pages = []
            image_bytes = 0
            for number, page in enumerate(pdf.pages, 1):
                locator = f"page:{number}"
                image = render_page_image(page, locator=locator)
                image_bytes += len(image["dataBase64"]) * 3 // 4
                if image_bytes > MAX_DOCUMENT_IMAGE_BYTES:
                    raise ValueError(f"PDF images exceed {MAX_DOCUMENT_IMAGE_BYTES} bytes")
                try:
                    text = page.extract_text() or ""
                except Exception as error:
                    if extraction_warnings is not None:
                        extraction_warnings.append(f"{locator}: native text extraction {type(error).__name__}: {error}")
                    text = ""
                if not text.strip() and page_ocr is not None:
                    try:
                        text = page_ocr.extract(page, locator=locator)
                    except Exception as error:
                        if extraction_warnings is not None:
                            extraction_warnings.append(f"{locator}: {type(error).__name__}: {error}")
                        text = ""
                pages.append({"locator": locator, "text": _normalize_text_whitespace(text), "image": image})
            return pages
    if "pdf" in content_type.lower():
        raise ValueError("PDF response did not contain a PDF document")
    if not ("html" in content_type.lower() or content.lstrip().startswith((b"<", b"<!"))):
        raise ValueError("document format is neither PDF nor HTML")
    soup = BeautifulSoup(content, "lxml")
    error_heading = re.compile(r"^(?:access denied|forbidden|page not found|404(?:\s|$)|403(?:\s|$)|error(?:\s|$)|just a moment|request rejected|attention required)", re.I)
    for heading in soup.select("title,h1"):
        if error_heading.search(heading.get_text(" ", strip=True)):
            raise ValueError("HTTP response contains an error page")
    for node in soup.select("script,style,noscript,template"):
        node.decompose()
    selected = None
    selector = ""
    for selector in ("article.news", ".module-news-details", ".evergreen-news-details", "article", "main"):
        selected = soup.select_one(selector)
        if selected is not None:
            break
    if selected is None and kind == "fleet-report":
        selected, selector = soup.body, "body"
    if selected is None:
        raise ValueError("HTML article body is missing")
    # Site chrome outside the selected body is irrelevant. Preserve headers,
    # footers and disclosure sections *inside* it: these are publisher content.
    for node in soup.select("nav,header,footer"):
        if selected not in node.parents and node not in selected.parents:
            node.decompose()
    # Heading/title alone cannot count as a successfully read article.
    body_copy = BeautifulSoup(str(selected), "lxml")
    for node in body_copy.select("h1,h2,h3,h4,h5,h6"):
        node.decompose()
    body_text = _html_text_flow(body_copy)
    if len(body_text) < 100:
        raise ValueError("HTML body is empty or unexpectedly short")
    text = _html_text_flow(selected)
    return [{"locator": f"html:{selector}", "text": text}]


def html_publication_date(content: bytes) -> str | None:
    """Read only explicit publication metadata on an HTML document."""
    soup = BeautifulSoup(content, "lxml")
    for selector in ('meta[property="article:published_time"]', 'meta[name="date"]',
                     'meta[itemprop="datePublished"]', 'time[datetime]',
                     '.evergreen-news-details-date', '.module_date-time', '.news-date'):
        node = soup.select_one(selector)
        if node is not None:
            published = publication_date(node.get("content") or node.get("datetime") or node.get_text(" ", strip=True))
            if published:
                return published
    return None


def _http_status(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    return response.status_code if response is not None else None


def collect_reading_materials(
    client: HttpClient, *, baseline: list[dict], prior_evidence: list[dict],
    reader_policy_sha256: str,
    previous_sources: Mapping[str, dict] | None = None,
    specs: Iterable[SourceSpec] = SOURCE_SPECS,
    preserved_news: Iterable[dict] = (), preserved_materials: Iterable[dict] = (), news_limit: int = 20,
    page_ocr: PageOCR | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Collect report/news inputs and a retryable, failure-isolated source ledger.

    ``previous_sources`` maps stable sourceId to the prior ledger record.
    ``preserved_news`` carries company/url and optional title/publishedAt for any
    previously discovered article that must be revisited, outside listing limits.
    ``preserved_materials`` additionally retains company/kind/url for unfinished
    or held reports and news no longer present in current discovery indexes.
    ``materials`` entries contain readingInput + original-byte documentSha256.
    ``sources`` records separate HTTP status from bodyRead/error/lastSuccessAt;
    ``errors`` is the failing subset plus discovery failures. ``changed`` aliases
    ``readingInputChanged``; ``documentChanged`` separately compares raw bytes.
    Raster/OCR differences may change the former without changing the latter.
    No files are written.
    """
    if not isinstance(reader_policy_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", reader_policy_sha256) is None:
        raise ValueError("reader_policy_sha256 must be supplied by the reader policy owner")
    previous_sources = previous_sources or {}
    page_ocr = page_ocr if page_ocr is not None else TesseractPageOCR()
    now = retrieved_at()
    context = context_sha256(baseline, prior_evidence)
    result: dict[str, list[dict[str, Any]]] = {"materials": [], "sources": [], "errors": []}
    candidates: list[dict] = []
    for spec in specs:
        report_diagnostics: list[dict] = []
        try:
            url, mode = discover_document(client, spec, diagnostics=report_diagnostics)
            candidates.append({"company": spec.company, "kind": "fleet-report", "url": url, "discoveryMode": mode})
            result["errors"].extend({**issue, "company": spec.company, "kind": "fleet-report",
                                     "phase": "discovery", "lastAttemptAt": now} for issue in report_diagnostics)
        except Exception as error:
            result["errors"].append({"company": spec.company, "kind": "fleet-report", "url": spec.index_url,
                                     "phase": "discovery", "error": type(error).__name__, "httpStatus": _http_status(error), "lastAttemptAt": now})
        news_diagnostics: list[dict] = []
        try:
            items, warning = discover_official_news(client, spec, limit=news_limit, diagnostics=news_diagnostics)
            candidates.extend({**item, "company": spec.company, "kind": "official-news"} for item in items)
            result["errors"].extend({**issue, "company": spec.company, "kind": "official-news",
                                     "phase": "discovery", "lastAttemptAt": now} for issue in news_diagnostics)
            if warning and not news_diagnostics:
                result["errors"].append({"company": spec.company, "kind": "official-news", "url": spec.news_url,
                                         "phase": "discovery", "error": warning, "httpStatus": None, "lastAttemptAt": now})
        except Exception as error:
            result["errors"].append({"company": spec.company, "kind": "official-news", "url": spec.news_url,
                                     "phase": "discovery", "error": type(error).__name__, "httpStatus": _http_status(error), "lastAttemptAt": now})
    candidates.extend({**item, "kind": "official-news", "discoveryMode": "preserved-official-news"} for item in preserved_news)
    candidates.extend({**item, "discoveryMode": PRESERVED_MATERIAL_DISCOVERY} for item in preserved_materials)
    seen = set()
    for candidate in candidates:
        company, kind, url = candidate.get("company"), candidate.get("kind"), candidate.get("url")
        if company not in _ISSUER_HOSTS or kind not in {"fleet-report", "official-news"} or not isinstance(url, str) or not url:
            result["errors"].append({"company": company, "kind": kind, "url": url,
                                     "phase": "collection", "error": "invalid source candidate metadata",
                                     "httpStatus": None, "lastSuccessAt": None, "lastAttemptAt": now})
            continue
        identity = source_id(company, kind, url)
        if identity in seen:
            continue
        seen.add(identity)
        previous = previous_sources.get(identity, {})
        record = {
            "sourceId": identity, "company": company, "kind": kind, "url": url,
            "title": candidate.get("title") or previous.get("title"),
            "publishedAt": publication_date(candidate.get("publishedAt")) or previous.get("publishedAt"),
            "discoveryMode": candidate.get("discoveryMode"), "lastAttemptAt": now,
            "lastSuccessAt": previous.get("lastSuccessAt"),
            "documentSha256": previous.get("documentSha256"), "sourceSha256": previous.get("sourceSha256"),
            "lastSuccessfulDocumentSha256": previous.get("lastSuccessfulDocumentSha256", previous.get("documentSha256")),
            "bodyRead": False, "httpStatus": None, "error": None, "changed": None,
            "documentChanged": None, "readingInputChanged": None, "extractionVersion": EXTRACTION_VERSION,
        }
        try:
            validate_official_source(company, url)
            response = client.get(url)
            record["httpStatus"] = response.status_code
            record["finalUrl"] = response.url
            validate_official_source(company, response.url)
            record["documentSha256"] = document_sha256(response.content)
            record["documentChanged"] = record["lastSuccessfulDocumentSha256"] != record["documentSha256"]
            extraction_warnings: list[str] = []
            pages = extract_document_pages(response.content, response.headers.get("content-type", ""), kind=kind, page_ocr=page_ocr,
                                           extraction_warnings=extraction_warnings)
            record["extractionWarnings"] = extraction_warnings
            if record["publishedAt"] is None and not response.content.lstrip().startswith(b"%PDF-"):
                record["publishedAt"] = html_publication_date(response.content)
            reading_hash = source_sha256(pages)
            record.update({"bodyRead": True, "lastSuccessAt": now, "sourceSha256": reading_hash,
                           "lastSuccessfulDocumentSha256": record["documentSha256"],
                           "changed": previous.get("sourceSha256") != reading_hash,
                           "readingInputChanged": previous.get("sourceSha256") != reading_hash})
            request = {
                "sourceId": identity, "sourceSha256": reading_hash, "company": company, "kind": kind,
                "url": response.url, "publishedAt": record["publishedAt"], "pages": pages,
                "baseline": deepcopy(baseline), "priorEvidence": deepcopy(prior_evidence),
                "contextSha256": context, "readerPolicySha256": reader_policy_sha256,
            }
            result["materials"].append({"readingInput": request, "documentSha256": record["documentSha256"],
                                        "retrievedAt": now, "discoveryMode": record["discoveryMode"],
                                        "extractionVersion": EXTRACTION_VERSION, "extractionWarnings": extraction_warnings})
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            record["httpStatus"] = _http_status(error) or record["httpStatus"]
            result["errors"].append(record)
        result["sources"].append(record)
    return result
