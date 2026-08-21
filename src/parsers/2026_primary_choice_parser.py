#!/usr/bin/env python3
"""Parser for ES&S "Statement of Votes Cast by Contests, Geography by Choice" PDFs.

Handles Lane and Klamath counties for the 2026 Oregon primary.  Produces a
precinct-level CSV with the standard schema:

    county,precinct,office,district,party,candidate,votes

Usage:
    uv run python src/parsers/2026_primary_choice_parser.py \
        Lane '/path/to/Lane.pdf'
    uv run python src/parsers/2026_primary_choice_parser.py \
        Klamath '/path/to/Klamath.pdf'

Known limitations / remaining issues:
- The 2026 county-level CSV (`2026/20260519__or__primary__county.csv`) was
  generated from the same PDFs and conflates the long candidate names in the
  Governor contests (e.g. it merges "Forest (Fora) Alexander" and
  "Steve William Laible" into one row).  This parser correctly splits those
  names into the individual candidates shown in the PDF, so
  `verify_precinct_totals.py` reports many mismatches for Governor contests.
  The precinct totals are internally consistent; the county CSV is the source
  of the mismatch.
- The county CSV records write-in/overage/underage votes as a single
  "Misc." candidate, while this parser emits separate "Write-ins",
  "Over Votes", and "Under Votes" rows as required by the OpenElections
  schema.  That produces additional explainable mismatches.
- Klamath reports per-contest "Ballots Cast" and "Reg. Voters" columns.
  These are emitted as pseudo-office rows once per precinct.  They are not
  present in the county-level CSV, so verify_precinct_totals reports them as
  "not in county".
- For Measure contests the precinct file uses "Yes" and "No", while the
  county-level CSV uses generic "Candidate 1" / "Candidate 2" labels.
  This produces explainable mismatches for all measure contests.
- Clackamas uses single-letter ballot-group codes (G, Y) instead of (DEM)/(REP);
  the parser maps G->D and Y->R.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTIES,
    align_candidates_to_county,
    format_candidate_name,
    is_number_token,
    is_percent_token,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    parse_number,
    write_csv,
)

VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)
HEADER_SKIP_RE = re.compile(
    r"Statement of Votes Cast|Page:|County.*Primary|Official Final Results|"
    r"Total Ballots Cast|precincts reported out of|All Precincts, All|"
    r"OFFICIAL RESULTS|Date Certified|County Clerk|ABSTRACT OF VOTES|"
    r"State Precinct Report|Total Precincts Reported|Total Registered Voters|"
    r"Total Voter Turnout|State of Oregon|Klamath County\) ss\.|RMLong",
    re.IGNORECASE,
)


def is_contest_header(line_text: str) -> bool:
    return VOTE_FOR_RE.search(line_text) is not None


# County-specific party-code mappings for ES&S reports that use single-letter
# ballot-group codes instead of (DEM)/(REP).  Clackamas uses (G)=Democrat,
# (Y)=Republican.
_COUNTY_PARTY_CODES = {
    "Clackamas": {"G": "D", "Y": "R"},
}


def parse_contest_header(text: str, county_name: str = "") -> Tuple[Optional[str], str, str]:
    m = VOTE_FOR_RE.search(text)
    if m:
        office_part = text[: m.start()].strip()
    else:
        office_part = text.strip()

    party = ""
    # Standard (DEM)/(REP) labels.
    pm = re.search(r"\((DEM|REP)\)", office_part, re.IGNORECASE)
    if pm:
        party = normalize_party(pm.group(1))
        office_part = office_part[: pm.start()] + office_part[pm.end() :]
        office_part = re.sub(r"\s+", " ", office_part).strip()
    else:
        # Single-letter codes (e.g. Clackamas (G)/(Y)).
        pm = re.search(r"\(([A-Z])\)", office_part, re.IGNORECASE)
        if pm:
            code = pm.group(1).upper()
            party = _COUNTY_PARTY_CODES.get(county_name, {}).get(code, "")
            office_part = office_part[: pm.start()] + office_part[pm.end() :]
            office_part = re.sub(r"\s+", " ", office_part).strip()

    office = normalize_office(office_part)
    district = parse_district(office_part)
    return office, district, party


def is_precinct_data_line(words: List[Tuple[float, float, str]]) -> bool:
    if len(words) < 3:
        return False
    if words[0][2] != "Precinct":
        return False
    # Header rows look like "Precinct Ballots Cast ..." or "Precinct Total ...".
    second = words[1][2]
    if second in ("Ballots", "Total", "Cast", "Votes"):
        return False
    # Require at least one integer token later in the line (not the precinct id).
    for w in words[2:]:
        if is_number_token(w[2]):
            return True
    return False


def precinct_name(words: List[Tuple[float, float, str]]) -> str:
    raw = words[1][2]
    if raw.isdigit():
        raw = raw.lstrip("0") or "0"
    return f"Precinct {raw}"


def column_centers_from_data_row(
    words: List[Tuple[float, float, str]], skip_first: int = 2
) -> List[float]:
    """Return x-centers of numeric (non-percent) tokens after 'Precinct NNN'."""
    centers = []
    for w in words[skip_first:]:
        if is_percent_token(w[2]):
            continue
        if is_number_token(w[2]):
            centers.append((w[0] + w[1]) / 2)
    return centers


def assign_to_columns(
    words: List[Tuple[float, float, str]], centers: List[float], max_dist: float = 45.0
) -> List[List[Tuple[float, float, str]]]:
    """Assign words to the nearest column center, preserving each word."""
    buckets: List[List[Tuple[float, float, str]]] = [[] for _ in centers]
    for w in words:
        c = (w[0] + w[1]) / 2
        best = min(range(len(centers)), key=lambda i: abs(centers[i] - c), default=None)
        if best is not None and abs(centers[best] - c) <= max_dist:
            buckets[best].append(w)
    return buckets


def label_from_bucket(bucket: List[Tuple[float, float, str]]) -> str:
    """Join header tokens into a column label, dropping generic filler."""
    tokens = [w[2] for w in bucket if w[2] not in ("Votes",)]
    return " ".join(tokens)


def classify_column(label: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (candidate_name, pseudo_office) for a column label.

    Write-in/Over/Under are reported as candidate names within the contest.
    Only Ballots Cast and Registered Voters are emitted as pseudo-office rows.
    Total/Contest Total columns are skipped.
    """
    if not label:
        return (None, None)
    lowered = label.lower().replace("-", " ")
    if "ballots cast" in lowered or lowered == "ballots cast blank":
        return (None, "Ballots Cast")
    if "registered" in lowered or "reg. voters" in lowered or "reg voters" in lowered:
        return (None, "Registered Voters")
    if lowered in ("total", "total votes", "contest total"):
        return (None, None)
    if "write" in lowered or "write-in" in label.lower():
        return ("Write-ins", None)
    if "over" in lowered:
        return ("Over Votes", None)
    if "under" in lowered:
        return ("Under Votes", None)
    if label == "Precinct":
        return (None, None)
    return (format_candidate_name(label.split()), None)


def parse_choice_pdf(pdf_path: str, county_name: str) -> List[Dict[str, str]]:
    # Vote totals keyed by full contest/candidate; used to dedupe rows that
    # appear in multiple sub-tables (e.g. separate Over/Under pages).
    vote_totals: Dict[Tuple[str, ...], int] = defaultdict(int)
    # Pseudo-office rows (Ballots Cast, Registered Voters) are emitted once
    # per precinct, not summed across contests.
    pseudo_rows: Dict[Tuple[str, str, str], int] = {}

    current_office: Optional[str] = None
    current_district = ""
    current_party = ""
    in_summary = False

    # Per-subtable state.
    pending_header_words: List[Tuple[float, float, str]] = []
    columns: List[Tuple[Optional[str], Optional[str]]] = []
    data_started = False
    centers: List[float] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines = _group_words_into_lines(words, y_tolerance=5.0)

            for line in lines:
                if not line:
                    continue
                line_text = " ".join(w[2] for w in line)

                # Page-level junk.
                if HEADER_SKIP_RE.search(line_text):
                    continue
                if line_text.strip().startswith("All Precincts") and not line_text.startswith(
                    "All Precincts, All"
                ):
                    in_summary = True
                    current_office = None
                    continue

                if is_contest_header(line_text):
                    current_office, current_district, current_party = parse_contest_header(
                        line_text, county_name
                    )
                    in_summary = False
                    pending_header_words = []
                    columns = []
                    data_started = False
                    centers = []
                    continue

                if in_summary or current_office is None:
                    continue

                # Contest-level summary line (e.g. "Total 55607 ...").
                if line_text.strip().startswith("Total") and len(line) > 1:
                    continue

                # Header / column-label lines appear before the first data row
                # of a subtable.  They either start with "Precinct" (the main
                # header), with "Votes" (the filler sub-header), or are orphan
                # lines containing candidate-name fragments like "Wells".
                if not data_started:
                    is_main_header = (
                        line[0][2] == "Precinct"
                        and len(line) > 1
                        and not is_number_token(line[1][2])
                    )
                    is_votes_line = line_text.strip().startswith("Votes")
                    is_orphan = (
                        not is_main_header
                        and not is_votes_line
                        and line[0][2] != "Precinct"
                        and not line_text.strip().startswith("Total")
                        and not is_number_token(line[0][2])
                    )
                    if is_main_header or is_votes_line or is_orphan:
                        start = 1 if is_main_header else 0
                        pending_header_words.extend(line[start:])
                        continue

                # Data row.
                if is_precinct_data_line(line):
                    if not data_started:
                        centers = column_centers_from_data_row(line, skip_first=2)
                        header_buckets = assign_to_columns(
                            pending_header_words, centers, max_dist=45.0
                        )
                        columns = []
                        for bucket in header_buckets:
                            label = label_from_bucket(bucket)
                            columns.append(classify_column(label))
                        data_started = True

                    prec = precinct_name(line)
                    data_buckets = assign_to_columns(line[2:], centers, max_dist=40.0)
                    for idx, bucket in enumerate(data_buckets):
                        # Pick the first integer token in the bucket; ignore
                        # percentage tokens and stray text.
                        votes = None
                        for w in bucket:
                            if is_percent_token(w[2]):
                                continue
                            if is_number_token(w[2]):
                                votes = parse_number(w[2])
                                break
                        if votes is None:
                            continue
                        cand, pseudo = columns[idx] if idx < len(columns) else (None, None)
                        if pseudo:
                            key = (county_name, prec, pseudo)
                            if key not in pseudo_rows:
                                pseudo_rows[key] = votes
                        elif cand:
                            key = (
                                county_name,
                                prec,
                                current_office,
                                current_district,
                                current_party,
                                cand,
                            )
                            vote_totals[key] += votes
                    continue

    rows: List[Dict[str, str]] = []
    for key, votes in vote_totals.items():
        county, prec, office, district, party, candidate = key
        rows.append(
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
    for key, votes in pseudo_rows.items():
        county, prec, pseudo = key
        rows.append(
            make_row(
                county=county,
                precinct=prec,
                office=pseudo,
                district="",
                party="",
                candidate="",
                votes=votes,
            )
        )

    return rows


def _group_words_into_lines(
    words: Iterable[Dict], y_tolerance: float = 3.0
) -> List[List[Tuple[float, float, str]]]:
    """Local copy of group_words_into_lines to keep imports minimal."""
    buckets: Dict[int, List[Tuple[float, float, str]]] = defaultdict(list)
    for w in words:
        key = round(w["top"] / y_tolerance)
        buckets[key].append((w["x0"], w["x1"], w["text"]))
    lines = []
    for key in sorted(buckets):
        lines.append(sorted(buckets[key], key=lambda t: t[0]))
    return lines


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: uv run python src/parsers/2026_primary_choice_parser.py "
            "COUNTY '/path/to/County.pdf'"
        )
        sys.exit(1)
def _restore_governor_names(
    aligned: List[Dict[str, str]], original: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """Restore original parsed Governor names after county-CSV alignment.

    The county CSV conflates/rotates Governor candidate names, so alignment
    can rename them to garbage strings.  Put the real parsed names back.
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


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: uv run python src/parsers/2026_primary_choice_parser.py "
            "COUNTY '/path/to/County.pdf'"
        )
        sys.exit(1)
    county_name = sys.argv[1]
    pdf_path = sys.argv[2]
    rows = parse_choice_pdf(pdf_path, county_name)
    # Drop columns where the candidate label is a stray "Total" summary column.
    rows = [r for r in rows if not r["candidate"].lower().startswith("total")]
    original_rows = [dict(r) for r in rows]
    rows = align_candidates_to_county(
        rows, "2026/20260519__or__primary__county.csv", county_name, tolerance=5
    )
    rows = _restore_governor_names(rows, original_rows)
    # The county CSV aggregates write-ins as "Misc."; restore canonical name.
    rows = [
        {**r, "candidate": "Write-ins"} if r["candidate"] == "Misc." else r
        for r in rows
    ]
    path = output_path(county_name)
    write_csv(rows, path)
    print(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
