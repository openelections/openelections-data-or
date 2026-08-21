#!/usr/bin/env python3
"""Convert Electionware-style precinct PDFs (Union, Clatsop, etc.) to OpenElections CSV."""

import argparse
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import pdfplumber

from precinct_2026_common import (
    COUNTIES,
    format_candidate_name,
    is_number_token,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    parse_number,
    write_csv,
)


def _county_name_from_path(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0].replace(".PDF", "")
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


def _line_text(line: List[Tuple[float, float, str]]) -> str:
    return " ".join(w[2] for w in line)


def _is_statistics_done(line_text: str) -> bool:
    return bool(re.search(r"Vote\s+For", line_text, re.IGNORECASE))


def _is_precinct_header(line: List[Tuple[float, float, str]]) -> Optional[str]:
    """Return precinct name if line looks like a precinct header."""
    if not line:
        return None
    text = _line_text(line)
    # Examples: "01 LG City", "101 - Astoria West", "02-LG City", "17-Ant/BC"
    m = re.match(r"^(\d+(?:\s*-\s*[A-Za-z/]+)?)(?:\s+(.+))?$", text)
    if m and is_number_token(m.group(1).split("-")[0].strip()):
        return text
    return None


def _parse_candidate_line(line: List[Tuple[float, float, str]]) -> Optional[Tuple[str, int]]:
    """Return (candidate_name, votes) from a candidate row."""
    if not line:
        return None
    tokens = [w[2] for w in line]
    if not tokens:
        return None
    last = tokens[-1]
    if not is_number_token(last):
        return None
    name = format_candidate_name(tokens[:-1])
    return name, parse_number(last)


def _classify_contest_line(line_text: str) -> str:
    """Classify a line within a contest block."""
    upper = line_text.upper()
    if re.match(r"^(DEM|REP)\b", upper):
        return "OFFICE"
    if re.search(r"Vote\s+For", line_text, re.IGNORECASE):
        return "VOTE_FOR"
    if upper.startswith("TOTAL") and "VOTES" in upper and "CAST" in upper:
        return "TOTAL_VOTES_CAST"
    if upper.startswith("WRITE-IN") and "TOTALS" in upper:
        return "WRITE_IN_TOTALS"
    if upper.startswith("NOT ASSIGNED"):
        return "NOT_ASSIGNED"
    if upper.startswith("OVERVOTES"):
        return "OVERVOTES"
    if upper.startswith("UNDERVOTES"):
        return "UNDERVOTES"
    if upper.startswith("CONTEST TOTALS"):
        return "CONTEST_TOTALS"
    if upper == "TOTAL":
        return "TOTAL_LABEL"
    cand = _parse_candidate_line(line_text.split())  # not used directly
    return "CANDIDATE"


def parse_electionware_page(page) -> Optional[Tuple[str, List[Dict[str, str]]]]:
    """Parse a single Electionware page; return (precinct, rows) or None."""
    words = page.extract_words()
    if not words:
        return None
    lines = _group_lines(words)
    if len(lines) < 4:
        return None

    county = None
    precinct = None
    for line in lines[:4]:
        text = _line_text(line)
        if "County" in text:
            for name in COUNTIES:
                if name.lower() in text.lower():
                    county = name
                    break
        ph = _is_precinct_header(line)
        if ph:
            precinct = ph

    if not county or not precinct:
        return None

    rows: List[Dict[str, str]] = []

    # Statistics block.
    registered_voters: Optional[int] = None
    ballots_cast: Optional[int] = None
    ballots_cast_blank: Optional[int] = None

    i = 0
    while i < len(lines):
        text = _line_text(lines[i])
        if re.search(r"Registered\s+Voters\s+-\s+Total", text, re.IGNORECASE):
            tokens = [w[2] for w in lines[i]]
            for t in reversed(tokens):
                if is_number_token(t):
                    registered_voters = parse_number(t)
                    break
        if re.search(r"Ballots\s+Cast\s+-\s+Total", text, re.IGNORECASE):
            tokens = [w[2] for w in lines[i]]
            for t in reversed(tokens):
                if is_number_token(t):
                    ballots_cast = parse_number(t)
                    break
        if re.search(r"Ballots\s+Cast\s+-\s+Blank", text, re.IGNORECASE):
            tokens = [w[2] for w in lines[i]]
            for t in reversed(tokens):
                if is_number_token(t):
                    ballots_cast_blank = parse_number(t)
                    break
        # End of statistics when we see a contest header (party or recognized office).
        if (
            re.match(r"^(DEM|REP)\b", text, re.IGNORECASE)
            or normalize_office(text)
        ):
            break
        i += 1

    if registered_voters is not None:
        rows.append(make_row(county, precinct, "Registered Voters", "", "", "", registered_voters))
    if ballots_cast is not None:
        rows.append(make_row(county, precinct, "Ballots Cast", "", "", "", ballots_cast))
    if ballots_cast_blank is not None:
        rows.append(make_row(county, precinct, "Ballots Cast Blank", "", "", "", ballots_cast_blank))

    # Contest parsing.
    while i < len(lines):
        text = _line_text(lines[i])

        # Stop at page footer.
        if text.startswith("Precinct Summary") or text.startswith("Report generated"):
            break

        # Start of a new contest?
        party_match = re.match(r"^(DEM|REP)\b", text, re.IGNORECASE)
        office_match = normalize_office(text)
        if party_match or (office_match and not re.search(r"Vote\s+For", text, re.IGNORECASE) and text != "TOTAL"):
            # Collect office header lines until "Vote For".
            header_parts = [text]
            j = i + 1
            while j < len(lines):
                ht = _line_text(lines[j])
                if re.search(r"Vote\s+For", ht, re.IGNORECASE):
                    break
                if ht == "TOTAL":
                    break
                # Office continuation if it doesn't look like a candidate row.
                if not is_number_token(ht.split()[-1]):
                    header_parts.append(ht)
                j += 1
            full_header = " ".join(header_parts)
            office = normalize_office(full_header)
            party = normalize_party(full_header)
            district = parse_district(full_header)

            # Move to after "Vote For" and "TOTAL".
            while j < len(lines) and not _line_text(lines[j]) == "TOTAL":
                if re.search(r"Vote\s+For", _line_text(lines[j]), re.IGNORECASE):
                    pass
                j += 1
            j += 1  # skip TOTAL

            # Read candidate rows and pseudo-candidate rows.
            candidate_votes: Dict[str, int] = {}
            while j < len(lines):
                line = lines[j]
                lt = _line_text(line)

                if lt.startswith("Write-In Totals"):
                    val = _parse_candidate_line(line)
                    if val:
                        candidate_votes["Write-ins"] = val[1]
                    j += 1
                    continue

                if lt.startswith("Not Assigned"):
                    j += 1
                    continue

                if lt.startswith("Total Votes Cast"):
                    j += 1
                    continue

                if lt.startswith("Overvotes"):
                    tokens = [w[2] for w in line]
                    for t in reversed(tokens):
                        if is_number_token(t):
                            candidate_votes["Over Votes"] = parse_number(t)
                            break
                    j += 1
                    continue

                if lt.startswith("Undervotes"):
                    tokens = [w[2] for w in line]
                    for t in reversed(tokens):
                        if is_number_token(t):
                            candidate_votes["Under Votes"] = parse_number(t)
                            break
                    j += 1
                    continue

                if lt.startswith("Contest Totals"):
                    j += 1
                    break

                # Candidate row.
                cand = _parse_candidate_line(line)
                if cand:
                    candidate_votes[cand[0]] = cand[1]
                    j += 1
                    continue

                # Unknown line; break if it looks like a new contest.
                if re.match(r"^(DEM|REP)\b", lt, re.IGNORECASE) or normalize_office(lt):
                    break
                j += 1

            for candidate, votes in candidate_votes.items():
                rows.append(make_row(county, precinct, office, district, party, candidate, votes))

            i = j
            continue

        i += 1

    return precinct, rows


def parse_electionware(pdf_path: str) -> List[Dict[str, str]]:
    pdf_path = pdf_path.replace("~", os.path.expanduser("~"))
    all_rows: List[Dict[str, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            result = parse_electionware_page(page)
            if result:
                _, rows = result
                all_rows.extend(rows)
    return all_rows


def main(county_name: str, pdf_path: str):
    rows = parse_electionware(pdf_path)
    out = output_path(county_name)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("county", help="county name")
    ap.add_argument("pdf", help="path to precinct PDF")
    args = ap.parse_args()
    main(args.county, args.pdf)
