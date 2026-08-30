#!/usr/bin/env python3
"""Parse Multnomah County 2026 primary precinct results from the SOVC2 CSV.

Multnomah exports a Hart-style "Statement of Votes Cast" CSV with one row per
(contest, choice, precinct), which replaces the OCR-based pipeline used for
the earlier PDF abstract. The registration-totals XLSX that ships alongside
the SOVC CSV is not needed for results and is ignored.

Contest names look like "US Senator (DEM) (Vote for 1)" or
"Judge of the Circuit Court, 4th District, Position 5 (Vote for 1)".
Choices include literal "Write-in" rows, which become "Write-ins".

Usage:
    uv run python src/parsers/2026_primary_multnomah_parser.py \
        '/path/to/Multnomah OR SOVC2 - election20260519Primary.csv'
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    align_candidates_to_county,
    make_row,
    normalize_party,
    output_path,
    write_csv,
)

COUNTY = "Multnomah"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"

VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)\s*$", re.IGNORECASE)
PARTY_RE = re.compile(r"\s*\((DEM|REP)\)\s*$", re.IGNORECASE)
DISTRICT_RE = re.compile(r"(\d+)\w*\s+District", re.IGNORECASE)
POSITION_RE = re.compile(r"Position\s+(\d+)", re.IGNORECASE)
MEASURE_RE = re.compile(r"(?:State\s+)?Measure\s+(\S+)", re.IGNORECASE)

# Contest name -> office, taken verbatim where the canonical office already
# matches the verifier's valid-office list.
METRO_OFFICES = {
    "Metro Council President": "Metro Council President",
    "Metro Auditor": "Metro Auditor",
    "Metro Councilor": "Metro Councilor",
}


def _ordinal(n: int) -> str:
    s = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
    if 11 <= n <= 13:
        s = "th"
    return f"{n}{s}"


def parse_contest_name(text):
    """Return (office, district, party) for a SOVC contest name."""
    text = text.strip()
    text = VOTE_FOR_RE.sub("", text).strip()
    party = ""
    m = PARTY_RE.search(text)
    if m:
        party = normalize_party(m.group(1))
        text = text[: m.start()].strip()
    text = text.strip(" -,:")
    if not text:
        return None, None, None

    district = ""

    if text.startswith("US Senator"):
        office = "U.S. Senate"
    elif text.startswith("US Representative"):
        office = "U.S. House"
        dm = DISTRICT_RE.search(text)
        district = dm.group(1) if dm else ""
    elif text.startswith("Governor"):
        office = "Governor"
    elif text.startswith("State Senator"):
        office = "State Senate"
        dm = DISTRICT_RE.search(text)
        district = dm.group(1) if dm else ""
    elif text.startswith("State Representative"):
        office = "State House"
        dm = DISTRICT_RE.search(text)
        district = dm.group(1) if dm else ""
    elif "Labor" in text or "Commissioner of the Bureau" in text:
        office = "Labor Commissioner"
    elif "Supreme Court" in text:
        office = "Judge of the Supreme Court"
        pm = POSITION_RE.search(text)
        district = f"Position {pm.group(1)}" if pm else ""
    elif "Court of Appeals" in text:
        office = "Judge of the Court of Appeals"
        pm = POSITION_RE.search(text)
        district = f"Position {pm.group(1)}" if pm else ""
    elif "Circuit Court" in text:
        office = "Judge of the Circuit Court"
        dm = DISTRICT_RE.search(text)
        pm = POSITION_RE.search(text)
        if dm and pm:
            district = (
                f"{_ordinal(int(dm.group(1)))} District, Position {pm.group(1)}"
            )
        elif pm:
            district = f"Position {pm.group(1)}"
    else:
        for raw, office in METRO_OFFICES.items():
            if text.startswith(raw):
                dm = DISTRICT_RE.search(text)
                district = dm.group(1) if dm else ""
                break
        else:
            mm = MEASURE_RE.search(text)
            if mm:
                office = f"Measure {mm.group(1).rstrip(':,')}"
                district = ""
            else:
                return None, None, None
        return office, district, party

    return office, district, party


def normalize_candidate(name):
    name = name.strip()
    if name.lower() in ("write-in", "write in", "write-ins"):
        return "Write-ins"
    if name in ("Yes", "No"):
        return name
    # Add periods to bare middle initials ("Deborah C Brown" ->
    # "Deborah C. Brown") so names match the county-level CSV style.
    parts = [p + "." if re.fullmatch(r"[A-Z]", p) else p for p in name.split()]
    return " ".join(parts)


def parse_sovc(path):
    rows = []
    with open(path, newline="") as f:
        for rec in csv.DictReader(f):
            office, district, party = parse_contest_name(rec["Contest"])
            if office is None:
                raise ValueError(f"Unrecognized contest: {rec['Contest']!r}")
            if office.startswith("Measure"):
                party = ""
            candidate = normalize_candidate(rec["Choice"])
            votes = int(rec["Votes"].replace(",", ""))
            rows.append(
                make_row(
                    county=COUNTY,
                    precinct=f"Precinct {rec['Precinct'].strip()}",
                    office=office,
                    district=district,
                    party=party,
                    candidate=candidate,
                    votes=votes,
                )
            )
    return rows


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: uv run python src/parsers/2026_primary_multnomah_parser.py "
            "'/path/to/Multnomah OR SOVC2 - election20260519Primary.csv'"
        )
        sys.exit(1)
    rows = parse_sovc(sys.argv[1])
    rows = align_candidates_to_county(rows, COUNTY_CSV, COUNTY, tolerance=5)
    out = output_path(COUNTY)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()