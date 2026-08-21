#!/usr/bin/env python3
"""Parse Washington County 2026 Oregon primary precinct PDF.

The PDF has a searchable text layer.  Each contest is spread across one or more
pages; each page shows a subset of candidates with precinct-level votes and
percentages.  We use pdfplumber word positions to align the multi-line headers
and interleaved vote/percent columns, then accumulate candidate totals across
pages.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    raise SystemExit("pdfplumber is required; run: uv sync")

from precinct_2026_common import (
    COUNTY_FILENAME,
    align_candidates_to_county,
    assign_words_to_columns,
    extract_column_centers,
    group_words_into_lines,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY_NAME = "Washington"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"

VOTE_FOR_RE = re.compile(r"\(Vote\s+For\s+\d+\)|VOTE\s+FOR\s+\d+", re.IGNORECASE)
PERCENT_RE = re.compile(r"\d{1,3}(?:\.\d+)?%")


def _looks_like_title(text: str) -> bool:
    """Return True if a line is a contest title."""
    t = text.strip()
    if not t:
        return False
    if VOTE_FOR_RE.search(t):
        return True
    if re.search(r"\bMeasure\s+\d+", t, re.IGNORECASE):
        return True
    return False


def _parse_office(text: str) -> Tuple[str, str, str]:
    text = text.strip()
    text = VOTE_FOR_RE.sub("", text).strip(" -")
    party = ""
    pm = re.search(r"\b(DEM|REP|OEM)\b", text, re.IGNORECASE)
    if pm:
        party = normalize_party(pm.group(1))
        text = text[: pm.start()] + text[pm.end() :]
        text = re.sub(r"^\s*-\s*", "", text)
    office = normalize_office(text)
    if office is None:
        office = text.rstrip(" ,")
    district = parse_district(text)
    return office, district, party


def _candidate_label(text: str) -> Optional[str]:
    """Clean a header cell into a candidate/pseudo-candidate label."""
    t = text.strip()
    if not t or t.lower() in {"precinct", "ballots", "cast", "reg.", "voters", "total", "votes"}:
        return None
    tl = t.lower()
    if tl in {"over votes", "overvotes"}:
        return "Over Votes"
    if tl in {"under votes", "undervotes"}:
        return "Under Votes"
    if "write-in" in tl or "write in" in tl:
        return "Write-ins"
    return t


def _is_data_line(line: List[Tuple[float, float, str]]) -> bool:
    """Return True if a line is a precinct data row."""
    if not line:
        return False
    return bool(re.match(r"Precinct\s+\d+", line[0][2].strip()))


def _is_total_line(line: List[Tuple[float, float, str]]) -> bool:
    if not line:
        return False
    return line[0][2].strip().lower() == "total"


def _parse_page(page) -> List[Dict[str, str]]:
    """Parse a single PDF page into precinct rows."""
    words = page.extract_words()
    if not words:
        return []

    lines = group_words_into_lines(words, y_tolerance=3.0)
    # Find the title line and the table body.
    title = ""
    table_start = None
    for i, line in enumerate(lines):
        joined = " ".join(w[2] for w in line).strip()
        if _looks_like_title(joined):
            title = joined
            table_start = i + 1
            break
    if not title or table_start is None:
        return []

    office, district, party = _parse_office(title)
    if not office:
        return []

    # The next one or two lines are header lines; the first data line starts with "Precinct".
    header_lines: List[List[Tuple[float, float, str]]] = []
    data_start = None
    for i in range(table_start, len(lines)):
        joined = " ".join(w[2] for w in lines[i]).strip()
        if joined.startswith("Precinct "):
            data_start = i
            break
        header_lines.append(lines[i])

    if data_start is None or not header_lines:
        return []

    # Build column centers from the header lines and the first few data rows.
    center_rows = header_lines + lines[data_start : data_start + 3]
    centers = extract_column_centers(center_rows, skip_first_n=1)
    if len(centers) < 4:
        return []

    # Map each header cell to a column and collect candidate/pseudo labels.
    header_labels: Dict[int, str] = {}
    for line in header_lines:
        for col_idx, words_group in enumerate(assign_words_to_columns(line, centers)):
            text = " ".join(words_group).strip()
            label = _candidate_label(text)
            if label:
                # A header cell may span two columns (votes + percent); place it
                # in the left (vote) column if it is not already occupied.
                vote_col = col_idx
                if vote_col not in header_labels:
                    header_labels[vote_col] = label

    # Determine which columns hold candidate votes, Over Votes, Under Votes.
    candidate_cols = []
    over_col = None
    under_col = None
    for col_idx in sorted(header_labels):
        label = header_labels[col_idx]
        if label == "Over Votes":
            over_col = col_idx
        elif label == "Under Votes":
            under_col = col_idx
        else:
            candidate_cols.append((col_idx, label))

    rows: List[Dict[str, str]] = []
    for line in lines[data_start:]:
        if _is_total_line(line):
            break
        if not _is_data_line(line):
            continue
        precinct = line[0][2].strip()
        col_words = assign_words_to_columns(line, centers)
        for col_idx, cand in candidate_cols:
            if col_idx >= len(col_words):
                continue
            text = " ".join(col_words[col_idx]).strip().replace(",", "")
            if not re.fullmatch(r"\d+", text):
                continue
            rows.append(
                make_row(
                    county=COUNTY_NAME,
                    precinct=precinct,
                    office=office,
                    district=district,
                    party=party,
                    candidate=cand,
                    votes=int(text),
                )
            )
        if over_col is not None and over_col < len(col_words):
            text = " ".join(col_words[over_col]).strip().replace(",", "")
            if re.fullmatch(r"\d+", text):
                rows.append(
                    make_row(
                        county=COUNTY_NAME,
                        precinct=precinct,
                        office=office,
                        district=district,
                        party=party,
                        candidate="Over Votes",
                        votes=int(text),
                    )
                )
        if under_col is not None and under_col < len(col_words):
            text = " ".join(col_words[under_col]).strip().replace(",", "")
            if re.fullmatch(r"\d+", text):
                rows.append(
                    make_row(
                        county=COUNTY_NAME,
                        precinct=precinct,
                        office=office,
                        district=district,
                        party=party,
                        candidate="Under Votes",
                        votes=int(text),
                    )
                )
    return rows


def parse_pdf(pdf_path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            rows.extend(_parse_page(page))
    return rows


def main():
    if len(sys.argv) < 2:
        pdf_path = "/Users/dwillis/code/openelections-sources-or/2026/primary/Washington.pdf"
    else:
        pdf_path = sys.argv[1]
    out_rows = parse_pdf(pdf_path)
    out_rows = align_candidates_to_county(out_rows, COUNTY_CSV, COUNTY_NAME, tolerance=5)
    path = (
        Path("2026/counties")
        / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY_NAME]}__precinct.csv"
    )
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
