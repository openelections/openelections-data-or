#!/usr/bin/env python3
"""Parse ES&S "Statement of Votes Cast by Contests, Geography by Choice" OCR
markdown for Oregon 2026 primary image-only PDFs.

Handles Coos and Crook counties. Each page reports one or more precincts;
contests are headed by office text (markdown heading, centered div, or plain
line), usually followed by a metadata sentence and a candidate/result table.
Multiple contests are sometimes stacked inside a single HTML table.

Usage:
    uv run python src/parsers/2026_primary_choice_ocr_parser.py \
        Coos '/path/to/Coos.pdf'
    uv run python src/parsers/2026_primary_choice_ocr_parser.py \
        Crook '/path/to/Crook.pdf'
"""

import argparse
import csv
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import extract_pages  # noqa: E402
from precinct_2026_common import (  # noqa: E402
    align_candidates_to_county,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)

VOTE_FOR_RE = re.compile(r"\(\s*Vote\s+for\s+\d+\s*\)", re.IGNORECASE)
BALLOTS_RE = re.compile(r"(\d[\d,]*)\s+ballots", re.IGNORECASE)
REG_VOTERS_RE = re.compile(r"(\d[\d,]*)\s+registered\s+voters", re.IGNORECASE)
PCP_RE = re.compile(r"prec(inct)?\s+committe(e|\s+)?person", re.IGNORECASE)
PRECINCT_RE = re.compile(r"precinct\s+(\d+)", re.IGNORECASE)
ALL_PRECINCTS_RE = re.compile(r"^\s*all\s+precincts\s*$", re.IGNORECASE)

# Crook OCR occasionally renders (DEM)/(REP) as single-letter (X)/(Y).
PARTY_CODE_MAP = {
    "X": "D",
    "Y": "R",
    "DEM": "D",
    "REP": "R",
    "D": "D",
    "R": "R",
}

# County-specific offices that normalize_office does not know about.
_EXTRA_OFFICE_MAP = {
    "coos county clerk": "Coos County Clerk",
    "coos county commissioner": "Coos County Commissioner",
    "district attorney": "District Attorney",
    "county commissioner": "County Commissioner",
    "county clerk": "County Clerk",
}

# Measure identifiers in OCR text.
MEASURE_ID_RE = re.compile(r"\bM\s*(\d+)[A-Z]?\b", re.IGNORECASE)
LOCAL_MEASURE_RE = re.compile(r"\b(\d+-\d+)\b")

PSEUDO_CANDIDATES = {"Write-ins", "Over Votes", "Under Votes"}


def _clean_text(text: str) -> str:
    """Remove HTML tags, normalize whitespace, and unescape HTML entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[\s\xa0]+", " ", text).strip()
    return text


def _expand_table(html_fragment: str) -> List[List[str]]:
    """Expand one HTML table into a rectangular grid of cleaned cell text."""
    raw_rows: List[List[Tuple[str, int, int]]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_fragment, flags=re.S | re.I):
        cells: List[Tuple[str, int, int]] = []
        for m in re.finditer(r"<td\b([^>]*)>(.*?)</td>", tr, flags=re.S | re.I):
            attrs = m.group(1)
            text = _clean_text(m.group(2))
            cs = 1
            rs = 1
            cm = re.search(r'colspan=["\']?(\d+)', attrs, re.I)
            if cm:
                cs = int(cm.group(1))
            rm = re.search(r'rowspan=["\']?(\d+)', attrs, re.I)
            if rm:
                rs = int(rm.group(1))
            cells.append((text, cs, rs))
        if cells:
            raw_rows.append(cells)

    grid: List[List[Optional[str]]] = []
    blocked: set = set()
    for ri, cells in enumerate(raw_rows):
        while len(grid) <= ri:
            grid.append([])
        col = 0
        for text, cs, rs in cells:
            while (ri, col) in blocked:
                col += 1
            while len(grid[ri]) <= col:
                grid[ri].append(None)
            for dr in range(rs):
                rr = ri + dr
                while len(grid) <= rr:
                    grid.append([])
                for dc in range(cs):
                    cc = col + dc
                    while len(grid[rr]) <= cc:
                        grid[rr].append(None)
                    grid[rr][cc] = text
            col += cs
    return [[cell or "" for cell in row] for row in grid]


def _extract_tables_with_positions(md: str) -> List[Tuple[int, List[List[str]]]]:
    """Return (position, grid) for every HTML table in the markdown."""
    out: List[Tuple[int, List[List[str]]]] = []
    for m in re.finditer(r"<table[^>]*>.*?</table>", md, flags=re.S | re.I):
        out.append((m.start(), _expand_table(m.group(0))))
    return out


def _is_page_header_table(grid: List[List[str]]) -> bool:
    """True for the page-level summary table at the top of every page."""
    if not grid:
        return False
    flat = " ".join(c for row in grid for c in row).lower()
    return (
        "total ballots cast" in flat
        and "registered voters" in flat
        and "turnout" in flat
    )


def _is_choice_header_table(grid: List[List[str]]) -> bool:
    """True when the first row looks like the standard Choice/Votes/Vote% header."""
    if not grid:
        return False
    first = [c.strip().lower() for c in grid[0]]
    return "choice" in first and "votes" in first


def _extract_precinct_from_label_table(grid: List[List[str]]) -> Optional[str]:
    """Return the precinct number from a Choice/Votes/Vote% label table, if any."""
    for row in grid[1:]:
        if not row:
            continue
        for cell in row:
            cell = cell.strip()
            m = PRECINCT_RE.search(cell)
            if m:
                return f"Precinct {m.group(1).lstrip('0') or '0'}"
    return None


def _has_stacked_contest_header(grid: List[List[str]]) -> bool:
    """True if a table row is a stacked contest header (same text across cells)."""
    for row in grid[1:]:
        non_empty = [c.strip() for c in row if c.strip()]
        if non_empty and all(c == non_empty[0] for c in non_empty):
            if _looks_like_contest_header(non_empty[0]):
                return True
    return False


def _find_total_row(grid: List[List[str]]) -> Optional[int]:
    """Return the numeric total from a Total row, if present."""
    for row in grid[1:]:
        if not row:
            continue
        first = row[0].strip().lower()
        if first in {"total", "total votes cast"}:
            for cell in row[1:]:
                val = cell.strip().replace(",", "")
                if re.fullmatch(r"\d+", val):
                    return int(val)
    return None


def _is_continuation_summary(
    grid: List[List[str]],
    state: Dict[str, Optional[str]],
    contest_sums: Dict[Tuple[str, str, str, str], int],
) -> bool:
    """True when a Choice-only table is the tail (Total/Over/Under) of the
    current contest that started on the previous page."""
    if not state.get("office") or not state.get("precinct"):
        return False
    key = (
        str(state["office"]),
        str(state.get("district") or ""),
        str(state.get("party") or ""),
        str(state["precinct"]),
    )
    total = _find_total_row(grid)
    if total is None:
        return False
    current_sum = contest_sums.get(key, 0)
    # Allow a small tolerance for OCR read errors in the Total row.
    return abs(current_sum - total) <= 1


def _looks_like_contest_header(text: str) -> bool:
    """Return True if the text starts a new contest."""
    text = text.strip()
    if not text:
        return False
    low = text.lower()
    if "vote for" in low:
        return True
    if "measure" in low:
        return True
    if MEASURE_ID_RE.search(text):
        return True
    if LOCAL_MEASURE_RE.search(text) and "vote for" in low:
        return True
    if normalize_office(text):
        return True
    for key in _EXTRA_OFFICE_MAP:
        if key in low:
            return True
    if "district attorney" in low:
        return True
    return False


def _is_pcp_header(text: str) -> bool:
    return PCP_RE.search(text) is not None


def _is_precinct_header(text: str) -> bool:
    return PRECINCT_RE.match(text.strip()) is not None


def _is_all_precincts_header(text: str) -> bool:
    return ALL_PRECINCTS_RE.match(text.strip()) is not None


def _parse_contest_header(text: str) -> Tuple[Optional[str], str, str]:
    text = _clean_text(text)
    text = VOTE_FOR_RE.sub("", text).strip(" -")

    party = ""
    pm = re.search(r"\(\s*([A-Z]{1,3})\s*\)", text)
    if pm:
        code = pm.group(1).upper()
        party = PARTY_CODE_MAP.get(code, normalize_party(code))
        text = text[: pm.start()] + text[pm.end() :]
        text = re.sub(r"^\s*-\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip()

    # Secondary party token.
    if not party:
        m = re.search(r"\b(DEM|REP)\b", text, re.IGNORECASE)
        if m:
            party = normalize_party(m.group(1))

    # Local measures like "6-228 City of ...".
    lm = LOCAL_MEASURE_RE.search(text)
    if lm and "vote for" in text.lower():
        return f"Measure {lm.group(1)}", "", ""

    # State measures like "M120 Transportation Tax".
    mm = MEASURE_ID_RE.search(text)
    if mm:
        return f"Measure {mm.group(1)}", "", ""

    office = normalize_office(text)
    if office is None:
        low = text.lower()
        for key, value in _EXTRA_OFFICE_MAP.items():
            if key in low:
                office = value
                break
    if office is None:
        return None, "", ""

    district = parse_district(text)
    return office, district, party


def _classify_candidate(name: str) -> Optional[str]:
    low = name.lower().replace("-", " ")
    if "write" in low and "in" in low:
        return "Write-ins"
    if low in {
        "overvotes",
        "over votes",
        "oversvotes",
        "overtoves",
        "overotes",
        "overtotes",
        "overvoles",
        "overvoies",
        "overtvotes",
    }:
        return "Over Votes"
    if low in {
        "undervotes",
        "under votes",
        "underotes",
        "undervotates",
    }:
        return "Under Votes"
    if low == "total" or low.startswith("total "):
        return None
    return format_candidate_name(name.split())


def _parse_metadata(text: str) -> Tuple[Optional[int], Optional[int]]:
    ballots = None
    reg_voters = None
    m = BALLOTS_RE.search(text)
    if m:
        ballots = int(m.group(1).replace(",", ""))
    m = REG_VOTERS_RE.search(text)
    if m:
        reg_voters = int(m.group(1).replace(",", ""))
    return ballots, reg_voters


def _emit_pseudo_offices(
    rows: List[Dict[str, str]],
    county: str,
    precinct: str,
    text: str,
    emitted: Dict[Tuple[str, str], bool],
) -> None:
    ballots, reg_voters = _parse_metadata(text)
    for office, value in (
        ("Ballots Cast", ballots),
        ("Registered Voters", reg_voters),
    ):
        if value is None:
            continue
        key = (precinct, office)
        if emitted.get(key):
            continue
        emitted[key] = True
        rows.append(
            make_row(
                county=county,
                precinct=precinct,
                office=office,
                district="",
                party="",
                candidate="",
                votes=value,
            )
        )


def _update_contest_from_header(
    header_text: str,
    state: Dict[str, Optional[str]],
) -> bool:
    """Update state from a contest/PCP/precinct header; return True if state changed."""
    raw = header_text.strip()
    if _is_pcp_header(raw):
        if "RURAL COQUILLE" in raw or "MYRTLE POINT" in raw:
            print(f"PCP_HEADER matched: {raw[:80]}")
        state["office"] = None
        state["pcp_active"] = "true"
        return True
    if _is_precinct_header(raw):
        state["pcp_active"] = None
        return False

    office, district, party = _parse_contest_header(raw)
    if office:
        state["office"] = office
        state["district"] = district
        state["party"] = party
        state["pcp_active"] = None
        state["header_text"] = raw
        return True
    return False


def _normalize_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _process_table_rows(
    grid: List[List[str]],
    state: Dict[str, Optional[str]],
    rows: List[Dict[str, str]],
    county: str,
    emitted_pseudo: Dict[Tuple[str, str], bool],
    candidate_map: Optional[Dict[str, Tuple[str, str, str]]],
    contest_sums: Optional[Dict[Tuple[str, str, str, str], int]] = None,
    collect_map: Optional[Dict[str, Dict[Tuple[str, str, str], int]]] = None,
) -> None:
    """Process rows of a table, updating contest state for stacked headers."""
    for row in grid:
        if not row:
            continue

        # Stacked header row: same text across all non-empty cells.
        non_empty = [c.strip() for c in row if c.strip()]
        if non_empty and all(c == non_empty[0] for c in non_empty):
            candidate_header = non_empty[0]
            if _looks_like_contest_header(candidate_header):
                _update_contest_from_header(candidate_header, state)
                continue

        first = row[0].strip()
        if not first:
            continue

        # Metadata row inside a table (colspan cell duplicated by expansion).
        is_metadata = (
            (all(not c.strip() for c in row[1:]) or (non_empty and all(c == non_empty[0] for c in non_empty)))
            and (BALLOTS_RE.search(first) or REG_VOTERS_RE.search(first))
        )
        if is_metadata:
            if state["precinct"] and not collect_map:
                _emit_pseudo_offices(
                    rows, county, str(state["precinct"]), first, emitted_pseudo
                )
            continue

        if state["precinct"] is None:
            continue
        if state.get("pcp_active"):
            continue

        votes = None
        for cell in row[1:]:
            cell = cell.strip().replace(",", "")
            if re.fullmatch(r"\d+", cell):
                votes = int(cell)
                break
        if votes is None:
            continue

        candidate = _classify_candidate(first)
        if candidate is None:
            continue

        office = str(state["office"]) if state["office"] else ""
        district = str(state["district"]) if state["district"] else ""
        party = str(state["party"]) if state["party"] else ""

        if collect_map is not None:
            if office and candidate not in PSEUDO_CANDIDATES and candidate not in {"Yes", "No"}:
                key = _normalize_name(candidate)
                collect_map[key][(office, district, party)] += 1
            continue

        # Repair rows whose contest header was missed by OCR.
        if candidate_map and candidate not in PSEUDO_CANDIDATES and candidate not in {"Yes", "No"}:
            mapped = candidate_map.get(_normalize_name(candidate))
            if mapped:
                m_office, m_district, m_party = mapped
                if m_office != office or m_district != district or m_party != party:
                    state["office"] = m_office
                    state["district"] = m_district
                    state["party"] = m_party
                    office, district, party = m_office, m_district, m_party

        if not office:
            continue

        if candidate in {"Mary Graham", "Amanda J. Hawker", "Ivan H. Hawker"}:
            print(f"EMIT {candidate} office={office} district={district} party={party} precinct={state['precinct']} pcp_active={state.get('pcp_active')} header={state.get('header_text')}")

        rows.append(
            make_row(
                county=county,
                precinct=str(state["precinct"]),
                office=office,
                district=district,
                party=party,
                candidate=candidate,
                votes=votes,
            )
        )
        if contest_sums is not None and candidate not in PSEUDO_CANDIDATES and candidate != "Total":
            sum_key = (
                office,
                district,
                party,
                str(state["precinct"]),
            )
            contest_sums[sum_key] = contest_sums.get(sum_key, 0) + votes


def _extract_headers_with_positions(md: str) -> List[Tuple[int, str]]:
    """Return (position, raw_header_text) for contest/precinct/PCP headers.

    Positions are always offsets in the original markdown so that headers and
    tables can be sorted together reliably.  HTML tables and centered divs are
    replaced with placeholder whitespace before scanning for plain-text lines,
    which prevents their inner text from being picked up as duplicate headers.
    """
    headers: List[Tuple[int, str]] = []

    # Extract centered divs first, before their text is blanked out.
    for m in re.finditer(
        r'<div[^>]*style="text-align:\s*center;"[^>]*>(.*?)</div>',
        md,
        flags=re.S | re.I,
    ):
        headers.append((m.start(), _clean_text(m.group(1))))

    # Build a version of the markdown with centered divs and HTML tables
    # replaced by spaces of identical length.  Markdown headings and any
    # plain-text contest/precinct/PCP lines remain in their original positions.
    def _blank(match: re.Match) -> str:
        return " " * len(match.group(0))

    blanked = re.sub(
        r'<div[^>]*style="text-align:\s*center;"[^>]*>.*?</div>',
        _blank,
        md,
        flags=re.S | re.I,
    )
    blanked = re.sub(r"<table[^>]*>.*?</table>", _blank, blanked, flags=re.S | re.I)
    blanked = re.sub(r"<br\s*/?>", "\n", blanked, flags=re.IGNORECASE)
    blanked = re.sub(r"<[^>]+>", " ", blanked)
    blanked = html.unescape(blanked)

    # Markdown headings.
    for m in re.finditer(r"^\s*#{1,6}\s+(.*?)$", blanked, flags=re.M):
        headers.append((m.start(), m.group(1).strip()))

    # Plain-text lines outside tables/divs.
    for m in re.finditer(r"^([^\n]+)$", blanked, flags=re.M):
        clean = re.sub(r"[\s\xa0]+", " ", m.group(1)).strip()
        if not clean:
            continue
        if (
            _looks_like_contest_header(clean)
            or _is_pcp_header(clean)
            or _is_precinct_header(clean)
            or _is_all_precincts_header(clean)
        ):
            headers.append((m.start(), clean))

    # Deduplicate by position; if the same text appears at two positions,
    # keep only the earliest.
    by_pos: Dict[int, str] = {}
    for pos, text in headers:
        if pos in by_pos:
            continue
        by_pos[pos] = text
    return sorted(by_pos.items(), key=lambda x: x[0])


def _load_county_candidate_map(county_csv: str, county: str) -> Dict[str, Tuple[str, str, str]]:
    """Build candidate->(office,district,party) from the county CSV.

    Excludes Governor (the county CSV conflates those names) and pseudo-
    candidates.
    """
    mapping: Dict[str, Tuple[str, str, str]] = {}
    with open(county_csv) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != county:
                continue
            office = row["office"].strip()
            if office == "Governor":
                continue
            cand = row["candidate"].strip()
            if cand in {"Misc.", "Candidate 1", "Candidate 2"}:
                continue
            key = _normalize_name(cand)
            mapping[key] = (office, row["district"].strip(), row["party"].strip())
    return mapping


def _build_ocr_candidate_map(
    pdf_path: str,
    county_name: str,
) -> Dict[str, Tuple[str, str, str]]:
    """Two-pass helper: collect candidate->contest mappings from the OCR itself.

    The county CSV omits county/judicial/local offices, so a map built from the
    PDF headers fills the gaps for rows whose header was missed by OCR.
    """
    counts: Dict[str, Dict[Tuple[str, str, str], int]] = defaultdict(
        lambda: defaultdict(int)
    )
    state: Dict[str, Optional[str]] = {
        "office": None,
        "district": "",
        "party": "",
        "precinct": None,
        "header_text": "",
        "pcp_active": None,
    }

    for _page_num, md in extract_pages(pdf_path):
        headers = _extract_headers_with_positions(md)
        tables = _extract_tables_with_positions(md)

        # Stop at the county-wide summary section.
        done = False
        for _pos, text in headers:
            if _is_all_precincts_header(text):
                done = True
                break
        if done:
            break

        items: List[Tuple[int, str, object]] = [
            (pos, "header", text) for pos, text in headers
        ] + [(pos, "table", grid) for pos, grid in tables]
        items.sort(key=lambda x: x[0])

        for _pos, kind, payload in items:
            if kind == "header":
                text = str(payload)
                if _is_all_precincts_header(text):
                    break
                if _is_precinct_header(text):
                    m = PRECINCT_RE.search(text)
                    if m:
                        state["precinct"] = f"Precinct {m.group(1).lstrip('0') or '0'}"
                    continue
                _update_contest_from_header(text, state)
                continue

            grid: List[List[str]] = payload  # type: ignore
            if _is_page_header_table(grid):
                continue
            prec = _extract_precinct_from_label_table(grid)
            if prec:
                state["precinct"] = prec
                continue
            _process_table_rows(
                grid,
                state,
                [],
                county_name,
                {},
                None,
                None,
                collect_map=counts,
            )

    mapping: Dict[str, Tuple[str, str, str]] = {}
    for cand, contest_counts in counts.items():
        best = max(contest_counts.items(), key=lambda x: x[1])[0]
        mapping[cand] = best
    return mapping


def _parse_county(pdf_path: str, county_name: str) -> List[Dict[str, str]]:
    county_map = _load_county_candidate_map(
        "2026/20260519__or__primary__county.csv", county_name
    )
    ocr_map = _build_ocr_candidate_map(pdf_path, county_name)
    # County CSV (statewide offices) takes precedence; OCR map fills gaps.
    candidate_map = {**ocr_map, **county_map}

    rows: List[Dict[str, str]] = []
    contest_sums: Dict[Tuple[str, str, str, str], int] = {}
    state: Dict[str, Optional[str]] = {
        "office": None,
        "district": "",
        "party": "",
        "precinct": None,
        "header_text": "",
        "pcp_active": None,
    }
    emitted_pseudo: Dict[Tuple[str, str], bool] = {}

    for _page_num, md in extract_pages(pdf_path):
        tables = _extract_tables_with_positions(md)
        headers = _extract_headers_with_positions(md)

        items: List[Tuple[int, str, object]] = [
            (pos, "header", text) for pos, text in headers
        ] + [(pos, "table", grid) for pos, grid in tables]
        items.sort(key=lambda x: x[0])

        done = False
        for _pos, kind, payload in items:
            if kind == "header":
                header_text = str(payload)
                if _is_all_precincts_header(header_text):
                    done = True
                    break
                if _is_precinct_header(header_text):
                    m = PRECINCT_RE.search(header_text)
                    if m:
                        state["precinct"] = f"Precinct {m.group(1).lstrip('0') or '0'}"
                    continue
                _update_contest_from_header(header_text, state)
                continue

            grid: List[List[str]] = payload  # type: ignore
            if _is_page_header_table(grid):
                continue

            prec = _extract_precinct_from_label_table(grid)
            if prec:
                state["precinct"] = prec
                continue

            if _is_choice_header_table(grid):
                # A Choice-only table is either a precinct label (handled
                # above), a stacked contest table, or a summary tail.
                if _has_stacked_contest_header(grid):
                    _process_table_rows(
                        grid, state, rows, county_name, emitted_pseudo,
                        candidate_map, contest_sums,
                    )
                elif _is_continuation_summary(grid, state, contest_sums):
                    _process_table_rows(
                        grid, state, rows, county_name, emitted_pseudo,
                        candidate_map, contest_sums,
                    )
                continue

            _process_table_rows(
                grid, state, rows, county_name, emitted_pseudo,
                candidate_map, contest_sums,
            )

        if done:
            break

        # Plain-text metadata blocks that were not inside tables.
        text_only = re.sub(r"<table[^>]*>.*?</table>", "\n\n", md, flags=re.S | re.I)
        text_only = re.sub(r"<br\s*/?>", "\n", text_only, flags=re.IGNORECASE)
        text_only = re.sub(r"<[^>]+>", " ", text_only)
        text_only = html.unescape(text_only)
        for block in re.split(r"\n\s*\n", text_only):
            block = re.sub(r"[\s\xa0]+", " ", block).strip()
            if not block:
                continue
            if BALLOTS_RE.search(block) or REG_VOTERS_RE.search(block):
                if state["precinct"]:
                    _emit_pseudo_offices(
                        rows, county_name, str(state["precinct"]), block, emitted_pseudo
                    )

    return rows


def _aggregate_pseudo_candidates(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Collapse multiple Write-ins/Over/Under rows per contest into one row."""
    sums: Dict[Tuple[str, str, str, str, str, str, str], int] = defaultdict(int)
    other: List[Dict[str, str]] = []
    county = rows[0]["county"] if rows else ""
    for r in rows:
        if r["candidate"] in PSEUDO_CANDIDATES:
            key = (
                r["county"],
                r["precinct"],
                r["office"],
                r["district"],
                r["party"],
                r["candidate"],
            )
            sums[key] += int(r["votes"])
        else:
            other.append(r)
    out = list(other)
    for (county, precinct, office, district, party, candidate), votes in sums.items():
        out.append(
            make_row(
                county=county,
                precinct=precinct,
                office=office,
                district=district,
                party=party,
                candidate=candidate,
                votes=votes,
            )
        )
    return out


def _align_rows(
    rows: List[Dict[str, str]],
    county_csv_path: str,
    county_name: str,
) -> List[Dict[str, str]]:
    """Align parsed names to the county CSV, except Governor and write-ins."""
    gov = [r for r in rows if r["office"] == "Governor"]
    writeins = [r for r in rows if r["candidate"] == "Write-ins"]
    rest = [
        r
        for r in rows
        if r["office"] != "Governor" and r["candidate"] != "Write-ins"
    ]
    aligned = align_candidates_to_county(rest, county_csv_path, county_name, tolerance=5)
    return aligned + writeins + gov


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("county", help="County name")
    ap.add_argument("pdf_path", help="Path to source PDF (used to locate OCR cache)")
    args = ap.parse_args()

    rows = _parse_county(args.pdf_path, args.county)
    rows = _aggregate_pseudo_candidates(rows)
    rows = _align_rows(
        rows,
        "2026/20260519__or__primary__county.csv",
        args.county,
    )

    out = output_path(args.county)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    sys.exit(main())
