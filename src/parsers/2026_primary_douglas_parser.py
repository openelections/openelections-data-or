"""Parser for Douglas County 2026 primary "Abstract of Votes" PDF.

The source is an ES&S "Abstract of Votes" report: one section per precinct
("Precinct NN" label), contests listed vertically inside each section, and a
single county-wide "All Precincts" summary at the end of the report.

This version extracts words with natural_pdf and regroups them into visual
lines by y-coordinate before parsing.  Naive text extraction splits the
Choice/Votes/Vote% columns into separate line streams, which silently drops
candidate rows (the previous pdfplumber-based parser lost most judicial
contests and 5 of 29 Governor precincts that way).

Three further fixes over the previous parser:

- Measure contests' statistics line carries ", N registered voters, turnout
  N%"; that is per-precinct data, not a county summary, so lines are no
  longer skipped on "registered voters".  (This is what lost Measure 120.)
- Contest headers that start with a number are measures: "120 Increases Fuel
  Taxes" -> office "Measure 120"; "10-379 Siuslaw School District..." ->
  office "Measure 10-379".
- PCP headers are long enough that "(Vote for" wraps to the next line; the
  continuation merge now triggers on a line *ending* with "(Vote for" (the
  old test required no ")" anywhere in the line, which never fired because
  the party "(DEM)" already supplies one).  PCP rows use the office name
  "Precinct Committee Person", district blank, and the precinct number from
  the PCP header itself.

Usage:
    uv run python src/parsers/2026_primary_douglas_parser.py \
        '/path/to/Douglas.pdf'
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import natural_pdf

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
# Statewide measure ("120 Increases Fuel Taxes") or local measure
# ("10-379 Siuslaw School District ...").
MEASURE_RE = re.compile(r"^(?P<num>\d{1,2}-\d{1,3}|\d{1,3})\s+\S")
# Orphaned "(Vote for" continuation fragment, e.g. "6) 273 ballots (...)".
ORPHAN_VOTEFOR_RE = re.compile(r"^\d+\)\s")


def is_junk_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    lowered = text.lower()
    # Page / report titles (the page header words share a baseline, so they
    # arrive pre-joined: "Abstract of Votes Page: 1 of 154").
    if text.startswith("Abstract of Votes"):
        return True
    if text.startswith("Douglas County, May 19,"):
        return True
    # The county-wide summary section is a *stop marker* in the main loop;
    # this catches its ballot-count line on the same page.
    if text.startswith("All Precincts,"):
        return True
    if text.startswith("Total Ballots Cast:"):
        return True
    if "precincts reported out of" in lowered:
        return True
    if text == "Choice Votes Vote %":
        return True
    return False


def merge_header_continuations(lines):
    """Join ES&S contest headers whose "(Vote for N)" wrapped to the next line.

    The wrap point is a line *ending* in "(Vote for": PCP headers are long
    enough to wrap there, and the "(" ")" characters earlier in the line
    (party markers) would defeat a "no closing paren in line" test.
    """
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.endswith("(Vote for") and i + 1 < len(lines):
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

    district = parse_district(office_part)
    office = normalize_office(office_part)

    if office is None:
        mm = MEASURE_RE.match(office_part)
        if mm:
            office = f"Measure {mm.group('num')}"
            district = ""

    if office is None:
        # Unmapped county office: keep the name, but move the position/district
        # fragment ("... Commissioner, Position 1") into the district column.
        office = office_part
        if district:
            idx = office_part.find(district)
            if idx > 0:
                office = office_part[:idx].rstrip(" ,")
        # Canonical county-office names drop the county prefix ("Douglas
        # County Assessor" -> "County Assessor"); the county column already
        # disambiguates.
        cm = re.match(
            r"^[A-Za-z .']+ County "
            r"(Commissioner|Clerk|Surveyor|Assessor|Treasurer|Legal Counsel)$",
            office,
        )
        if cm:
            office = f"County {cm.group(1).title()}"

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
    if lowered in ("yes", "no"):
        return n.title()
    return format_candidate_name(n.split())


def is_statistics_line(text: str) -> bool:
    """Per-contest metadata, e.g. "170 ballots (0 over voted ballots, ...)".

    Measure contests append ", N registered voters, turnout N%" — that is
    still per-precinct data and must NOT be treated as a county summary.
    """
    return re.match(r"^\d+\s+ballots", text) is not None


def _line_text(band) -> str:
    """Render one y-band of chars as a text line, left to right.

    Space characters exist as chars in this PDF, so plain concatenation
    yields the words; a gap larger than any kerning (no space char between
    the words) still gets a space inserted defensively.
    """
    out = []
    prev = None
    for c in sorted(band, key=lambda c: c.x0):
        if (
            prev is not None
            and c.text != " "
            and prev.text != " "
            and c.x0 - prev.x1 > 1.5
        ):
            out.append(" ")
        out.append(c.text)
        prev = c
    return "".join(out).strip()


def extract_lines(pdf_path: str):
    """Yield layout-reconstructed text lines from every page in order.

    Lines are rebuilt from characters, not words: natural_pdf's word
    tokenizer splits words on pages where glyph baselines jitter
    ("Douglas" -> "Dou" + "glas"), which mangles office and candidate
    names.  Characters are grouped into visual lines by y-center with a
    3pt tolerance; in the table area within-row center spread is <= 1.5pt
    and row-to-row gaps are >= 9pt, so the tolerance is safe.

    The certification stamp (12pt, right margin, x >= 489 on every page)
    shares y-centers with the last table rows of a page; its text then
    rides along on real lines ("... (Vote for 1) recorded on this") and
    junk-filters them away.  Characters at x >= 470 — stamp, page
    numbers, timestamps, nothing in the table area — are dropped before
    clustering instead.
    """
    pdf = natural_pdf.PDF(pdf_path)
    for page in pdf.pages:
        chars = [
            c
            for c in page.chars
            if c.x0 < 470
        ]
        chars.sort(key=lambda c: ((c.top + c.bottom) / 2, c.x0))
        band = []
        prev_center = None
        for c in chars:
            center = (c.top + c.bottom) / 2
            if band and prev_center is not None and center - prev_center > 3.0:
                yield _line_text(band)
                band = []
            band.append(c)
            prev_center = center
        if band:
            yield _line_text(band)


def parse_pdf(pdf_path: str):
    rows = defaultdict(int)
    current_precinct: str | None = None
    current_office: str | None = None
    current_district = ""
    current_party = ""
    skip_current = False

    all_lines = [
        line.strip()
        for line in extract_lines(pdf_path)
        if not is_junk_line(line)
    ]
    all_lines = merge_header_continuations(all_lines)

    for text in all_lines:
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
            current_district = district
            current_party = party
            skip_current = False

            # PCP sections do not repeat the "Precinct NN" label; the header
            # itself carries the precinct number.
            if PCP_RE.search(text):
                current_office = "Precinct Committee Person"
                current_district = ""
                pcp_pm = PCP_PRECINCT_RE.search(text)
                if pcp_pm:
                    current_precinct = f"Precinct {int(pcp_pm.group(1))}"
            else:
                current_office = office
            continue

        # Per-contest metadata line ("NNN ballots (... registered voters,
        # turnout N%)" on measures).  Checked before the candidate regex so
        # the trailing "N undervotes" cannot phantom-match as a candidate.
        if is_statistics_line(text):
            continue

        # Orphaned "(Vote for" continuation that failed to merge.
        if ORPHAN_VOTEFOR_RE.match(text):
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