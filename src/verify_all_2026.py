#!/usr/bin/env python3
"""Run verify_precinct_totals for all 2026 county precinct CSVs and summarize."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "parsers"))
from precinct_2026_common import COUNTY_FILENAME


def verify(county: str) -> dict:
    result = subprocess.run(
        [sys.executable, "src/verify_precinct_totals.py", "--county", county],
        capture_output=True,
        text=True,
    )
    out = result.stdout + result.stderr
    summary = {"mismatches": None, "not_in_county": None, "not_in_precincts": None}
    for line in out.splitlines():
        if line.startswith("Summary:"):
            parts = line.replace(",", "").split()
            summary["mismatches"] = int(parts[1])
            # "N county totals missing in precincts" = NOT_IN_PRECINCTS
            summary["not_in_precincts"] = int(parts[3])
            # "N precinct sums missing in county CSV" = NOT_IN_COUNTY
            for tok in reversed(parts):
                if tok.isdigit():
                    summary["not_in_county"] = int(tok)
                    break
    return summary


def main() -> int:
    rows = []
    fn_to_county = {fn: name for name, fn in COUNTY_FILENAME.items()}
    for path in sorted(Path("2026/counties").glob("*.csv")):
        fn = path.stem.replace("20260519__or__primary__", "").replace("__precinct", "")
        county = fn_to_county.get(fn, fn.replace("_", " ").title())
        summary = verify(county)
        rows.append((county, summary["mismatches"], summary["not_in_county"], summary["not_in_precincts"]))

    print(f"{'County':<15} {'Mismatch':>10} {'NotInPrecincts':>16} {'NotInCounty':>12}")
    for county, mm, nic, nip in rows:
        print(f"{county:<15} {mm:>10} {nip:>16} {nic:>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
