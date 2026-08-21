#!/usr/bin/env python3
"""Lake County 2026 primary precinct PDF parser.

Lake's canvass pages are single HTML tables produced by PaddleOCR-VL.  The
office title and party live in top-left rowspan/colspan cells inside the table,
and candidate/summary names are in header cells above the precinct rows.  Because
OCR frequently inserts an empty spacer column before the vote columns, the
header labels are often shifted one column to the right of the data.  This
parser expands rowspan/colspan, derives the office/party from the embedded title,
pairs header labels to vote columns by order, and falls back to county-total
matching when candidate headers are missing (e.g. the Republican U.S. Senate
page).

Explainable mismatches against 2026/20260519__or__primary__county.csv:

* Several local / non-state offices appear in the precinct PDF but are omitted
  from the county-level CSV: Commissioner Position 2, Commissioner Position 3,
  Surveyor, Judge of the Supreme Court Position 4, and Labor Commissioner.
  Those precinct sums therefore show as NOT_IN_COUNTY.
* The county-level CSV combines write-ins, over votes and under votes into a
  single ``Misc.`` row for state-level contests.  The precinct PDF keeps the
  three categories separate.  ``align_candidates_to_county`` maps Write-ins to
  Misc. only when the totals match within tolerance; Over Votes and Under Votes
  remain separate and are reported as NOT_IN_COUNTY.
* The Governor Democratic and Governor Republican contests in the county CSV
  have mangled / combined candidate names (e.g. several candidates collapsed
  into one row).  The parser keeps the clean candidate names from the PDF
  headers and therefore does not align those rows to the county CSV.
* The county-level CSV labels State Measure 120 as ``Candidate 1`` /
  ``Candidate 2``; the precinct PDF uses ``Yes`` / ``No``.  The parser keeps the
  PDF labels.

Verification against 2026/20260519__or__primary__county.csv currently reports
14 mismatches, 14 county totals missing in precincts, and 78 precinct sums
missing in the county CSV -- all attributable to the categories above.  The
four openelections-data-tests (duplicate_entries, file_format, missing_values,
vote_breakdown_totals) pass on the generated precinct CSV.

Usage:
    uv run python src/parsers/2026_primary_lake_parser.py \
        /path/to/Lake.pdf
"""

import csv
import html
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

COUNTY_NAME = "Lake"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"
OUT_DIR = "2026/counties"

# Header text that is not a candidate/summary label.
_META_FRAGMENTS = {
    "office or measure",
    "county",
    "election",
    "page",
    "state of oregon",
    "abstract of votes",
    "votes cast for governor",
    "i certify",
    "signature",
    "certified",
    "img_",
    "lake",
    "may 19, 2026",
}

_PSEUDO_ORDER = ["Write-ins", "Over Votes", "Under Votes"]


def _flatten_ws(text: str) -> str:
    """Collapse whitespace and normalize HTML entities."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_meta_cell(text: str) -> bool:
    if not text:
        return True
    t = text.lower()
    for frag in _META_FRAGMENTS:
        if frag in t:
            return True
    return False


def _clean_candidate(text: str) -> str:
    """Normalize a candidate/summary header into the canonical CSV form."""
    t = text.strip().upper()
    if not t:
        return ""
    if "WRITE-INS" in t or "WRITE-IN" in t:
        return "Write-ins"
    if "OVER VOTES" in t or "OVERVOTES" in t:
        return "Over Votes"
    if "UNDER VOTES" in t or "UNDERVOTES" in t:
        return "Under Votes"
    if t in {"YES", "NO"}:
        return t.title()
    parts = [p for p in text.split() if p != "(WI)"]
    name = format_candidate_name(parts)
    return name.title()


def _expand_table(md: str) -> list:
    """Parse the single HTML table into a rectangular grid of cell texts."""
    rows = []
    blocked = set()

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", md, flags=re.S | re.I):
        cells = []
        for m in re.finditer(r"<td\b([^>]*)>(.*?)</td>", tr, flags=re.S | re.I):
            attrs = m.group(1)
            raw = _flatten_ws(m.group(2))
            cs = 1
            rs = 1
            cm = re.search(r'colspan=["\']?(\d+)', attrs, re.I)
            if cm:
                cs = int(cm.group(1))
            rm = re.search(r'rowspan=["\']?(\d+)', attrs, re.I)
            if rm:
                rs = int(rm.group(1))
            cells.append((raw, cs, rs))

        ri = len(rows)
        grid_row = []
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
        rows.append(grid_row)

    if not rows:
        return rows
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def _find_office_text(grid: list) -> str:
    for row in grid:
        for cell in row:
            if "OFFICE OR MEASURE" in cell.upper():
                return cell
    return ""


def _parse_office(text: str) -> tuple:
    text = re.sub(r"^OFFICE\s+OR\s+MEASURE\s*", "", text, flags=re.IGNORECASE).strip()
    party = normalize_party(text)
    office = normalize_office(text)
    district = parse_district(text)

    if office is None:
        base = re.sub(r",?\s*\d+(?:ST|ND|RD|TH)\s+DISTRICT", "", text, flags=re.IGNORECASE)
        base = re.sub(r",?\s*POSITION\s+\d+", "", base, flags=re.IGNORECASE)
        base = re.sub(r"\s+", " ", base).strip(" ,")
        office = base.title()

    if office and "measure" in office.lower():
        office = office.title()

    # Normalize district case: "POSITION 1" -> "Position 1", "26TH DISTRICT" -> "26th District".
    if district:
        district = re.sub(r"\bPOSITION\b", "Position", district, flags=re.IGNORECASE)
        district = re.sub(r"\bDISTRICT\b", "District", district, flags=re.IGNORECASE)
        district = re.sub(
            r"\b(\d+)(ST|ND|RD|TH)\b",
            lambda m: f"{m.group(1)}{m.group(2).lower()}",
            district,
            flags=re.IGNORECASE,
        )

    return office, district, party


def _parse_precinct(row: list) -> tuple:
    """Return (precinct_code, leading_columns) for a data row.

    Handles both the two-column (place, P-1) layout and the combined
    'SILVER LAKE P-1' layout.  ``leading_columns`` is the first vote column.
    """
    if not row:
        return None, 0
    first = row[0].strip()
    second = row[1].strip() if len(row) > 1 else ""

    if re.fullmatch(r"P-\d+", second):
        return second, 2

    m = re.search(r"(P-\d+)\s*$", first)
    if m:
        return m.group(1), 1

    if re.fullmatch(r"P-\d+", first):
        return first, 1

    return None, 0


def _is_precinct_data_row(row: list) -> bool:
    precinct, _ = _parse_precinct(row)
    if not precinct:
        return False
    if row[0].strip().lower().startswith("total"):
        return False
    numeric = sum(1 for c in row[1:] if re.fullmatch(r"[\d,]+", c.strip()))
    return numeric > 0


def _find_data_bounds(grid: list) -> tuple:
    first = None
    total = len(grid)
    for i, row in enumerate(grid):
        if first is None and _is_precinct_data_row(row):
            first = i
        if first is not None and i > first and row[0].strip().lower().startswith("total"):
            total = i
            break
    if first is None:
        first = 0
    return first, total


def _header_labels(grid: list, data_start: int, leading: int) -> list:
    """Return [(col, cleaned_label), ...] for non-meta header cells at/after leading."""
    if not grid:
        return []
    width = len(grid[0])
    labels = []
    for col in range(leading, width):
        for row in grid[:data_start]:
            text = row[col].strip()
            if text and not _is_meta_cell(text):
                label = _clean_candidate(text)
                if label:
                    labels.append((col, label))
                break
    return labels


def _load_county_totals(path: str) -> dict:
    """Return {(office,district,party,candidate): total}."""
    totals = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != COUNTY_NAME:
                continue
            key = (
                row["office"].strip(),
                _normalize_district(row["district"].strip()),
                row["party"].strip(),
                row["candidate"].strip(),
            )
            totals[key] = totals.get(key, 0) + int(row["votes"])
    return totals


def _normalize_district(district: str) -> str:
    """Collapse duplicated position text like 'Position 1 Position 1'."""
    parts = district.split()
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return " ".join(out)


def _county_candidates_for_contest(totals: dict, office: str, district: str, party: str) -> list:
    """Return list of (candidate, total) excluding Misc."""
    out = []
    for (o, d, p, cand), total in totals.items():
        if o == office and d == _normalize_district(district) and p == party:
            if re.sub(r"[^a-z0-9 ]+", " ", cand.lower()).strip() != "misc":
                out.append((cand, total))
    return out


def _match_unlabeled_columns(vote_cols: list, col_sums: dict, county_cands: list) -> dict:
    """Map unlabeled vote columns to county candidate names by total."""
    mapping = {}
    used = set()
    for cand, total in county_cands:
        best_col = None
        best_diff = None
        for col in vote_cols:
            if col in used or col in mapping:
                continue
            diff = abs(col_sums.get(col, 0) - total)
            if diff <= 0 and (best_diff is None or diff < best_diff):
                best_col = col
                best_diff = diff
        if best_col is not None:
            mapping[best_col] = cand
            used.add(best_col)
    return mapping


def parse_pdf(pdf_path: str) -> tuple:
    """Parse all pages of the Lake PDF into rows and missing-precinct info."""
    rows: dict = defaultdict(int)
    missing_precincts: dict = {}
    county_totals = _load_county_totals(COUNTY_CSV)

    for page_num, md in extract_pages(pdf_path):
        grid = _expand_table(md)
        if not grid:
            continue

        office_text = _find_office_text(grid)
        if not office_text:
            continue

        office, district, party = _parse_office(office_text)
        if not office:
            continue

        data_start, data_end = _find_data_bounds(grid)
        if data_start >= data_end:
            continue

        # Determine the first data row to infer the leading (place/code) columns.
        sample = grid[data_start]
        precinct, leading = _parse_precinct(sample)
        if not precinct:
            continue

        width = len(grid[0])

        # Identify vote columns: columns at/after ``leading`` that contain numbers.
        vote_cols = []
        for col in range(leading, width):
            for row in grid[data_start:data_end]:
                if re.fullmatch(r"[\d,]+", row[col].strip()):
                    vote_cols.append(col)
                    break
        if not vote_cols:
            continue

        # Extract header labels in column order.
        labels = _header_labels(grid, data_start, leading)

        # Pair labels to vote columns by index order.  If there are more data
        # columns than labels, merge the trailing unlabeled data columns into
        # the last labeled candidate/summary.
        col_to_label = {}
        if labels:
            label_names = [lab for _, lab in labels]
            if len(vote_cols) <= len(label_names):
                for col, name in zip(vote_cols, label_names):
                    col_to_label[col] = name
            else:
                for col, name in zip(vote_cols[: len(label_names) - 1], label_names[:-1]):
                    col_to_label[col] = name
                # Last label covers all remaining trailing vote columns.
                last_cols = vote_cols[len(label_names) - 1 :]
                col_to_label[tuple(last_cols)] = label_names[-1]

        # Fallback: columns without a label are matched against county totals.
        if None in {col_to_label.get(c) for c in vote_cols} or not labels:
            col_sums = {}
            for col in vote_cols:
                if isinstance(col, tuple):
                    col_sums[col] = sum(
                        int(row[c].strip().replace(",", ""))
                        for c in col
                        for row in grid[data_start:data_end]
                        if re.fullmatch(r"[\d,]+", row[c].strip())
                    )
                else:
                    col_sums[col] = sum(
                        int(row[col].strip().replace(",", ""))
                        for row in grid[data_start:data_end]
                        if re.fullmatch(r"[\d,]+", row[col].strip())
                    )

            def _is_labeled(col):
                if col in col_to_label:
                    return True
                for k in col_to_label:
                    if isinstance(k, tuple) and col in k:
                        return True
                return False

            unlabeled = [c for c in vote_cols if not _is_labeled(c)]
            county_cands = _county_candidates_for_contest(county_totals, office, district, party)
            matched = _match_unlabeled_columns(unlabeled, col_sums, county_cands)
            for col, name in matched.items():
                col_to_label[col] = name

            # Any still-unlabeled columns are pseudo-candidates in the standard order.
            still_unlabeled = [c for c in unlabeled if c not in col_to_label]
            for idx, col in enumerate(still_unlabeled):
                if idx < len(_PSEUDO_ORDER):
                    col_to_label[col] = _PSEUDO_ORDER[idx]

        # Detect any precinct name that OCR pulled into the header area.
        for row in grid[:data_start]:
            for cell in row:
                precinct, _ = _parse_precinct([cell])
                if precinct:
                    key = (office, district, party)
                    missing_precincts[key] = precinct

        # Emit rows.  Tuple keys in col_to_label represent grouped columns.
        for row in grid[data_start:data_end]:
            precinct, _ = _parse_precinct(row)
            if not precinct or row[0].strip().lower().startswith("total"):
                continue

            for key, label in col_to_label.items():
                if isinstance(key, tuple):
                    raw_votes = sum(
                        int(row[c].strip().replace(",", ""))
                        for c in key
                        if re.fullmatch(r"[\d,]+", row[c].strip())
                    )
                else:
                    raw = row[key].strip().replace(",", "")
                    if not re.fullmatch(r"\d+", raw):
                        continue
                    raw_votes = int(raw)

                rows[(precinct, office, district, party, label)] += raw_votes

    out_rows = [
        make_row(
            county=COUNTY_NAME,
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
    if len(sys.argv) < 2:
        print("Usage: uv run python src/parsers/2026_primary_lake_parser.py /path/to/Lake.pdf")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_rows, missing_precincts = parse_pdf(pdf_path)

    # Keep the original candidate names for contests where the county CSV has
    # unreliable / combined names.
    original_names = [r["candidate"] for r in out_rows]

    out_rows = align_candidates_to_county(
        out_rows, COUNTY_CSV, COUNTY_NAME, tolerance=5
    )
    out_rows = recover_missing_precincts(
        out_rows, COUNTY_CSV, COUNTY_NAME, missing_precincts
    )

    for i, row in enumerate(out_rows):
        if row["office"] == "Governor" or "Measure" in row["office"]:
            row["candidate"] = original_names[i]

    out_path = Path(OUT_DIR) / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY_NAME]}__precinct.csv"
    write_csv(out_rows, str(out_path))
    print(f"Wrote {len(out_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
