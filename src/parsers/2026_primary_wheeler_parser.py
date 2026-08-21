"""Wheeler County 2026 primary parser using PaddleOCR markdown.

The Wheeler source PDF is image-only, but PaddleOCR-VL produces clean
per-page markdown with a vertical contest layout:

- A page introduces a precinct like "Precinct 1: FOS 6 pages".
- Contest headers end with "(Vote For N)" and may carry a leading party
  ("DEM US Senator Federal").
- Candidate rows are "Name  Votes".
- Some pages are emitted as HTML tables with two columns (Contest | Votes).

Known verification caveats (the precinct CSV itself is internally
consistent; these mismatches come from the county-level CSV):

- The county CSV combines several Governor candidate names into single
  garbled rows (e.g. "Forest Steve (Fora) William Laible Alexander Atkinson")
  that cannot be matched against the individual OCR candidate names.
- The county CSV lists a Democratic State House 57 candidate (Jim E. Doherty)
  even though the precinct PDF reports "No Candidate Filed" for that contest.
- The county CSV labels Measure 120 choices as "Candidate 1" / "Candidate 2"
  while the precinct PDF uses "Yes" / "No".
- The county CSV contains "Misc." rows for several contests that are not
  present in the precinct PDF.

Usage:
    uv run python src/parsers/2026_primary_wheeler_parser.py \
        '/path/to/Wheeler.pdf'
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import extract_pages, plain_text, table_rows
from precinct_2026_common import (
    COUNTY_FILENAME,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY = "Wheeler"

# OCR-specific name corrections that do not affect other counties.
NAME_FIXES = {
    "Karen Ostry": "Karen Ostrye",
}

VOTE_FOR_RE = re.compile(r"\(Vote\s+For\s+\d+\)", re.IGNORECASE)
VOTE_FOR_INLINE_RE = re.compile(r"Vote\s+For\s+\d+\)?", re.IGNORECASE)
PARTY_RE = re.compile(r"\b(DEM|REP|OEM)\b", re.IGNORECASE)
PRECINCT_RE = re.compile(r"^Precinct\s+(\d+):\s+(\S+)", re.IGNORECASE)
PCP_RE = re.compile(r"Precinct\s+Committee\s+Person", re.IGNORECASE)
CANDIDATE_ROW_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>\d+)$")


def is_junk_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    lowered = text.lower()
    junk = [
        "detail results by precinct",
        "wheeler county may 2026 primary election",
        "machine id:",
        "machine #:",
        "first ballot date time",
        "last ballot date time",
        "total sheets processed",
        "total ballots cast",
        "blank sheets cast",
        "contest votes",
        "i,",
        "do hereby certify",
        "votes recorded on this abstract",
        "dated this",
        "fos",
        "mit",
        "spr",
        "wheel",
    ]
    if any(text.lower().startswith(j) for j in junk):
        return True
    if re.match(r"^\d{2}/\d{2}/\d{4}$", text):
        return True
    if re.match(r"^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}$", text):
        return True
    if re.fullmatch(r"\d+\s+of\s+\d+", text):
        return True
    if text == "•":
        return True
    return False


def _flatten_escapes(text: str) -> str:
    """OCR sometimes emits literal \\n / \\r instead of whitespace."""
    return text.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")


def clean_header_text(text: str) -> str:
    """Remove extra suffixes like 'Federal', 'WHEELER', duplicated district text."""
    text = _flatten_escapes(text)
    # Strip known trailing annotations.
    text = re.sub(r"\s+Federal\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Statewide\s+Nonpartisan\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Statewide\s+Partisan\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+WHEELER\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Circuit\s+Court\s+District\s+\d+\s*$", "", text, flags=re.IGNORECASE)
    # Remove duplicated office text after a comma/district (e.g.
    # "US Representative, 2nd District US Representative 2nd District").
    text = re.sub(
        r"(,?\s+\d+(?:st|nd|rd|th)\s+District)\s+\w+.*$",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def parse_contest_header(text: str):
    text = _flatten_escapes(text).strip()
    m = VOTE_FOR_RE.search(text)
    if m:
        office_part = text[: m.start()].strip()
    else:
        office_part = text

    # Some inline headers omit the parentheses, e.g. "OEM Governor Statewide Partisan Vote For 1)"
    if not m:
        m2 = VOTE_FOR_INLINE_RE.search(office_part)
        if m2:
            office_part = office_part[: m2.start()].strip()

    party = ""
    pm = PARTY_RE.search(office_part)
    if pm:
        party = normalize_party(pm.group(1))
        office_part = office_part[: pm.start()] + office_part[pm.end() :]
        office_part = re.sub(r"^\s*-\s*", "", office_part)

    office_part = clean_header_text(office_part)
    office = normalize_office(office_part)
    if office is None:
        office = office_part.rstrip(" ,")

    district = parse_district(office_part)
    return office, district, party


def candidate_label(name: str) -> str | None:
    n = _flatten_escapes(name).strip()
    lowered = re.sub(r"['`\"]", "", n.lower())
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    if lowered in ("over votes", "overvotes", "over vote"):
        return "Over Votes"
    if lowered in ("under votes", "undervotes", "under vote"):
        return "Under Votes"
    if lowered in ("no candidate filed", "no candidates"):
        return None
    return format_candidate_name(n.split())


def _looks_like_header(text: str) -> bool:
    """Return True for a line that is clearly the start of a contest header."""
    t = text.strip()
    if not t:
        return False
    lowered = t.lower()
    # Known office keywords (including Oregon-only aliases).
    office_keywords = [
        "senator",
        "representative",
        "governor",
        "attorney general",
        "secretary of state",
        "state treasurer",
        "auditor",
        "commissioner of agriculture",
        "labor commissioner",
        "judge of",
        "district attorney",
        "county",
        "justice of the peace",
        "measure",
        "precinct committee",
    ]
    if any(kw in lowered for kw in office_keywords):
        return True
    # Leading party followed by more text is almost always a header.
    if re.match(r"\b(DEM|REP|OEM)\b", t, re.IGNORECASE) and len(t.split()) > 1:
        return True
    return False


def _looks_like_candidate_row(text: str) -> bool:
    """Return True for Name  Votes rows that are not headers."""
    t = text.strip()
    if not t:
        return False
    if CANDIDATE_ROW_RE.match(t):
        # Reject if it is actually a header (party + office text).
        if _looks_like_header(t):
            return False
        return True
    return False


def _handle_candidate(rows, current_precinct, current_office, current_district, current_party, name, votes_text):
    if not current_precinct or not current_office:
        return
    if not re.fullmatch(r"\d+", votes_text):
        return
    if name.lower() == "total":
        return
    candidate = candidate_label(name)
    if candidate is None:
        return
    candidate = NAME_FIXES.get(candidate, candidate)
    key = (
        current_precinct,
        current_office,
        current_district,
        current_party,
        candidate,
    )
    rows[key] += int(votes_text)


def parse_pdf(pdf_path: str):
    rows = defaultdict(int)
    current_precinct = ""
    current_office = None
    current_district = ""
    current_party = ""
    abbrev_to_precinct: dict = {}
    pending_header = ""

    for page_num, md in extract_pages(pdf_path):
        # Always look at the plain-text rendering first so we catch precinct
        # markers and any header/candidate lines that live outside the table.
        plain_lines = [l.strip() for l in plain_text(md).splitlines()]
        i = 0
        while i < len(plain_lines):
            line = plain_lines[i]
            i += 1
            if is_junk_line(line):
                continue

            pm = PRECINCT_RE.match(line)
            if pm:
                num = int(pm.group(1))
                abbrev = pm.group(2).upper()
                current_precinct = f"Precinct {num}"
                abbrev_to_precinct[abbrev] = current_precinct
                current_office = None
                pending_header = ""
                continue

            # A header may be completed by a (Vote For N) line, either on the
            # same line or the next line.
            if VOTE_FOR_RE.search(line) or VOTE_FOR_INLINE_RE.search(line):
                header_text = (pending_header + " " + line).strip()
                pending_header = ""
                if current_precinct:
                    current_office, current_district, current_party = parse_contest_header(
                        header_text
                    )
                continue

            # Start or continue a multi-line header.
            if _looks_like_header(line):
                # If we were already building a header and this line itself looks
                # like a header, apply the pending one first (rare).
                if pending_header and VOTE_FOR_RE.search(pending_header + " " + line):
                    current_office, current_district, current_party = parse_contest_header(
                        pending_header + " " + line
                    )
                    pending_header = ""
                    continue
                pending_header = line
                continue

            # Plain-text candidate rows.
            cm = CANDIDATE_ROW_RE.match(line)
            if cm and current_precinct and current_office:
                name = cm.group("name").strip()
                votes = cm.group("votes")
                _handle_candidate(
                    rows,
                    current_precinct,
                    current_office,
                    current_district,
                    current_party,
                    name,
                    votes,
                )
                continue

        # For pages with HTML tables, also process table rows.  The precinct
        # and current office may already be set from the plain-text pass.
        pending_table_header = ""
        if "<table" in md.lower():
            trows = table_rows(md)
            for cells in trows:
                if not cells:
                    continue
                joined = " ".join(_flatten_escapes(c) for c in cells).strip()
                if is_junk_line(joined):
                    continue

                # Header detection may span multiple table rows (e.g. one row
                # with the party abbreviation and the next with the office).
                header_text = _flatten_escapes(pending_table_header + " " + joined).strip()
                if (
                    VOTE_FOR_RE.search(joined)
                    or VOTE_FOR_INLINE_RE.search(joined)
                    or VOTE_FOR_RE.search(header_text)
                    or VOTE_FOR_INLINE_RE.search(header_text)
                ):
                    if current_precinct:
                        current_office, current_district, current_party = parse_contest_header(
                            header_text
                        )
                    pending_table_header = ""
                    continue

                if _looks_like_header(joined):
                    pending_table_header = header_text
                    continue

                # Two-cell row: candidate | votes.
                if len(cells) == 2 and current_office and current_precinct:
                    _handle_candidate(
                        rows,
                        current_precinct,
                        current_office,
                        current_district,
                        current_party,
                        cells[0],
                        cells[1],
                    )

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
        print(
            "Usage: uv run python src/parsers/2026_primary_wheeler_parser.py "
            "'/path/to/Wheeler.pdf'"
        )
        sys.exit(1)
    pdf_path = sys.argv[1]
    out_rows = parse_pdf(pdf_path)
    path = Path("2026/counties") / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()
