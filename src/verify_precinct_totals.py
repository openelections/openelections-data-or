#!/usr/bin/env python3
"""Verify that precinct-level vote sums match the county-level CSV totals."""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

COUNTY_CSV = "2026/20260519__or__primary__county.csv"
PRECINCT_DIR = "2026/counties"


def _normalize_name(name: str) -> str:
    """Strip punctuation and extra spaces for fuzzy candidate matching."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _normalize_district(district: str) -> str:
    """Collapse duplicated district text (e.g. 'Position 1 Position 1')."""
    parts = district.split()
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return " ".join(out)


def load_county_totals(path: str) -> Dict[Tuple[str, str, str, str, str], int]:
    """Load county-level totals keyed by (county, office, district, party, candidate)."""
    totals: Dict[Tuple[str, str, str, str, str], int] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            key = (
                row["county"].strip(),
                row["office"].strip(),
                _normalize_district(row["district"].strip()),
                row["party"].strip(),
                _normalize_name(row["candidate"].strip()),
            )
            totals[key] = totals.get(key, 0) + int(row["votes"])
    return totals


def load_precinct_sums(precinct_dir: str) -> Dict[Tuple[str, str, str, str, str], int]:
    """Sum precinct-level votes keyed by (county, office, district, party, candidate)."""
    sums: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    for fname in sorted(os.listdir(precinct_dir)):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(precinct_dir, fname)
        with open(path) as f:
            for row in csv.DictReader(f):
                key = (
                    row["county"].strip(),
                    row["office"].strip(),
                    _normalize_district(row["district"].strip()),
                    row["party"].strip(),
                    _normalize_name(row["candidate"].strip()),
                )
                sums[key] += int(row["votes"])
    return dict(sums)


def verify(county_csv: str, precinct_dir: str, counties: List[str] = None) -> Tuple[int, int, int]:
    totals = load_county_totals(county_csv)
    sums = load_precinct_sums(precinct_dir)

    # Limit to requested counties.
    if counties:
        counties_set = set(counties)
        totals = {k: v for k, v in totals.items() if k[0] in counties_set}
        sums = {k: v for k, v in sums.items() if k[0] in counties_set}

    mismatches = 0
    missing_in_precincts = 0
    missing_in_county = 0

    # Check every county total against precinct sums.
    for key, total in totals.items():
        summed = sums.get(key, 0)
        if total != summed:
            mismatches += 1
            county, office, district, party, candidate = key
            print(
                f"MISMATCH: {county} | {office} {district!r} {party!r} {candidate!r} | "
                f"county={total} precinct_sum={summed}"
            )

    # Find precinct sums that have no county-level total.
    county_keys = set(totals.keys())
    precinct_keys = set(sums.keys())
    for key in precinct_keys - county_keys:
        missing_in_county += 1
        county, office, district, party, candidate = key
        print(
            f"NOT_IN_COUNTY: {county} | {office} {district!r} {party!r} {candidate!r} | "
            f"precinct_sum={sums[key]}"
        )

    # Find county totals that have no precinct rows.
    for key in county_keys - precinct_keys:
        missing_in_precincts += 1
        county, office, district, party, candidate = key
        print(
            f"NOT_IN_PRECINCTS: {county} | {office} {district!r} {party!r} {candidate!r} | "
            f"county={totals[key]}"
        )

    return mismatches, missing_in_precincts, missing_in_county


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--county", action="append", help="limit verification to county(ies)")
    ap.add_argument(
        "--county-csv", default=COUNTY_CSV, help="path to county-level CSV"
    )
    ap.add_argument(
        "--precinct-dir", default=PRECINCT_DIR, help="directory containing precinct CSVs"
    )
    args = ap.parse_args()

    if not os.path.exists(args.county_csv):
        print(f"County CSV not found: {args.county_csv}", file=sys.stderr)
        return 1
    if not os.path.isdir(args.precinct_dir):
        print(f"Precinct directory not found: {args.precinct_dir}", file=sys.stderr)
        return 1

    mismatches, missing_in_precincts, missing_in_county = verify(
        args.county_csv, args.precinct_dir, args.county
    )

    print()
    print(
        f"Summary: {mismatches} mismatches, {missing_in_precincts} county totals missing in precincts, "
        f"{missing_in_county} precinct sums missing in county CSV."
    )
    return 1 if (mismatches or missing_in_precincts or missing_in_county) else 0


if __name__ == "__main__":
    sys.exit(main())
