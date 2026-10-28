"""Source discovery/HTTP safety checks and payload pruning (no network)."""
from __future__ import annotations

import pytest
import requests

from automation.fleet_sync import sources as source_module
from automation.fleet_sync.pipeline import _prune_obsolete_payloads
from automation.fleet_sync.sources import HttpClient, SOURCE_SPECS, fetch_article


def test_valaris_prefers_stable_official_index_before_sec(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = next(item for item in SOURCE_SPECS if item.company == "Valaris")
    calls: list[str] = []

    def official(_client: object, _spec: object) -> str:
        calls.append("official")
        return "https://s23.q4cdn.com/report.pdf"

    def sec(_client: object) -> str:
        calls.append("sec")
        raise AssertionError("SEC should not be queried after official discovery succeeds")

    monkeypatch.setattr(source_module, "_discover_valaris_official", official)
    monkeypatch.setattr(source_module, "_discover_valaris_sec", sec)
    url, mode = source_module.discover_document(object(), spec)  # type: ignore[arg-type]
    assert (url, mode) == ("https://s23.q4cdn.com/report.pdf", "official-index")
    assert calls == ["official"]


def test_obsolete_content_addressed_payloads_are_pruned(tmp_path) -> None:
    for name in (
        "fleet.keep.json",
        "events.keep.json",
        "fleet.old.json",
        "events.old.json",
        "manifest.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    _prune_obsolete_payloads(tmp_path, {"fleet.keep.json", "events.keep.json"})

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "events.keep.json",
        "fleet.keep.json",
        "manifest.json",
    ]


def test_news_reader_extracts_article_not_navigation_or_scripts() -> None:
    class Client:
        def get(self, url):
            return _response(url, 200, (
                '<nav>unrelated stock data</nav><article class="news">'
                + 'An official contract announcement with source terms. ' * 10
                + '<script>untrusted script</script></article>'
            ).encode())
    text, sha = fetch_article(Client(), "https://www.deepwater.com/news/detail?ID=1")
    assert "official contract" in text
    assert "unrelated stock" not in text and "untrusted script" not in text
    assert len(sha) == 64


class _FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def request(self, method: str, url: str, **_: object) -> requests.Response:
        self.calls.append(url)
        return self.responses.pop(0)


def _response(url: str, status: int, body: bytes = b"", **headers: str) -> requests.Response:
    response = requests.Response()
    response.url = url
    response.status_code = status
    response.headers.update(headers)
    response._content = body
    response._content_consumed = True
    return response


def test_http_client_rejects_redirect_before_unallowlisted_request() -> None:
    client = HttpClient()
    session = _FakeSession(
        [
            _response(
                "https://www.deepwater.com/start",
                302,
                location="https://example.com/escape",
            )
        ]
    )
    client.session = session  # type: ignore[assignment]
    with pytest.raises(ValueError, match="not allowlisted"):
        client.get("https://www.deepwater.com/start")
    assert session.calls == ["https://www.deepwater.com/start"]


def test_http_client_stops_stream_at_hard_byte_cap() -> None:
    client = HttpClient()
    session = _FakeSession(
        [_response("https://www.deepwater.com/report.pdf", 200, b"123456")]
    )
    client.session = session  # type: ignore[assignment]
    with pytest.raises(ValueError, match="exceeds 5 bytes"):
        client._request(
            "GET",
            "https://www.deepwater.com/report.pdf",
            timeout=1,
            byte_limit=5,
        )
