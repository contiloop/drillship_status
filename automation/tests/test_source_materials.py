from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from automation.fleet_sync import source_materials as materials
from automation.fleet_sync.sources import SOURCE_SPECS
from automation.fleet_sync.reading_contracts import source_sha256

REAL_RENDER_PAGE_IMAGE = materials.render_page_image


class Response:
    def __init__(self, url, body, content_type='text/html'):
        self.url = url
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode(errors='replace')
        self.headers = {'content-type': content_type}
        self.status_code = 200


class Client:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url):
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def fake_render_for_fake_pdf_pages(monkeypatch):
    original = materials.render_page_image
    def render(page, *, locator):
        if isinstance(page, SimpleNamespace):
            import base64
            content = b'fake-page-image-' + locator.encode()
            return {'mimeType': 'image/png', 'sha256': materials.document_sha256(content), 'dataBase64': base64.b64encode(content).decode()}
        return original(page, locator=locator)
    monkeypatch.setattr(materials, 'render_page_image', render)


def article(text='All shareholders are invited to the company meeting. ' * 12):
    return f'<html><head><title>Company announcement</title></head><body><nav>Menu</nav><article><h1>Company announcement</h1><p>{text}</p></article></body></html>'


def collect(client, monkeypatch, *, specs=SOURCE_SPECS[:1], previous_sources=None, preserved_news=()):
    monkeypatch.setattr(materials, 'discover_document', lambda c, s, **kwargs: (s.fallback_document_url, 'official-index'))
    monkeypatch.setattr(materials, 'discover_official_news', lambda c, s, **kw: ([], None))
    return materials.collect_reading_materials(
        client, baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64,
        specs=specs, previous_sources=previous_sources, preserved_news=preserved_news,
    )


def test_extracts_every_pdf_page_and_distinguishes_document_hash(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'First page full text'), SimpleNamespace(extract_text=lambda: 'Second page full text')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report')
    assert [{'locator': p['locator'], 'text': p['text']} for p in pages] == [{'locator': 'page:1', 'text': 'First page full text'}, {'locator': 'page:2', 'text': 'Second page full text'}]
    assert all(page['image']['mimeType'] == 'image/png' for page in pages)
    assert source_sha256(pages) != materials.document_sha256(b'%PDF-original')


def test_page_without_text_remains_present_as_original_image(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'Readable'), SimpleNamespace(extract_text=lambda: '')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report')
    assert len(pages) == 2 and pages[1]['text'] == ''
    assert pages[1]['image']['dataBase64']


def test_failure_isolated_and_prior_success_preserved(monkeypatch):
    transocean, valaris = SOURCE_SPECS[:2]
    error = requests.HTTPError('403 denied', response=SimpleNamespace(status_code=403))
    responses = {transocean.fallback_document_url: error, valaris.fallback_document_url: Response(valaris.fallback_document_url, article())}
    previous = {materials.source_id(transocean.company, 'fleet-report', transocean.fallback_document_url): {'lastSuccessAt': '2026-10-01T00:00:00Z', 'sourceSha256': 'b' * 64}}
    result = collect(Client(responses), monkeypatch, specs=(transocean, valaris), previous_sources=previous)
    assert len(result['materials']) == 1
    assert result['materials'][0]['readingInput']['company'] == 'Valaris'
    failed = next(source for source in result['sources'] if source['company'] == 'Transocean')
    assert failed['httpStatus'] == 403 and failed['error']
    assert failed['lastSuccessAt'] == '2026-10-01T00:00:00Z'
    assert failed['sourceSha256'] == 'b' * 64
    assert len(result['errors']) == 1


def test_same_url_changed_body_creates_new_source_hash(monkeypatch):
    spec = SOURCE_SPECS[0]
    url = spec.fallback_document_url
    first = collect(Client({url: Response(url, article())}), monkeypatch)
    old = first['sources'][0]
    second = collect(Client({url: Response(url, article('Different company-wide announcement. ' * 20))}), monkeypatch, previous_sources={old['sourceId']: old})
    assert old['sourceId'] == second['sources'][0]['sourceId']
    assert old['sourceSha256'] != second['sources'][0]['sourceSha256']
    assert second['sources'][0]['changed'] is True
    third = collect(Client({url: Response(url, article())}), monkeypatch, previous_sources={old['sourceId']: old})
    assert third['sources'][0]['changed'] is False


def test_news_without_contract_keywords_is_read_and_parser_never_called(monkeypatch):
    from automation.fleet_sync import parsers
    for name in dir(parsers):
        if name.startswith('parse_'):
            monkeypatch.setattr(parsers, name, lambda *a, **kw: pytest.fail('collector invoked a semantic parser'))
    spec = SOURCE_SPECS[0]
    url = 'https://www.deepwater.com/news/detail?ID=99'
    result = collect(Client({spec.fallback_document_url: Response(spec.fallback_document_url, article()), url: Response(url, article())}), monkeypatch, preserved_news=[{'company': spec.company, 'url': url, 'title': 'Annual meeting invitation'}])
    news = [item for item in result['materials'] if item['readingInput']['kind'] == 'official-news']
    assert len(news) == 1
    assert 'shareholders' in news[0]['readingInput']['pages'][0]['text']


@pytest.mark.parametrize('body', ['<article><h1>Announcement title only</h1></article>', '<main><h1>Access Denied</h1><p>' + 'Error ' * 100 + '</p></main>', '<html></html>'])
def test_title_only_empty_and_http_200_error_pages_fail(body):
    with pytest.raises(ValueError):
        materials.extract_document_pages(body.encode(), 'text/html', kind='official-news')


def test_cross_issuer_and_external_sources_rejected():
    for url in ['https://example.com/a', 'https://www.valaris.com/news/a', 'https://s23.q4cdn.com/other-tenant/a.pdf', 'https://www.sec.gov/Archives/edgar/data/9999/a.htm']:
        with pytest.raises(ValueError):
            materials.validate_official_source('Transocean', url)


def test_preserved_news_failure_does_not_hide_report(monkeypatch):
    spec = SOURCE_SPECS[0]
    url = 'https://www.deepwater.com/news/detail?ID=404'
    result = collect(Client({spec.fallback_document_url: Response(spec.fallback_document_url, article()), url: Response(url, '<article><h1>Title only</h1></article>')}), monkeypatch, preserved_news=[{'company': spec.company, 'url': url}])
    assert len(result['materials']) == 1
    assert result['errors'][0]['kind'] == 'official-news'
    assert result['errors'][0]['lastSuccessAt'] is None


def test_discovery_does_not_filter_meeting_or_director_headlines():
    from automation.fleet_sync.sources import discover_official_news
    spec = SOURCE_SPECS[0]
    client = Client({spec.news_url: Response(spec.news_url, '<table><tr><td>October 20, 2026</td><td><a href="news/detail?ID=1">Annual general meeting</a></td></tr><tr><td><a href="news/detail?ID=2">New director appointment</a></td></tr></table>')})
    discovered, error = discover_official_news(client, spec)
    assert error is None
    assert len(discovered) == 2
    assert discovered[0]['publishedAt'] == '2026-10-20'


def test_q4_public_get_returns_every_headline_and_does_not_fetch_snippets():
    from automation.fleet_sync.sources import discover_official_news
    class Q4Client:
        def get(self, url):
            assert '/feed/PressRelease.svc/GetPressReleaseList?' in url
            assert 'pageSize=20' in url and 'pressReleaseDateFilter=3' in url
            return SimpleNamespace(json=lambda: {'GetPressReleaseListResult': [{'Headline': 'Annual general meeting voting', 'LinkToDetailPage': '/news/news-details/2026/meeting/default.aspx', 'PressReleaseDate': '10/20/2026 10:00:00'}]})
    discovered, error = discover_official_news(Q4Client(), SOURCE_SPECS[1])
    assert error is None and len(discovered) == 1
    assert discovered[0]['publishedAt'] == '2026-10-20'


def test_bad_preserved_metadata_does_not_abort_other_materials(monkeypatch):
    spec = SOURCE_SPECS[0]
    result = collect(Client({spec.fallback_document_url: Response(spec.fallback_document_url, article())}), monkeypatch, preserved_news=[{'company': 'Transocean'}])
    assert len(result['materials']) == 1
    assert result['errors'][0]['error'] == 'invalid source candidate metadata'


def test_html_publication_metadata_and_complete_tail_are_retained(monkeypatch):
    spec = SOURCE_SPECS[0]
    url = 'https://www.deepwater.com/news/detail?ID=2'
    html = article('Publication body. ' * 30 + 'This is the complete final paragraph.').replace('<head>', '<head><meta property="article:published_time" content="2026-10-20T09:00:00Z">')
    result = collect(Client({spec.fallback_document_url: Response(spec.fallback_document_url, article()), url: Response(url, html)}), monkeypatch, preserved_news=[{'company': spec.company, 'url': url}])
    request = result['materials'][1]['readingInput']
    assert request['publishedAt'] == '2026-10-20T09:00:00+00:00'
    assert request['pages'][0]['text'].endswith('This is the complete final paragraph.')


def test_final_redirect_to_another_issuer_cannot_be_read(monkeypatch):
    spec = SOURCE_SPECS[0]
    result = collect(Client({spec.fallback_document_url: Response('https://www.valaris.com/news/a', article())}), monkeypatch)
    assert not result['materials']
    assert result['sources'][0]['httpStatus'] == 200
    assert result['sources'][0]['bodyRead'] is False
    assert result['sources'][0]['lastSuccessAt'] is None


def test_pdf_header_mismatch_cannot_be_read_as_html():
    with pytest.raises(ValueError, match='did not contain a PDF'):
        materials.extract_document_pages(article().encode(), 'application/pdf', kind='fleet-report')


def test_ocr_port_recovers_only_missing_pages_without_skipping(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'Native text'), SimpleNamespace(extract_text=lambda: '')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class OCR:
        def extract(self, page, *, locator):
            assert locator == 'page:2'
            return 'Mechanically recognized image text'
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report', page_ocr=OCR())
    assert [p['locator'] for p in pages] == ['page:1', 'page:2']
    assert pages[1]['text'] == 'Mechanically recognized image text'


def test_empty_ocr_keeps_original_image_without_made_up_text(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: '')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class OCR:
        def extract(self, page, *, locator): return ''
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report', page_ocr=OCR())
    assert len(pages) == 1 and pages[0]['text'] == '' and pages[0]['image']


def test_original_images_affect_content_hash_even_with_identical_text():
    first = [{'locator': 'page:1', 'text': 'same', 'image': {'mimeType': 'image/png', 'sha256': 'a' * 64, 'dataBase64': 'YQ=='}}]
    second = [{'locator': 'page:1', 'text': 'same', 'image': {'mimeType': 'image/png', 'sha256': 'b' * 64, 'dataBase64': 'Yg=='}}]
    assert source_sha256(first) != source_sha256(second)


def test_render_failure_fails_whole_document_instead_of_dropping_a_page(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'First'), SimpleNamespace(extract_text=lambda: 'Second')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def render(page, *, locator):
        if locator == 'page:2': raise ValueError('render failed at page:2')
        return {'mimeType': 'image/png', 'sha256': 'a' * 64, 'dataBase64': 'YQ=='}
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    monkeypatch.setattr(materials, 'render_page_image', render)
    with pytest.raises(ValueError, match='render failed at page:2'):
        materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report')


def test_failed_ocr_is_reported_while_original_page_image_is_kept(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: '')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class OCR:
        def extract(self, page, *, locator): raise ValueError('OCR text unavailable')
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    warnings = []
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report', page_ocr=OCR(), extraction_warnings=warnings)
    assert pages[0]['text'] == '' and pages[0]['image']
    assert warnings == ['page:1: ValueError: OCR text unavailable']


def test_document_image_budget_is_atomic(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'First'), SimpleNamespace(extract_text=lambda: 'Second')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    monkeypatch.setattr(materials, 'MAX_DOCUMENT_IMAGE_BYTES', 1)
    with pytest.raises(ValueError, match='PDF images exceed 1 bytes'):
        materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report')


def test_ocr_missing_dependency_is_explicit(monkeypatch):
    from automation.fleet_sync import document_ocr
    monkeypatch.setattr(document_ocr.shutil, 'which', lambda _: None)
    with pytest.raises(ValueError, match='install Tesseract 5'):
        document_ocr.TesseractPageOCR().extract(object(), locator='page:2')


def test_noble_official_pdf_alternative_keeps_blocked_identity_separate():
    from automation.fleet_sync.sources import discover_official_news
    spec = SOURCE_SPECS[2]
    direct = 'https://noble.gcs-web.com/news-releases/news-release-details/results'
    pdf = 'https://noblecorp.com/wp-content/uploads/2026/10/release.pdf'
    result, warning = discover_official_news(Client({spec.news_url: Response(spec.news_url, f'<a href="{direct}">Noble results</a>'), 'https://noblecorp.com/our-investors/reports-filings/quarterly-results/': Response('https://noblecorp.com/our-investors/reports-filings/quarterly-results/', f'<a href="{pdf}">Earnings Release</a>')}), spec)
    assert warning is None
    assert [item['url'] for item in result] == [direct, pdf]
    assert result[1]['discoveryMode'] == 'official-quarterly-release-pdf'


def test_real_page_renderer_returns_valid_bounded_png():
    import base64
    from PIL import Image
    image = Image.new('RGB', (12, 8), 'white')
    class Page:
        width = 6
        height = 4
        def to_image(self, *, resolution):
            assert resolution == materials.PDF_IMAGE_DPI
            return SimpleNamespace(original=image)
    rendered = REAL_RENDER_PAGE_IMAGE(Page(), locator='page:1')
    content = base64.b64decode(rendered['dataBase64'], validate=True)
    assert content.startswith(b'\x89PNG\r\n\x1a\n')
    assert rendered['sha256'] == materials.document_sha256(content)


def test_large_page_is_rejected_before_allocating_an_image():
    class Page:
        width = 100000
        height = 100000
        def to_image(self, **kwargs): pytest.fail('oversize page rendered before limit')
    with pytest.raises(ValueError, match='pixels at page:1'):
        REAL_RENDER_PAGE_IMAGE(Page(), locator='page:1')


def test_ocr_keeps_readable_lines_and_all_word_coordinates(monkeypatch):
    from PIL import Image
    from automation.fleet_sync import document_ocr
    monkeypatch.setattr(document_ocr.shutil, 'which', lambda _: '/verified/tesseract')
    tsv = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t96\tDeepwater\n5\t1\t1\t1\t1\t2\t42\t20\t20\t40\t45\tTitan\n'
    def run(command, **kwargs):
        assert command[0] == '/verified/tesseract' and 'tsv' in command
        assert kwargs['input'].startswith(b'\x89PNG')
        return SimpleNamespace(returncode=0, stdout=tsv.encode(), stderr=b'')
    monkeypatch.setattr(document_ocr.subprocess, 'run', run)
    page = SimpleNamespace(to_image=lambda **kwargs: SimpleNamespace(original=Image.new('RGB', (2, 2))))
    text = document_ocr.TesseractPageOCR().extract(page, locator='page:1')
    assert 'Deepwater Titan' in text
    assert '[42,20,20,40; confidence=45] Titan' in text


def test_html_retains_article_header_and_footer_disclosures():
    content = ('<header>site navigation</header><article><header><h1>Release title</h1></header>'
               '<p>' + 'Full issuer body text. ' * 12 + '</p><footer>Disclosure with material release terms.</footer></article><footer>site footer</footer>')
    pages = materials.extract_document_pages(content.encode(), 'text/html', kind='official-news')
    assert 'Release title' in pages[0]['text']
    assert 'Disclosure with material release terms.' in pages[0]['text']
    assert 'site navigation' not in pages[0]['text'] and 'site footer' not in pages[0]['text']


def test_blocked_noble_news_index_does_not_block_official_release_index():
    from automation.fleet_sync.sources import discover_official_news
    spec = SOURCE_SPECS[2]
    quarterly = 'https://noblecorp.com/our-investors/reports-filings/quarterly-results/'
    pdf = 'https://noblecorp.com/wp-content/uploads/2026/10/release.pdf'
    calls = []
    class NobleClient:
        def get(self, url):
            calls.append(url)
            if url == spec.news_url:
                raise requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403))
            assert url == quarterly
            return Response(url, f'<a href="{pdf}">Earnings Release</a>')
    events, warning = discover_official_news(NobleClient(), spec)
    assert [event['url'] for event in events] == [pdf]
    assert calls == [spec.news_url, quarterly]
    assert warning and '403' in warning


def test_malformed_q4_item_cannot_discard_valid_peers_or_trigger_html_fallback():
    from automation.fleet_sync.sources import discover_official_news
    spec = SOURCE_SPECS[1]
    calls = []
    class Q4Client:
        def get(self, url):
            calls.append(url)
            if '/feed/' in url:
                return SimpleNamespace(json=lambda: {'GetPressReleaseListResult': [
                    {'Headline': 'Announcement A', 'LinkToDetailPage': '/news/news-details/2026/a/default.aspx'},
                    None,
                    {'Headline': 'Announcement B', 'LinkToDetailPage': '/news/news-details/2026/b/default.aspx'},
                ]})
            raise requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403))
    events, warning = discover_official_news(Q4Client(), spec)
    assert [event['title'] for event in events] == ['Announcement A', 'Announcement B']
    assert len(calls) == 1
    assert warning and 'item' in warning.lower()


def test_report_discovery_http_status_survives_successful_fallback(monkeypatch):
    spec = SOURCE_SPECS[0]
    monkeypatch.setattr(materials, 'discover_official_news', lambda *args, **kwargs: ([], None))
    client = Client({
        spec.index_url: requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403)),
        spec.fallback_document_url: Response(spec.fallback_document_url, article()),
    })
    result = materials.collect_reading_materials(client, baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64, specs=(spec,))
    assert len(result['materials']) == 1
    assert result['sources'][0]['bodyRead'] is True and result['sources'][0]['httpStatus'] == 200
    failures = [error for error in result['errors'] if error.get('phase') == 'discovery']
    assert len(failures) == 1 and failures[0]['httpStatus'] == 403
    assert failures[0]['url'] == spec.index_url


def test_noble_fallback_material_and_primary_403_diagnostic_coexist(monkeypatch):
    spec = SOURCE_SPECS[2]
    quarterly = 'https://noblecorp.com/our-investors/reports-filings/quarterly-results/'
    pdf = 'https://noblecorp.com/wp-content/uploads/2026/10/release.pdf'
    monkeypatch.setattr(materials, 'discover_document', lambda *args, **kwargs: (spec.fallback_document_url, 'official-index'))
    client = Client({
        spec.fallback_document_url: Response(spec.fallback_document_url, article()),
        spec.news_url: requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403)),
        quarterly: Response(quarterly, f'<a href="{pdf}">Earnings Release</a>'),
        pdf: Response(pdf, article()),
    })
    result = materials.collect_reading_materials(client, baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64, specs=(spec,))
    assert len(result['materials']) == 2
    successful = next(source for source in result['sources'] if source['url'] == pdf)
    assert successful['bodyRead'] is True and successful['httpStatus'] == 200
    assert len(result['errors']) == 1
    assert result['errors'][0]['url'] == spec.news_url and result['errors'][0]['httpStatus'] == 403
    assert result['errors'][0]['stage'] == 'official-news-index'


def test_q4_malformed_diagnostic_is_structured_and_good_bodies_are_collected(monkeypatch):
    spec = SOURCE_SPECS[1]
    first = 'https://www.valaris.com/news/news-details/2026/a/default.aspx'
    second = 'https://www.valaris.com/news/news-details/2026/b/default.aspx'
    monkeypatch.setattr(materials, 'discover_document', lambda *args, **kwargs: (spec.fallback_document_url, 'official-index'))
    class Q4Client:
        def get(self, url):
            if '/feed/' in url:
                return SimpleNamespace(json=lambda: {'GetPressReleaseListResult': [
                    {'Headline': 'Announcement A', 'LinkToDetailPage': first}, None,
                    {'Headline': 'Announcement B', 'LinkToDetailPage': second},
                ]})
            assert url in {spec.fallback_document_url, first, second}
            return Response(url, article())
    result = materials.collect_reading_materials(Q4Client(), baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64, specs=(spec,))
    assert len(result['materials']) == 3
    assert len(result['errors']) == 1
    assert result['errors'][0]['itemIndex'] == 1
    assert result['errors'][0]['errorType'] == 'ValueError'
    assert result['errors'][0]['httpStatus'] is None
    assert {source['url'] for source in result['sources']} >= {first, second}


def test_report_failed_index_and_failed_fallback_are_separate(monkeypatch):
    spec = SOURCE_SPECS[0]
    monkeypatch.setattr(materials, 'discover_official_news', lambda *args, **kwargs: ([], None))
    client = Client({
        spec.index_url: requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403)),
        spec.fallback_document_url: requests.HTTPError('Unavailable', response=SimpleNamespace(status_code=503)),
    })
    result = materials.collect_reading_materials(client, baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64, specs=(spec,))
    assert result['materials'] == []
    assert [failure['httpStatus'] for failure in result['errors']] == [403, 503]
    assert result['errors'][0]['url'] == spec.index_url
    assert result['errors'][1]['url'] == spec.fallback_document_url
    assert result['sources'][0]['bodyRead'] is False


def test_legacy_report_discovery_tuple_keeps_compatibility_and_optional_http_diagnostic(monkeypatch):
    from automation.fleet_sync import sources
    spec = SOURCE_SPECS[1]
    def blocked(*args):
        raise requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403))
    monkeypatch.setattr(sources, '_discover_valaris_official', blocked)
    monkeypatch.setattr(sources, '_discover_valaris_sec', lambda _: 'https://www.sec.gov/Archives/edgar/data/314808/report.htm')
    expected = ('https://www.sec.gov/Archives/edgar/data/314808/report.htm', 'sec-submissions-after-official-HTTPError')
    assert sources.discover_document(object(), spec) == expected
    diagnostics = []
    assert sources.discover_document(object(), spec, diagnostics=diagnostics) == expected
    assert len(diagnostics) == 1 and diagnostics[0]['httpStatus'] == 403
    assert diagnostics[0]['url'] == spec.index_url


def test_generic_preserved_materials_refetch_news_and_old_reports_with_official_validation(monkeypatch):
    old_report = 'https://noblecorp.com/old-report.pdf'
    old_news = 'https://noblecorp.com/old-news'
    result = materials.collect_reading_materials(
        Client({old_report: Response(old_report, article()), old_news: Response(old_news, article())}),
        baseline=[], prior_evidence=[], reader_policy_sha256='a'*64, specs=(),
        preserved_materials=[dict(company='Noble', kind='fleet-report', url=old_report),
                             dict(company='Noble', kind='official-news', url=old_news),
                             dict(company='Noble', kind='official-news', url='https://evil.example/body')])
    assert {m['readingInput']['url'] for m in result['materials']} == {old_report, old_news}
    assert result['errors'][0]['url'] == 'https://evil.example/body'
    assert 'not allowlisted' in result['errors'][0]['error']


def test_inline_dom_flow_preserves_words_amounts_and_punctuation_without_inserted_spaces():
    sentence = ('The <em>Deepwater Proteus</em> works in the <location>U.S</location>. Gulf and '
                '<a>India</a>. Revenue is $<span>180</span>,<strong>000</strong>; '
                'start <span>2026</span>-<span>10</span>-28; co<span>oper</span>ation continues.')
    expected = ('The Deepwater Proteus works in the U.S. Gulf and India. Revenue is $180,000; '
                'start 2026-10-28; cooperation continues.')
    pages = materials.extract_document_pages(f'<article><p>{sentence}</p></article>'.encode(), 'text/html', kind='official-news')
    assert pages[0]['text'] == expected


def test_block_dom_boundaries_separate_words_without_splitting_inline_text():
    tail = 'Complete publisher body text remains available to the reader. ' * 3
    body = f'<article><h2>Heading</h2><div>First<p>Second<br/>Third</p>Fourth</div><ul><li>Fifth</li><li>Sixth</li></ul><p>{tail}</p></article>'
    pages = materials.extract_document_pages(body.encode(), 'text/html', kind='official-news')
    assert pages[0]['text'] == 'Heading First Second Third Fourth Fifth Sixth ' + tail.strip()


def test_table_cells_remain_separate_and_inline_currency_tokens_remain_joined():
    tail = 'Full publisher disclosure text follows this complete table. ' * 3
    body = ('<article><table><thead><tr><th>Vessel</th><th>Rate</th></tr></thead><tbody><tr>'
            '<td><em>Deepwater</em> Titan</td><td>$<span>480</span>,<span>000</span></td>'
            f'</tr></tbody></table><p>{tail}</p></article>')
    pages = materials.extract_document_pages(body.encode(), 'text/html', kind='official-news')
    assert pages[0]['text'] == 'Vessel Rate Deepwater Titan $480,000 ' + tail.strip()


def test_pdf_whitespace_normalization_keeps_every_page_and_original_image(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: '  The\nDeepwater\tProteus\r\nworks in the U.S. Gulf.  '),
                 SimpleNamespace(extract_text=lambda: ' Client\nName\t$180,000\n2026-10-28. ')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    expected_images = [materials.render_page_image(page, locator=f'page:{i}') for i, page in enumerate(PDF.pages, 1)]
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report')
    assert [page['locator'] for page in pages] == ['page:1', 'page:2']
    assert [page['text'] for page in pages] == ['The Deepwater Proteus works in the U.S. Gulf.', 'Client Name $180,000 2026-10-28.']
    assert [page['image'] for page in pages] == expected_images


def test_ocr_whitespace_is_normalized_by_the_same_page_boundary(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: '')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class OCR:
        def extract(self, page, *, locator): return '  Mechanically\nrecognized\timage text.  '
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    pages = materials.extract_document_pages(b'%PDF-original', 'application/pdf', kind='fleet-report', page_ocr=OCR())
    assert pages[0]['text'] == 'Mechanically recognized image text.'
    assert pages[0]['image']


def test_dom_flow_extraction_changes_the_reader_policy_identity():
    from automation.fleet_sync.reading_contracts import policy_sha256
    from automation.fleet_sync.reading_service import build_reader_policy
    old_version = 'official-materials-v3-pdf-image144-ocr-tsv'
    assert materials.EXTRACTION_VERSION != old_version
    assert policy_sha256(build_reader_policy(extraction_version=materials.EXTRACTION_VERSION)) != policy_sha256(build_reader_policy(extraction_version=old_version))


def test_legacy_article_hash_matches_old_reader_from_the_same_single_get():
    from automation.fleet_sync.sources import fetch_article
    url = 'https://www.deepwater.com/news/detail?ID=30626'
    body = ('<html><head><script>discard script</script></head><body><nav>discard nav</nav>'
            '<main><article class="news"><header>discard legacy header</header>'
            '<p>The <em>Deepwater Proteus</em> works in the <span>U.S</span>. Gulf. '
            + 'Full original release body including amounts and disclosures. ' * 8
            + '</p><footer>discard legacy footer</footer></article></main></body></html>')
    response = Response(url, body)
    class RecordingClient:
        def __init__(self): self.calls = []
        def get(self, requested):
            self.calls.append(requested)
            return response
    client = RecordingClient()
    result = materials.collect_reading_materials(client, baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64,
                                                 specs=(), preserved_news=[{'company': 'Transocean', 'url': url}])
    expected = fetch_article(Client({url: response}), url)[1]
    material, source = result['materials'][0], result['sources'][0]
    assert client.calls == [url]
    assert material['legacyArticleSha256'] == source['legacyArticleSha256'] == expected
    assert 'legacyArticleSha256' not in material['readingInput']
    assert material['documentSha256'] == materials.document_sha256(response.content)
    assert material['readingInput']['sourceSha256'] == source_sha256(material['readingInput']['pages'])
    assert 'The Deepwater Proteus' in material['readingInput']['pages'][0]['text']
    assert 'U.S. Gulf.' in material['readingInput']['pages'][0]['text']
    assert 'discard legacy footer' in material['readingInput']['pages'][0]['text']


def test_news_pdf_does_not_claim_a_legacy_html_article_hash(monkeypatch):
    class PDF:
        pages = [SimpleNamespace(extract_text=lambda: 'Native PDF page text')]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(materials.pdfplumber, 'open', lambda _: PDF())
    url = 'https://noblecorp.com/wp-content/uploads/2026/10/release.pdf'
    result = materials.collect_reading_materials(Client({url: Response(url, b'%PDF-document', 'application/pdf')}),
                                                 baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64,
                                                 specs=(), preserved_news=[{'company': 'Noble', 'url': url}])
    assert result['materials'][0]['legacyArticleSha256'] is None
    assert result['sources'][0]['legacyArticleSha256'] is None
    assert result['materials'][0]['readingInput']['pages'][0]['image']


def test_unreadable_html_does_not_offer_a_legacy_hash_as_success():
    url = 'https://www.deepwater.com/news/detail?ID=404'
    result = materials.collect_reading_materials(Client({url: Response(url, '<article><h1>Title only</h1></article>')}),
                                                 baseline=[], prior_evidence=[], reader_policy_sha256='a' * 64,
                                                 specs=(), preserved_news=[{'company': 'Transocean', 'url': url}])
    assert result['materials'] == []
    assert result['sources'][0]['legacyArticleSha256'] is None
    assert result['sources'][0]['bodyRead'] is False
