#!/usr/bin/env python3
"""Generic PaddleOCR table parser for Oregon 2026 primary precinct PDFs.

Many counties (Jefferson, Morrow, Baker, Lake, etc.) produce image-only or
scanned PDFs.  PaddleOCR-VL returns per-page markdown containing one or more
HTML tables.  This parser expands rowspan/colspan so side-by-side contests
become separate sub-tables, then emits one precinct row per candidate / summary
column.

Usage:
    uv run python src/parsers/2026_primary_ocr_table_parser.py \
        Jefferson '/path/to/Jefferson.pdf'

Note:
    Measure contests are emitted with candidates "Yes" and "No".  The county-level
    CSV uses generic "Candidate 1" / "Candidate 2" labels, so
    verify_precinct_totals.py will report explainable mismatches for those rows.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import extract_pages
from precinct_2026_common import (
    COUNTY_FILENAME,
    align_candidates_to_county,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    recover_missing_precincts,
    write_csv,
)

# Header fragments that identify a summary/candidate column.
WRITE_IN_LABELS = {"write-in totals", "write in totals", "write-in"}
OVER_LABELS = {"overvotes", "over votes", "overtoves"}
UNDER_LABELS = {"undervotes", "under votes"}
SKIP_LABELS = {
    "total votes cast",
    "contest total",
    "total",
    "assigned",
    "write-in: not",
    "write-in: assigned",
    "write in: not",
    "write in: assigned",
}

# Token patterns that signal the start of a contest office block.
OFFICE_START_RE = re.compile(
    r"\b("
    r"US\s+Senator|US\s+Representative|State\s+Senator|State\s+Representative|"
    r"Governor|Attorney\s+General|Secretary\s+of\s+State|State\s+Treasurer|"
    r"Auditor|Commissioner\s+of\s+Agriculture|Labor\s+Commissioner|"
    r"Bureau\s+of\s+Labor\s+and\s+Industries|Judge\s+of\s+the|District\s+Attorney|"
    r"Measure|Local\s+Option|Levy|County\s+Commissioner|County\s+Assessor|"
    r"Justice\s+of\s+the\s+Peace|Precinct\s+Committee\s+Person"
    r")\b",
    re.IGNORECASE,
)
PARTY_RE = re.compile(r"\b(DEM|REP|OEM)\b", re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"\(Vote\s+For\s+\d+\)|VOTE\s+FOR\s+\d+", re.IGNORECASE)
REPORTING_RE = re.compile(
    r"\d+\s+of\s+\d+\s+precincts?\s+reporting", re.IGNORECASE
)
CONTEST_TOTAL_RE = re.compile(r"\bcontest\s+total\b", re.IGNORECASE)
NO_CANDIDATE_RE = re.compile(r"no\s+candidate\s+filed", re.IGNORECASE)


def _flatten_escapes(text: str) -> str:
    return (
        text.replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\t", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
    )


def _is_statistics_table(rows: list) -> bool:
    """Return True if the table is the registered-voter / ballots-cast block."""
    if not rows:
        return False
    first = " ".join(c.strip().lower() for c in rows[0] if c)
    return "statistics" in first or "registered voters" in first


def _looks_like_office(text: str) -> bool:
    text = _flatten_escapes(text).strip()
    if not text:
        return False
    if PARTY_RE.search(text):
        return True
    if OFFICE_START_RE.search(text):
        return True
    if VOTE_FOR_RE.search(text):
        return True
    return False


def _is_meta_cell(text: str) -> bool:
    """True for cells that are office text, party, vote-for, or reporting counts."""
    t = _flatten_escapes(text).strip()
    if not t:
        return False
    if _looks_like_office(t):
        return True
    if REPORTING_RE.search(t):
        return True
    if CONTEST_TOTAL_RE.search(t) and not any(k in t.lower() for k in OVER_LABELS | UNDER_LABELS):
        # A standalone "Contest Total" in an office row is a trailing total, not meta.
        return False
    return False


PRECINCT_RE = re.compile(r"\(\d+\)")


def _looks_like_precinct(text: str) -> bool:
    """Return True for a first-column cell that names a precinct."""
    t = _flatten_escapes(text).strip()
    if not t:
        return False
    if t.lower() in {"totals", "statistics", "precinct", "contest"}:
        return False
    return PRECINCT_RE.search(t) is not None


def _is_data_row_simple(cells: list) -> bool:
    """Return True if the row looks like per-precinct numeric data."""
    if len(cells) < 2:
        return False
    first = cells[0].strip()
    if not first or first.lower() in {"totals", "statistics"}:
        return False
    non_empty = 0
    numeric = 0
    for c in cells[1:]:
        t = c.strip()
        if not t:
            continue
        non_empty += 1
        if re.fullmatch(r"[\d,]+", t):
            numeric += 1
    return non_empty >= 2 and numeric >= non_empty // 2


def _has_candidate_like_label(cells: list) -> bool:
    """Return True if a row has at least one plausible candidate/summary label."""
    found = 0
    for c in cells[1:]:
        t = c.strip()
        if not t:
            continue
        tl = t.lower()
        if any(k in tl for k in WRITE_IN_LABELS | OVER_LABELS | UNDER_LABELS):
            found += 1
        elif re.fullmatch(r"[\d,]+", t.replace(".", "")):
            continue
        elif not _is_meta_cell(t) and len(t) > 1:
            found += 1
    return found >= 1


def _find_header_row(grid: list) -> tuple[int | None, int | None]:
    """Return (candidate_header_index, first_data_index) for a contest grid.

    The candidate header is the row immediately above the first data row that
    carries candidate/summary labels.  Rows that are purely meta text are skipped.
    """
    data_idx = None
    for i, row in enumerate(grid):
        if _is_data_row_simple(row):
            data_idx = i
            break
    if data_idx is None or data_idx == 0:
        return None, None

    header_idx = data_idx - 1
    while header_idx >= 0:
        if _has_candidate_like_label(grid[header_idx]):
            break
        header_idx -= 1
    if header_idx < 0:
        return None, None
    return header_idx, data_idx


def _candidate_header_index(grid: list) -> int | None:
    """Return the index of the candidate-header row, or None."""
    return _find_header_row(grid)[0]


def _classify_column(text: str) -> str | None:
    """Return the candidate/summary label for a column header cell, or None to skip."""
    t = _flatten_escapes(text).strip().lower()
    if not t:
        return None
    if t in WRITE_IN_LABELS or t == "write-in":
        return "Write-ins"
    if any(k in t for k in OVER_LABELS):
        return "Over Votes"
    if any(k in t for k in UNDER_LABELS):
        return "Under Votes"
    if any(skip in t for skip in SKIP_LABELS):
        return None
    if t in {"yes", "no"}:
        return text.strip().title()
    if NO_CANDIDATE_RE.search(t):
        return None
    # Purely numeric headers are page numbers / artifacts, not candidates.
    if re.fullmatch(r"[\d,]+", t):
        return None
    # Skip single-character artifacts.
    if len(t) <= 1:
        return None
    return format_candidate_name(text.split())


def _parse_office_text(text: str):
    """Parse a contest office line into (office, district, party)."""
    text = _flatten_escapes(text).strip()
    # Remove a trailing (Vote For N) / VOTE FOR N marker.
    text = VOTE_FOR_RE.sub("", text).strip(" -")

    party = ""
    pm = PARTY_RE.search(text)
    if pm:
        party = normalize_party(pm.group(1))
        text = text[: pm.start()] + text[pm.end() :]
        text = re.sub(r"^\s*-\s*", "", text)

    office = normalize_office(text)
    if office is None:
        office = text.rstrip(" ,")

    district = parse_district(text)
    return office, district, party


def _is_data_row(cells: list, header_labels: list) -> bool:
    """Return True if this row looks like per-precinct data for the contest."""
    if len(cells) < 2:
        return False
    first = cells[0].strip()
    if not first:
        return False
    if first.lower() == "totals":
        return False
    if first.lower() == "statistics":
        return False
    # The remaining cells should mostly be numeric votes.
    numeric = 0
    total = 0
    for i, c in enumerate(cells[1:], start=1):
        if i - 1 >= len(header_labels):
            break
        if header_labels[i - 1] is None:
            continue
        total += 1
        if re.fullmatch(r"[\d,]+", c.strip()):
            numeric += 1
    return total > 0 and numeric >= total // 2


def _extract_tables(md: str) -> list:
    """Return a list of tables as rectangular cell-text grids.

    Expands rowspan/colspan so multi-column office titles and side-by-side
    contests occupy predictable column positions.
    """
    tables = []
    current_rows = []
    blocked: set = set()

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", md, flags=re.S | re.I):
        cells = []
        for m in re.finditer(r"<td\b([^>]*)>(.*?)</td>", tr, flags=re.S | re.I):
            attrs = m.group(1)
            text = re.sub(r"<[^>]+>", " ", m.group(2))
            text = re.sub(r"\s+", " ", text).strip()
            cs = 1
            rs = 1
            cm = re.search(r'colspan=["\']?(\d+)', attrs, re.I)
            if cm:
                cs = int(cm.group(1))
            rm = re.search(r'rowspan=["\']?(\d+)', attrs, re.I)
            if rm:
                rs = int(rm.group(1))
            cells.append((text, cs, rs))

        # A completely empty row terminates a table.
        if not cells:
            if current_rows:
                tables.append(_normalize_grid(current_rows))
                current_rows = []
                blocked = set()
            continue

        ri = len(current_rows)
        grid_row: list = []
        col = 0
        for text, cs, rs in cells:
            while (ri, col) in blocked:
                col += 1
            while len(grid_row) <= col:
                grid_row.append("")
            grid_row[col] = text
            for dr in range(rs):
                for dc in range(cs):
                    blocked.add((ri + dr, col + dc))
            col += cs
        current_rows.append(grid_row)

    if current_rows:
        tables.append(_normalize_grid(current_rows))
    return tables


def _normalize_grid(rows: list) -> list:
    """Pad every row to the same width."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def _candidate_header_index(grid: list) -> int | None:
    """Return the index of the candidate-header row, or None."""
    return _find_header_row(grid)[0]


def _split_side_by_side(grid: list) -> list:
    """Split a grid that contains two or more side-by-side contests."""
    header_idx = _candidate_header_index(grid)
    if header_idx is None:
        return [grid]

    # Find every column that carries an office-like title in the rows above
    # the candidate header.
    office_cols = []
    for row in grid[:header_idx]:
        for c_idx, cell in enumerate(row):
            if cell.strip() and _looks_like_office(cell):
                office_cols.append(c_idx)
    office_cols = sorted(set(office_cols))
    if len(office_cols) <= 1:
        return [grid]

    # Boundaries are the starts of the right-hand contests.  If the column
    # directly under a boundary is a trailing "Contest Total", it belongs to
    # the previous contest, so shift the boundary right.
    boundaries = []
    header_row = grid[header_idx]
    for c in office_cols[1:]:
        while c < len(header_row) and (
            header_row[c].strip() == ""
            or CONTEST_TOTAL_RE.search(header_row[c])
        ):
            c += 1
        boundaries.append(c)

    # Build column slices.  The first data column is the first office column
    # (usually 1, just after the precinct column at 0).
    starts = [office_cols[0]] + boundaries
    ends = boundaries + [len(grid[0])]
    groups = [(s, e) for s, e in zip(starts, ends) if s < e]

    subgrids = []
    for s, e in groups:
        sub = []
        for row in grid:
            new_row = [row[0]] + row[s:e]
            sub.append(new_row)
        subgrids.append(sub)
    return subgrids


def _build_header_labels(header_cells: list) -> list:
    """Classify header cells into candidate/summary labels."""
    return [_classify_column(c) for c in header_cells]


def _parse_subtable(grid: list, rows: dict):
    """Parse a single contest sub-table into the shared rows accumulator."""
    header_idx = _candidate_header_index(grid)
    if header_idx is None:
        return None, "", ""

    office_parts = []
    for row in grid[:header_idx]:
        for cell in row:
            if _looks_like_office(cell):
                office_parts.append(cell)

    office_text = " ".join(office_parts).strip()
    if not office_text:
        return None, "", ""
    return _parse_office_text(office_text)


def parse_pdf(pdf_path: str, county_name: str):
    rows = defaultdict(int)
    missing_precincts: dict = {}

    for page_num, md in extract_pages(pdf_path):
        tables = _extract_tables(md)
        for grid in tables:
            if _is_statistics_table(grid):
                # TODO: parse registered voters / ballots cast if desired.
                continue

            for sub in _split_side_by_side(grid):
                current_office, current_district, current_party = _parse_subtable(
                    sub, rows
                )
                if not current_office:
                    continue

                header_idx = _candidate_header_index(sub)
                header_cells = sub[header_idx]
                if len(header_cells) < 2:
                    continue

                # If the candidate-header row starts with a precinct name, that
                # precinct's data row was consumed by OCR as the header.  Record
                # it so we can later reconstruct the missing row from county totals.
                first_header_cell = header_cells[0].strip()
                if _looks_like_precinct(first_header_cell):
                    contest_key = (current_office, current_district, current_party)
                    missing_precincts[contest_key] = first_header_cell

                header_labels = _build_header_labels(header_cells[1:])

                for row in sub[header_idx + 1 :]:
                    if not row or not row[0].strip():
                        continue
                    if row[0].strip().lower() == "totals":
                        continue
                    if not _is_data_row(row, header_labels):
                        continue

                    precinct = row[0].strip()
                    for i, cell in enumerate(row[1:], start=1):
                        if i - 1 >= len(header_labels):
                            break
                        label = header_labels[i - 1]
                        if label is None:
                            continue
                        votes_text = cell.strip().replace(",", "")
                        if not re.fullmatch(r"\d+", votes_text):
                            continue
                        key = (
                            precinct,
                            current_office,
                            current_district,
                            current_party,
                            label,
                        )
                        rows[key] += int(votes_text)

    out_rows = [
        make_row(
            county=county_name,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in rows.items()
    ]
    return out_rows, missing_precincts


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: uv run python src/parsers/2026_primary_ocr_table_parser.py "
            "COUNTY '/path/to/County.pdf'"
        )
        sys.exit(1)
    county_name = sys.argv[1]
    pdf_path = sys.argv[2]
    out_rows, missing_precincts = parse_pdf(pdf_path, county_name)
    out_rows = align_candidates_to_county(
        out_rows,
        "2026/20260519__or__primary__county.csv",
        county_name,
        tolerance=5,
    )
    out_rows = recover_missing_precincts(
        out_rows,
        "2026/20260519__or__primary__county.csv",
        county_name,
        missing_precincts,
    )
    path = (
        Path("2026/counties")
        / f"20260519__or__primary__{COUNTY_FILENAME[county_name]}__precinct.csv"
    )
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
