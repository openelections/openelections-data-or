#!/usr/bin/env python3
"""Build 2026 Oregon primary precinct-level CSVs for Benton, Polk, and Tillamook."""

import csv
import html
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
    align_candidates_to_county,
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

GOV_REF: Dict[str, Dict[str, Dict[str, int]]] = {
    "Benton": {
        "D": {
            "Forest (Fora) Alexander": 349,
            "James Atkinson IV": 111,
            "Cal Kishawi": 49,
            "Tina Kotek": 12135,
            "Donnie M Beckwith": 18,
            "David W Beem": 73,
            "Steve William Laible": 86,
            "Brittany Jones": 303,
            "Tristan Sheppard": 164,
            "Miranda Weigler": 284,
            "Write-ins": 221,
        },
        "R": {
            "Danielle Bethell": 146,
            "Hope A Dalrymple": 7,
            "Ed Diehl": 2435,
            "Christine Drazan": 3072,
            "Chris Dudley": 1050,
            "Kyle M Duyck": 33,
            "David Medina": 235,
            "Robert Neuman": 15,
            "Brad T Peters": 42,
            "Paul J Romero Jr": 27,
            "Wen Waddell": 3,
            "Martin Ward": 8,
            "Tim O Youker": 10,
            "DeAngelo Leroy Turner": 6,
            "Write-ins": 42,
        },
    },
    "Polk": {
        "D": {
            "Forest (Fora) Alexander": 199,
            "James Atkinson IV": 133,
            "Cal Kishawi": 48,
            "Tina Kotek": 7087,
            "Donnie M Beckwith": 31,
            "David W Beem": 101,
            "Steve William Laible": 104,
            "Brittany Jones": 318,
            "Tristan Sheppard": 129,
            "Miranda Weigler": 202,
            "Write-ins": 365,
        },
        "R": {
            "Danielle Bethell": 350,
            "Hope A Dalrymple": 11,
            "Ed Diehl": 4775,
            "Christine Drazan": 4509,
            "Chris Dudley": 1667,
            "Kyle M Duyck": 28,
            "David Medina": 561,
            "Robert Neuman": 12,
            "Brad T Peters": 51,
            "Paul J Romero Jr": 41,
            "Wen Waddell": 6,
            "Martin Ward": 25,
            "Tim O Youker": 8,
            "DeAngelo Leroy Turner": 12,
            "Write-ins": 42,
        },
    },
    "Tillamook": {
        "D": {
            "Forest (Fora) Alexander": 58,
            "James Atkinson IV": 46,
            "Cal Kishawi": 19,
            "Tina Kotek": 2873,
            "Donnie M Beckwith": 25,
            "David W Beem": 53,
            "Steve William Laible": 27,
            "Brittany Jones": 108,
            "Tristan Sheppard": 49,
            "Miranda Weigler": 86,
            "Write-ins": 133,
        },
        "R": {
            "Danielle Bethell": 65,
            "Hope A Dalrymple": 2,
            "Ed Diehl": 1466,
            "Christine Drazan": 1525,
            "Chris Dudley": 670,
            "Kyle M Duyck": 18,
            "David Medina": 157,
            "Robert Neuman": 10,
            "Brad T Peters": 15,
            "Paul J Romero Jr": 11,
            "Wen Waddell": 6,
            "Martin Ward": 1,
            "Tim O Youker": 2,
            "DeAngelo Leroy Turner": 2,
            "Write-ins": 16,
        },
    },
}


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def expand_table(html: str) -> List[List[str]]:
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
    if not text or text.lower() in {"totals", "total", "registered voters - total"}:
        return False
    return bool(
        re.fullmatch(
            r"[0-9]{1,4}|[0-9]{1,4}[A-Z]?|[A-Z]{1,4}-?\d+|[A-Z]{2,4}",
            text.strip(),
        )
    )


def is_numeric_or_empty(text: str) -> bool:
    if not text:
        return True
    return bool(re.fullmatch(r"[0-9,]+|\d{1,3}(?:\.\d+)?%", text.strip()))


def parse_vote(text: str) -> Optional[int]:
    if not text:
        return None
    text = text.replace(",", "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def classify_columns(headers: List[str]) -> Tuple[List[Tuple[int, str]], Dict[str, List[int]]]:
    cands: List[Tuple[int, str]] = []
    summary: Dict[str, List[int]] = defaultdict(list)
    for idx, h in enumerate(headers):
        key = normalize_key(h)
        matched = False
        for kw in SUMMARY_KEYWORDS:
            nkw = normalize_key(kw)
            if nkw in key or key in nkw or nkw == key:
                summary[kw].append(idx)
                matched = True
                break
        if not matched:
            cands.append((idx, h))
    return cands, dict(summary)


def parse_title_segments(grid: List[List[str]]) -> List[Tuple[str, int, int]]:
    if not grid:
        return []
    title_row = grid[0]
    segments = []
    current_text = None
    start = 0
    for i, cell in enumerate(title_row):
        if cell and cell != current_text:
            if current_text is not None and current_text.strip():
                segments.append((current_text, start, i))
            current_text = cell
            start = i
    if current_text and current_text.strip():
        segments.append((current_text, start, len(title_row)))
    if segments and not segments[0][0].strip():
        segments = segments[1:]
    if not segments:
        joined = " ".join(c for c in title_row if c).strip()
        return [(joined, 0, len(title_row))]
    return segments


def extract_row_layout_contests(grid: List[List[str]]) -> List[Dict]:
    if not grid or len(grid) < 2:
        return []

    first_data_idx = None
    for i, row in enumerate(grid):
        if not row:
            continue
        first = row[0].strip()
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
    full_width = len(grid[0])
    for title, start, end in title_segments:
        if start == 0 and end == full_width:
            header_cols = grid[header_idx][1:]
            cand_info, summary = classify_columns(header_cols)
            cand_names = [(idx + 1, name) for idx, name in cand_info]
            summary_indices = {k: [v + 1 for v in vals] for k, vals in summary.items()}
        else:
            header_slice = grid[header_idx][start:end]
            while header_slice and not header_slice[0].strip():
                header_slice = header_slice[1:]
                start += 1
            if not header_slice:
                continue
            cand_info, summary = classify_columns(header_slice)
            cand_names = [(start + idx, name) for idx, name in cand_info]
            summary_indices = {k: [start + v for v in vals] for k, vals in summary.items()}

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

        office = normalize_office(title)
        contests.append(
            {
                "title": title,
                "office": office if office else title,
                "district": parse_district(title),
                "party": normalize_party(title),
                "cand_names": cand_names,
                "summary_indices": summary_indices,
                "data_rows": data_rows,
            }
        )
    return contests


def rows_from_row_layout(contests: List[Dict], county: str) -> List[Dict]:
    out: List[Dict] = []
    for c in contests:
        office = c["office"]
        district = c["district"]
        party = c["party"]

        for col_idx, candidate in c["cand_names"]:
            for prec, row in c["data_rows"]:
                if col_idx >= len(row):
                    continue
                votes = parse_vote(row[col_idx])
                if votes is None:
                    continue
                out.append(
                    make_row(
                        county=county,
                        precinct=prec,
                        office=office,
                        district=district,
                        party=party,
                        candidate=candidate,
                        votes=votes,
                    )
                )

        for kw, indices in c["summary_indices"].items():
            if "write-in" in kw:
                candidate = "Write-ins"
            elif kw == "overvotes":
                candidate = "Over Votes"
            elif kw == "undervotes":
                candidate = "Under Votes"
            else:
                continue
            for prec, row in c["data_rows"]:
                total = 0
                for idx in indices:
                    if idx >= len(row):
                        continue
                    v = parse_vote(row[idx])
                    if v is not None:
                        total += v
                out.append(
                    make_row(
                        county=county,
                        precinct=prec,
                        office=office,
                        district=district,
                        party=party,
                        candidate=candidate,
                        votes=total,
                    )
                )
    return out


def is_stats_table(grid: List[List[str]]) -> bool:
    if not grid:
        return False
    title_row = " ".join(grid[0]).lower()
    if "statistics" in title_row or "registered voters - total" in title_row:
        return True
    # Title may live outside the table; look at the first-column labels instead.
    for row in grid[1:]:
        if not row:
            continue
        label = row[0].lower()
        if "registered voters - total" in label or "ballots cast - total" in label:
            return True
    return False


def parse_row_layout_stats(grid: List[List[str]], county: str) -> List[Dict]:
    """Parse Benton/Polk style statistics table."""
    if not grid or len(grid) < 2:
        return []

    # Find first data row.
    first_data_idx = None
    for i, row in enumerate(grid):
        if not row:
            continue
        first = row[0].strip()
        if is_precinct_token(first) and all(is_numeric_or_empty(c) for c in row[1:]):
            first_data_idx = i
            break
    if first_data_idx is None or first_data_idx == 0:
        return []
    header_idx = first_data_idx - 1
    headers = [normalize_key(h) for h in grid[header_idx]]

    # Locate columns by keyword.
    def find_col(*keywords: str) -> Optional[int]:
        for idx, h in enumerate(headers):
            if any(kw in h for kw in keywords):
                return idx
        return None

    reg_idx = find_col("voters total", "voters - total")
    cast_idx = find_col("ballots cast total", "ballots cast - total")
    blank_idx = find_col("ballots cast blank", "ballots cast - blank")

    out: List[Dict] = []
    for row in grid[first_data_idx:]:
        if not row:
            continue
        prec = row[0].strip()
        if prec.lower() in {"totals", "total"} or not is_precinct_token(prec):
            continue

        if reg_idx is not None and reg_idx < len(row):
            v = parse_vote(row[reg_idx])
            if v is not None:
                out.append(
                    make_row(county, prec, "Registered Voters", "", "", "Registered Voters", v)
                )
        if cast_idx is not None and cast_idx < len(row):
            v = parse_vote(row[cast_idx])
            if v is not None:
                out.append(
                    make_row(county, prec, "Ballots Cast", "", "", "Ballots Cast", v)
                )
        if blank_idx is not None and blank_idx < len(row):
            v = parse_vote(row[blank_idx])
            if v is not None:
                out.append(
                    make_row(county, prec, "Ballots Cast Blank", "", "", "Ballots Cast Blank", v)
                )
    return out


def skip_table(title: str) -> bool:
    t = title.lower()
    skip_phrases = [
        "county polk",
        "totals (cont.)",
        "contact kim williams",
        "recount",
        "precinct or batch",
        "candidate name or measure response",
        "sel 797",
    ]
    return any(p in t for p in skip_phrases)


def parse_row_layout_county(county: str, cache_dir: Path) -> List[Dict]:
    rows: List[Dict] = []
    for page_path in sorted(cache_dir.glob("p*.md")):
        page_text = page_path.read_text()
        tables = re.split(r"(?=<table)", page_text)
        for html in tables:
            if "<table" not in html:
                continue
            grid = expand_table(html)
            if not grid:
                continue
            title_segments = parse_title_segments(grid)
            title = " ".join(seg[0] for seg in title_segments)
            if skip_table(title):
                continue
            if is_stats_table(grid):
                rows.extend(parse_row_layout_stats(grid, county))
                continue
            contests = extract_row_layout_contests(grid)
            rows.extend(rows_from_row_layout(contests, county))
    return rows


def extract_tillamook_precinct(page_text: str) -> Optional[str]:
    m = re.search(r"^##\s+(.+)$", page_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def _is_contest_heading(line: str) -> bool:
    """Return True if *line* names an office, measure, or local contest."""
    if normalize_office(line):
        return True
    if re.search(r"\bmeasure\b", line, re.IGNORECASE):
        return True
    if re.search(r"\b(vote for \d+|district attorney|judge of|county commissioner|"
                 r"county assessor|justice of the peace|fire district|rfpd|"
                 r"water district|school district|library district|city of)",
                 line, re.IGNORECASE):
        return True
    return False


def tillamook_table_title(preceding_text: str) -> str:
    """Return the contest title that precedes a table.

    The cached markdown alternates between contest headings, an optional
    ``Vote For N`` line, and the candidate table.  We strip HTML tags,
    discard trailing ``Vote For N`` tokens and precinct headings, and use the
    last remaining meaningful line as the title.
    """
    # Collapse HTML tags and normalize whitespace.
    text = re.sub(r"<[^>]+>", " ", preceding_text)
    text = html.unescape(text)
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Drop trailing "Vote For N" lines and standalone precinct headings.
    while lines:
        last = lines[-1]
        if re.fullmatch(r"Vote\s+For\s+\d+", last, re.IGNORECASE):
            lines.pop()
            continue
        # Precinct headings look like "## BAY" and are not contest titles.
        if re.fullmatch(r"##?\s*[A-Z][A-Za-z0-9\s&'-]*", last) and not _is_contest_heading(last):
            lines.pop()
            continue
        # Lines that are only the word "STATISTICS" are not contest titles.
        if re.fullmatch(r"STATISTICS", last, re.IGNORECASE):
            lines.pop()
            continue
        break

    # If the final line still ends with "Vote For N", strip that suffix.
    if lines:
        last = lines[-1]
        m = re.search(r"\s*\(?Vote\s+For\s+\d+\)?\s*$", last, re.IGNORECASE)
        if m:
            last = last[:m.start()].strip(" -,:")
            if last:
                lines[-1] = last

    if not lines:
        return ""
    title = re.sub(r"^##?\s*", "", lines[-1]).strip(" -,:")
    # Collapse duplicated consecutive words/fragments that OCR sometimes repeats.
    title = re.sub(r"\b(\w+(?:\s+\w+){1,4})\s+\1\b", r"\1", title, flags=re.IGNORECASE)
    return title


def parse_tillamook_table(grid: List[List[str]], county: str, precinct: str, title: str) -> List[Dict]:
    if not grid or len(grid) < 2:
        return []

    office = normalize_office(title)
    if office is None:
        office = title
    district = parse_district(title)
    party = normalize_party(title)

    out: List[Dict] = []
    for row in grid[1:]:
        if not row or len(row) < 2:
            continue
        candidate = row[0].strip()
        if not candidate:
            continue
        votes = parse_vote(row[1])
        if votes is None:
            continue

        key = normalize_key(candidate)
        if key in {"total votes cast", "contest totals", "contest total"}:
            continue
        if "write-in" in key:
            candidate = "Write-ins"
        elif key == "overvotes":
            candidate = "Over Votes"
        elif key == "undervotes":
            candidate = "Under Votes"
        elif key == "yes":
            candidate = "Yes"
        elif key == "no":
            candidate = "No"

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


def parse_tillamook_stats(grid: List[List[str]], county: str, precinct: str) -> List[Dict]:
    out: List[Dict] = []
    for row in grid[1:]:
        if not row or len(row) < 2:
            continue
        label = normalize_key(row[0])
        votes = parse_vote(row[1])
        if votes is None:
            continue
        if label.startswith("registered voters total"):
            out.append(make_row(county, precinct, "Registered Voters", "", "", "Registered Voters", votes))
        elif label.startswith("ballots cast total"):
            out.append(make_row(county, precinct, "Ballots Cast", "", "", "Ballots Cast", votes))
        elif label.startswith("ballots cast blank"):
            out.append(make_row(county, precinct, "Ballots Cast Blank", "", "", "Ballots Cast Blank", votes))
    return out


def parse_tillamook_county(county: str, cache_dir: Path) -> List[Dict]:
    rows: List[Dict] = []
    for page_path in sorted(cache_dir.glob("p*.md")):
        page_text = page_path.read_text()
        precinct = extract_tillamook_precinct(page_text)
        if not precinct:
            continue

        # Split page at table boundaries.  PaddleOCR sometimes puts the heading
        # for the next table at the tail of the previous table segment, so we
        # carry that tail forward as the "preceding" context for the next table.
        parts = re.split(r"(?=<table)", page_text)
        preceding = ""
        for part in parts:
            if "<table" not in part:
                preceding = part
                continue

            table_start = part.lower().find("<table")
            prefix = part[:table_start] if table_start >= 0 else ""
            title = tillamook_table_title(preceding + prefix)

            grid = expand_table(part)
            if not grid:
                # Still keep any trailing text after a failed table for context.
                table_end = part.lower().rfind("</table>")
                if table_end >= 0:
                    preceding = part[table_end + len("</table>") :]
                else:
                    preceding = ""
                continue

            if skip_table(title):
                table_end = part.lower().rfind("</table>")
                if table_end >= 0:
                    preceding = part[table_end + len("</table>") :]
                else:
                    preceding = ""
                continue

            if is_stats_table(grid):
                rows.extend(parse_tillamook_stats(grid, county, precinct))
            else:
                rows.extend(parse_tillamook_table(grid, county, precinct, title))

            table_end = part.lower().rfind("</table>")
            if table_end >= 0:
                preceding = part[table_end + len("</table>") :]
            else:
                preceding = ""
    return rows


def all_partitions(n: int, k: int) -> Iterable[Tuple[int, ...]]:
    if k == 1:
        yield (n,)
        return
    if k > n or k < 1:
        return
    for first in range(1, n - k + 2):
        for rest in all_partitions(n - first, k - 1):
            yield (first,) + rest


def name_token_overlap(raw_names: List[str], ref_name: str) -> int:
    ref_tokens = set(normalize_key(ref_name).split())
    joined = normalize_key(" ".join(raw_names))
    raw_tokens = set(joined.split())
    return len(ref_tokens & raw_tokens)


def match_governor_candidates(rows: List[Dict], county: str, tolerance: int = 5) -> List[Dict]:
    """Map parsed Governor rows to canonical official names using vote totals."""
    gov_ref = GOV_REF.get(county, {})
    if not gov_ref:
        return rows

    pseudo = {"write ins", "over votes", "under votes", "misc"}
    by_party: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        if row["office"].strip() != "Governor":
            continue
        by_party[row["party"].strip() or ""].append(row)

    rename_map: Dict[Tuple[str, str, str], str] = {}

    for party, party_rows in by_party.items():
        official = gov_ref.get(party)
        if not official:
            continue
        official_candidates = {c: t for c, t in official.items() if c != "Write-ins"}
        if not official_candidates:
            continue

        # Compute raw candidate totals.
        totals: Dict[str, int] = defaultdict(int)
        for row in party_rows:
            cand = row["candidate"].strip()
            if normalize_key(cand) in pseudo:
                continue
            totals[cand] += int(row["votes"])
        raw_list = list(totals.items())
        k = len(official_candidates)
        n = len(raw_list)
        if n < k:
            continue

        best_score = -1
        best_assignment: List[Tuple[List[str], str]] = []
        official_sorted = sorted(official_candidates.items(), key=lambda x: x[1])
        official_totals = [t for _, t in official_sorted]

        for parts in all_partitions(n, k):
            groups: List[Tuple[List[str], int]] = []
            idx = 0
            ok = True
            for size in parts:
                group_names = [raw_list[idx + i][0] for i in range(size)]
                group_total = sum(raw_list[idx + i][1] for i in range(size))
                groups.append((group_names, group_total))
                idx += size
            group_totals_sorted = sorted([t for _, t in groups])
            if any(abs(a - b) > tolerance for a, b in zip(group_totals_sorted, official_totals)):
                continue
            # Assign groups to official candidates by total, tie-break by name overlap.
            used = set()
            assignment = []
            score = 0
            for group_names, group_total in groups:
                candidates = [
                    (c, t) for c, t in official_candidates.items()
                    if abs(t - group_total) <= tolerance and c not in used
                ]
                if not candidates:
                    ok = False
                    break
                best = max(
                    candidates,
                    key=lambda ct: (name_token_overlap(group_names, ct[0]), -abs(ct[1] - group_total)),
                )
                used.add(best[0])
                assignment.append((group_names, best[0]))
                score += name_token_overlap(group_names, best[0])
            if not ok:
                continue
            if score > best_score:
                best_score = score
                best_assignment = assignment

        if not best_assignment:
            continue

        # Build rename map from raw candidate to official candidate.
        for group_names, official_name in best_assignment:
            for raw_name in group_names:
                rename_map[(party, raw_name)] = official_name

    out = []
    for row in rows:
        if row["office"].strip() != "Governor":
            out.append(row)
            continue
        party = row["party"].strip() or ""
        cand = row["candidate"].strip()
        key = (party, cand)
        if key in rename_map:
            new_row = dict(row)
            new_row["candidate"] = rename_map[key]
            out.append(new_row)
        else:
            out.append(row)
    return out


def rename_misc_to_writeins(rows: List[Dict]) -> List[Dict]:
    out = []
    for row in rows:
        if row["candidate"].strip().lower() == "misc.":
            new_row = dict(row)
            new_row["candidate"] = "Write-ins"
            out.append(new_row)
        else:
            out.append(row)
    return out


def dedupe_stats(rows: List[Dict]) -> List[Dict]:
    """Remove duplicate pseudo-office rows that may appear on multiple pages."""
    seen = set()
    out = []
    for row in rows:
        if row["office"] in {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}:
            key = (row["county"], row["precinct"], row["office"], row["candidate"])
            if key in seen:
                continue
            seen.add(key)
        out.append(row)
    return out


def build_county_csv(
    county: str,
    cache_dir: Path,
    county_csv_path: str = "2026/20260519__or__primary__county.csv",
) -> str:
    if county in {"Benton", "Polk"}:
        raw_rows = parse_row_layout_county(county, cache_dir)
    elif county == "Tillamook":
        raw_rows = parse_tillamook_county(county, cache_dir)
    else:
        raise ValueError(f"Unsupported county: {county}")

    # Governor mapping to canonical names.
    gov_mapped = match_governor_candidates(raw_rows, county)

    # Separate Governor from other offices for county-csv alignment.
    gov_rows = [r for r in gov_mapped if r["office"].strip() == "Governor"]
    other_rows = [r for r in gov_mapped if r["office"].strip() != "Governor"]

    aligned = align_candidates_to_county(other_rows, county_csv_path, county)
    aligned = rename_misc_to_writeins(aligned)

    stats_rows = [r for r in aligned if r["office"] in {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}]
    contest_rows = [r for r in aligned if r["office"] not in {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}]

    # Governor stats should not exist, but keep any pseudo rows.
    gov_stats = [r for r in gov_rows if r["office"] in {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}]
    gov_contest = [r for r in gov_rows if r["office"] not in {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}]

    all_rows = dedupe_stats(stats_rows + gov_stats) + contest_rows + gov_contest
    out_path = output_path(county, "2026/counties")
    write_csv(all_rows, out_path)
    return out_path


if __name__ == "__main__":
    base_cache = Path("/Users/dwillis/code/openelections-data-or/.paddleocr_cache")
    for county in ["Benton", "Polk", "Tillamook"]:
        path = build_county_csv(county, base_cache / county)
        print(f"Wrote {path}")
