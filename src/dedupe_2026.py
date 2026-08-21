#!/usr/bin/env python3
"""Sum duplicate rows in 2026 precinct CSVs.

Some parsers emit duplicate (county,precinct,office,district,party,candidate)
rows when the source PDF lists the same candidate more than once (e.g., Oregon
ballot-name rotation artifacts).  This script collapses those duplicates by
summing votes.
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path


def dedupe_file(path: str) -> int:
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    summed: defaultdict[tuple, int] = defaultdict(int)
    for row in rows:
        key = tuple(row[k] for k in fieldnames if k != "votes")
        summed[key] += int(row["votes"])

    out_rows = []
    for key, votes in summed.items():
        out_rows.append(dict(zip([k for k in fieldnames if k != "votes"], key)) | {"votes": str(votes)})

    # Preserve original field order.
    out_rows = sorted(out_rows, key=lambda r: tuple(r[k] for k in fieldnames))

    changed = len(rows) - len(out_rows)
    if changed:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
    return changed


def main() -> int:
    total = 0
    for path in sorted(Path("2026/counties").glob("*.csv")):
        n = dedupe_file(str(path))
        if n:
            print(f"{path}: removed {n} duplicate rows")
            total += n
    print(f"Total duplicate rows removed: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
