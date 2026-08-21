#!/usr/bin/env python3
"""One-time cleanup of 2026 primary precinct CSVs to satisfy src/verifier.py.

Applies office and candidate normalizations that are too broad to fit in the
parser-specific scripts, and aggregates individual write-in names to the
canonical ``Write-ins`` pseudo-candidate per contest/precinct.
"""

import csv
import glob
import os
import re
from collections import defaultdict


def normalize_office_for_2026(office: str) -> str:
    """Return a canonical office name acceptable to src/verifier.py."""
    orig = office.strip()
    text = orig

    # Strip leading party prefixes on county offices (e.g. "DEM County Commissioner").
    text = re.sub(r"^(DEM|REP)\s+", "", text, flags=re.IGNORECASE)

    # County offices – collapse to canonical names, keeping position/district in
    # the district column where possible.
    if re.search(r"county\s+commissioner", text, re.IGNORECASE):
        return "County Commissioner"
    if re.search(r"county\s+clerk", text, re.IGNORECASE):
        return "County Clerk"
    if re.search(r"county\s+assessor", text, re.IGNORECASE):
        return "County Assessor"
    if re.search(r"county\s+surveyor", text, re.IGNORECASE):
        return "County Surveyor"
    if re.search(r"county\s+treasurer", text, re.IGNORECASE):
        return "County Treasurer"
    if re.search(r"justice\s+of\s+(the\s+)?peace", text, re.IGNORECASE):
        return "Justice of the Peace"
    if re.search(r"county\s+legal\s+counsel", text, re.IGNORECASE):
        return "County Legal Counsel"

    # Hood River office variants.
    if re.search(r"hood\s+river\s+county,?\s+commissioner", text, re.IGNORECASE):
        return "County Commissioner"

    # Municipal offices.
    if re.search(r"city\s+of\s+[^,]+council", text, re.IGNORECASE) or \
       re.search(r"council\s+member|councilor", text, re.IGNORECASE):
        return "City Council"
    if re.search(r"mayor", text, re.IGNORECASE):
        return "Mayor"
    if re.search(r"municipal\s+judge", text, re.IGNORECASE):
        return "Municipal Judge"

    # Metro / regional.
    if re.search(r"metro\s+council\s+president", text, re.IGNORECASE):
        return "Metro Council President"

    # Special-district director races (e.g. "Director Tillamook Bay Fire &
    # Rescue RFPD (Proposed)").
    if re.search(r"director", text, re.IGNORECASE) and \
       re.search(r"fire\s+(&amp;|&|and)\s+rescue", text, re.IGNORECASE):
        return "Fire District Director"

    # Ballot measures.  Any office containing a dashed measure number
    # (e.g. "2-143 City of Corvallis", "Question 14-120",
    # "Tualatin Hills Park & Recreation District 34-350") becomes
    # "Measure <id>"; a leading plain number (e.g. "120 Increase Fuel
    # Taxes") is a statewide measure number.
    m = re.search(r"\d+-\d+", text)
    if m:
        return f"Measure {m.group(0)}"
    m = re.match(r"(\d{2,3})\s+\D", text)
    if m:
        return f"Measure {m.group(1)}"

    # Generic "Commissioner" / "Assessor" / "Surveyor" without County prefix
    # are local offices; canonicalize them.
    if re.fullmatch(r"Commissioner", text, re.IGNORECASE):
        return "Commissioner"
    if re.fullmatch(r"Assessor", text, re.IGNORECASE):
        return "Assessor"
    if re.fullmatch(r"Surveyor", text, re.IGNORECASE):
        return "Surveyor"

    # Precinct Committee Person – collapse all variants.
    if re.search(r"precinct\s+committee\s+person", text, re.IGNORECASE):
        return "Precinct Committee Person"

    # Already canonical state/federal offices are not touched because the
    # verifier accepts them directly.
    return orig


def normalize_candidate_for_2026(candidate: str) -> str:
    """Return a canonical candidate name; drop rows by returning None."""
    c = candidate.strip()
    # Collapse literal "\n" sequences (and real newlines) left by PDF parsing.
    c = re.sub(r"\\n|\n", " ", c).strip()

    # Rows that are summary/total rows, not candidates.
    if re.fullmatch(r"total\s+votes\s+cast", c, re.IGNORECASE):
        return None
    if re.search(r"write-?in\s*:?\s*not\s*(cast|assigned)", c, re.IGNORECASE):
        return None

    # Aggregated write-in labels.
    if re.fullmatch(r"write-?in\s+totals?", c, re.IGNORECASE):
        return "Write-ins"
    if re.fullmatch(r"write-?ins?", c, re.IGNORECASE):
        return "Write-ins"

    # Pseudo-candidate OCR typos (catches Oversvotes, Overtoves, etc. – any
    # jumbling of the letters in "votes" after "over"/"under").
    if re.fullmatch(r"over\s?[stvoe]+", c, re.IGNORECASE):
        return "Over Votes"
    if re.fullmatch(r"under\s?[stvoe]+", c, re.IGNORECASE):
        return "Under Votes"

    # Individual write-in names are aggregated separately by aggregate_writeins().
    return c


def aggregate_writeins(rows: list) -> list:
    """Sum all individual write-in candidates per contest/precinct into ``Write-ins``.

    When the precinct/contest already has an official ``Write-ins`` total row,
    keep that row and drop the itemized names rather than adding a second
    (duplicated, double-counting) ``Write-ins`` row.
    """
    grouped = defaultdict(int)
    passthrough = []
    has_official_writeins = set()
    for row in rows:
        key = (row["county"], row["precinct"], row["office"], row["district"], row["party"])
        if row["candidate"] == "Write-ins":
            has_official_writeins.add(key)
            passthrough.append(row)
        elif is_individual_writein(row["candidate"]):
            grouped[key] += int(row["votes"])
        else:
            passthrough.append(row)

    out = list(passthrough)
    for key, total in grouped.items():
        if total > 0 and key not in has_official_writeins:
            out.append({
                "county": key[0],
                "precinct": key[1],
                "office": key[2],
                "district": key[3],
                "party": key[4],
                "candidate": "Write-ins",
                "votes": str(total),
            })
    return dedupe_rows(out)


def dedupe_rows(rows: list) -> list:
    """Collapse rows sharing a unique key.

    Exact duplicates (same key and votes) are dropped.  Multiple ``Write-ins``
    rows for one key (a leftover synthesized partial next to the official
    county total) are merged by keeping the larger count.
    """
    by_key = {}
    order = []
    for row in rows:
        key = tuple(row[c] for c in ("county", "precinct", "office", "district", "party", "candidate"))
        if key not in by_key:
            by_key[key] = dict(row)
            order.append(key)
        elif row["candidate"] == "Write-ins":
            by_key[key]["votes"] = str(max(int(by_key[key]["votes"]), int(row["votes"])))
        # else: exact duplicate or conflicting non-write-in row; keep the first.
    return [by_key[k] for k in order]


def is_individual_writein(candidate: str) -> bool:
    """True for ``Write-in: Name`` and ``Write-in Name`` style rows."""
    return bool(re.match(r"write-?in[:\s]", candidate, re.IGNORECASE)) and \
           not re.fullmatch(r"write-?ins?", candidate, re.IGNORECASE) and \
           not re.fullmatch(r"write-?in\s+totals?", candidate, re.IGNORECASE)


def extract_position_or_district(office: str, district: str) -> str:
    """Pull Position/District numbers out of the office string if district is blank."""
    d = district.strip()
    if d:
        return d
    m = re.search(r"Position\s+(\d+|#\d+)", office, re.IGNORECASE)
    if m:
        return m.group(1).replace("#", "")
    m = re.search(r"District\s+(\d+)", office, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def clean_rows(rows: list) -> list:
    """Apply normalizations and drop invalid rows."""
    cleaned = []
    for row in rows:
        office = normalize_office_for_2026(row["office"])
        district = extract_position_or_district(row["office"], row["district"])
        candidate = normalize_candidate_for_2026(row["candidate"])
        if candidate is None:
            continue
        cleaned.append({
            "county": row["county"].strip(),
            "precinct": row["precinct"].strip(),
            "office": office,
            "district": district,
            "party": row["party"].strip(),
            "candidate": candidate,
            "votes": row["votes"].strip(),
        })
    return aggregate_writeins(cleaned)


def sort_rows(rows: list) -> list:
    return sorted(
        rows,
        key=lambda r: (
            r["county"],
            r["office"],
            r["district"],
            r["party"],
            r["candidate"],
            r["precinct"],
        ),
    )


def main():
    paths = sorted(glob.glob("2026/counties/*.csv"))
    for path in paths:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        cleaned = clean_rows(rows)
        cleaned = sort_rows(cleaned)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["county", "precinct", "office", "district", "party", "candidate", "votes"],
            )
            writer.writeheader()
            writer.writerows(cleaned)
        print(f"Cleaned {path}: {len(rows)} -> {len(cleaned)} rows")


if __name__ == "__main__":
    main()
