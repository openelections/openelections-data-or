#!/usr/bin/env python3
"""Convert Umatilla County 2026 primary precinct PDF to OpenElections CSV.

Source layout
-------------
The Umatilla PDF is an ES&S "Custom Table Report".  Pages 1 and 2 (and their
repeated siblings) are statistics tables and are skipped here.  Starting on
page 3, each page contains one or more contest tables rendered as pdfplumber
extractable tables.

Each contest table has:
* a precinct label in the first column,
* one or more header rows with the office/party/district,
* a candidate header row (candidate names are stacked vertically with "\n"),
* per-precinct data rows with vote totals,
* a trailing "Totals" row that is ignored.

Multiple contests can appear side-by-side on a page (e.g. judicial races and
Measure 120).  They are split by detecting the office cells in the first row.

Write-in handling
-----------------
Most contests have a single "Write-in Totals" summary column that is mapped to
the canonical pseudo-candidate ``Write-ins``.  The Democratic State House 57
contest lists individual write-in candidate columns spread across several
pages; the first of those pages also contains a "Write-in Totals" column for
precincts 101-124R and a later page contains a "Write-in Totals" column for
precincts 125R-133.  The parser records the first "Write-in Totals" value it
sees for each precinct/contest and ignores any subsequent individual write-in
or "Total Votes Cast" columns for the same precinct/contest, preventing the
overlapping detail pages from being double-counted.  For tables without a
"Write-in Totals" column, all ``Write-in: <name>`` columns are aggregated into
``Write-ins``.  ``Write-in: Assigned Not`` and ``Write-in: Blank``
sub-categories are always skipped.

Known verification caveats
----------------------------
The generated precinct CSV is internally consistent (candidate votes within
each row add up to the row's reported Total Votes Cast).  When compared against
``2026/20260519__or__primary__county.csv`` the following differences are
expected and explainable:

* The county-level CSV combines several Governor candidate names (both
  Democratic and Republican) into single garbled rows (e.g.
  ``Forest Steve (Fora) William Laible Alexander Atkinson``,
  ``Waddell Ed Wen Diehl``, ``Christine Martin Ward Drazan``) that cannot be
  matched against the individual precinct candidate names.
* The county-level CSV labels write-in aggregates as ``Misc.`` while the
  precinct PDF uses ``Write-ins``.
* The county-level CSV labels Measure 120 choices as ``Candidate 1`` /
  ``Candidate 2`` while the precinct PDF uses ``Yes`` / ``No``.
* The county-level CSV reports Democratic State House 57 candidate
  ``Jim E. Doherty`` and ``Misc.`` even though the precinct PDF lists ``No
  Candidate Filed`` and many individual write-in names; the parser aggregates
  those write-ins into ``Write-ins``.
* Over/under vote rows and non-partisan judicial races are present in the
  precinct PDF but are absent from the county-level CSV, so they appear as
  ``NOT_IN_COUNTY`` in ``verify_precinct_totals.py``.

Usage:
    uv run python src/parsers/2026_primary_umatilla_parser.py
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    parse_number,
    write_csv,
)

COUNTY = "Umatilla"
PDF_PATH = "~/code/openelections-sources-or/2026/primary/Umatilla.pdf"

# Candidate header cells are often stacked as "Last\nFirst" or with initials
# split across lines.  Map the cleaned header text to a canonical name.
NAME_FIXES: Dict[str, str] = {
    # DEM US Senate
    "Jeff Merkley": "Jeff Merkley",
    "Paul Damian Wells": "Paul Damian Wells",
    # REP US Senate
    "Brent Barker": "Brent Barker",
    "Deborah Brown C": "Deborah C. Brown",
    "David A Burch": "David A. Burch",
    "McAlmond Russell": "Russell McAlmond",
    "Perkins Jo Rae": "Jo Rae Perkins",
    "Skelton Timothy": "Timothy Skelton",
    "David Smith Brock": "David Brock Smith",
    # DEM US House
    "Chris Beck": "Chris Beck",
    "Mary Doyle": "Mary Doyle",
    "Rebecca Mueller": "Rebecca Mueller",
    "Patty Snow": "Patty Snow",
    "Rasmussen Dawn": "Dawn Rasmussen",
    "Peter Quince": "Peter Quince",
    # REP US House
    "Cliff Bentz": "Cliff Bentz",
    "Andrea Carr": "Andrea Carr",
    "Peter J Larson": "Peter J. Larson",
    # DEM Governor
    "Forest Alexander (Fora)": "Forest (Fora) Alexander",
    "Atkinson James IV": "James Atkinson IV",
    "Cal Kishawi": "Cal Kishawi",
    "Tina Kotek": "Tina Kotek",
    "Beckwith Donnie M": "Donnie M. Beckwith",
    "David W Beem": "David W. Beem",
    "Steve William Laible": "Steve William Laible",
    "Brittany Jones": "Brittany Jones",
    "Sheppard Tristan": "Tristan Sheppard",
    "Weigler Miranda": "Miranda Weigler",
    # REP Governor
    "Danielle Bethell": "Danielle Bethell",
    "Dalrymple Hope A": "Hope A. Dalrymple",
    "Ed Diehl": "Ed Diehl",
    "Christine Drazan": "Christine Drazan",
    "Chris Dudley": "Chris Dudley",
    "Kyle M Duyck": "Kyle M. Duyck",
    "David Medina": "David Medina",
    "Neuman Robert": "Robert Neuman",
    "Brad T Peters": "Brad T. Peters",
    "Paul J Romero Jr": "Paul J. Romero Jr.",
    "Wen Waddell": "Wen Waddell",
    "Martin Ward": "Martin Ward",
    "Tim O Youker": "Tim O. Youker",
    "Leroy DeAngelo Turner": "DeAngelo Leroy Turner",
    # State House
    "Campbell Brian": "Brian Campbell",
    "Bobby Levy": "Bobby Levy",
    "Jim E Doherty": "Jim E. Doherty",
    "Greg Smith": "Greg Smith",
    # Labor Commissioner
    "Chris Lynch": "Chris Lynch",
    "Stephenson Christina E": "Christina E. Stephenson",
    # Judicial
    "Christopher Garrett L": "Christopher L. Garrett",
    "O'Connor Ryan T": "Ryan T. O'Connor",
    "Jacqueline Kamins": "Jacqueline Kamins",
    "Lagesen Erin C": "Erin C. Lagesen",
    "Doug Tookey": "Doug Tookey",
}

PRECINCT_RE = re.compile(r"^\d{3}[A-Z]?-")
SKIP_HEADER_LABELS = re.compile(
    r"vote\s+for\s+\d+|precincts\s+reporting|45\s+of\s+45",
    re.IGNORECASE,
)


def clean_cell(cell: Optional[str]) -> str:
    if not cell:
        return ""
    return (
        cell.replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )


def table_hash(table: List[List[Optional[str]]]) -> int:
    """Hash a table's cleaned content to detect duplicate pages."""
    return hash(
        tuple(
            tuple(clean_cell(c) if c else "" for c in row)
            for row in table
        )
    )


def split_table_segments(
    table: List[List[Optional[str]]],
) -> List[Tuple[int, int, str]]:
    """Return (value_start_col, value_end_col_exclusive, office_text) per contest.

    Column 0 is always the shared precinct column.  Each contest's value columns
    start at the office cell and run up to (but not including) the next office
    cell or the end of the row.
    """
    if not table or not table[0]:
        return []
    office_row = table[0]
    # Non-empty cells after the first column mark the start of a contest.
    offices: List[Tuple[int, str]] = []
    for i, cell in enumerate(office_row[1:], start=1):
        text = clean_cell(cell)
        if text:
            offices.append((i, text))
    if not offices:
        return []
    segments = []
    for idx, (col, office_text) in enumerate(offices):
        end = offices[idx + 1][0] if idx + 1 < len(offices) else len(office_row)
        segments.append((col, end, office_text))
    return segments


def classify_header_labels(
    labels: List[str],
) -> List[Tuple[str, Optional[str]]]:
    """Classify candidate-header labels for one contest segment.

    Returns a list aligned with ``labels`` where each item is
    (class, canonical_name).  Classes:

    * ``CANDIDATE`` – regular candidate (canonical_name set)
    * ``WRITEINS`` – the write-in aggregate column
    * ``WRITEIN_SUB`` – an individual write-in name to be aggregated
    * ``OVER_VOTES`` / ``UNDER_VOTES`` / ``TOTAL_VOTES_CAST`` / ``CONTEST_TOTAL``
    * ``SKIP`` – ignored column (e.g. "Write-in: Assigned Not")
    """
    result: List[Tuple[str, Optional[str]]] = []
    has_writein_totals = any(
        "write-in totals" in clean_cell(lab).lower() for lab in labels
    )

    for lab in labels:
        text = clean_cell(lab)
        lower = text.lower()

        if not text or SKIP_HEADER_LABELS.search(text):
            result.append(("SKIP", None))
            continue

        if "total votes cast" in lower:
            result.append(("TOTAL_VOTES_CAST", None))
            continue
        if "contest total" in lower:
            result.append(("CONTEST_TOTAL", None))
            continue
        if text == "Overvotes":
            result.append(("OVER_VOTES", None))
            continue
        if text == "Undervotes":
            result.append(("UNDER_VOTES", None))
            continue
        if lower == "yes" or lower == "no":
            result.append(("CANDIDATE", text.capitalize()))
            continue
        if lower == "no candidate filed":
            result.append(("CANDIDATE", "No Candidate Filed"))
            continue
        if "write-in totals" in lower:
            result.append(("WRITEINS", None))
            continue
        if "write-in:" in lower:
            # Aggregate individual write-in names only when no overall total is present.
            if has_writein_totals:
                result.append(("SKIP", None))
            else:
                result.append(("WRITEIN_SUB", None))
            continue

        # Regular candidate name.
        canonical = NAME_FIXES.get(text)
        if canonical is None:
            canonical = format_candidate_name(text.split())
        result.append(("CANDIDATE", canonical))

    return result


def find_candidate_row_index(
    table: List[List[Optional[str]]], value_start: int, value_end: int
) -> Optional[int]:
    """Locate the candidate-header row for a table contest.

    Column 0 is the shared precinct column; value columns are
    ``value_start`` (inclusive) to ``value_end`` (exclusive).
    """
    for i in range(2, len(table)):
        row = table[i]
        # Column 0 should be empty in header rows and contain the precinct in
        # data rows.
        first = clean_cell(row[0]) if row else ""
        if first and PRECINCT_RE.match(first):
            # Reached data rows before finding a candidate row.
            return None
        # Count non-empty label cells in this contest's value columns.
        non_empty = [
            j
            for j in range(value_start, min(value_end, len(row)))
            if row[j] and clean_cell(row[j])
        ]
        if not non_empty:
            continue
        # Skip the "45 of 45 Precincts Reporting" meta row.
        if len(non_empty) == 1 and "precincts reporting" in clean_cell(
            row[non_empty[0]]
        ).lower():
            continue
        return i
    return None


def parse_pdf(pdf_path: str) -> List[Dict[str, str]]:
    pdf_path = pdf_path.replace("~", os.path.expanduser("~"))
    rows: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    seen_tables: set = set()
    # Track precincts for which an authoritative write-in total has already
    # been recorded, so overlapping candidate-detail pages are not double-counted.
    writein_authority: set = set()

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue

            for table in tables:
                if not table:
                    continue

                h = table_hash(table)
                if h in seen_tables:
                    continue
                seen_tables.add(h)

                segments = split_table_segments(table)
                if not segments:
                    continue

                for value_start, value_end, office_text in segments:
                    office = normalize_office(office_text)
                    if office is None:
                        continue
                    party = normalize_party(office_text)
                    district = parse_district(office_text)

                    cand_row_idx = find_candidate_row_index(table, value_start, value_end)
                    if cand_row_idx is None:
                        continue

                    # Build column mapping for this segment.
                    cand_row = table[cand_row_idx]
                    labels = [
                        cand_row[j]
                        for j in range(value_start, min(value_end, len(cand_row)))
                    ]
                    classes = classify_header_labels(labels)

                    absolute_cols = list(range(value_start, value_start + len(labels)))

                    # Resolve write-in aggregation.
                    has_writeins = any(cls == "WRITEINS" for cls, _ in classes)

                    for r in table[cand_row_idx + 1 :]:
                        if not r:
                            continue
                        precinct = clean_cell(r[0])
                        if not precinct or precinct.startswith("Totals"):
                            continue
                        if not PRECINCT_RE.match(precinct):
                            continue

                        regular_sum = 0
                        writeins_value: Optional[int] = None
                        total_votes_cast: Optional[int] = None
                        overvotes: Optional[int] = None
                        undervotes: Optional[int] = None

                        for col, (cls, cand_name) in zip(absolute_cols, classes):
                            raw = r[col] if col < len(r) else None
                            text = clean_cell(raw)
                            if not text:
                                continue
                            try:
                                value = parse_number(text)
                            except ValueError:
                                continue

                            if cls == "CANDIDATE" and cand_name is not None:
                                key = (precinct, office, district, party, cand_name)
                                rows[key] += value
                                regular_sum += value
                            elif cls == "WRITEINS":
                                writeins_value = (writeins_value or 0) + value
                            elif cls == "WRITEIN_SUB":
                                # Aggregate individual write-in names into Write-ins
                                # when the table has no Write-in Totals column.
                                if not has_writeins:
                                    writeins_value = (writeins_value or 0) + value
                            elif cls == "TOTAL_VOTES_CAST":
                                total_votes_cast = value
                            elif cls == "OVER_VOTES":
                                overvotes = value
                            elif cls == "UNDER_VOTES":
                                undervotes = value

                        authority_key = (precinct, office, district, party)
                        if authority_key in writein_authority:
                            # An authoritative write-in total was already recorded
                            # for this precinct/contest; skip overlapping detail pages.
                            writeins_value = None
                            total_votes_cast = None

                        # Emit write-in aggregate.
                        if writeins_value is not None:
                            key = (precinct, office, district, party, "Write-ins")
                            rows[key] += writeins_value
                            writein_authority.add(authority_key)
                        elif total_votes_cast is not None:
                            residual = total_votes_cast - regular_sum
                            if residual > 0:
                                key = (
                                    precinct,
                                    office,
                                    district,
                                    party,
                                    "Write-ins",
                                )
                                rows[key] += residual
                                writein_authority.add(authority_key)

                        if overvotes is not None:
                            key = (precinct, office, district, party, "Over Votes")
                            rows[key] += overvotes
                        if undervotes is not None:
                            key = (precinct, office, district, party, "Under Votes")
                            rows[key] += undervotes

    out_rows = [
        make_row(
            county=COUNTY,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in rows.items()
    ]
    return out_rows


def main():
    rows = parse_pdf(PDF_PATH)
    out = output_path(COUNTY)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
