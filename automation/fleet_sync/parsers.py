from __future__ import annotations

import io
import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Iterable

import pdfplumber
from bs4 import BeautifulSoup

from .core import clean_text, number_word, parse_day_rate, split_lines
from .dates import (
    iso,
    next_day,
    parse_long_date,
    parse_period_end_exclusive_boundary,
    parse_period_end_inclusive,
    parse_period_start,
)
from .model import ContractObservation, OperationalObservation, ParseResult, VesselSnapshot


FULL_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b"
)

TRANSOCEAN_ROW_STATUSES = {
    "firm",
    "contingent",
    "priced option",
    "priced options",
    "idle",
    "out of service",
    "stacked",
}


def _raise_unknown_vessels(company: str, names: Iterable[str]) -> None:
    unexpected = sorted(set(names), key=str.casefold)
    if unexpected:
        raise ValueError(f"{company} drillship registry changed; unexpected={unexpected}")


def _match_transocean_name(extracted: str, expected_names: set[str]) -> str | None:
    for name in sorted(expected_names, key=len, reverse=True):
        if extracted == name:
            return name
        if extracted.startswith(f"{name} "):
            # The PDF places numeric footnote markers in the rig-name cell.
            # Only accept that narrow suffix; a real name extension such as
            # "Deepwater Atlas II" must be treated as a new vessel.
            suffix = extracted[len(name) :].strip()
            if re.fullmatch(r"[\d,\s]+", suffix):
                return name
    return None


def _is_transocean_vessel_row(name: str, year: str, status: str) -> bool:
    return bool(re.search(r"[A-Za-z]", name)) and (
        bool(re.fullmatch(r"(?:19|20)\d{2}", clean_text(year)))
        or clean_text(status).casefold() in TRANSOCEAN_ROW_STATUSES
    )


def _append_operational_note(
    vessel: VesselSnapshot,
    note: str,
    *,
    page: int | None,
    row: str,
) -> None:
    normalized = clean_text(note)
    if not normalized:
        return
    vessel.notes.append(normalized)
    vessel.operational_observations.append(
        OperationalObservation(note=normalized, page=page, row=row)
    )


def _noble_operational_note(operator: object, comments: object) -> str | None:
    operator_text = clean_text(operator)
    comments_text = clean_text(comments)
    if "available" in operator_text.casefold():
        return operator_text
    if "held for sale" in comments_text.casefold():
        return comments_text
    return None


def _match_seadrill_name(extracted: str, expected_names: set[str]) -> str:
    if extracted in expected_names:
        return extracted
    # Two Sonangol names carry a superscript footnote marker that pdfplumber
    # emits as a trailing "1".  Strip it only when the remainder is already in
    # the reviewed registry; never normalize an unknown name into silence.
    if extracted.endswith("1") and extracted[:-1] in expected_names:
        return extracted[:-1]
    return extracted


def _report_date_from_pdf(pdf: pdfplumber.PDF) -> str:
    for page in pdf.pages[:2]:
        text = page.extract_text() or ""
        match = FULL_DATE.search(text)
        if match:
            return iso(parse_long_date(match.group(0)))
    raise ValueError("fleet report cover does not contain a report date")


def _expand(values: list[str], count: int, *, take_last: bool = False) -> list[str]:
    if count == 0:
        return []
    if not values:
        return [""] * count
    if len(values) == count:
        return values
    if len(values) > count:
        return values[-count:] if take_last else values[:count]
    if len(values) == 1:
        return values * count
    output = list(values)
    while len(output) < count:
        output.append(output[-1])
    return output


def _group_word_lines(page: pdfplumber.page.Page, tolerance: float = 2.0) -> list[tuple[float, list[dict]]]:
    lines: list[tuple[float, list[dict]]] = []
    for word in page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False):
        for index, (top, words) in enumerate(lines):
            if abs(top - word["top"]) <= tolerance:
                words.append(word)
                lines[index] = ((top + word["top"]) / 2, words)
                break
        else:
            lines.append((word["top"], [word]))
    return sorted(lines, key=lambda item: item[0])


def _cells_for_line(words: list[dict], boundaries: list[float]) -> list[str]:
    cells: list[list[dict]] = [[] for _ in range(len(boundaries) - 1)]
    for word in sorted(words, key=lambda item: item["x0"]):
        for index in range(len(boundaries) - 1):
            if boundaries[index] <= word["x0"] < boundaries[index + 1]:
                cells[index].append(word)
                break
    return [clean_text(" ".join(word["text"] for word in cell)) for cell in cells]


def parse_transocean_pdf(content: bytes, expected_names: set[str]) -> tuple[ParseResult, str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        report_date = _report_date_from_pdf(pdf)
        page = next(
            (item for item in pdf.pages if "Ultra-Deepwater Drillships" in (item.extract_text() or "")),
            None,
        )
        if page is None:
            raise ValueError("Transocean drillship table was not found")

        scale = page.width / 1224.0
        boundaries = [value * scale for value in (35, 280, 350, 450, 510, 600, 690, 770, 840, 920, 1020, 1224)]
        vessels = {name: VesselSnapshot(name=name, status="Idle") for name in expected_names}
        current_name: str | None = None
        seen_names: set[str] = set()
        explicitly_unavailable: set[str] = set()
        unknown_names: set[str] = set()

        for top, words in _group_word_lines(page):
            cells = _cells_for_line(words, boundaries)
            name_cell, _, _, year, region, client, raw_status, start, end, rate, comments = cells
            normalized_name_cell = clean_text(name_cell)
            matched = _match_transocean_name(normalized_name_cell, expected_names)
            if matched:
                current_name = matched
                seen_names.add(matched)
            elif _is_transocean_vessel_row(normalized_name_cell, year, raw_status):
                # Do not let an unknown vessel's continuation rows attach to
                # the preceding known vessel while we wait to fail coverage.
                unknown_names.add(normalized_name_cell)
                current_name = None
            if current_name is None:
                continue

            status_text = clean_text(raw_status).casefold()
            if status_text == "stacked":
                vessels[current_name].status = "Cold-Stacked"
                _append_operational_note(
                    vessels[current_name],
                    clean_text(comments) or "Stacked",
                    page=page.page_number,
                    row=f"y={top:.1f}",
                )
                continue
            if status_text in {"idle", "out of service"}:
                vessels[current_name].status = "Idle"
                explicitly_unavailable.add(current_name)
                _append_operational_note(
                    vessels[current_name],
                    raw_status + " " + comments,
                    page=page.page_number,
                    row=f"y={top:.1f}",
                )
                continue

            status_map = {
                "firm": "Firm",
                "contingent": "Contingent",
                "priced option": "Option",
                "priced options": "Option",
            }
            contract_status = status_map.get(status_text)
            if not contract_status or not start or not end:
                continue
            if region == "India Reliance" and client == "Industries":
                # In this report the long customer name visually overflows into
                # the right-aligned location column.  Keep the published fields
                # semantically intact instead of accepting a coordinate split.
                region, client = "India", "Reliance Industries"
            day_rate, disclosure = parse_day_rate(rate)
            vessels[current_name].contracts.append(
                ContractObservation(
                    client=client if client not in {"", "-"} else "Not Disclosed",
                    region=region if region not in {"", "-"} else "Not Disclosed",
                    start_date=iso(parse_period_start(start)),
                    end_date=iso(parse_period_end_inclusive(end)),
                    day_rate=day_rate,
                    status=contract_status,  # type: ignore[arg-type]
                    page=page.page_number,
                    row=f"y={top:.1f}",
                    rate_disclosure=disclosure,
                )
            )

        _raise_unknown_vessels("Transocean", unknown_names)
        if seen_names != expected_names:
            missing = sorted(expected_names - seen_names)
            unexpected = sorted(seen_names - expected_names)
            raise ValueError(f"Transocean vessel coverage changed; missing={missing}, unexpected={unexpected}")
        for vessel in vessels.values():
            if vessel.name not in explicitly_unavailable and vessel.status not in {"Cold-Stacked", "Warm-Stacked"}:
                vessel.status = "Active" if any(
                    item.status == "Firm" and item.start_date <= report_date <= item.end_date
                    for item in vessel.contracts
                ) else "Idle"
        result = ParseResult(company="Transocean", vessels=vessels)
        result.warnings.append("Source dates have month precision; same-month transitions may overlap in ISO projection.")
        return result, report_date


def _valaris_rows_to_result(
    rows: list[list[object]],
    stacked_rows: list[list[object]],
    expected_names: set[str],
    report_date: str,
    *,
    source_page: int | None,
    row_locators: list[str] | None = None,
    stacked_row_locators: list[str] | None = None,
) -> ParseResult:
    vessels = {name: VesselSnapshot(name=name, status="Idle") for name in expected_names}
    seen: set[str] = set()
    warnings: list[str] = []
    pending: list[dict] = []

    def table_name(row: list[object]) -> str | None:
        name = clean_text((list(row) + [""])[0])
        if not name or name.casefold() in {
            "asset category / rig",
            "drillships",
            "stacked",
        }:
            return None
        return name

    unknown_names = {
        name
        for row in [*rows, *stacked_rows]
        if (name := table_name(row)) is not None and name not in expected_names
    }
    _raise_unknown_vessels("Valaris", unknown_names)

    for row_index, row in enumerate(rows):
        padded = list(row) + [""] * (9 - len(row))
        name = clean_text(padded[0])
        if name not in expected_names:
            continue
        row_locator = (
            row_locators[row_index]
            if row_locators is not None and row_index < len(row_locators)
            else f"row={row_index + 1}"
        )
        seen.add(name)
        clients = split_lines(padded[3])
        locations = split_lines(padded[4])
        starts = split_lines(padded[5])
        ends = split_lines(padded[6])
        rates = split_lines(padded[7])
        comments = clean_text(padded[8])
        count = min(len(starts), len(ends))
        clients = _expand(clients, count)
        locations = _expand(locations, count, take_last=True)
        rates = rates[:count] + [""] * max(0, count - len(rates))
        for index in range(count):
            if (
                name == "VALARIS DS-18"
                and index == 0
                and re.search(r"\bLOA\b", comments)
            ):
                pending.append(
                    {
                        "company": "Valaris",
                        "vessel": name,
                        "classification": "letter-of-award",
                        "start": starts[index],
                        "end": ends[index],
                        "autoApplied": False,
                        "reason": "The report explicitly labels this period as an LOA, not firm backlog.",
                    }
                )
                continue
            day_rate, disclosure = parse_day_rate(rates[index])
            vessels[name].contracts.append(
                ContractObservation(
                    client=clients[index] or "Undisclosed",
                    region=locations[index] or "Undisclosed",
                    start_date=iso(parse_period_start(starts[index])),
                    end_date=iso(parse_period_end_inclusive(ends[index])),
                    day_rate=day_rate,
                    status="Firm",
                    page=source_page,
                    row=f"{row_locator},slot={index + 1}",
                    rate_disclosure=disclosure,
                )
            )
        if "warm stacked" in comments.casefold():
            vessels[name].status = "Warm-Stacked"
            _append_operational_note(
                vessels[name],
                comments,
                page=source_page,
                row=row_locator,
            )
        if re.search(r"\boptions?\b", comments, re.I):
            warnings.append(f"{name}: undated option language is retained as a warning, not an invented interval")

    for row_index, row in enumerate(stacked_rows):
        name = clean_text((list(row) + [""])[0])
        if name in expected_names:
            seen.add(name)
            vessels[name].status = "Cold-Stacked"
            row_locator = (
                stacked_row_locators[row_index]
                if stacked_row_locators is not None and row_index < len(stacked_row_locators)
                else f"row={row_index + 1}"
            )
            _append_operational_note(
                vessels[name],
                "Listed in the official stacked-rig section",
                page=source_page,
                row=row_locator,
            )

    if seen != expected_names:
        raise ValueError(f"Valaris vessel coverage changed; missing={sorted(expected_names - seen)}")
    for vessel in vessels.values():
        if vessel.status not in {"Warm-Stacked", "Cold-Stacked"}:
            vessel.status = "Active" if any(
                item.start_date <= report_date <= item.end_date and item.status == "Firm"
                for item in vessel.contracts
            ) else "Idle"
    return ParseResult(
        company="Valaris",
        vessels=vessels,
        warnings=warnings,
        pending_events=pending,
    )


def parse_valaris_pdf(content: bytes, expected_names: set[str]) -> tuple[ParseResult, str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        report_date = _report_date_from_pdf(pdf)
        page = next(
            (
                item
                for item in pdf.pages
                if "Asset Category / Rig" in (item.extract_text() or "")
                and "VALARIS DS-18" in (item.extract_text() or "")
            ),
            None,
        )
        if page is None:
            raise ValueError("Valaris drillship table was not found")
        tables = page.extract_tables()
        rows = next((table for table in tables if any(clean_text(row[0]) == "VALARIS DS-18" for row in table if row)), None)
        stacked = next((table for table in tables if any(clean_text(row[0]) == "VALARIS DS-14" for row in table if row)), None)
        if rows is None or stacked is None:
            raise ValueError("Valaris active or stacked drillship table was not found")
        return (
            _valaris_rows_to_result(
                rows,
                stacked,
                expected_names,
                report_date,
                source_page=page.page_number,
            ),
            report_date,
        )


def _html_slots(cell) -> list[str]:
    direct_divs = cell.find_all("div", recursive=False)
    if direct_divs:
        return [clean_text(div.get_text(" ")) for div in direct_divs]
    html = cell.decode_contents().replace("<br/>", "\n").replace("<br>", "\n").replace("<br />", "\n")
    return [clean_text(item) for item in BeautifulSoup(html, "lxml").get_text("\n").splitlines()]


def parse_valaris_html(content: bytes, expected_names: set[str]) -> tuple[ParseResult, str]:
    soup = BeautifulSoup(content, "lxml")
    full_text = clean_text(soup.get_text(" "))
    date_match = FULL_DATE.search(full_text)
    if not date_match:
        raise ValueError("Valaris exhibit does not contain a report date")
    report_date = iso(parse_long_date(date_match.group(0)))
    target = None
    target_index: int | None = None
    header_indexes: dict[str, int] = {}
    required = {
        "rig": "asset category / rig",
        "customer": "customer",
        "location": "location",
        "start": "contract start date",
        "end": "contract end date",
        "rate": "day rate",
        "comments": "comments",
    }
    for table_index, table in enumerate(soup.find_all("table")):
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            values = [clean_text(cell.get_text(" ")).casefold() for cell in cells]
            if all(any(label in value for value in values) for label in required.values()):
                target = table
                target_index = table_index
                for key, label in required.items():
                    header_indexes[key] = next(index for index, value in enumerate(values) if label in value)
                break
        if target is not None:
            break
    if target is None:
        raise ValueError("Valaris SEC exhibit fleet table header was not found")

    active_rows: list[list[object]] = []
    stacked_rows: list[list[object]] = []
    active_locators: list[str] = []
    stacked_locators: list[str] = []
    context = ""
    for row_index, row in enumerate(target.find_all("tr")):
        cells = row.find_all("td", recursive=False)
        if not cells or header_indexes["rig"] >= len(cells):
            continue
        rig_text = clean_text(cells[header_indexes["rig"]].get_text(" "))
        section = rig_text.casefold()
        if "stacked" in section and not section.startswith("valaris"):
            context = "stacked"
            continue
        if "drillship" in section and not section.startswith("valaris"):
            context = "drillships"
            continue
        if any(
            label in section
            for label in ("semisub", "semi-sub", "jackup", "jack-up")
        ) and not section.startswith("valaris"):
            context = "other"
            continue
        if max(header_indexes.values()) >= len(cells):
            continue
        is_stacked_drillship = context == "stacked" and bool(
            re.fullmatch(r"VALARIS\s+DS-[A-Za-z0-9-]+", rig_text, re.I)
        )
        if context != "drillships" and not is_stacked_drillship:
            # Stacked semisubs and jackups share the stacked section in SEC
            # exhibits, but their MS/JU names are outside this drillship parser.
            continue
        serialized = [""] * 9
        serialized[0] = rig_text
        serialized[3] = "\n".join(_html_slots(cells[header_indexes["customer"]]))
        serialized[4] = "\n".join(_html_slots(cells[header_indexes["location"]]))
        serialized[5] = "\n".join(_html_slots(cells[header_indexes["start"]]))
        serialized[6] = "\n".join(_html_slots(cells[header_indexes["end"]]))
        serialized[7] = "\n".join(_html_slots(cells[header_indexes["rate"]]))
        serialized[8] = clean_text(cells[header_indexes["comments"]].get_text(" "))
        locator = f"html-table={target_index + 1},tr={row_index + 1}"
        if context == "stacked":
            stacked_rows.append(serialized)
            stacked_locators.append(locator)
        else:
            active_rows.append(serialized)
            active_locators.append(locator)
    return (
        _valaris_rows_to_result(
            active_rows,
            stacked_rows,
            expected_names,
            report_date,
            source_page=None,
            row_locators=active_locators,
            stacked_row_locators=stacked_locators,
        ),
        report_date,
    )


def _add_years_minus_day(start_iso: str, years: int) -> str:
    start = date.fromisoformat(start_iso)
    try:
        boundary = start.replace(year=start.year + years)
    except ValueError:
        boundary = start.replace(year=start.year + years, day=28)
    return (boundary - timedelta(days=1)).isoformat()


def parse_noble_pdf(content: bytes, expected_names: set[str]) -> tuple[ParseResult, str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        report_date = _report_date_from_pdf(pdf)
        vessels = {name: VesselSnapshot(name=name, status="Idle") for name in expected_names}
        seen: set[str] = set()
        unknown_names: set[str] = set()
        page_notes: dict[str, str] = {}
        page_numbers: dict[str, int] = {}
        base_x = [38.8, 127, 216, 258, 326, 409, 482, 548, 608, 675, 921.1]

        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Drillships (" not in text:
                continue
            scale = page.width / 960.0
            horizontals = sorted(
                {
                    line["top"]
                    for line in page.lines
                    if abs(line["y1"] - line["y0"]) < 1
                    and abs(line["x1"] - line["x0"]) > page.width * 0.7
                }
            )
            settings = {
                "vertical_strategy": "explicit",
                "horizontal_strategy": "explicit",
                "explicit_vertical_lines": [value * scale for value in base_x],
                "explicit_horizontal_lines": horizontals,
                "snap_tolerance": 4,
                "join_tolerance": 4,
                "intersection_tolerance": 8,
                "text_tolerance": 2,
            }
            table = page.extract_table(settings) or []
            for row_index, row in enumerate(table):
                padded = list(row) + [""] * (10 - len(row))
                name = clean_text(padded[0])
                built = clean_text(padded[2])
                if (
                    name not in expected_names
                    and re.fullmatch(r"(?:19|20)\d{2}", built)
                    and re.search(r"[A-Za-z]", name)
                ):
                    unknown_names.add(name)
                    continue
                if name not in expected_names:
                    continue
                seen.add(name)
                locations = split_lines(padded[4])
                clients = split_lines(padded[5])
                starts = split_lines(padded[6])
                ends = split_lines(padded[7])
                rates = split_lines(padded[8])
                comments = clean_text(padded[9])
                page_notes[name] = comments
                page_numbers[name] = page.page_number
                count = min(len(starts), len(ends))
                locations = _expand(locations, count)
                clients = _expand(clients, count)
                rates = _expand(rates, count)
                for index in range(count):
                    if starts[index] in {"-", "–", "—"} or ends[index] in {"-", "–", "—"}:
                        continue
                    day_rate, disclosure = parse_day_rate(rates[index])
                    vessels[name].contracts.append(
                        ContractObservation(
                            client=clients[index] or "Undisclosed",
                            region=locations[index] or "Undisclosed",
                            start_date=iso(parse_period_start(starts[index])),
                            end_date=iso(parse_period_end_exclusive_boundary(ends[index])),
                            day_rate=day_rate,
                            status="Firm",
                            page=page.page_number,
                            row=f"row={row_index + 1},slot={index + 1}",
                            rate_disclosure=disclosure,
                        )
                    )
                operational_note = _noble_operational_note(padded[5], comments)
                if operational_note:
                    vessels[name].status = "Idle"
                    _append_operational_note(
                        vessels[name],
                        operational_note,
                        page=page.page_number,
                        row=f"row={row_index + 1}",
                    )

        _raise_unknown_vessels("Noble", unknown_names)
        if seen != expected_names:
            raise ValueError(f"Noble vessel coverage changed; missing={sorted(expected_names - seen)}")

        warnings: list[str] = []
        for name, vessel in vessels.items():
            comments = page_notes.get(name, "")
            firm = sorted(vessel.contracts, key=lambda item: item.end_date)
            if firm:
                last = firm[-1]
                option_years = None
                match = re.search(r"\b(one|two|three|four|five|\d+)\s+1-year\s+(?:priced\s+|unpriced\s+)?options?\b", comments, re.I)
                if match:
                    option_years = number_word(match.group(1))
                if option_years is None:
                    match = re.search(r"\b(one|two|three|four|five|\d+)\s+years?\s+of\s+options?\b", comments, re.I)
                    if match:
                        option_years = number_word(match.group(1))
                if option_years is None and re.search(r"\b1-year\s+(?:priced\s+|unpriced\s+)?option\b", comments, re.I):
                    option_years = 1
                if option_years:
                    option_start = next_day(last.end_date)
                    vessel.contracts.append(
                        ContractObservation(
                            client=last.client,
                            region=last.region,
                            start_date=option_start,
                            end_date=_add_years_minus_day(option_start, option_years),
                            day_rate=0,
                            status="Option",
                            page=page_numbers.get(name, 0),
                            row="option-duration-from-comments",
                            rate_disclosure="undisclosed",
                        )
                    )
                else:
                    through = re.search(r"options?\s+to\s+(Q[1-4][ -]\d{2,4})", comments, re.I)
                    if through:
                        option_start = next_day(last.end_date)
                        vessel.contracts.append(
                            ContractObservation(
                                client=last.client,
                                region=last.region,
                                start_date=option_start,
                                end_date=iso(parse_period_end_exclusive_boundary(through.group(1))),
                                day_rate=0,
                                status="Option",
                                page=page_numbers.get(name, 0),
                                row="option-boundary-from-comments",
                                rate_disclosure="undisclosed",
                            )
                        )
            if re.search(r"\boptions?\b", comments, re.I) and not any(item.status == "Option" for item in vessel.contracts):
                warnings.append(f"{name}: option language has no exact date interval and was not synthesized")
            vessel.status = "Active" if any(
                item.status == "Firm" and item.start_date <= report_date <= item.end_date
                for item in vessel.contracts
            ) else "Idle"
        return ParseResult(company="Noble", vessels=vessels, warnings=warnings), report_date


def _parse_seadrill_rate_bands(row_start: str, notes: str, page: int) -> list[ContractObservation]:
    # Superscript footnote markers are sometimes extracted into a date as
    # "April 1, 1 2027".  Remove that isolated marker before matching dates.
    notes = re.sub(r"(\b[A-Za-z]+\s+\d{1,2},)\s+1\s+(20\d{2}\b)", r"\1 \2", notes)
    pattern = re.compile(
        r"\$(\d{1,3}(?:,\d{3})+)\s+from\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+through\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        re.I,
    )
    matches = list(pattern.finditer(notes))
    if not matches:
        return []
    result: list[ContractObservation] = []
    first_start = parse_long_date(matches[0].group(2))
    row_date = parse_period_start(row_start)
    if row_date < first_start:
        result.append(
            ContractObservation(
                client="Petrobras",
                region="Brazil",
                start_date=iso(row_date),
                end_date=iso(first_start - timedelta(days=1)),
                day_rate=0,
                status="Firm",
                page=page,
                row="rate-band-prior-undisclosed",
                rate_disclosure="undisclosed",
                date_precision="mixed",
            )
        )
    for index, match in enumerate(matches, start=1):
        result.append(
            ContractObservation(
                client="Petrobras",
                region="Brazil",
                start_date=iso(parse_long_date(match.group(2))),
                end_date=iso(parse_long_date(match.group(3))),
                day_rate=int(match.group(1).replace(",", "")),
                status="Firm",
                page=page,
                row=f"rate-band={index}",
                rate_disclosure="reported",
                date_precision="day",
            )
        )
    return result


def parse_seadrill_pdf(content: bytes, expected_names: set[str]) -> tuple[ParseResult, str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        report_date = _report_date_from_pdf(pdf)
        vessels = {name: VesselSnapshot(name=name, status="Idle") for name in expected_names}
        seen: set[str] = set()
        unknown_names: set[str] = set()
        base_x = [30, 130, 190, 260, 330, 415, 468, 520, 930]
        warnings: list[str] = []

        for page in pdf.pages:
            if "Fleet Contract Status" not in (page.extract_text() or ""):
                continue
            scale = page.width / 960.0
            settings = {
                "vertical_strategy": "explicit",
                "horizontal_strategy": "lines",
                "explicit_vertical_lines": [value * scale for value in base_x],
                "snap_tolerance": 4,
                "join_tolerance": 4,
                "intersection_tolerance": 8,
                "text_tolerance": 2,
            }
            for table in page.extract_tables(settings):
                for row_index, row in enumerate(table):
                    padded = list(row) + [""] * (8 - len(row))
                    extracted_name = clean_text(padded[0])
                    name = _match_seadrill_name(extracted_name, expected_names)
                    rig_type = clean_text(padded[2])
                    if rig_type != "Drillship":
                        continue
                    if name not in expected_names:
                        unknown_names.add(
                            extracted_name
                            or f"<blank name at page={page.page_number},row={row_index + 1}>"
                        )
                        continue
                    seen.add(name)
                    location = clean_text(padded[3]) or "Undisclosed"
                    clients = split_lines(padded[4])
                    starts = split_lines(padded[5])
                    ends = split_lines(padded[6])
                    notes = clean_text(padded[7])
                    if name == "West Carina" and "Available" in notes:
                        vessels[name].status = "Idle"
                        _append_operational_note(
                            vessels[name],
                            "Available in Namibia",
                            page=page.page_number,
                            row=f"row={row_index + 1}",
                        )
                        continue
                    if name == "West Polaris":
                        bands = _parse_seadrill_rate_bands(starts[0], notes, page.page_number)
                        if not bands:
                            raise ValueError("West Polaris rate bands were not recoverable")
                        vessels[name].contracts.extend(bands)
                    else:
                        count = min(len(starts), len(ends))
                        clients = _expand(clients, count)
                        for index in range(count):
                            vessels[name].contracts.append(
                                ContractObservation(
                                    client=clients[index] or "Undisclosed",
                                    region=location,
                                    start_date=iso(parse_period_start(starts[index])),
                                    end_date=iso(parse_period_end_inclusive(ends[index])),
                                    day_rate=0,
                                    status="Firm",
                                    page=page.page_number,
                                    row=f"row={row_index + 1},slot={index + 1}",
                                    rate_disclosure="undisclosed",
                                )
                            )
                    through = re.search(r"priced options? through ([A-Za-z]+) (\d{4})", notes, re.I)
                    if through and vessels[name].contracts:
                        latest = max(vessels[name].contracts, key=lambda item: item.end_date)
                        option_start = next_day(latest.end_date)
                        month = parse_period_start(f"{through.group(1)[:3]}-{through.group(2)}")
                        option_end = date(month.year, month.month, monthrange(month.year, month.month)[1]).isoformat()
                        vessels[name].contracts.append(
                            ContractObservation(
                                client=latest.client,
                                region=latest.region,
                                start_date=option_start,
                                end_date=option_end,
                                day_rate=0,
                                status="Option",
                                page=page.page_number,
                                row="priced-option-through-comments",
                                rate_disclosure="undisclosed",
                            )
                        )

        _raise_unknown_vessels("Seadrill", unknown_names)
        if seen != expected_names:
            raise ValueError(f"Seadrill vessel coverage changed; missing={sorted(expected_names - seen)}")
        for vessel in vessels.values():
            vessel.status = "Active" if any(
                item.status == "Firm" and item.start_date <= report_date <= item.end_date
                for item in vessel.contracts
            ) else "Idle"
        warnings.append("The report has no dayrate column; contract values were not divided by durations.")
        warnings.append("Source dates have month precision; same-month transitions may overlap in ISO projection.")
        return ParseResult(company="Seadrill", vessels=vessels, warnings=warnings), report_date
