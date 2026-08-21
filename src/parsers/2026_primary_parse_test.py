#!/usr/bin/env python3
"""Prototype parser for 2026 primary precinct CSVs (Benton, Polk, Tillamook)."""

import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
from precinct_2026_common import (
    ELECTION_DATE,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)


SUMMARY_KEYWORDS = {
    "write-in totals",
    "write-in: assigned",
    "write-in: not assigned",
    "total votes cast",
    "overvotes",
    "undervotes",
    "contest total",
    "contest totals",
    "totals",
}


def expand_table(html: str) -> List[List[str]]:
    """Expand a table's row/colspan cells into a uniform 2-D string grid."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    raw_rows = []
    for tr in table.find_all("tr"):
        cells = []
        for td in tr.find_all(["td", "th"]):
            cells.append(
                {
                    "text": re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip(),
                    "colspan": int(td.get("colspan", 1) or 1),
                    "rowspan": int(td.get("rowspan", 1) or 1),
                }
            )
        raw_rows.append(cells)

    # Compute logical width.
    width = 0
    for r in raw_rows:
        w = sum(c["colspan"] for c in r)
        width = max(width, w)

    grid: List[List[Optional[str]]] = [[None] * width for _ in range(len(raw_rows))]
    for i, r in enumerate(raw_rows):
        for c in r:
            j = 0
            while j < width and grid[i][j] is not None:
                j += 1
            if j >= width:
                continue
            for ci in range(c["colspan"]):
                for ri in range(c["rowspan"]):
                    if i + ri < len(grid) and j + ci < width:
                        grid[i + ri][j + ci] = c["text"]
    return [[cell or "" for cell in row] for row in grid]


def is_precinct_token(text: str) -> bool:
    """Heuristic: precinct identifiers are short alphanumeric tokens."""
    if not text or text.lower() in {"totals", "total"}:
        return False
    # Allow numeric or codes like AC-1, 001, BAY.
    return bool(re.fullmatch(r"[0-9]{1,4}|[0-9]{1,4}[A-Z]?|[A-Z]{1,3}-?\d+", text))


def is_numeric_or_empty(text: str) -> bool:
    if not text:
        return True
    return bool(re.fullmatch(r"[0-9,]+|" + r"\d{1,3}(?:\.\d+)?%", text))


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def classify_columns(headers: List[str]) -> Tuple[List[Tuple[int, str]], Dict[str, List[int]]]:
    """Return candidate (index, name) pairs and summary keyword -> indices mapping."""
    cands: List[Tuple[int, str]] = []
    summary: Dict[str, List[int]] = defaultdict(list)
    for idx, h in enumerate(headers):
        key = normalize_key(h)
        matched = False
        for kw in SUMMARY_KEYWORDS:
            if normalize_key(kw) in key or key in normalize_key(kw):
                summary[kw].append(idx)
                matched = True
                break
        if not matched:
            cands.append((idx, h))
    return cands, dict(summary)


def parse_vote(text: str) -> Optional[int]:
    """Parse an integer vote count, skipping blanks and percentages."""
    if not text:
        return None
    text = text.replace(",", "")
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def parse_title_segments(grid: List[List[str]]) -> List[Tuple[str, int, int]]:
    """Detect side-by-side contests from a title row.

    Returns list of (title_text, start_col, end_col_exclusive)."""
    if not grid:
        return []
    title_row = grid[0]
    segments = []
    current_text = None
    start = 0
    for i, cell in enumerate(title_row):
        if cell and cell != current_text:
            if current_text is not None and current_text:
                segments.append((current_text, start, i))
            current_text = cell
            start = i
    if current_text:
        segments.append((current_text, start, len(title_row)))
    # Drop leading empty segment if present.
    if segments and not segments[0][0].strip():
        segments = segments[1:]
    # If no real segments found, treat whole row as one contest.
    if not segments:
        return [(" ".join(c for c in title_row if c).strip(), 0, len(title_row))]
    return segments


def extract_row_layout_contests(
    grid: List[List[str]], county: str
) -> List[Dict]:
    """Extract contest slices from a row-as-precinct table grid."""
    if not grid or len(grid) < 2:
        return []

    # Find first data row: first cell is a precinct token and most other cells numeric/empty.
    header_idx = None
    first_data_idx = None
    for i, row in enumerate(grid):
        if not row:
            continue
        first = row[0]
        rest = row[1:]
        if is_precinct_token(first) and all(is_numeric_or_empty(c) for c in rest):
            first_data_idx = i
            break
    if first_data_idx is None:
        return []
    header_idx = first_data_idx - 1
    if header_idx < 0:
        return []

    title_segments = parse_title_segments(grid)
    contests: List[Dict] = []
    for title, start, end in title_segments:
        if start == 0 and end == len(grid[0]):
            # Single contest; use header row excluding first (precinct) column.
            header_cols = grid[header_idx][1:]
            cand_info, summary = classify_columns(header_cols)
            # Map candidate column indices back to full-row indices (1-based).
            cand_names = [(c + 1, name) for c, name in cand_info]
            summary_indices = {k: [v + 1 for v in vals] for k, vals in summary.items()}
        else:
            # Side-by-side: slice within header row and data rows.
            # start/end are within full row; the first column of the slice may be empty due to rowspan.
            header_slice = grid[header_idx][start:end]
            # Remove leading empty cells if any.
            while header_slice and not header_slice[0].strip():
                header_slice = header_slice[1:]
                start += 1
            if not header_slice:
                continue
            cand_info, summary = classify_columns(header_slice)
            cand_names = [(start + idx, name) for idx, name in cand_info]
            summary_indices = {k: [start + v for v in vals] for k, vals in summary.items()}

        # Determine data rows for this segment. Use all data rows that have a precinct in col 0.
        data_rows = []
        for row in grid[first_data_idx:]:
            if not row:
                continue
            prec = row[0].strip()
            if not prec or prec.lower() in {"totals", "total"}:
                continue
            if not is_precinct_token(prec):
                continue
            data_rows.append((prec, row))

        contests.append(
            {
                "title": title,
                "office": normalize_office(title),
                "district": parse_district(title),
                "party": normalize_party(title),
                "cand_names": cand_names,
                "summary_indices": summary_indices,
                "data_rows": data_rows,
            }
        )
    return contests


def rows_from_row_layout(contests: List[Dict], county: str) -> List[Dict]:
    """Convert row-layout contest slices to CSV rows."""
    out: List[Dict] = []
    for c in contests:
        office = c["office"]
        district = c["district"]
        party = c["party"]
        title = c["title"]
        if office is None:
            office = title

        cand_indices = c["cand_indices"]
        summary_indices = c["summary_indices"]

        # Candidate names from the table header (slice) -- not used if we remap later,
        # but keep as raw candidate labels.
        # We'll derive raw names from the first data row's corresponding cells? No,
        # names are in header row. For row-layout, cand_indices point into header row.
        # We need header row text. We don't store header row; let's store raw names in contest.
        # Adjust extract function to include cand_names.
        pass
    return out


if __name__ == "__main__":
    cache = Path("/Users/dwillis/code/openelections-data-or/.paddleocr_cache/Benton")
    page = (cache / "p010.md").read_text()
    tables = re.split(r"(?=<table)", page)
    for t in tables:
        if "<table" not in t:
            continue
        grid = expand_table(t)
        if not grid:
            continue
        print("Table:", grid[0])
        for c in extract_row_layout_contests(grid, "Benton"):
            print("  contest:", c["title"], "party", c["party"], "office", c["office"])
            print("    cand names:", c["cand_names"])
            print("    summary:", c["summary_indices"])
            print("    rows:", len(c["data_rows"]))
