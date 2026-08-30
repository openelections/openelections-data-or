#!/usr/bin/env python3
"""Build the 2026 Oregon primary precinct-level CSV for Wasco County.

Wasco's certified report is an ES&S "Statement of Votes Cast, Official
Certified with Precincts" layout. The 2026-06-10 certified edition has an
embedded text layer, so the PDF is parsed with pdfplumber instead of OCR.
Each precinct section starts with a "Precinct NN" line and contains one block
per contest: a heading line, a ballots/turnout line (which carries the
overvotes and undervotes), candidate rows, then Total/Overvotes/Undervotes
lines. Multiple "Write-in" rows within one contest are summed into a single
"Write-ins" row, matching the convention in other Oregon county files.

Usage:
    uv run python src/parsers/2026_primary_wasco_parser.py \
        '/path/to/Wasco.pdf'
"""

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    align_candidates_to_county,
    make_row,
    normalize_office,
    output_path,
    parse_district,
    write_csv,
)

COUNTY_NAME = "Wasco"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"

VOTE_FOR_RE = re.compile(r"\(Vote\s+For\s+(\d+)\)", re.IGNORECASE)
PRECINCT_RE = re.compile(r"^Precinct\s+(\d+)\s*$", re.IGNORECASE)
BOILERPLATE_RE = re.compile(
    r"^("
    r"Statement of Votes Cast|Wasco County, OR|All Precincts|"
    r"Total Ballots Cast:|Choice Votes Vote %"
    r")",
    re.IGNORECASE,
)
BALLOTS_RE = re.compile(
    r"^(\d+)\s+ballots\s+\((\d+)\s+over voted ballots,\s*(\d+)\s+overvotes,\s*"
    r"(\d+)\s+undervotes\)",
    re.IGNORECASE,
)
TOTAL_RE = re.compile(r"^Total\s+\d[\d,]*\s+[\d.]+%\s*$", re.IGNORECASE)
OVERUNDER_RE = re.compile(r"^(Overvotes|Undervotes)\s+(\d[\d,]*)\s*$", re.IGNORECASE)
CANDIDATE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>\d[\d,]*)\s+(?P<pct>\d+(?:\.\d+)?%)\s*$")
PCP_RE = re.compile(
    r"^Precinct Committee Person\s+-\s+(?:Democrat|Republican)\s+-\s+(.+?)\s*"
    r"\((?:DEM|REP)\)",
    re.IGNORECASE,
)


def _extract_party(text: str) -> str:
    pm = re.search(r"\((DEM|REP)\)", text, re.IGNORECASE)
    if pm:
        return "D" if pm.group(1).upper() == "DEM" else "R"
    return ""


def _parse_contest_heading(line: str) -> Optional[Tuple[str, str, str]]:
    """Return (office, district, party) for a contest heading line."""
    line = VOTE_FOR_RE.sub("", line).strip(" -,:")

    party = _extract_party(line)
    detect = re.sub(r"\((DEM|REP)\)", "", line, flags=re.IGNORECASE).strip(" -,:")
    detect = re.sub(r"\b(Democrat|Republican)\b", "", detect, flags=re.IGNORECASE).strip(" -,:")

    if PCP_RE.match(line):
        return "Precinct Committee Person", "", party

    office = normalize_office(detect)
    district = parse_district(detect)

    if office is None:
        if re.search(r"^Commissioner,\s*Position\b", detect, re.IGNORECASE):
            office = "County Commissioner"
            pm = re.search(r"Position\s+(\d+)", detect, re.IGNORECASE)
            district = pm.group(1) if pm else ""
        else:
            m = re.search(r"(?:State\s+)?Measure\s+(\S+)", detect, re.IGNORECASE)
            if m:
                office = f"Measure {m.group(1).rstrip(':,')}"
                district = ""
            else:
                office = detect
                district = ""

    if office.startswith("Measure"):
        party = ""

    return office, district, party


def _normalize_candidate(text: str) -> Optional[str]:
    """Return canonical candidate name or None for rows to skip."""
    text = text.strip()
    if not text:
        return None
    low = text.lower()
    if low in {"total", "contest total", "total votes cast"}:
        return None
    if "write-in" in low or "write in" in low:
        return "Write-ins"
    if low == "yes":
        return "Yes"
    if low == "no":
        return "No"
    # Add periods to bare middle initials ("Robb E Van Cleave" ->
    # "Robb E. Van Cleave") so names match the county-level CSV style.
    parts = text.split()
    parts = [p + "." if re.fullmatch(r"[A-Z]", p) else p for p in parts]
    return " ".join(parts)


def _parse_vote(text: str) -> Optional[int]:
    text = text.replace(",", "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def parse_wasco(pdf_path: str) -> List[Dict[str, str]]:
    """Parse the Wasco SOVC PDF into precinct-level CSV rows."""
    rows: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    precinct = ""
    office = district = party = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for raw_line in (page.extract_text() or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # A bare "All Precincts" line starts the countywide summary
                # section at the end of the report; skip it and everything
                # after it (no precinct heading intervenes).  This must be
                # checked before the boilerplate rule, which also matches.
                if line == "All Precincts":
                    precinct = ""
                    continue

                if BOILERPLATE_RE.match(line):
                    continue

                pm = PRECINCT_RE.match(line)
                if pm:
                    precinct = pm.group(1)
                    continue

                # A bare "All Precincts" line starts the countywide summary
                # section at the end of the report; skip it and everything
                # after it (no precinct heading intervenes).
                if line == "All Precincts":
                    precinct = ""
                    continue

                if VOTE_FOR_RE.search(line):
                    contest = _parse_contest_heading(line)
                    if contest is None:
                        continue
                    office, district, party = contest
                    continue

                if not precinct:
                    continue

                bm = BALLOTS_RE.match(line)
                if bm:
                    over, under = int(bm.group(3)), int(bm.group(4))
                    if over or under:
                        rows[(precinct, office, district, party, "Over Votes")] += over
                        rows[(precinct, office, district, party, "Under Votes")] += under
                    continue

                if TOTAL_RE.match(line) or OVERUNDER_RE.match(line):
                    continue

                cm = CANDIDATE_RE.match(line)
                if cm:
                    candidate = _normalize_candidate(cm.group("name"))
                    votes = _parse_vote(cm.group("votes"))
                    if candidate and votes is not None:
                        rows[(precinct, office, district, party, candidate)] += votes
                    continue

    return [
        make_row(
            county=COUNTY_NAME,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in sorted(rows.items())
    ]


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: uv run python src/parsers/2026_primary_wasco_parser.py "
            "'/path/to/Wasco.pdf'"
        )
        sys.exit(1)
    rows = parse_wasco(sys.argv[1])
    rows = align_candidates_to_county(rows, COUNTY_CSV, COUNTY_NAME, tolerance=5)
    out = output_path(COUNTY_NAME)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()