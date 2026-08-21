"""Parser for Douglas County 2026 primary "Abstract of Votes" PDF.

This source is an ES&S "Abstract of Votes" (not the geography report). It lists
contests vertically and mixes per-precinct sections with county-wide summary
sections. Summary sections are identified by the phrase "registered voters" in
their ballot-count line and are skipped. Per-precinct PCP sections do not repeat
the "Precinct NN" label, so the precinct is pulled from the PCP header itself.

Usage:
    uv run python src/parsers/2026_primary_douglas_parser.py \
        '/path/to/Douglas.pdf'
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
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY = "Douglas"

# Candidate rows look like "Name Votes Percent%" (percent is optional). Extra
# certification text may trail the percent; allow it.
CANDIDATE_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<votes>\d+)(?:\s+\d+(?:\.\d+)?%)?(?:\s+.*)?$"
)
VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)
PARTY_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
PCP_RE = re.compile(r"Precinct\s+Committee\s+Person", re.IGNORECASE)
PCP_PRECINCT_RE = re.compile(
    r"Precinct\s+Committee\s+Person\s+-\s+\w+\s+-\s+(\d+)", re.IGNORECASE
)


def is_junk_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    lowered = text.lower()
    # Page / report titles.
    if text.startswith("Abstract of Votes Page:"):
        return True
    if text.startswith("Douglas County, May 19,"):
        return True
    if text.startswith("All Precincts,"):
        return True
    # The standalone "All Precincts" line marks the start of the county-wide
    # summary section; it is handled as a stop marker by the main loop.
    if text.startswith("Total Ballots Cast:"):
        return True
    if "precincts reported out of" in lowered:
        return True
    if text == "Choice Votes Vote %":
        return True
    # Certification stamp fragments that float through the text.
    if text.startswith("I certify the votes"):
        return True
    if "recorded on this" in lowered:
        return True
    if "abstract correctly" in lowered:
        return True
    if "summarize the tally" in lowered:
        return True
    if "of votes cast at the" in lowered:
        return True
    if lowered.startswith("election indicated"):
        return True
    if text == "Daniel J. Loomis":
        return True
    if text == "Douglas County Clerk":
        return True
    if text == "June 11th, 2026":
        return True
    return False


def merge_header_continuations(lines):
    """Join ES&S contest headers that were split across two lines."""
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "(Vote for" in line and ")" not in line:
            if i + 1 < len(lines):
                line = line + " " + lines[i + 1]
                i += 1
        merged.append(line)
        i += 1
    return merged


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


def label_candidate(name: str) -> str:
    n = name.strip()
    lowered = n.lower()
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    if lowered == "overvotes":
        return "Over Votes"
    if lowered == "undervotes":
        return "Under Votes"
    return format_candidate_name(n.split())


def is_statistics_line(text: str) -> bool:
    return re.match(r"^\d+\s+ballots", text) is not None


def is_summary_statistics(text: str) -> bool:
    return "registered voters" in text.lower()


def parse_pdf(pdf_path: str):
    rows = defaultdict(int)
    current_precinct: str | None = None
    current_office: str | None = None
    current_district = ""
    current_party = ""
    skip_current = False

    with pdfplumber.open(pdf_path) as pdf:
        # Flatten every page into one sequential line stream. Page breaks are
        # not important for this report because each contest is self-contained.
        all_lines = []
        for page in pdf.pages:
            for line in page.extract_text().splitlines():
                if is_junk_line(line):
                    continue
                all_lines.append(line)

        all_lines = merge_header_continuations(all_lines)

        for line in all_lines:
            text = line.strip()

            # County-wide summary section starts here; everything after it is
            # aggregates, not per-precinct data.
            if text == "All Precincts":
                break

            # Precinct labels like "Precinct 01".
            pm = re.match(r"^Precinct\s+(\d+)$", text)
            if pm:
                current_precinct = f"Precinct {int(pm.group(1))}"
                current_office = None
                current_district = ""
                current_party = ""
                skip_current = False
                continue

            if not current_precinct:
                continue

            # Contest header.
            if VOTE_FOR_RE.search(text):
                office, district, party = parse_contest_header(text)
                current_office = office
                current_district = district
                current_party = party
                skip_current = False

                # PCP headers encode the precinct number in the header text.
                if PCP_RE.search(text):
                    pcp_pm = PCP_PRECINCT_RE.search(text)
                    if pcp_pm:
                        current_precinct = f"Precinct {int(pcp_pm.group(1))}"
                continue

            # Per-contest metadata line.
            if is_statistics_line(text):
                if is_summary_statistics(text):
                    skip_current = True
                    current_office = None
                continue

            if skip_current or not current_office:
                continue

            # Candidate / Total / Overvotes / Undervotes rows.
            cm = CANDIDATE_RE.match(text)
            if cm:
                name = cm.group("name").strip()
                votes = int(cm.group("votes"))
                if name.lower() == "total":
                    continue
                candidate = label_candidate(name)
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
            county=COUNTY,
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
        print("Usage: uv run python src/parsers/2026_primary_douglas_parser.py '/path/to/Douglas.pdf'")
        sys.exit(1)
    pdf_path = sys.argv[1]
    out_rows = parse_pdf(pdf_path)
    path = Path("2026/counties") / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
