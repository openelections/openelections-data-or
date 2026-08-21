#!/usr/bin/env python3
"""Build the 2026 Oregon primary precinct-level CSV for Wasco County.

Wasco's PDF uses an ES&S "Statement of Votes Cast, Official Certified with
Precincts" layout.  The PaddleOCR-VL-1.6 cache stores per-page markdown where
contests are introduced by markdown headings and candidate results live in
single-column tables.

The parser processes each page in order, carrying the current precinct and
contest forward so that multi-page contests keep the correct context.  It emits
rows for every candidate plus separate Over Votes / Under Votes rows.
"""

import html
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    align_candidates_to_county,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)

COUNTY_NAME = "Wasco"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"
CACHE_DIR = Path("/Users/dwillis/code/openelections-data-or/.paddleocr_cache/Wasco")

VOTE_FOR_RE = re.compile(r"\(Vote\s+For\s+\d+\)", re.IGNORECASE)
PRECINCT_RE = re.compile(r"^##\s+Precinct\s+(\d+)\b", re.IGNORECASE)
META_RE = re.compile(
    r"Statement of Votes Cast|Page:\s*\d+|Total Ballots Cast|Registered Voters|"
    r"Overall Turnout|All Precincts|All Districts|All Counter|All ScanStations|"
    r"May \d+,\s*2026 Primary Election|Wasco County,\s*OR|Choice\s+Votes\s+Vote\s*%|"
    r"^\d+\s+ballots\s+\(",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    """Remove HTML tags, unescape entities, and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _looks_like_heading(line: str) -> bool:
    """Return True if *line* is a precinct or contest heading."""
    if PRECINCT_RE.match(line):
        return True
    if VOTE_FOR_RE.search(line):
        return True
    if re.search(r"\bMeasure\b", line, re.IGNORECASE):
        return True
    if normalize_office(line):
        return True
    if re.search(
        r"\b(District Attorney|Judge of|County Commissioner|Justice of the Peace|"
        r"Precinct Committee Person|Commissioner,\s*Position)\b",
        line,
        re.IGNORECASE,
    ):
        return True
    return False


def _is_meta_line(line: str) -> bool:
    """Return True for page header / ballot-count boilerplate lines."""
    if not line:
        return True
    if META_RE.search(line):
        return True
    return False


def _extract_party(text: str) -> str:
    """Return D/R for Wasco party markers, including OCR misreads."""
    # Look for explicit (DEM)/(REP) labels first.
    pm = re.search(r"\((DEM|REP|DFM)\)", text, re.IGNORECASE)
    if pm:
        code = pm.group(1).upper()
        if code in {"DEM", "DFM"}:
            return "D"
        if code == "REP":
            return "R"
    # Fallback to bare DEM/REP tokens.
    if re.search(r"\bDEM\b", text, re.IGNORECASE) or re.search(r"\bDFM\b", text, re.IGNORECASE):
        return "D"
    if re.search(r"\bREP\b", text, re.IGNORECASE):
        return "R"
    return ""


def _parse_contest_heading(line: str) -> Tuple[str, str, str]:
    """Return (office, district, party) for a contest heading line."""
    line = _clean_text(line)
    line = VOTE_FOR_RE.sub("", line).strip(" -,:")

    party = _extract_party(line)

    # Strip party markers from the office-detection string.
    detect = re.sub(r"\((DEM|REP|DFM)\)", "", line, flags=re.IGNORECASE).strip(" -,:")
    detect = re.sub(r"\b(Democrat|Republican)\b", "", detect, flags=re.IGNORECASE).strip(" -,:")

    office = normalize_office(detect)
    district = parse_district(detect)

    if office is None:
        m = re.search(r"(State\s+)?Measure\s+\d+", detect, re.IGNORECASE)
        if m:
            office = m.group(0).title()
        elif re.search(r"\bPrecinct\s+Committee\s+Person\b", detect, re.IGNORECASE):
            office = "Precinct Committee Person"
            # District can be the precinct area named in the heading.
            m = re.search(r"-\s*(?:Democrat|Republican)\s*-\s*(.+?)(?:\s*\(|$)", detect, re.IGNORECASE)
            if m:
                district = m.group(1).strip()
        elif re.search(r"\bCounty\s+Commissioner\b", detect, re.IGNORECASE):
            office = "County Commissioner"
            m = re.search(r"Position\s+(\d+)", detect, re.IGNORECASE)
            if m:
                district = m.group(1)
        elif re.search(r"\bCommissioner,\s*Position\b", detect, re.IGNORECASE):
            office = "County Commissioner"
            m = re.search(r"Position\s+(\d+)", detect, re.IGNORECASE)
            if m:
                district = m.group(1)
        else:
            office = detect

    if office is None:
        office = ""
    return office, district, party


def _normalize_candidate(text: str) -> Optional[str]:
    """Return canonical candidate name or None for rows to skip."""
    text = text.strip()
    if not text:
        return None
    low = text.lower()
    if low in {"total", "contest total", "total votes cast"}:
        return None
    if "write-in" in low or "write in" in low:
        return "Write-ins"
    if low in {"overvotes", "over votes", "overvoted", "overtotes", "over votes"}:
        return "Over Votes"
    if low in {"undervotes", "under votes", "undervoted"}:
        return "Under Votes"
    if low == "yes":
        return "Yes"
    if low == "no":
        return "No"
    return text


def _parse_vote(text: str) -> Optional[int]:
    text = text.replace(",", "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _expand_table(table_html: str) -> List[List[str]]:
    """Convert a <table> block into a grid of plain-text cell values."""
    rows: List[List[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)
        cells = [_clean_text(re.sub(r"<[^>]+>", " ", c)) for c in cells]
        rows.append(cells)
    return rows


def _extract_headings(text: str) -> List[str]:
    """Return heading lines found in a plain-text page segment."""
    headings: List[str] = []
    for raw_line in text.splitlines():
        # Strip HTML tags and collapse whitespace inside the line, but keep
        # lines separate so markdown headings are not joined together.
        line = _clean_text(raw_line)
        if not line:
            continue
        if _is_meta_line(line):
            continue
        if _looks_like_heading(line):
            headings.append(line)
    return headings


def _parse_page(
    md: str,
    current_precinct: str,
    current_office: str,
    current_district: str,
    current_party: str,
) -> Tuple[List[Dict[str, str]], str, str, str, str]:
    """Parse one page of markdown and return rows plus updated state."""
    rows: List[Dict[str, str]] = []
    precinct = current_precinct
    office, district, party = current_office, current_district, current_party

    table_re = re.compile(r"<table\b.*?</table>", re.S | re.I)
    prev_end = 0
    for m in table_re.finditer(md):
        context = md[prev_end : m.start()]
        for heading in _extract_headings(context):
            pm = PRECINCT_RE.match(heading)
            if pm:
                precinct = pm.group(1)
                # Reset contest when a new precinct starts.
                office, district, party = "", "", ""
            else:
                office, district, party = _parse_contest_heading(heading)

        table_html = m.group(0)
        prev_end = m.end()

        # Skip tables that appear before the first precinct heading (countywide summary).
        if not precinct:
            continue

        grid = _expand_table(table_html)
        for row in grid:
            if len(row) < 2:
                continue
            candidate = _normalize_candidate(row[0])
            if candidate is None:
                continue
            votes = _parse_vote(row[1])
            if votes is None:
                continue

            use_office = office
            use_district = district
            use_party = party
            if use_office.startswith("Measure"):
                use_party = ""

            rows.append(
                make_row(
                    county=COUNTY_NAME,
                    precinct=precinct,
                    office=use_office,
                    district=use_district,
                    party=use_party,
                    candidate=candidate,
                    votes=votes,
                )
            )

    return rows, precinct, office, district, party


def parse_wasco(cache_dir: Path = CACHE_DIR) -> List[Dict[str, str]]:
    """Parse all cached Wasco pages into precinct-level CSV rows."""
    rows: List[Dict[str, str]] = []
    precinct = ""
    office = district = party = ""

    for page_path in sorted(cache_dir.glob("p*.md")):
        page_rows, precinct, office, district, party = _parse_page(
            page_path.read_text(), precinct, office, district, party
        )
        rows.extend(page_rows)

    return rows


def main() -> None:
    rows = parse_wasco()
    rows = align_candidates_to_county(rows, COUNTY_CSV, COUNTY_NAME, tolerance=5)
    out = output_path(COUNTY_NAME)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
