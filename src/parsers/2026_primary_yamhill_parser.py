#!/usr/bin/env python3
"""Dedicated parser for Yamhill County 2026 primary precinct PDF.

Yamhill publishes a "Canvass Results Report" with the office title as plain
text above a single wide HTML table.  The table header is:

    Precinct, <candidates...>, Misc. Write-in (W), Cast Votes, Undervotes,
    Overvotes, Misc. Write-in, Vote By Mail Ballots Cast, Total Ballots Cast,
    Registered Voters, Turnout Percentage

Wide contests are split across two pages: a left page with candidate columns
(and some metadata) and a right page with the remaining metadata columns.
A few compact pages (e.g. State House 26 R on p025) drop the candidate labels
from the header entirely; those columns are reconstructed from the county CSV.

Candidate names in the Governor races are rotated/truncated by OCR into many
short fragments; the county CSV conflates those fragments into combined names,
so the Governor totals cannot be reconciled exactly.  Measure 120 uses Yes/No
in the PDF while the county CSV labels them Candidate 1 / Candidate 2.

Explainable mismatches documented in this file:
- Governor Democratic / Republican: the PDF's rotated column headers split each
candidate name into multiple fragments, while the county CSV conflates those
fragments into a single candidate name.  The parser emits the fragments as
separate candidates and lets align_candidates_to_county match by vote total,
so precinct sums for Governor do not match the county CSV's conflated names.
- State Senate 13 / 16 and State House 26 / 31: these contests appear in the
county CSV but the cached markdown only contains chart images (no extractable
table text), so no precinct rows are emitted and their county totals show as
missing in precincts.
- County Commissioner Position 3: the PDF lists Jason Fields, but the county
CSV only contains Neyssa Hays and David S Wall, so Jason Fields is reported as
a precinct sum missing from the county CSV.
- Local measures 36-239 and 36-240: they are present in the PDF but absent from
the county CSV, so their Yes/No/Over/Under rows are reported as missing from the
county CSV.
- Registered Voters, Ballots Cast, Over Votes and Under Votes are emitted as
pseudo-office/pseudo-candidate rows; the county CSV does not include them, so
verify_precinct_totals reports them as "precinct sums missing in county CSV".
- Some write-in totals (e.g. Governor D) are not listed separately in the
county CSV, so they are also reported as "precinct sums missing in county CSV".

Usage:
    uv run python src/parsers/2026_primary_yamhill_parser.py
"""

import csv
import glob
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import plain_text, table_rows
from precinct_2026_common import (
    COUNTY_FILENAME,
    ELECTION_DATE,
    align_candidates_to_county,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    parse_number,
    recover_missing_precincts,
    write_csv,
)

COUNTY_NAME = "Yamhill"
CACHE_DIR = ".paddleocr_cache/Yamhill"
COUNTY_CSV_PATH = "2026/20260519__or__primary__county.csv"

# Offices we care about; committee-person pages are skipped.
_OFFICE_TITLE_RE = re.compile(
    r"^(.*?)\s*-\s*(Democratic|Republican|Nonpartisan|.*Party)\s*-\s*\d+\s+Year\s+Term\s*-\s*Vote\s+for\s+(?:One|Two|Three|Four|Five|Six)\s*$",
    re.IGNORECASE,
)

# Fallback for Measure / local-option lines that do not end with "Term - Vote for N".
_SIMPLE_OFFICE_RE = re.compile(
    r"^(.*?)\s*-\s*(Democratic|Republican|Nonpartisan|.*Party)\s*$", re.IGNORECASE
)

# Recognized summary columns in the wide header.
META_LABELS = {
    "Cast Votes": None,
    "Undervotes": "Under Votes",
    "Under Votes": "Under Votes",
    "Overvotes": "Over Votes",
    "Over Votes": "Over Votes",
    "Misc. Write-in": None,  # duplicate of (W); ignore
    "Misc Write-in": None,
    "Vote By Mail Ballots Cast": None,
    "Total Ballots Cast": "Total Ballots Cast",
    "Registered Voters": "Registered Voters",
    "Turnout Percentage": None,
    "Ballots Cast Blank": "Ballots Cast Blank",
}


def _extract_office_title(md: str) -> Optional[str]:
    """Return the office title line from the plain text above the data table."""
    text = plain_text(md)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip metadata fragments and certification text.
        if line.lower().startswith("canvass"):
            continue
        if "certify" in line.lower() or "clerk" in line.lower():
            continue
        if _OFFICE_TITLE_RE.match(line) or _SIMPLE_OFFICE_RE.match(line):
            return line
    return None


def _parse_office_title(line: str) -> Tuple[Optional[str], str, str]:
    """Parse an office title into (office, district, party)."""
    # Remove Vote-for / term suffixes.
    line = re.sub(r"\s*-\s*\d+\s+Year\s+Term\s*-\s*Vote\s+for\s+\w+", "", line, flags=re.IGNORECASE)
    line = line.strip(" -")

    party = ""
    # Find the party segment near the end.
    for m in re.finditer(r"-\s*([^-]+?)(?:\s+Party)?\s*$", line, flags=re.IGNORECASE):
        candidate = m.group(1)
        party = normalize_party(candidate)
        if party or "nonpartisan" in candidate.lower():
            line = line[: m.start()].strip(" -")
            if not party:
                party = ""
            break

    office = normalize_office(line)
    if office is None:
        office = line.rstrip(" ,")

    district = parse_district(line)
    return office, district, party


def _extract_tables(md: str) -> List[List[List[str]]]:
    """Return tables as lists of rows, each row a list of cell texts."""
    tables: List[List[List[str]]] = []
    for table_html in re.findall(r"<table\b[^>]*>(.*?)</table>", md, flags=re.S | re.I):
        rows: List[List[str]] = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S | re.I):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                for c in cells
            ]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _normalize_header_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", label.lower())).strip()


def _load_county_candidates(path: str) -> Dict[Tuple[str, str, str], List[str]]:
    """Return candidate names per contest from the county-level CSV.

    The county CSV labels write-ins as ``Misc.``; we normalise that to
    ``Write-ins`` so it can be used as a PDF candidate column label.
    """
    out: Dict[Tuple[str, str, str], List[str]] = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["county"].strip() != COUNTY_NAME:
                continue
            key = (row["office"].strip(), _normalize_district(row["district"].strip()), row["party"].strip())
            cand = row["candidate"].strip()
            if _normalize_header_label(cand) == "misc":
                cand = "Write-ins"
            out.setdefault(key, []).append(cand)
    return out


def _normalize_district(district: str) -> str:
    """Collapse duplicated tokens like 'Position 1 Position 1'."""
    parts = district.split()
    seen: set = set()
    out: List[str] = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return " ".join(out)


def _parse_header(
    header: List[str],
    office: str,
    district: str,
    party: str,
    county_candidates: Dict[Tuple[str, str, str], List[str]],
    first_data_row: Optional[List[str]] = None,
) -> Tuple[List[Tuple[int, str]], Dict[str, int]]:
    """Classify header cells into candidate and metadata columns.

    Normal headers list candidate names followed by metadata labels.  Some
    compact continuation pages (e.g. p025) drop candidate labels and only keep
    ``Precinct`` and ``Turnout Percentage`` in the header row; for those we
    infer candidate names from the county CSV and the standard metadata order.
    """
    candidates: List[Tuple[int, str]] = []
    meta: Dict[str, int] = {}

    if not header or _normalize_header_label(header[0]) != "precinct":
        return candidates, meta

    # Candidate columns appear between "Precinct" and the first metadata marker.
    in_candidates = True
    for idx, cell in enumerate(header[1:], start=1):
        raw = cell.strip()
        norm = _normalize_header_label(raw)
        if not raw:
            continue

        # Candidate write-in column.
        if norm in ("misc write in w", "misc write-in (w)", "write in w", "write-in (w)"):
            candidates.append((idx, "Write-ins"))
            continue
        if norm in ("write in", "write-in"):
            candidates.append((idx, "Write-ins"))
            continue

        # Known metadata labels stop the candidate scan.
        matched_meta_output: Optional[str] = None
        is_meta_label = False
        for key, value in META_LABELS.items():
            key_norm = _normalize_header_label(key)
            if norm == key_norm or norm.startswith(key_norm):
                matched_meta_output = value
                is_meta_label = True
                break

        if is_meta_label:
            in_candidates = False
            if matched_meta_output:
                meta[matched_meta_output] = idx
            continue

        if in_candidates:
            if raw.lower() in ("yes", "no"):
                candidates.append((idx, raw))
            elif raw:
                # Strip elected/asterisk markers and other artifacts.
                candidates.append((idx, raw.lstrip("*").strip()))

    # Compact header: no candidate labels were found.  Reconstruct candidate
    # columns from the county CSV and the standard metadata order.
    if not candidates and first_data_row:
        contest_key = (office, district, party)
        cands = county_candidates.get(contest_key, [])

        # Find Turnout Percentage (the trailing percentage cell) and work back
        # through the standard metadata columns.
        turnout_idx = None
        for i, cell in enumerate(first_data_row):
            if re.fullmatch(r"\d{1,3}(?:\.\d+)?%", cell.strip()):
                turnout_idx = i
                break

        if turnout_idx is not None:
            standard_meta = [
                ("Turnout Percentage", None),
                ("Registered Voters", "Registered Voters"),
                ("Total Ballots Cast", "Total Ballots Cast"),
                ("Vote By Mail Ballots Cast", None),
                ("Misc. Write-in", None),
                ("Over Votes", "Over Votes"),
                ("Under Votes", "Under Votes"),
                ("Cast Votes", None),
            ]
            for offset, (label, output_name) in enumerate(standard_meta):
                idx = turnout_idx - offset
                if idx <= 0:
                    break
                if output_name:
                    meta[output_name] = idx
            first_meta_idx = turnout_idx - len(standard_meta) + 1
            if first_meta_idx < 1:
                first_meta_idx = 1
            # Candidate columns sit between Precinct (0) and the first metadata column.
            candidate_count = min(len(cands), max(0, first_meta_idx - 1))
            for offset in range(candidate_count):
                candidates.append((1 + offset, cands[offset]))

    return candidates, meta


def _is_data_row(row: List[str], candidate_cols: List[Tuple[int, str]]) -> bool:
    """Return True if the row looks like a per-precinct data row."""
    if not row:
        return False
    first = row[0].strip()
    if not first or first.lower() in ("totals", "total", "precinct"):
        return False
    # At least one candidate cell must be numeric.
    for idx, _ in candidate_cols:
        if idx < len(row) and re.fullmatch(r"[\d,]+", row[idx].strip()):
            return True
    return False


def _looks_like_precinct(text: str) -> bool:
    t = text.strip()
    if not t or t.lower() in ("totals", "total", "precinct"):
        return False
    # Accept strings like "01", "09/11/21", "1A", etc.
    return bool(re.search(r"\d", t))


def _merge_candidate_aliases(
    rows: List[Dict[str, str]],
    county_candidates: Dict[Tuple[str, str, str], List[str]],
) -> List[Dict[str, str]]:
    """Merge parsed candidate names that are the same up to punctuation/spacing.

    Some OCR headers drop periods from initials (e.g. ``David W Beem`` vs the
    county CSV's ``David W. Beem``).  Grouping by a normalized key and summing
    votes prevents the same person from appearing as two candidates.
    """
    county_sets: Dict[Tuple[str, str, str], set] = {
        k: set(cands) for k, cands in county_candidates.items()
    }

    # Choose a canonical name for each normalized-key group.  Prefer a name
    # that already appears in the county CSV for that contest; seed the map
    # with those names so punctuation-only OCR variants get renamed to the
    # county form before missing-precinct recovery runs.
    canonical: Dict[Tuple[str, str, str, str], str] = {}
    for key, cands in county_candidates.items():
        for cand in cands:
            if cand in {"", "Under Votes", "Over Votes", "Write-ins"}:
                continue
            norm = _normalize_header_label(cand)
            group_key = (key[0], key[1], key[2], norm)
            canonical[group_key] = cand

    for row in rows:
        cand = row["candidate"]
        if cand in {"", "Under Votes", "Over Votes", "Write-ins"}:
            continue
        key = (row["office"], row["district"], row["party"])
        norm = _normalize_header_label(cand)
        group_key = (key[0], key[1], key[2], norm)
        existing = canonical.get(group_key)
        if existing is None:
            canonical[group_key] = cand
        else:
            cset = county_sets.get(key, set())
            if cand in cset and existing not in cset:
                canonical[group_key] = cand

    totals: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in rows:
        cand = row["candidate"]
        key = (row["office"], row["district"], row["party"])
        if cand in {"", "Under Votes", "Over Votes", "Write-ins"}:
            totals[(row["county"], row["precinct"], row["office"], row["district"], row["party"], cand)] += int(row["votes"])
            continue
        norm = _normalize_header_label(cand)
        group_key = (key[0], key[1], key[2], norm)
        canonical_cand = canonical.get(group_key, cand)
        totals[(row["county"], row["precinct"], row["office"], row["district"], row["party"], canonical_cand)] += int(row["votes"])

    out: List[Dict[str, str]] = []
    for (county, precinct, office, district, party, cand), votes in totals.items():
        out.append(
            make_row(
                county=county,
                precinct=precinct,
                office=office,
                district=district,
                party=party,
                candidate=cand,
                votes=votes,
            )
        )
    return out


def parse_yamhill(cache_dir: str = CACHE_DIR) -> Tuple[List[Dict[str, str]], Dict[Tuple[str, str, str], str]]:
    """Parse all cached Yamhill markdown files into precinct rows."""
    rows: List[Dict[str, str]] = []
    registered_voters: Dict[str, int] = {}
    ballots_cast: Dict[str, int] = {}
    ballots_cast_blank: Dict[str, int] = {}
    seen_contest_precincts: Dict[Tuple[str, str, str], set] = defaultdict(set)
    all_precincts: set = set()

    county_candidates = _load_county_candidates(COUNTY_CSV_PATH)

    md_paths = sorted(glob.glob(os.path.join(cache_dir, "p*.md")))

    for md_path in md_paths:
        md = Path(md_path).read_text()

        title = _extract_office_title(md)
        if not title:
            continue
        office, district, party = _parse_office_title(title)
        if not office:
            continue

        # Skip precinct committee-person contests (not in county CSV).
        if "Committee Person" in title or ("Committee" in title.lower() and "Person" in title.lower()):
            continue

        tables = _extract_tables(md)
        # Skip pages that only contain the metadata table.
        if not tables or len(tables) == 1:
            continue

        for table in tables[1:]:  # first table is always the report metadata
            if not table:
                continue

            header_idx = None
            for i, row in enumerate(table):
                if row and _normalize_header_label(row[0]) == "precinct":
                    header_idx = i
                    break
            if header_idx is None:
                continue

            header = table[header_idx]
            first_data_row = None
            for row in table[header_idx + 1 :]:
                if row and row[0].strip() and _looks_like_precinct(row[0]):
                    first_data_row = row
                    break

            candidate_cols, meta_cols = _parse_header(
                header, office, district, party, county_candidates, first_data_row
            )

            # Some tables (e.g. Governor R on p017) are so wide that the
            # right-hand metadata columns are dropped.  That is fine; we still
            # capture them from other pages/tables.
            for row in table[header_idx + 1 :]:
                if not row or not row[0].strip():
                    continue
                precinct = row[0].strip()
                if not _looks_like_precinct(precinct):
                    continue
                if precinct.lower() in ("totals", "total"):
                    continue

                all_precincts.add(precinct)
                seen_contest_precincts[(office, district, party)].add(precinct)

                # Candidate / pseudo-candidate vote columns.
                for idx, label in candidate_cols:
                    if idx >= len(row):
                        continue
                    votes_text = row[idx].strip().replace(",", "")
                    if not re.fullmatch(r"\d+", votes_text):
                        continue
                    rows.append(
                        make_row(
                            county=COUNTY_NAME,
                            precinct=precinct,
                            office=office,
                            district=district,
                            party=party,
                            candidate=label,
                            votes=parse_number(votes_text),
                        )
                    )

                # Metadata columns.
                for meta_name, idx in meta_cols.items():
                    if idx >= len(row):
                        continue
                    votes_text = row[idx].strip().replace(",", "")
                    if not re.fullmatch(r"\d+", votes_text):
                        continue
                    votes = parse_number(votes_text)
                    if meta_name == "Registered Voters":
                        registered_voters[precinct] = max(registered_voters.get(precinct, 0), votes)
                    elif meta_name == "Total Ballots Cast":
                        ballots_cast[precinct] = max(ballots_cast.get(precinct, 0), votes)
                    elif meta_name == "Ballots Cast Blank":
                        ballots_cast_blank[precinct] = max(ballots_cast_blank.get(precinct, 0), votes)
                    elif meta_name in ("Under Votes", "Over Votes"):
                        rows.append(
                            make_row(
                                county=COUNTY_NAME,
                                precinct=precinct,
                                office=office,
                                district=district,
                                party=party,
                                candidate=meta_name,
                                votes=votes,
                            )
                        )

    # Emit county-wide pseudo-office rows once per precinct.
    for precinct in sorted(all_precincts):
        if precinct in registered_voters:
            rows.append(
                make_row(
                    county=COUNTY_NAME,
                    precinct=precinct,
                    office="Registered Voters",
                    district="",
                    party="",
                    candidate="",
                    votes=registered_voters[precinct],
                )
            )
        if precinct in ballots_cast:
            rows.append(
                make_row(
                    county=COUNTY_NAME,
                    precinct=precinct,
                    office="Ballots Cast",
                    district="",
                    party="",
                    candidate="",
                    votes=ballots_cast[precinct],
                )
            )
        if precinct in ballots_cast_blank:
            rows.append(
                make_row(
                    county=COUNTY_NAME,
                    precinct=precinct,
                    office="Ballots Cast Blank",
                    district="",
                    party="",
                    candidate="",
                    votes=ballots_cast_blank[precinct],
                )
            )

    # Detect missing precinct rows per contest.  A precinct is considered missing
    # from a contest if it has registered voters county-wide but no candidate
    # rows for that contest.  This relies on the PDF including all contest votes
    # in the displayed rows (possibly combined as e.g. "09/11/21"), so it mainly
    # catches continuation pages that only supplied metadata.
    missing_precincts: Dict[Tuple[str, str, str], str] = {}
    for contest_key, precincts in seen_contest_precincts.items():
        for precinct in all_precincts:
            if precinct not in precincts:
                # Only flag if we have not already recorded another missing one.
                missing_precincts.setdefault(contest_key, precinct)

    return rows, missing_precincts


def main():
    rows, missing_precincts = parse_yamhill()
    rows = align_candidates_to_county(rows, COUNTY_CSV_PATH, COUNTY_NAME, tolerance=5)
    # Merge punctuation-only variants (e.g. ``David W Beem`` / ``David W. Beem``)
    # before recovering missing precincts so the recovered difference is accurate.
    rows = _merge_candidate_aliases(rows, _load_county_candidates(COUNTY_CSV_PATH))
    if missing_precincts:
        rows = recover_missing_precincts(rows, COUNTY_CSV_PATH, COUNTY_NAME, missing_precincts)

    out_path = (
        Path("2026/counties")
        / f"{ELECTION_DATE}__or__primary__{COUNTY_FILENAME[COUNTY_NAME]}__precinct.csv"
    )
    write_csv(rows, str(out_path))
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
