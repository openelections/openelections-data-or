#!/usr/bin/env python3
"""Parse Gilliam and Harney 2026 primary precinct PDFs from cached PaddleOCR markdown.

Both counties share an "Abstract of Votes" layout where candidates are rows and
precincts are columns. The parser reads per-page markdown from
.paddleocr_cache/{County}/p*.md (it does not re-run OCR), expands HTML
rowspan/colspan, identifies the precinct header row, tracks office/district/party
from section headers, and emits one row per (precinct, candidate).

Explainable mismatches vs. 2026/20260519__or__primary__county.csv:

* Governor (D and R): the county CSV conflates multiple candidate names into
  single garbled rows and omits several candidates entirely. Because the county
  totals cannot be reconciled with individual candidate names, the Governor
  contest is excluded from candidate-name alignment and the precinct file
  preserves the real parsed candidate names.

* Write-ins: the county CSV labels aggregated write-in votes as "Misc.", while
  the precinct file uses the canonical OpenElections pseudo-candidate name
  "Write-ins". This produces MISMATCH/NOT_IN_COUNTY/NOT_IN_PRECINCTS entries
  for every contest that has write-in votes.

* Measure 120: the county CSV labels the choices as generic "Candidate 1" and
  "Candidate 2"; the precinct file emits the actual choices "Yes" and "No".

* State House 57 (Gilliam D): the county CSV lists both "Jim E. Doherty" (8
  votes) and "Misc." (14 votes). The precinct file only records the 14
  write-in aggregate from the main abstract; the write-in detail page listing
  individual names is skipped. The county CSV therefore double-counts the
  write-in detail against the aggregate.

* Judicial offices and county offices: these contests appear in the source PDFs
  but are absent from the county CSV for Gilliam/Harney, so their precinct sums
  are reported as NOT_IN_COUNTY by verify_precinct_totals.py.

* Precinct Committeeperson (PCP) contests are skipped because they are not
  included in the county-level CSV.
"""

import argparse
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from paddleocr_extract import plain_text  # noqa: E402
from precinct_2026_common import (  # noqa: E402
    align_candidates_to_county,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    recover_missing_precincts,
    write_csv,
)

# Local office mappings for contests that normalize_office does not catch.
_EXTRA_OFFICE_MAP = {
    "COMMISSIONER OF BUREAU LABOR AND INDUSTRIES": "Labor Commissioner",
    "COUNTY SURVEYOR": "County Surveyor",
    "COUNTY TREASURER": "County Treasurer",
    "COUNTY COMMISSIONER": "County Commissioner",
    "COUNTY JUSTICE OF PEACE": "Justice of the Peace",
    "STATE MEASURE": "Measure 120",
    "JUDGE SUPREME COURT": "Judge of the Supreme Court",
    "JUDGE COURT OF APPEALS": "Judge of the Court of Appeals",
    "JUDGE CIRCUIT COURT": "Judge of the Circuit Court",
}


def _local_normalize_office(text: str) -> Optional[str]:
    """Return canonical office, including a few county-specific mappings."""
    canonical = normalize_office(text)
    if canonical:
        return canonical
    upper = text.upper()
    for key, value in _EXTRA_OFFICE_MAP.items():
        if key in upper:
            return value
    return None


def _is_int(text: str) -> bool:
    return bool(text and re.fullmatch(r"[0-9,]+", text))


def _to_int(text: str) -> int:
    return int(text.replace(",", "")) if text else 0


def _has_numbers(values: Iterable[str]) -> bool:
    return any(_is_int(v) for v in values if v)


def _canonicalize_district(district: str) -> str:
    """Return district text in title case while keeping ordinals lowercase."""
    if not district:
        return district
    canonical = district.title()
    canonical = re.sub(
        r"(\d+)(St|Nd|Rd|Th)\b",
        lambda m: f"{m.group(1)}{m.group(2).lower()}",
        canonical,
    )
    return canonical


def _normalize_candidate(text: str) -> str:
    """Normalize pseudo-candidates and clean regular names."""
    text = text.strip()
    if not text:
        return ""
    low = text.lower()
    if "write" in low and "in" in low:
        return "Write-ins"
    if low in {"overvote", "overvotes", "over votes", "total overvotes", "oversvotes"}:
        return "Over Votes"
    if low in {"undervote", "undervotes", "under votes", "total undervotes"}:
        return "Under Votes"
    if low == "totals" or low == "total":
        return "Totals"
    if low == "no candidate filed":
        return "No Candidate Filed"
    if low == "name of write in" or low == "misc. other":
        return "Write-ins"
    if low in {"yes", "no"}:
        return text.strip().title()
    return format_candidate_name(text.split())


def _expand_table(md: str) -> List[List[str]]:
    """Extract all <table> blocks from markdown and expand rowspan/colspan.

    Returns a rectangular grid of cell texts.  Empty cells become "".
    """
    raw_rows: List[List[Tuple[str, int, int]]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", md, flags=re.S | re.I):
        cells = []
        for attrs, content in re.findall(
            r"<td([^>]*)>(.*?)</td>", tr, flags=re.S | re.I
        ):
            rowspan = 1
            m = re.search(r'rowspan\s*=\s*["\']?(\d+)["\']?', attrs, re.I)
            if m:
                rowspan = int(m.group(1))
            colspan = 1
            m = re.search(r'colspan\s*=\s*["\']?(\d+)["\']?', attrs, re.I)
            if m:
                colspan = int(m.group(1))
            stripped = re.sub(r"<[^>]+>", " ", content)
            stripped = html.unescape(stripped)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            cells.append((stripped, rowspan, colspan))
        raw_rows.append(cells)

    grid: List[List[Optional[str]]] = []
    for r, cells in enumerate(raw_rows):
        while len(grid) <= r:
            grid.append([])
        c = 0
        for text, rowspan, colspan in cells:
            while c < len(grid[r]) and grid[r][c] is not None:
                c += 1
            for dr in range(rowspan):
                rr = r + dr
                while len(grid) <= rr:
                    grid.append([])
                for dc in range(colspan):
                    cc = c + dc
                    while len(grid[rr]) <= cc:
                        grid[rr].append(None)
                    grid[rr][cc] = text
            c += colspan
    return [[cell or "" for cell in row] for row in grid]


def _find_header_and_layout(
    grid: List[List[str]],
) -> Tuple[Optional[int], Optional[str]]:
    """Return (header_row_index, layout_name) for the precinct header row.

    Layouts:
      - "gilliam": columns are [office, candidate, precincts..., total]
      - "harney": columns are [office/candidate, precincts..., total]
    """
    for i, row in enumerate(grid):
        has_precinct = any(
            re.search(r"Prec\s*#?\s*\d+|Pct\.\s*\d+", c, re.I) for c in row
        )
        if not has_precinct:
            continue
        if any("OFFICE" in c.upper() or "CANDIDATES" in c.upper() for c in row):
            return i, "gilliam"
        return i, "harney"
    return None, None


def _extract_precincts(header_row: List[str], precinct_start: int) -> List[str]:
    """Return precinct labels from a header row, stopping at the total column."""
    precincts = []
    for c in header_row[precinct_start:]:
        if re.search(r"total|totals", c, re.I):
            break
        if c:
            precincts.append(c)
    return precincts


def _page_party(md: str) -> str:
    """Infer party from page-level header text."""
    text = plain_text(md).upper()
    if "NON-PARTISAN" in text or "NONPARTISAN" in text:
        return ""
    if "REPUBLICAN" in text:
        return "R"
    if "DEMOCRAT" in text:
        return "D"
    return ""


def _is_overall_page(md: str) -> bool:
    return "overall" in plain_text(md).lower()


def _looks_like_pcp(text: str) -> bool:
    low = text.lower()
    return "prec committeeperson" in low or "prec committee person" in low


def _looks_like_metadata_row(text: str) -> bool:
    low = text.lower()
    return "registered voters" in low or "total voting" in low or "turnout" in low


def _merge_split_office_headers(grid: List[List[str]]) -> List[List[str]]:
    """Merge Gilliam office headers that span two rows.

    Some pages split a long office name across two rows, e.g.
    "COMMISSIONER OF BUREAU" / "LABOR AND INDUSTRIES".  When neither row
    alone normalizes but their concatenation does, merge them into the first
    row and blank the second row's office column so it is treated as a normal
    candidate row.
    """
    out = [list(row) for row in grid]
    for i in range(len(out) - 1):
        col0 = out[i][0].strip()
        next_col0 = out[i + 1][0].strip()
        if not col0:
            continue
        if _local_normalize_office(col0):
            continue
        low = col0.lower()
        # Do not merge page headers, column headers, or metadata rows with data.
        if (
            "office" in low
            or "candidates" in low
            or "page" in low
            or _looks_like_metadata_row(col0)
        ):
            continue
        combined = (col0 + " " + next_col0).strip()
        if _local_normalize_office(combined):
            out[i][0] = combined
            out[i + 1][0] = ""
    return out


def _parse_gilliam_page(
    grid: List[List[str]], md: str, county: str, metadata_emitted: bool
) -> Tuple[List[Dict[str, str]], bool]:
    """Parse a Gilliam page and return (rows, updated_metadata_emitted)."""
    grid = _merge_split_office_headers(grid)
    header_idx, layout = _find_header_and_layout(grid)
    if header_idx is None or layout != "gilliam":
        return [], metadata_emitted

    header_row = grid[header_idx]
    precincts = _extract_precincts(header_row, precinct_start=2)
    if not precincts:
        return [], metadata_emitted

    # Skip write-in detail pages that list individual write-in names; the
    # aggregate Write-ins row on the main abstract page already captures those
    # votes and the individual names are not canonical candidates.
    for row in grid:
        if len(row) > 1 and re.search(r"NAME\s+OF\s+WRITE\s+IN", row[1], re.I):
            return [], metadata_emitted

    page_party = _page_party(md)
    rows: List[Dict[str, str]] = []
    office = ""
    district = ""
    party = page_party

    for i in range(header_idx + 1, len(grid)):
        row = grid[i]
        if not row or all(not c for c in row):
            continue

        col0 = row[0].strip()
        candidate = row[1].strip() if len(row) > 1 else ""
        next_col0 = grid[i + 1][0].strip() if i + 1 < len(grid) else ""

        # Metadata rows (only emit once, preferring the overall summary page).
        if re.search(r"Total Registered Voters", col0, re.I):
            if not metadata_emitted or _is_overall_page(md):
                for p_idx, prec in enumerate(precincts):
                    val = _to_int(row[2 + p_idx] if 2 + p_idx < len(row) else "")
                    rows.append(
                        make_row(county, prec, "Registered Voters", "", "", "", val)
                    )
                metadata_emitted = True
            continue
        if re.search(r"Total Voting", col0, re.I):
            if not metadata_emitted or _is_overall_page(md):
                for p_idx, prec in enumerate(precincts):
                    val = _to_int(row[2 + p_idx] if 2 + p_idx < len(row) else "")
                    rows.append(
                        make_row(county, prec, "Ballots Cast", "", "", "", val)
                    )
                metadata_emitted = True
            continue
        if re.search(r"% Turnout", col0, re.I):
            continue

        # Office/district transitions.
        if col0 and not _looks_like_metadata_row(col0):
            norm_office = _local_normalize_office(col0)
            if norm_office:
                office = norm_office
                district = parse_district(col0)
                party = normalize_party(col0) or page_party
                # Many Gilliam contests put the district on the row following the
                # office header (e.g., "US REPRESENTATIVE" / "Chris Beck",
                # then "2ND DISTRICT" / "Mary Doyle").  Look ahead and apply the
                # district to the first candidate too.
                if not district and parse_district(next_col0):
                    district = parse_district(next_col0)
                # Do not continue: the first candidate of the contest is often
                # on the same row as the office header (e.g., "GOVERNOR" /
                # "Forest Alexander").
            elif parse_district(col0):
                # Some offices span two rows, with the district on the second row.
                district = parse_district(col0)
                # Do not continue: the candidate is also on this row (e.g.,
                # "57TH DISTRICT" / "Greg Smith").
            elif _looks_like_pcp(col0):
                # Entering a PCP section; clear office so continuation rows are
                # skipped instead of being emitted under the previous contest.
                office = ""
                continue
            else:
                # Unrecognized non-empty text in the office column.  If an office
                # is already active and this row has a candidate (e.g., a ballot
                # measure description continuation), treat it as a candidate row;
                # otherwise skip it.
                if not office or not candidate:
                    continue

        # Skip PCP continuation rows and rows without a candidate or office.
        if _looks_like_pcp(office):
            continue
        if not candidate or not office:
            continue

        cand_norm = _normalize_candidate(candidate)
        if not cand_norm or cand_norm == "Totals" or cand_norm == "No Candidate Filed":
            continue

        values = [
            row[2 + p_idx] if 2 + p_idx < len(row) else "" for p_idx in range(len(precincts))
        ]
        has_numbers = _has_numbers(values)
        for p_idx, prec in enumerate(precincts):
            val_str = values[p_idx]
            if not _is_int(val_str):
                if not has_numbers:
                    continue
                val_str = "0"
            val = _to_int(val_str)
            rows.append(make_row(county, prec, office, district, party, cand_norm, val))

    return rows, metadata_emitted


def _parse_harney_page(grid: List[List[str]], county: str) -> List[Dict[str, str]]:
    """Parse a Harney page and return rows."""
    header_idx, layout = _find_header_and_layout(grid)
    if header_idx is None or layout != "harney":
        return []

    header_row = grid[header_idx]
    precincts = _extract_precincts(header_row, precinct_start=1)
    if not precincts:
        return []

    rows: List[Dict[str, str]] = []
    office = ""
    district = ""
    party = ""

    for row in grid[header_idx + 1 :]:
        if not row or all(not c for c in row):
            continue
        col0 = row[0].strip()
        if not col0:
            continue

        norm_office = _local_normalize_office(col0)
        if norm_office:
            office = norm_office
            district = parse_district(col0)
            party = normalize_party(col0) or party
            continue

        cand_norm = _normalize_candidate(col0)
        if not cand_norm or cand_norm == "Totals":
            continue
        if not office:
            continue

        values = [
            row[1 + p_idx] if 1 + p_idx < len(row) else "" for p_idx in range(len(precincts))
        ]
        has_numbers = _has_numbers(values)
        for p_idx, prec in enumerate(precincts):
            val_str = values[p_idx]
            if not _is_int(val_str):
                if not has_numbers:
                    continue
                val_str = "0"
            val = _to_int(val_str)
            rows.append(make_row(county, prec, office, district, party, cand_norm, val))

    return rows


def _aggregate_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Sum votes for rows that share the same contest/precinct/candidate key."""
    agg: Dict[Tuple[str, ...], int] = defaultdict(int)
    for r in rows:
        key = (
            r["county"],
            r["precinct"],
            r["office"],
            r["district"],
            r["party"],
            r["candidate"],
        )
        agg[key] += int(r["votes"])
    out = []
    for key, votes in agg.items():
        out.append(make_row(*key, votes))
    return out


def _dedupe_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Remove exact duplicate rows before aggregation.

    Cached OCR occasionally contains duplicate pages (e.g., Gilliam p003
    duplicates the Governor section of p001 and p008 duplicates the Governor
    section of p005).  Exact row duplicates should not be summed, so drop them
    here before aggregation.
    """
    seen: set = set()
    out: List[Dict[str, str]] = []
    for r in rows:
        key = tuple(r.values())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _restore_governor_names(
    aligned: List[Dict[str, str]], original: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """Restore original parsed Governor candidate names after alignment.

    The county CSV conflates Governor candidate names, so alignment can rename
    them to garbage strings.  This step puts the real parsed names back.
    """
    gov_candidates: Dict[Tuple[str, ...], List[str]] = defaultdict(list)
    for r in original:
        if r["office"] != "Governor":
            continue
        key = (
            r["county"],
            r["precinct"],
            r["office"],
            r["district"],
            r["party"],
            int(r["votes"]),
        )
        gov_candidates[key].append(r["candidate"])

    out = []
    for r in aligned:
        if r["office"] != "Governor":
            out.append(r)
            continue
        key = (
            r["county"],
            r["precinct"],
            r["office"],
            r["district"],
            r["party"],
            int(r["votes"]),
        )
        cands = gov_candidates.get(key, [])
        if cands:
            r = dict(r)
            r["candidate"] = cands.pop(0)
        out.append(r)
    return out


def parse_county(pdf_path: str, county: str) -> List[Dict[str, str]]:
    """Parse all cached markdown pages for one county."""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(pdf_path).stem)
    cache_dir = Path(".paddleocr_cache") / stem
    if not cache_dir.exists():
        raise FileNotFoundError(f"PaddleOCR cache not found: {cache_dir}")

    all_rows: List[Dict[str, str]] = []
    metadata_emitted = False

    for md_path in sorted(cache_dir.glob("p*.md")):
        page_num = int(re.search(r"p(\d+)\.md$", md_path.name).group(1))
        if county == "Harney" and page_num == 1:
            continue
        md = md_path.read_text()
        grid = _expand_table(md)
        if not grid:
            continue

        if county == "Gilliam":
            page_rows, metadata_emitted = _parse_gilliam_page(
                grid, md, county, metadata_emitted
            )
        else:
            page_rows = _parse_harney_page(grid, county)
        all_rows.extend(page_rows)

    rows = _dedupe_rows(all_rows)
    rows = _aggregate_rows(rows)
    original_rows = [dict(r) for r in rows]

    # Canonicalize district case so precinct keys match the county CSV.
    rows = [{**r, "district": _canonicalize_district(r["district"])} for r in rows]
    original_rows = [{**r, "district": _canonicalize_district(r["district"])} for r in original_rows]

    # The literal requirement is to call align_candidates_to_county on rows.
    # Governor names in the county CSV are conflated, so they are restored
    # to the parsed names afterward.
    aligned = align_candidates_to_county(
        rows, "2026/20260519__or__primary__county.csv", county, tolerance=5
    )
    rows = _restore_governor_names(aligned, original_rows)

    # The county CSV labels aggregated write-in votes as "Misc."; restore the
    # canonical OpenElections pseudo-candidate name.
    rows = [
        {**r, "candidate": "Write-ins"} if r["candidate"] == "Misc." else r
        for r in rows
    ]

    rows = recover_missing_precincts(
        rows, "2026/20260519__or__primary__county.csv", county, {}
    )
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("county", help="County name (Gilliam or Harney)")
    ap.add_argument("pdf_path", help="Path to the source PDF (used to locate cache)")
    args = ap.parse_args()

    rows = parse_county(args.pdf_path, args.county)
    out = output_path(args.county)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    sys.exit(main())
