#!/usr/bin/env python3
"""Convert Columbia County 2026 primary precinct PDF to OpenElections CSV.

Source: ``/Users/dwillis/code/openelections-sources-or/2026/primary/Columbia.pdf``
Output: ``2026/counties/20260519__or__primary__columbia__precinct.csv``

Layout:
- Page 1 is a precinct statistics page (registered voters / ballots cast). It is
  skipped to keep the output focused on contests.
- Pages 2-18 contain one or more contests side-by-side in a transposed table:
  * the first visible column is the precinct name (pdfplumber does not always
    retain it in ``extract_table()``, so the parser pulls precinct names from
    the matching plain-text line);
  * following columns are candidates;
  * each contest ends with summary columns ``Write-in Totals``,
    ``Total Votes Cast``, ``Overvotes``, ``Undervotes``, ``Contest Total``;
  * empty columns separate adjacent contests.

Office text appears in the plain text between the column headers and the data
rows, sometimes split across lines. The parser detects office/district/party
chunks with a regex of known office / party / measure patterns and aligns them
left-to-right with the table blocks.

Known verification caveats (the precinct CSV is internally consistent; these
come from the county-level CSV):

- The county CSV combines several 2026 primary Governor candidates into garbled,
    multi-name rows, so individual precinct candidate names do not match.
- The county CSV labels Measure 120 choices as ``Candidate 1`` / ``Candidate 2``
    while the precinct PDF uses ``Yes`` / ``No``.
- The county CSV contains ``Misc.`` rows for write-in/over/under totals that
    cannot be matched to the precinct PDF's separate ``Write-ins``,
    ``Over Votes``, and ``Under Votes`` rows.
- Local measures (5-308, 5-309, 5-311) and county offices (Assessor,
    Commissioner Position 2) are not present in the county CSV.
- The county CSV duplicates position text for judicial races
    (e.g. ``Position 1 Position 1``); the precinct CSV uses a single position.

Usage:
    uv run python src/parsers/2026_primary_columbia_parser.py
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    DISTRICTED_OFFICES,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)

COUNTY = "Columbia"
PDF_PATH = "~/code/openelections-sources-or/2026/primary/Columbia.pdf"

# Regexes that mark the beginning of an office chunk in the plain text.
OFFICE_START_RE = re.compile(
    r"(?:"
    r"DEM\s+(?:US\s+Senator|US\s+Representative|Governor|State\s+Senator|State\s+Representative)"
    r"|REP\s+(?:US\s+Senator|US\s+Representative|Governor|State\s+Senator|State\s+Representative)"
    r"|Commissioner\s+Bureau\s+of\s+Labor\s+and\s+Industries"
    r"|Judge\s+of\s+the\s+(?:Supreme\s+Court|Court\s+of\s+Appeals|Circuit\s+Court)"
    r"|State\s+Measure\s+\S+"
    r"|Columbia\s+County\s+(?:Assessor|Commissioner)"
    r"|(?:Columbia\s+County\s+)?(?:Assessor|Commissioner)"
    r"|\b5-\d{3}\b"
    r")",
    re.IGNORECASE,
)

PRECINCT_RE = re.compile(r"^(\d+)\s+(.+?)(?:\s+\d|$)")

DISTRICT_TOKEN_RE = re.compile(
    r"(\d+(?:st|nd|rd|th)\s+District(?:,\s*Position\s+\d+)?|Position\s+\d+)",
    re.IGNORECASE,
)

SUMMARY_LABELS = {
    "write-in totals": "Write-ins",
    "write in totals": "Write-ins",
    "overvotes": "Over Votes",
    "undervotes": "Under Votes",
}


def _expand_path(path: str) -> str:
    return path.replace("~", os.path.expanduser("~"))


def _clean_plain_text(text: str) -> str:
    """Return page text with report headers and data rows removed."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^Page\s+\d+\s+of\s+\d+", line):
            continue
        if "May 19, 2026 Primary Election" in line:
            continue
        if "Columbia County, Oregon" in line:
            continue
        if "Precinct Abstract Report" in line:
            continue
        if "June 11, 2026" in line:
            continue
        if line == "PRECINCT NAME":
            continue
        if line == "2026 May 19":
            continue
        if re.match(r"^\d+\s+\S", line):
            # Per-precinct data row.
            continue
        if line.lower().startswith("totals"):
            continue
        lines.append(line)
    return " ".join(lines)


def _normalize_local_office(text: str) -> Tuple[str, str]:
    """Handle county offices / measures that ``normalize_office`` does not map."""
    t = text.strip()
    lowered = t.lower()

    if "columbia county assessor" in lowered:
        return "Assessor", ""
    if "columbia county commissioner" in lowered or (
        lowered.startswith("commissioner") and "bureau" not in lowered
    ):
        district = parse_district(t)
        return "Commissioner", district or ""

    if re.match(r"^5-\d{3}\b", t):
        # Keep the full local measure description as the office name.
        return t, ""

    return "", ""


def _fix_measure_chunk(chunk: str) -> str:
    """Clean up stacked measure descriptions on pages with two local measures."""
    if re.match(r"^5-308\b", chunk, re.IGNORECASE) and "Five Year Local Option Levy Five Year Levy" in chunk:
        return chunk.replace(" Five Year Local Option Levy Five Year Levy", " Five Year Levy")
    if re.match(r"^5-309\b", chunk, re.IGNORECASE) and "Five Year" not in chunk:
        return chunk + " Five Year Local Option Levy"
    return chunk


def detect_office_info(text: str) -> List[Tuple[Optional[str], str, str]]:
    """Return a list of (office, district, party) for each contest on the page.

    Office starts are detected with ``OFFICE_START_RE``. District / position
    tokens are extracted once from the whole page text and assigned to offices
    left-to-right, which handles the common side-by-side layout where two
    offices share a single line of district text.
    """
    clean = _clean_plain_text(text)
    starts = list(OFFICE_START_RE.finditer(clean))
    if not starts:
        return []

    # District / position tokens, in reading order.
    district_tokens = [m.group() for m in DISTRICT_TOKEN_RE.finditer(clean)]

    results = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(clean)
        chunk = clean[m.start():end].strip()

        # Guard against a leading standalone "Columbia County" fragment.
        lowered = chunk.lower()
        if "columbia county" in lowered and not any(
            kw in lowered for kw in ("assessor", "commissioner", "labor")
        ):
            chunk = re.sub(r"^Columbia\s+County\s*", "", chunk, flags=re.IGNORECASE).strip()

        chunk = _fix_measure_chunk(chunk)

        party = normalize_party(chunk)
        office_text = re.sub(r"\b(DEM|REP)\b\s*", "", chunk, flags=re.IGNORECASE).strip()

        local_office, local_district = _normalize_local_office(office_text)
        if local_office:
            office = local_office
            district = local_district
        else:
            office = normalize_office(office_text)
            if office is None:
                office = office_text.rstrip(" ,")
            district = parse_district(office_text)

        results.append((office, district, party))

    # Assign district / position tokens to offices that take them. This handles
    # side-by-side pages where a single line of district text serves multiple
    # contests (e.g. "Position 4 Position 1" or "31st District 32nd District").
    district_needing_indices = [
        i
        for i, (office, _, _) in enumerate(results)
        if office and (
            office in DISTRICTED_OFFICES
            or office.startswith("Judge of")
            or office in {"Commissioner"}
        )
    ]
    for j, token in enumerate(district_tokens):
        if j >= len(district_needing_indices):
            break
        idx = district_needing_indices[j]
        parsed = parse_district(token)
        if parsed:
            office, _, party = results[idx]
            results[idx] = (office, parsed, party)

    return results


def _column_label(header_rows: List[List[Optional[str]]], col_idx: int) -> str:
    """Concatenate all header cell text for a column."""
    parts = []
    for row in header_rows:
        if col_idx < len(row) and row[col_idx]:
            parts.extend(str(row[col_idx]).replace("\n", " ").split())
    return " ".join(parts)


def _split_into_blocks(
    header_labels: List[str],
) -> List[Tuple[int, int]]:
    """Return contest column ranges, ignoring the leading precinct column."""
    blocks = []
    start = None
    for idx, label in enumerate(header_labels):
        if label.strip():
            if start is None:
                start = idx
        else:
            if start is not None:
                blocks.append((start, idx))
                start = None
    if start is not None:
        blocks.append((start, len(header_labels)))
    # Drop block at column 0 (precinct label).
    return [(s, e) for s, e in blocks if s > 0]


def _candidate_for_label(label: str) -> Optional[str]:
    """Map a header label to a candidate / pseudo-candidate name."""
    lowered = label.lower().strip()

    for key, value in SUMMARY_LABELS.items():
        if key in lowered:
            return value

    if "total votes cast" in lowered or "contest total" in lowered:
        return None  # skipped

    # Measures: Yes / No are the choices.
    if lowered in {"yes", "no"}:
        return label.strip()

    parts = [p for p in label.split() if p]
    if not parts:
        return None

    return format_candidate_name(parts)


def _extract_precinct(line: str) -> Optional[str]:
    """Return ``Precinct N`` from a plain-text data line like ``01 City of ...``."""
    m = PRECINCT_RE.match(line.strip())
    if not m:
        return None
    number = int(m.group(1))
    return f"Precinct {number}"


def _plain_text_precincts(text: str) -> List[str]:
    """Return ordered precinct data lines from plain page text."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "2026 May 19" in line:
            continue
        if re.match(r"^\d+\s+\S", line):
            lines.append(line)
    return lines


def _parse_page(
    page,
    carry_office: Optional[str],
    carry_district: str,
    carry_party: str,
) -> Tuple[List[Dict[str, str]], Optional[str], str, str]:
    """Return rows from a single PDF page plus the office state to carry forward."""
    text = page.extract_text() or ""
    table = page.extract_table()
    if not table:
        return [], carry_office, carry_district, carry_party

    # Page 1 is the precinct statistics page; nothing to emit.
    if "STATISTICS" in text:
        return [], carry_office, carry_district, carry_party

    # The Columbia abstract uses a single header row, then one table row per
    # precinct in the same order as the plain-text precinct list, then a Totals
    # row. pdfplumber does not capture the precinct label inside the table row,
    # so we pull the precinct name from the matching plain-text line.
    precinct_lines = _plain_text_precincts(text)
    if len(table) < 2 or not precinct_lines:
        return [], carry_office, carry_district, carry_party

    header_row = table[0]
    data_rows = table[1:-1]
    totals_row = table[-1]
    if not totals_row or str(totals_row[0]).strip().lower() != "totals":
        data_rows = table[1:]

    if len(precinct_lines) != len(data_rows):
        # If counts disagree, fall back to the data rows we have and keep the
        # tail of precinct lines for those rows.
        data_rows = data_rows[: min(len(precinct_lines), len(data_rows))]
        precinct_lines = precinct_lines[: len(data_rows)]

    num_cols = len(header_row)
    header_labels = [_column_label([header_row], c) for c in range(num_cols)]
    blocks = _split_into_blocks(header_labels)

    if not blocks:
        return [], carry_office, carry_district, carry_party

    office_info = detect_office_info(text)

    rows: List[Dict[str, str]] = []

    # Align blocks left-to-right with office info. If there are fewer entries
    # than blocks, carry the last-known office to the rightmost blocks.
    current_office = carry_office
    current_district = carry_district
    current_party = carry_party

    for block_idx, (start, end) in enumerate(blocks):
        if block_idx < len(office_info):
            office, district, party = office_info[block_idx]
            if office:
                current_office = office
                current_district = district
                current_party = party

        if not current_office:
            continue

        # Determine which columns in this block are candidates vs. summary.
        candidate_specs: List[Tuple[int, str]] = []
        for c in range(start, end):
            candidate = _candidate_for_label(header_labels[c])
            if candidate:
                candidate_specs.append((c, candidate))

        if not candidate_specs:
            continue

        for precinct_line, row in zip(precinct_lines, data_rows):
            if len(row) < end:
                continue
            precinct = _extract_precinct(precinct_line)
            if not precinct:
                continue

            # If the first candidate cell of this block is empty, the contest
            # does not include this precinct (district-specific contests).
            first_cand_col, _ = candidate_specs[0]
            if first_cand_col >= len(row) or not str(row[first_cand_col]).strip():
                continue

            for col_idx, candidate in candidate_specs:
                if col_idx >= len(row):
                    continue
                raw = str(row[col_idx]).strip().replace(",", "")
                if raw == "":
                    continue
                try:
                    votes = int(raw)
                except ValueError:
                    continue
                rows.append(
                    make_row(
                        county=COUNTY,
                        precinct=precinct,
                        office=current_office,
                        district=current_district,
                        party=current_party,
                        candidate=candidate,
                        votes=votes,
                    )
                )

    return rows, current_office, current_district, current_party


def parse_pdf(pdf_path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    carry_office: Optional[str] = None
    carry_district = ""
    carry_party = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_rows, carry_office, carry_district, carry_party = _parse_page(
                page, carry_office, carry_district, carry_party
            )
            rows.extend(page_rows)

    return rows


def main():
    pdf_path = _expand_path(PDF_PATH)
    rows = parse_pdf(pdf_path)
    out = output_path(COUNTY)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
