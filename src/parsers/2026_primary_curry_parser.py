#!/usr/bin/env python3
"""Convert Curry County 2026 primary precinct PDF to OpenElections CSV."""

import os
import re
import sys
from statistics import median
from typing import Dict, List, Optional, Tuple

# Allow imports of sibling modules when run as a script.
sys.path.insert(0, os.path.dirname(__file__))

import pdfplumber

from precinct_2026_common import (
    COUNTIES,
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

PDF_PATH = "~/code/openelections-sources-or/2026/primary/Curry.pdf"


def _county_name_from_path(path: str) -> str:
    stem = path.split("/")[-1].replace(".pdf", "").replace(".PDF", "")
    for name in COUNTIES:
        if name.lower() == stem.lower():
            return name
    raise ValueError(f"Unknown county in path: {path}")


def _group_lines(words, y_tolerance: float = 3.0) -> List[List[Tuple[float, float, str]]]:
    lines: Dict[int, List[Tuple[float, float, str]]] = {}
    for w in words:
        key = round(w["top"] / y_tolerance)
        lines.setdefault(key, []).append((w["x0"], w["x1"], w["text"]))
    return [sorted(lines[k], key=lambda t: t[0]) for k in sorted(lines)]


def _find_office_party(lines: List[List[Tuple[float, float, str]]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (office, district, party) from the office header block.

    Office/district/party may be split across consecutive lines before the
    table header.
    """
    for i, line in enumerate(lines):
        text = " ".join(w[2] for w in line)
        office = normalize_office(text)
        if office is None:
            continue
        # Combine following lines until we hit the table header or a data row.
        combined = [text]
        for j in range(i + 1, len(lines)):
            next_text = " ".join(w[2] for w in lines[j])
            if "Precinct" in next_text and ("Ballots" in next_text or "Reg." in next_text or "Total" in next_text):
                break
            if _is_precinct_row(lines[j]):
                break
            combined.append(next_text)
        full_text = " ".join(combined)
        party = normalize_party(full_text)
        district = parse_district(full_text)
        return office, district, party
    return None, None, None


def _extract_row_integers(row: List[Tuple[float, float, str]]) -> Optional[Tuple[str, List[Tuple[float, int]]]]:
    """Return (precinct, [(center, value), ...]) for all integer vote columns."""
    tokens = list(row)
    precinct_idx = None
    for i, w in enumerate(tokens):
        if w[2] == "Precinct" and i + 1 < len(tokens) and is_number_token(tokens[i + 1][2]):
            precinct_idx = i
            break
    if precinct_idx is None:
        return None
    precinct = tokens[precinct_idx + 1][2]

    integers = []
    for w in tokens[precinct_idx + 2 :]:
        if is_number_token(w[2]):
            center = (w[0] + w[1]) / 2
            integers.append((center, parse_number(w[2])))
    if len(integers) < 3:
        return None
    return precinct, integers


def _build_column_centers(int_rows: List[List[Tuple[float, int]]], min_cols: int = 3) -> List[float]:
    """Median x-center for each integer-token column across rows.

    Integer tokens include the fixed columns (ballots, reg, total) followed by
    vote entities. Caller should drop the first three centers as labels.
    """
    by_idx: Dict[int, List[float]] = {}
    for integers in int_rows:
        for idx, (center, value) in enumerate(integers):
            by_idx.setdefault(idx, []).append(center)
    return [median(by_idx[i]) for i in sorted(by_idx)]


def _assign_to_columns(words: List[Tuple[float, float, str]], centers: List[float], max_dist: float = 50.0) -> List[List[str]]:
    buckets: List[List[str]] = [[] for _ in centers]
    for word in words:
        c = (word[0] + word[1]) / 2
        best_idx = None
        best_dist = float("inf")
        for i, center in enumerate(centers):
            d = abs(c - center)
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx is not None and best_dist <= max_dist:
            buckets[best_idx].append(word[2])
    return buckets


def _is_precinct_row(line: List[Tuple[float, float, str]]) -> bool:
    if not line:
        return False
    first = line[0][2]
    return first == "Precinct" and len(line) >= 2 and is_number_token(line[1][2])


def _is_total_row(line: List[List[Tuple[float, float, str]]]) -> bool:
    if not line:
        return False
    return line[0][2] in {"Total", "TOTAL"}


def parse_curry_page(page) -> Optional[Dict]:
    words = page.extract_words()
    if not words:
        return None
    lines = _group_lines(words)

    office, district, party = _find_office_party(lines)
    if office is None:
        return None

    # Find the two header rows that start the table.
    header_idx = None
    for i, line in enumerate(lines):
        if not line:
            continue
        text = " ".join(w[2] for w in line)
        # The table header line contains "Precinct" and "Ballots" / "Reg." / "Total".
        if "Precinct" in text and ("Ballots" in text or "Reg." in text or "Total" in text):
            header_idx = i
            break
    if header_idx is None:
        return None

    # Data rows start after the header rows.
    data_rows: List[List[Tuple[float, float, str]]] = []
    for line in lines[header_idx + 2 :]:
        if _is_precinct_row(line):
            data_rows.append(line)
        elif _is_total_row(line):
            break

    # Extract integer vote data from each precinct row.
    integer_rows: List[Tuple[str, List[Tuple[float, int]]]] = []
    for row in data_rows:
        parsed = _extract_row_integers(row)
        if parsed:
            integer_rows.append(parsed)

    if not integer_rows:
        return None

    # Build centers for all integer columns. The first three are fixed labels
    # (ballots, reg, total); the rest are vote entities.
    centers = _build_column_centers([ints for precinct, ints in integer_rows])
    if len(centers) <= 3:
        return None
    entity_centers = centers[3:]

    # Header rows are the two lines starting at header_idx.
    header_lines = lines[header_idx : header_idx + 2]
    header_assignments = [_assign_to_columns(line, centers) for line in header_lines]

    # Drop the first three columns (ballots, reg, total); the rest are vote entities.
    candidates: List[str] = []
    for col_idx in range(3, len(centers)):
        parts = []
        for assignment in header_assignments:
            parts.extend(assignment[col_idx])
        name = format_candidate_name(parts)
        if not name:
            name = f"Column {col_idx - 2}"
        candidates.append(name)

    # Parse each data row into vote values aligned by entity index.
    parsed_rows: List[Tuple[str, int, int, int, List[int]]] = []
    for precinct, integers in integer_rows:
        ballots_cast = integers[0][1]
        reg_voters = integers[1][1]
        total_votes = integers[2][1]
        values = [value for center, value in integers[3:]]
        parsed_rows.append((f"Precinct {precinct}", ballots_cast, reg_voters, total_votes, values))

    return {
        "office": office,
        "district": district,
        "party": party,
        "candidates": candidates,
        "rows": parsed_rows,
    }


def parse_curry(pdf_path: str) -> List[Dict[str, str]]:
    pdf_path = pdf_path.replace("~", os.path.expanduser("~"))
    county = _county_name_from_path(pdf_path)
    rows: List[Dict[str, str]] = []

    # Track pseudo-office values per precinct so we emit them only once.
    registered_voters: Dict[str, int] = {}
    ballots_cast: Dict[str, int] = {}

    with pdfplumber.open(pdf_path) as pdf:
        blocks: List[Dict] = []
        for page in pdf.pages:
            block = parse_curry_page(page)
            if block is None:
                continue
            blocks.append(block)

    for block in blocks:
        for precinct, ballots, reg, total, votes in block["rows"]:
            # First time we see a precinct, record the pseudo-office values.
            if precinct not in registered_voters:
                registered_voters[precinct] = reg
                ballots_cast[precinct] = ballots

            if len(votes) != len(block["candidates"]):
                # Skip rows that don't align; usually the totals row.
                continue

            for candidate, vote in zip(block["candidates"], votes):
                rows.append(
                    make_row(county, precinct, block["office"], block["district"], block["party"], candidate, vote)
                )

    # Emit pseudo-office rows once per precinct.
    for precinct, votes in registered_voters.items():
        rows.append(make_row(county, precinct, "Registered Voters", "", "", "", votes))
    for precinct, votes in ballots_cast.items():
        rows.append(make_row(county, precinct, "Ballots Cast", "", "", "", votes))

    return rows


def main():
    rows = parse_curry(PDF_PATH)
    out = output_path("Curry")
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
