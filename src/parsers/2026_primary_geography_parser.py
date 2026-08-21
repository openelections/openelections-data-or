"""Generic parser for ES&S "Statement of Votes Cast by Geography" PDFs.

Handles the 2026 Oregon primary layout used by Josephine, Jackson, Linn,
Hood River, and similar counties:

- Each page starts with a standard header block.
- A line like "Precinct 001" / "Precinct 01" starts a new precinct.
- Contests are listed vertically with a header like "US Senator (DEM) (Vote for 1)".
- Candidate rows follow as "Name  Votes  Percent%".
- Each contest ends with Total / Overvotes / Undervotes rows.
- A standalone "All Precincts" line starts a county-wide summary section at the
  end of the file that should be skipped.

Usage:
    uv run python src/parsers/2026_primary_geography_parser.py \
        Josephine '/path/to/Josephine.pdf'
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    format_candidate_name,
    group_words_into_lines,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    write_csv,
)

PARTY_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)


def standalone_party(line_text: str) -> str:
    """Return normalized party if the line is just '(DEM)' / '(REP)'."""
    m = PARTY_RE.fullmatch(line_text.strip())
    if m:
        return normalize_party(m.group(1))
    return ""


def is_header_line(line_text: str) -> bool:
    """Return True for repeated page header/title lines that are not content."""
    text = line_text.strip()
    if not text:
        return True
    # Column header line.
    if text.startswith("Choice"):
        return True
    # County-wide header line on every page.
    if text.startswith("All Precincts,"):
        return True
    # Summary lines that mix county totals with per-precinct metadata.
    if "Total Ballots Cast" in text or "Registered Voters" in text or "Overall Turnout" in text:
        return True
    if "precincts reported out of" in text.lower():
        return True
    # Page number / county title / certified lines float at wider x positions
    # and are filtered by x position in the main loop, but catch any stragglers.
    if text.startswith("Page:"):
        return True
    if "Primary Election" in text and "Certified" in text:
        return True
    if re.match(r"^(Statement of|Precinct Report|Final Official|Official Results|Official Election)", text):
        return True
    return False


def is_precinct_line(words):
    if len(words) < 2:
        return False
    return words[0][2] == "Precinct" and words[1][2].isdigit()


def parse_precinct(words):
    return f"Precinct {words[1][2].lstrip('0') or '0'}"


def is_statistics_line(words):
    """Lines like '625 ballots (...)' are per-contest metadata. Skip them."""
    text = " ".join(w[2] for w in words)
    return re.match(r"^\d+\s+ballots", text) is not None


def is_contest_header_line(words):
    text = " ".join(w[2] for w in words)
    return VOTE_FOR_RE.search(text) is not None


def parse_contest_header(text: str):
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
    """Return (name, votes) using x-position of the votes column."""
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
    n = name.strip()
    lowered = n.lower()
    # Jackson/Linn/Hood River PCP rows may be "Write-in 1", "Write-in 2", etc.
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    if lowered == "overvotes":
        return "Over Votes"
    if lowered == "undervotes":
        return "Under Votes"
    return format_candidate_name(n.split())


def parse_geography_pdf(pdf_path: str, county_name: str):
    rows = defaultdict(int)
    current_precinct = None
    current_office = None
    current_district = ""
    current_party = ""
    in_summary = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Some counties (e.g., Douglas) stamp a certification message down the
            # right-hand margin. Crop it off before grouping words so it does not
            # cause line-wrapping artifacts that break contest headers.
            crop_box = (0, 0, page.width * 0.75, page.height)
            cropped = page.crop(crop_box)
            words = cropped.extract_words()
            lines = group_words_into_lines(words, y_tolerance=7.0)

            for line in lines:
                if not line:
                    continue
                first_x = line[0][0]
                line_text = " ".join(w[2] for w in line)

                # Title / page header lines are outside the left content margin.
                # Some content tokens (e.g. a standalone party label) appear farther
                # right, so only filter on the left edge.
                if first_x < 30:
                    continue

                if is_header_line(line_text):
                    continue

                # County-wide summary section at the end of the report.
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

                # Contest headers and statistics lines share the contest indent.
                if 48 <= first_x <= 66:
                    if is_statistics_line(line):
                        continue
                    if is_contest_header_line(line):
                        current_office, current_district, current_party = parse_contest_header(line_text)
                    continue

                # Candidate / Total / Overvotes / Undervotes rows.
                if first_x > 66 and current_precinct and current_office:
                    party_only = standalone_party(line_text)
                    if party_only:
                        # Some pages split the party onto its own line after the
                        # contest office. Apply it if the header did not capture it.
                        if not current_party:
                            current_party = party_only
                        continue
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
    if len(sys.argv) < 3:
        print(
            "Usage: uv run python src/parsers/2026_primary_geography_parser.py "
            "COUNTY '/path/to/County.pdf'"
        )
        sys.exit(1)
    county_name = sys.argv[1]
    pdf_path = sys.argv[2]
    out_rows = parse_geography_pdf(pdf_path, county_name)
    path = output_path(county_name)
    write_csv(out_rows, path)
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
