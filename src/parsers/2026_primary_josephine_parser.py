"""Parser for Josephine County 2026 primary precinct PDF.

Layout: ES&S "Statement of Votes Cast by Geography". Each page starts with a
standard header and then lists contests vertically. A line like
"Precinct 01" starts a new precinct; contests and candidates continue across
pages until the next precinct line.

Usage:
    uv run python src/parsers/2026_primary_josephine_parser.py \
        '/path/to/Josephine.pdf'
"""

import sys
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    ELECTION_DATE,
    format_candidate_name,
    group_words_into_lines,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)

# Josephine reports party in contest headers like "US Senator (DEM)".
PARTY_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
# Header wrapper that always appears in a contest line.
VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)


def is_precinct_line(words):
    return (
        len(words) >= 2
        and words[0][2] == "Precinct"
        and words[1][2].isdigit()
    )


def parse_precinct(words):
    return f"Precinct {words[1][2]}"


def is_statistics_line(words):
    """Lines like '625 ballots (...)' are per-contest metadata. Skip them."""
    text = " ".join(w[2] for w in words)
    return re.match(r"^\d+\s+ballots", text) is not None


def is_contest_header_line(words):
    text = " ".join(w[2] for w in words)
    return VOTE_FOR_RE.search(text) is not None


def parse_contest_header(text: str):
    """Return (office, district, party) from a contest header line."""
    # Strip the vote-for clause.
    m = VOTE_FOR_RE.search(text)
    if m:
        office_part = text[: m.start()].strip()
    else:
        office_part = text.strip()

    party = ""
    pm = PARTY_RE.search(office_part)
    if pm:
        party = normalize_party(pm.group(1))
        office_part = office_part[: pm.start()] + office_part[pm.end() :]
        office_part = office_part.strip()

    office = normalize_office(office_part)
    if office is None:
        office = office_part.rstrip(" ,")

    district = parse_district(office_part)
    return office, district, party


def parse_candidate_row(words):
    """Return (name, votes) from a candidate row using the votes column.

    Candidate names start near x=72 and the votes column is near x=219.
    Everything before the first integer token with x > 190 is the name.
    """
    name_parts = []
    votes = None
    for x0, _x1, text in words:
        if votes is None and text.isdigit() and x0 > 190:
            votes = int(text)
            break
        if votes is None:
            name_parts.append(text)
    name = " ".join(name_parts).strip()
    return name, votes


def candidate_label(name: str) -> str:
    """Map PDF candidate labels to OpenElections pseudo-candidate names."""
    n = name.strip()
    if n.lower() in {"write-in", "write in"}:
        return "Write-ins"
    if n.lower() == "overvotes":
        return "Over Votes"
    if n.lower() == "undervotes":
        return "Under Votes"
    return format_candidate_name(n.split())


def parse_josephine(pdf_path: str, county_name: str = "Josephine"):
    rows = defaultdict(int)
    current_precinct = None
    current_office = None
    current_district = ""
    current_party = ""
    in_summary = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines = group_words_into_lines(words, y_tolerance=3.0)

            for line in lines:
                if not line:
                    continue
                first_x = line[0][0]
                line_text = " ".join(w[2] for w in line)

                # Skip repeated page header/title lines.
                if line_text.startswith((
                    "Statement of Votes Cast by Geography",
                    "Josephine,",
                    "Certified Precinct Level Results",
                    "All Precincts,",
                    "Total Ballots Cast",
                    "Choice Votes Vote",
                )):
                    continue

                # A standalone "All Precincts" line starts the county-wide summary
                # section at the end of the report. Skip everything until a real
                # precinct line appears (it never does in this layout).
                if line_text.strip() == "All Precincts":
                    in_summary = True
                    current_office = None
                    continue

                if is_precinct_line(line):
                    current_precinct = parse_precinct(line)
                    current_office = None
                    in_summary = False
                    continue

                if in_summary:
                    continue

                # Contest headers appear at the same indent as the statistics line.
                if 48 <= first_x <= 66:
                    if is_statistics_line(line):
                        continue
                    if is_contest_header_line(line):
                        current_office, current_district, current_party = parse_contest_header(line_text)
                    continue

                # Candidate/total/over/under rows are indented further right.
                if first_x > 66 and current_precinct and current_office:
                    name, votes = parse_candidate_row(line)
                    if name is None or votes is None:
                        continue
                    if name.lower() == "total":
                        continue
                    candidate = candidate_label(name)
                    key = (
                        current_precinct,
                        current_office,
                        current_district,
                        current_party,
                        candidate,
                    )
                    rows[key] += votes

    out_rows = [
        make_row(
            county=county_name,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in rows.items()
    ]
    return out_rows


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: uv run python src/parsers/2026_primary_josephine_parser.py "
            "'/path/to/Josephine.pdf'"
        )
        sys.exit(1)
    pdf_path = sys.argv[1]
    county_name = "Josephine"
    out_rows = parse_josephine(pdf_path, county_name)
    path = output_path(county_name)
    write_csv(out_rows, path)
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
