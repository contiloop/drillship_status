from __future__ import annotations

import pytest

from automation.fleet_sync import parsers
from automation.fleet_sync.parsers import (
    _is_transocean_vessel_row,
    _match_seadrill_name,
    _match_transocean_name,
    _noble_operational_note,
    _valaris_rows_to_result,
    parse_valaris_html,
)


class _FakePdf:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def _valaris_row(
    name: str,
    *,
    start: str = "",
    end: str = "",
    comments: str = "",
) -> list[object]:
    return [
        name,
        "Design",
        "2015",
        "Customer",
        "Region",
        start,
        end,
        "$400,000" if start and end else "",
        comments,
    ]


def test_transocean_name_matching_only_accepts_numeric_footnotes() -> None:
    expected = {"Deepwater Atlas"}

    assert _match_transocean_name("Deepwater Atlas", expected) == "Deepwater Atlas"
    assert _match_transocean_name("Deepwater Atlas 1, 2", expected) == "Deepwater Atlas"
    assert _match_transocean_name("Deepwater Atlas II", expected) is None
    assert _is_transocean_vessel_row("Deepwater Atlas II", "2026", "Firm")
    assert not _is_transocean_vessel_row("1, 2", "", "Firm")


def test_transocean_parser_fails_on_unknown_drillship_row(monkeypatch) -> None:
    class Page:
        width = 1224.0
        page_number = 4

        def extract_text(self) -> str:
            return "Fleet Status Report August 5, 2026 Ultra-Deepwater Drillships"

    cells = iter(
        [
            ["Known Rig", "", "", "2020", "", "", "Idle", "", "", "", ""],
            ["New Rig", "", "", "2026", "", "", "Idle", "", "", "", ""],
        ]
    )
    monkeypatch.setattr(parsers.pdfplumber, "open", lambda *_args, **_kwargs: _FakePdf([Page()]))
    monkeypatch.setattr(parsers, "_group_word_lines", lambda _page: [(1.0, []), (2.0, [])])
    monkeypatch.setattr(parsers, "_cells_for_line", lambda _words, _boundaries: next(cells))

    with pytest.raises(ValueError, match=r"Transocean.*New Rig"):
        parsers.parse_transocean_pdf(b"ignored", {"Known Rig"})


def test_valaris_selected_table_rejects_unknown_drillship() -> None:
    with pytest.raises(ValueError, match=r"Valaris.*VALARIS DS-19"):
        _valaris_rows_to_result(
            [_valaris_row("VALARIS DS-18"), _valaris_row("VALARIS DS-19")],
            [],
            {"VALARIS DS-18"},
            "2026-08-05",
            source_page=4,
        )


def test_valaris_warm_stack_note_retains_comment_and_locator() -> None:
    comment = "Rig is warm stacked in Las Palmas, Spain"
    result = _valaris_rows_to_result(
        [_valaris_row("VALARIS DS-15", comments=comment)],
        [],
        {"VALARIS DS-15"},
        "2026-08-05",
        source_page=4,
    )

    vessel = result.vessels["VALARIS DS-15"]
    assert vessel.status == "Warm-Stacked"
    assert vessel.notes == [comment]
    assert vessel.operational_observations[0].page == 4
    assert vessel.operational_observations[0].row == "row=1"


def _valaris_html(rig_name: str) -> bytes:
    return f"""
    <html><body>
      <p>Fleet Status Report August 5, 2026</p>
      <table>
        <tr>
          <td>Asset Category / Rig</td><td>Customer</td><td>Location</td>
          <td>Contract Start Date</td><td>Contract End Date</td>
          <td>Day Rate</td><td>Comments</td>
        </tr>
        <tr><td>Drillships</td></tr>
        <tr>
          <td>{rig_name}</td><td>Customer</td><td>Brazil</td>
          <td>Jul 26</td><td>Dec 26</td><td>$400,000</td><td></td>
        </tr>
        <tr><td>Semisubmersibles</td></tr>
        <tr>
          <td>VALARIS MS-1</td><td>Customer</td><td>Brazil</td>
          <td>Jul 26</td><td>Dec 26</td><td>$200,000</td><td></td>
        </tr>
      </table>
    </body></html>
    """.encode()


def test_valaris_html_uses_nullable_page_and_explicit_html_row_locator() -> None:
    result, report_date = parse_valaris_html(
        _valaris_html("VALARIS DS-18"),
        {"VALARIS DS-18"},
    )

    observation = result.vessels["VALARIS DS-18"].contracts[0]
    assert report_date == "2026-08-05"
    assert observation.page is None
    assert observation.row == "html-table=1,tr=3,slot=1"


def test_valaris_html_rejects_unknown_name_but_excludes_semisub_section() -> None:
    with pytest.raises(ValueError, match=r"Valaris.*VALARIS DS-19"):
        parse_valaris_html(_valaris_html("VALARIS DS-19"), {"VALARIS DS-18"})


def test_noble_held_for_sale_note_does_not_fall_back_to_dash_operator() -> None:
    assert _noble_operational_note("–", "Held for sale.") == "Held for sale."


def test_noble_parser_fails_on_unknown_drillship_row(monkeypatch) -> None:
    class Page:
        width = 960.0
        page_number = 3
        lines: list[dict] = []

        def extract_text(self) -> str:
            return "Fleet Status Report August 5, 2026 Drillships (7G)"

        def extract_table(self, _settings) -> list[list[object]]:
            return [
                ["Known Rig", "Design", "2020", "12,000", "Brazil", "Available", "", "", "", ""],
                ["New Rig", "Design", "2026", "12,000", "Brazil", "Available", "", "", "", ""],
            ]

    monkeypatch.setattr(parsers.pdfplumber, "open", lambda *_args, **_kwargs: _FakePdf([Page()]))

    with pytest.raises(ValueError, match=r"Noble.*New Rig"):
        parsers.parse_noble_pdf(b"ignored", {"Known Rig"})


def test_seadrill_only_strips_trailing_footnote_for_reviewed_name() -> None:
    expected = {"Sonangol Libongos"}

    assert _match_seadrill_name("Sonangol Libongos1", expected) == "Sonangol Libongos"
    assert _match_seadrill_name("Unknown Drillship1", expected) == "Unknown Drillship1"


def test_seadrill_parser_fails_on_unknown_drillship_row(monkeypatch) -> None:
    class Page:
        width = 960.0
        page_number = 3

        def extract_text(self) -> str:
            return "Fleet Status Report August 5, 2026 Fleet Contract Status"

        def extract_tables(self, _settings) -> list[list[list[object]]]:
            return [[
                ["Known Rig", "2020", "Drillship", "Brazil", "Customer", "Jul-26", "Dec-26", ""],
                ["New Rig", "2026", "Drillship", "Brazil", "Customer", "Jul-26", "Dec-26", ""],
                ["Known Semi", "2020", "Semi-Sub", "Brazil", "Customer", "Jul-26", "Dec-26", ""],
            ]]

    monkeypatch.setattr(parsers.pdfplumber, "open", lambda *_args, **_kwargs: _FakePdf([Page()]))

    with pytest.raises(ValueError, match=r"Seadrill.*New Rig"):
        parsers.parse_seadrill_pdf(b"ignored", {"Known Rig"})
