#!/usr/bin/env python3
"""Shared utilities for parsing 2026 Oregon primary precinct-level PDFs."""

import csv
import os
import re
from collections import defaultdict
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

# OpenElections canonical office names.
# Keys are substrings we search for in source text.
OFFICE_MAP = {
    "US Senator": "U.S. Senate",
    "US Representative": "U.S. House",
    "State Senator": "State Senate",
    "State Representative": "State House",
    "Governor": "Governor",
    "Attorney General": "Attorney General",
    "Secretary of State": "Secretary of State",
    "State Treasurer": "State Treasurer",
    "Auditor": "Auditor",
    "Commissioner of Agriculture": "Commissioner of Agriculture",
    "Bureau of Labor and Industries": "Labor Commissioner",
    "Judge of the Supreme Court": "Judge of the Supreme Court",
    "Judge of the Court of Appeals": "Judge of the Court of Appeals",
    "Judge of the Circuit Court": "Judge of the Circuit Court",
    "District Attorney": "District Attorney",
}

# Offices that require a numeric district.
DISTRICTED_OFFICES = {"U.S. House", "State Senate", "State House"}

# Pseudo-offices that may appear in source files.
PSEUDO_OFFICES = {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}

# Pseudo-candidates that may appear within contests.
PSEUDO_CANDIDATES = {"Over Votes", "Under Votes", "Write-ins"}

COUNTIES = {
    "Baker", "Benton", "Clackamas", "Clatsop", "Columbia", "Coos", "Crook", "Curry",
    "Deschutes", "Douglas", "Gilliam", "Grant", "Harney", "Hood River", "Jackson",
    "Jefferson", "Josephine", "Klamath", "Lake", "Lane", "Lincoln", "Linn", "Malheur",
    "Marion", "Morrow", "Multnomah", "Polk", "Sherman", "Tillamook", "Umatilla",
    "Union", "Wallowa", "Wasco", "Washington", "Wheeler", "Yamhill",
}

COUNTY_FILENAME = {
    "Baker": "baker",
    "Benton": "benton",
    "Clackamas": "clackamas",
    "Clatsop": "clatsop",
    "Columbia": "columbia",
    "Coos": "coos",
    "Crook": "crook",
    "Curry": "curry",
    "Deschutes": "deschutes",
    "Douglas": "douglas",
    "Gilliam": "gilliam",
    "Grant": "grant",
    "Harney": "harney",
    "Hood River": "hood_river",
    "Jackson": "jackson",
    "Jefferson": "jefferson",
    "Josephine": "josephine",
    "Klamath": "klamath",
    "Lake": "lake",
    "Lane": "lane",
    "Lincoln": "lincoln",
    "Linn": "linn",
    "Malheur": "malheur",
    "Marion": "marion",
    "Morrow": "morrow",
    "Multnomah": "multnomah",
    "Polk": "polk",
    "Sherman": "sherman",
    "Tillamook": "tillamook",
    "Umatilla": "umatilla",
    "Union": "union",
    "Wallowa": "wallowa",
    "Wasco": "wasco",
    "Washington": "washington",
    "Wheeler": "wheeler",
    "Yamhill": "yamhill",
}

ELECTION_DATE = "20260519"


def county_name_from_path(path: str) -> str:
    """Return canonical county name from a source filename like 'Curry.pdf'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    # The county-level PDF has a long name; ignore it.
    if "May Primary Election Official Results" in stem:
        return ""
    # Match against canonical names (case-insensitive).
    lower = stem.lower()
    for name, fn in COUNTY_FILENAME.items():
        if fn.replace("_", " ") == lower:
            return name
    raise ValueError(f"Could not determine county from path: {path}")


def output_path(county_name: str, out_dir: str = "2026/counties") -> str:
    """Return the standard precinct-level CSV path for a county."""
    fn = COUNTY_FILENAME[county_name]
    return os.path.join(out_dir, f"{ELECTION_DATE}__or__primary__{fn}__precinct.csv")


def normalize_office(text: str) -> Optional[str]:
    """Return canonical office name; None if unrecognized."""
    if not text:
        return None
    text = text.strip()
    for known, canonical in OFFICE_MAP.items():
        if known.lower() in text.lower():
            return canonical
    if "measure" in text.lower():
        # Keep just the measure identifier, strip descriptions and (Vote for N).
        m = re.search(r"(?:State\s+)?(Measure\s+\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1).rstrip(":,").title()
        return text
    return None


def normalize_party(text: str) -> str:
    """Return canonical party abbreviation; blank for nonpartisan."""
    if not text:
        return ""
    text = text.strip().upper()
    text = re.sub(r"[()]", "", text)
    tokens = re.findall(r"\b[A-Z]+\b", text)
    for tok in tokens:
        if tok in {"D", "DEM", "OEM", "DEMOCRAT", "DEMOCRATIC", "DEMOCRATIC PARTY"}:
            return "D"
        if tok in {"R", "REP", "REPUBLICAN", "REPUBLICAN PARTY"}:
            return "R"
    return ""


def parse_district(text: str) -> str:
    """Extract district/position from a district string."""
    if not text:
        return ""
    # Judicial races with both district and position: extract just that part.
    m = re.search(
        r"(\d+(?:st|nd|rd|th)\s+District)[,\.]?\s*(Position\s+\d+)", text, re.IGNORECASE
    )
    if m:
        return f"{m.group(1)}, {m.group(2)}"
    # U.S. House / State Senate / State House: just the district number.
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+District", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Court of Appeals / other position-only races.
    m = re.search(r"(Position\s+\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def format_candidate_name(parts: List[str]) -> str:
    """Join name tokens, clean asterisks and write-in markers, format initials."""
    cleaned = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p == "(WI)":
            continue
        # Strip nominee/elected markers.
        p = p.lstrip("*").strip()
        # Add period to single-letter initials.
        if re.fullmatch(r"[A-Z]", p):
            p = p + "."
        cleaned.append(p)
    return " ".join(cleaned)


def is_number_token(text: str) -> bool:
    return re.fullmatch(r"[0-9,]+", text) is not None


def parse_number(text: str) -> int:
    return int(text.replace(",", ""))


def is_percent_token(text: str) -> bool:
    return re.fullmatch(r"\d{1,3}(?:\.\d+)?%", text) is not None


def make_row(
    county: str,
    precinct: str,
    office: str,
    district: str,
    party: str,
    candidate: str,
    votes: int,
) -> Dict[str, str]:
    return {
        "county": county,
        "precinct": precinct,
        "office": office,
        "district": district,
        "party": party,
        "candidate": candidate,
        "votes": str(votes),
    }


def sort_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Sort rows for stable output."""
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


def write_csv(rows: Iterable[Dict[str, str]], path: str) -> None:
    """Write rows to a precinct-level CSV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows_list = sort_rows(list(rows))
    fieldnames = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_list)


def _normalize_name_key(name: str) -> str:
    """Strip punctuation and compress whitespace for fuzzy matching."""
    name = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def align_candidates_to_county(
    precinct_rows: List[Dict[str, str]],
    county_csv_path: str,
    county_name: str,
    tolerance: int = 5,
) -> List[Dict[str, str]]:
    """Rename parsed candidates to match the canonical names in the county CSV.

    For each (office, district, party) contest, this matches precinct-level
    candidate totals to county-level totals.  When the number of candidates is
    the same on both sides, candidates are paired by sorted vote total, which
    handles small OCR read errors and missing first-precinct rows.  Otherwise
    it falls back to exact/near-exact total matching.

    Write-ins are mapped to the county CSV's ``Misc.`` candidate where present.
    """
    # Load county totals per (office, district, party, candidate).
    county_groups: Dict[Tuple[str, str, str, str], int] = {}
    with open(county_csv_path) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != county_name:
                continue
            key = (
                row["office"].strip(),
                _normalize_district_string(row["district"].strip()),
                row["party"].strip(),
                row["candidate"].strip(),
            )
            county_groups[key] = county_groups.get(key, 0) + int(row["votes"])

    # Sum parsed precinct rows per (office, district, party, candidate).
    parsed_groups: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    for row in precinct_rows:
        key = (
            row["office"].strip(),
            _normalize_district_string(row["district"].strip()),
            row["party"].strip(),
            row["candidate"].strip(),
        )
        parsed_groups[key] += int(row["votes"])

    rename_map: Dict[Tuple[str, str, str, str], str] = {}

    # Collect the set of contest keys from both sides.
    contests = {
        (office, district, party)
        for (office, district, party, _) in set(parsed_groups.keys()) | set(county_groups.keys())
    }

    for office, district, party in contests:
        # Measure contests use Yes/No in precinct files; the county CSV uses
        # generic Candidate 1/Candidate 2 labels, so never rename them.
        if office.startswith("Measure"):
            continue

        county_cands = [
            (cand, total)
            for (o, d, p, cand), total in county_groups.items()
            if o == office and d == district and p == party
        ]
        parsed_cands = [
            (cand, total)
            for (o, d, p, cand), total in parsed_groups.items()
            if o == office and d == district and p == party
        ]

        if not county_cands or not parsed_cands:
            continue

        # Separate pseudo-candidates.  We only try to map write-ins to Misc.
        county_misc = [(c, t) for c, t in county_cands if _normalize_name_key(c) == "misc"]
        county_real = [(c, t) for c, t in county_cands if _normalize_name_key(c) != "misc"]
        parsed_writeins = [(c, t) for c, t in parsed_cands if _normalize_name_key(c) == "write ins"]
        parsed_real = [(c, t) for c, t in parsed_cands if _normalize_name_key(c) not in {"write ins", "over votes", "under votes"}]

        # Map write-ins to Misc. when totals are within tolerance.
        if county_misc and parsed_writeins:
            cm = min(county_misc, key=lambda x: x[1])
            pw = min(parsed_writeins, key=lambda x: x[1])
            if abs(cm[1] - pw[1]) <= tolerance:
                rename_map[(office, district, party, pw[0])] = cm[0]

        # If counts match, sort both by total and pair in order.
        if len(county_real) == len(parsed_real) and len(parsed_real) > 0:
            county_sorted = sorted(county_real, key=lambda x: x[1])
            parsed_sorted = sorted(parsed_real, key=lambda x: x[1])
            for (cand_c, _), (cand_p, _) in zip(county_sorted, parsed_sorted):
                rename_map[(office, district, party, cand_p)] = cand_c
            continue

        # Otherwise fall back to exact/near-exact total matching.
        used_county = set()
        for cand_p, total_p in parsed_real:
            best = None
            best_diff = None
            for cand_c, total_c in county_real:
                if cand_c in used_county:
                    continue
                diff = abs(total_c - total_p)
                if diff <= tolerance and (best_diff is None or diff < best_diff):
                    best = cand_c
                    best_diff = diff
            if best is not None:
                used_county.add(best)
                rename_map[(office, district, party, cand_p)] = best

    out = []
    for row in precinct_rows:
        key = (
            row["office"].strip(),
            _normalize_district_string(row["district"].strip()),
            row["party"].strip(),
            row["candidate"].strip(),
        )
        new_name = rename_map.get(key)
        if new_name:
            row = dict(row)
            row["candidate"] = new_name
        out.append(row)
    return out


def _normalize_district_string(district: str) -> str:
    """Collapse duplicated district text (e.g. 'Position 1 Position 1')."""
    parts = district.split()
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return " ".join(out)


def recover_missing_precincts(
    precinct_rows: List[Dict[str, str]],
    county_csv_path: str,
    county_name: str,
    missing_precincts: Dict[Tuple[str, str, str], str],
) -> List[Dict[str, str]]:
    """Reconstruct precinct rows that OCR swallowed into the candidate header.

    For each contest key in *missing_precincts*, compare the county-level total
    for every candidate to the sum of the parsed precinct rows.  The difference
    is assigned to the missing precinct.  Negative differences (indicating other
    parse errors) are ignored.
    """
    if not missing_precincts:
        return precinct_rows

    county_totals: Dict[Tuple[str, str, str, str], int] = {}
    with open(county_csv_path) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != county_name:
                continue
            key = (
                row["office"].strip(),
                _normalize_district_string(row["district"].strip()),
                row["party"].strip(),
                row["candidate"].strip(),
            )
            county_totals[key] = county_totals.get(key, 0) + int(row["votes"])

    parsed_sums: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    for row in precinct_rows:
        key = (
            row["office"].strip(),
            _normalize_district_string(row["district"].strip()),
            row["party"].strip(),
            row["candidate"].strip(),
        )
        parsed_sums[key] += int(row["votes"])

    out = list(precinct_rows)
    for (office, district, party), precinct in missing_precincts.items():
        # Measure contests use Yes/No in precinct files; the county CSV uses
        # Candidate 1/Candidate 2, so do not recover from county totals here.
        if office.startswith("Measure"):
            continue
        for (o, d, p, cand), total in county_totals.items():
            if o != office or d != district or p != party:
                continue
            summed = parsed_sums.get((office, district, party, cand), 0)
            diff = total - summed
            if diff > 0:
                out.append(
                    make_row(
                        county=county_name,
                        precinct=precinct,
                        office=office,
                        district=district,
                        party=party,
                        candidate=cand,
                        votes=diff,
                    )
                )
    return out


def group_words_into_lines(
    words: Iterable[Dict],
    y_tolerance: float = 3.0,
) -> List[List[Tuple[float, float, str]]]:
    """Group pdfplumber-style word dicts into text lines by y position.

    Each word dict is expected to have keys 'text', 'x0', 'x1', 'top'.
    Returns lines as lists of (x0, x1, text) sorted by x0.
    """
    buckets: Dict[int, List[Tuple[float, float, str]]] = defaultdict(list)
    for w in words:
        key = round(w["top"] / y_tolerance)
        buckets[key].append((w["x0"], w["x1"], w["text"]))
    lines = []
    for key in sorted(buckets):
        line = sorted(buckets[key], key=lambda t: t[0])
        lines.append(line)
    return lines


def extract_column_centers(
    rows: List[List[Tuple[float, float, str]]],
    skip_first_n: int = 1,
) -> List[float]:
    """Compute median x-center of each numeric column across rows.

    skip_first_n columns are treated as non-vote (e.g. precinct label).
    """
    col_values: Dict[int, List[float]] = defaultdict(list)
    for row in rows:
        for idx, word in enumerate(row[skip_first_n:], start=skip_first_n):
            center = (word[0] + word[1]) / 2
            col_values[idx].append(center)
    centers = []
    for idx in sorted(col_values):
        centers.append(median(col_values[idx]))
    return centers


def assign_words_to_columns(
    words: List[Tuple[float, float, str]],
    centers: List[float],
    max_distance: float = 60.0,
) -> List[List[str]]:
    """Assign each word to the nearest center column and return grouped tokens."""
    assignments: List[List[str]] = [[] for _ in range(len(centers))]
    for word in words:
        center = (word[0] + word[1]) / 2
        best_idx = None
        best_dist = float("inf")
        for idx, c in enumerate(centers):
            dist = abs(center - c)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx is not None and best_dist <= max_distance:
            assignments[best_idx].append(word[2])
    return assignments
