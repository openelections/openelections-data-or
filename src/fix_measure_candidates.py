#!/usr/bin/env python3
"""Normalize Measure contest candidate names to Yes/No in precinct CSVs."""

import csv
import os
import sys
from pathlib import Path


def fix_file(path: str) -> int:
    changed = 0
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row["office"].strip().lower().startswith("measure"):
                if row["office"].strip() != row["office"].strip().title():
                    row["office"] = row["office"].strip().title()
                    changed += 1
                cand = row["candidate"].strip()
                if cand == "Candidate 1":
                    row["candidate"] = "Yes"
                    changed += 1
                elif cand == "Candidate 2":
                    row["candidate"] = "No"
                    changed += 1
                elif cand.lower() == "yes" and cand != "Yes":
                    row["candidate"] = "Yes"
                    changed += 1
                elif cand.lower() == "no" and cand != "No":
                    row["candidate"] = "No"
                    changed += 1
            rows.append(row)
    if changed:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return changed


def main() -> int:
    total = 0
    for path in sorted(Path("2026/counties").glob("*.csv")):
        n = fix_file(str(path))
        if n:
            print(f"{path}: {n} rows")
            total += n
    print(f"Total rows changed: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
