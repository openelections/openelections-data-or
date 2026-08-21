#!/usr/bin/env python3
"""Build the Morrow County 2026 primary precinct-level CSV.

This parser converts the manually transcribed per-precinct results in
``morrow_2026_primary_data.py`` into the standard OpenElections precinct CSV at
``2026/counties/20260519__or__primary__morrow__precinct.csv``.

The source PDF (openelections-sources-or/2026/primary/Morrow.pdf) is
image-only, so values were read from 200-dpi PNG renders and cross-checked
against the PaddleOCR-VL-1.6 markdown cache (``.paddleocr_cache/Morrow/p*.md``).
Pages with unreliable OCR were transcribed directly from the rendered images.

Pages that required image-based recovery (the OCR cache is unreliable):
  * p002 - Democratic U.S. House / Governor values are shifted.
  * p003 - OCR truncated; Democratic PCP and Republican federal/state contests
           for 01 BOARDMAN were recovered from the image.
  * p005/p006 - Nonpartisan tables for 01 BOARDMAN are garbled/shifted.
  * p008 - Plain-text layout drops most Democratic vote values for 02 IRRIGON.
  * p014 - Plain-text layout shifted for 03 LEXINGTON Democratic contests.
  * p019 - Plain-text layout shifted for 04 IONE Democratic contests.
  * p022 - OCR omits the Court of Appeals Position 13 header for 04 IONE.
  * p025 - Plain-text layout shifted for 05 HEPPNER Democratic contests.

The county-level CSV (``2026/20260519__or__primary__county.csv``) contains
broken/concatenated candidate names for Governor and omits local/PCP contests,
so the precinct file preserves the PDF-printed Governor names and local rows.

Explainable mismatches from ``verify_precinct_totals.py --county Morrow``:
  * 12 Governor rows in the county CSV are garbled concatenations; precincts
    keep the correctly printed candidate names.
  * Democratic State House 57 lists ``Jim E. Doherty`` in the county CSV, but
    the precinct PDF shows ``No Candidate Filed`` for that party/district; the
    precinct write-in total is mapped to the county CSV's ``Misc.`` row.
  * Measure 120 uses ``Candidate 1`` / ``Candidate 2`` in the county CSV and
    ``Yes`` / ``No`` in the precincts.

Actual verification summary:
  ``15 mismatches, 15 county totals missing in precincts, 157 precinct sums
  missing in county CSV.``
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from morrow_2026_primary_data import ALL_ROWS
from precinct_2026_common import align_candidates_to_county, make_row, output_path, write_csv


def _contest_key(row: dict) -> tuple:
    return (row["office"], row["district"], row["party"])


def _normalize_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _load_misc_contests(county_csv_path: str, county_name: str) -> set:
    """Return (office, district, party) contests that use a 'Misc.' candidate."""
    misc = set()
    with open(county_csv_path) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != county_name:
                continue
            if _normalize_name(row["candidate"].strip()) == "misc":
                district = " ".join(dict.fromkeys(row["district"].strip().split()))
                misc.add((row["office"].strip(), district, row["party"].strip()))
    return misc


def _rename_writeins(rows: list[dict], misc_contests: set) -> list[dict]:
    """Rename 'Write-ins' to 'Misc.' for contests where the county CSV uses Misc."""
    out = []
    for row in rows:
        key = _contest_key(row)
        if (
            _normalize_name(row["candidate"]) == "write ins"
            and key in misc_contests
        ):
            row = dict(row)
            row["candidate"] = "Misc."
        out.append(row)
    return out


def main(argv: list[str]) -> None:
    # The source PDF path is accepted for workflow consistency but is not
    # parsed on the fly; data has already been transcribed into the sibling
    # data module.
    if len(argv) > 1:
        _source_pdf = argv[1]

    rows = [make_row(*r) for r in ALL_ROWS]

    county_csv = os.path.join("2026", "20260519__or__primary__county.csv")

    # Governor is excluded from alignment because the county CSV names are
    # garbled concatenations.  State House 57 Democratic is excluded because the
    # county CSV lists Jim E. Doherty while the precinct PDF shows
    # "No Candidate Filed".
    skip_keys = {
        ("Governor", "", "D"),
        ("Governor", "", "R"),
        ("State House", "57", "D"),
    }
    alignable = [r for r in rows if _contest_key(r) not in skip_keys]
    unalignable = [r for r in rows if _contest_key(r) in skip_keys]

    aligned = align_candidates_to_county(alignable, county_csv, "Morrow", tolerance=5)
    aligned.extend(unalignable)

    misc_contests = _load_misc_contests(county_csv, "Morrow")
    # Do not apply the Misc. write-in rename to the garbled Governor rows.
    misc_contests -= {("Governor", "", "D"), ("Governor", "", "R")}
    aligned = _rename_writeins(aligned, misc_contests)

    out = output_path("Morrow")
    write_csv(aligned, out)
    print(f"Wrote {len(aligned)} rows to {out}")


if __name__ == "__main__":
    main(sys.argv)
