#!/usr/bin/env python3
"""Parser for Yamhill County 2026 primary "Canvass Results Report" PDF.

The PDF has an exact text layer, so tables are extracted with natural_pdf
(pdfplumber's grid strategy) — no OCR.  Column headers are rotated 90
degrees: each header cell decodes by reversing every line and then the
line order ("yelkreM\\nffeJ" -> "Jeff Merkley", ")W(\\nni-etirW\\n.csiM" ->
"Misc. Write-in (W)").

Layout: one contest per page pair.  Most contests fit on one page width
(precincts 01-26 on the left page, 28 on the right page); the wide
Governor R contest splits across four pages (candidates + Cast/Under/Over
on the left pages, the remaining metadata columns on the right pages).
Every page of a contest repeats the title line, so rows merge by
(precinct, candidate).  Committee-person pages and the "End of report"
page are skipped.

Usage:
    uv run python src/parsers/2026_primary_yamhill_parser.py [Yamhill.pdf]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    ELECTION_DATE,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY = "Yamhill"
DEFAULT_PDF = str(Path.home() / "code/openelections-sources-or/2026/primary/Yamhill.pdf")

# "US Senator - Democratic Party - 6 Year Term - Vote for One" or
# "Measure 120 Referendum ... - Nonpartisan Party" (measures carry no term).
TITLE_RE = re.compile(
    r"^(?P<title>.*?)\s+-\s+(?P<party>Democratic|Republican|Nonpartisan)(?:\s+Party)?"
    r"(?:\s+-\s+\d+\s+Year\s+Term\s+-\s+Vote\s+for\s+\w+)?\s*$",
    re.IGNORECASE,
)

# Decoded header labels.
META_CAST = "Cast Votes"
META_MAP = {
    "Undervotes": "Under Votes",
    "Under Votes": "Under Votes",
    "Overvotes": "Over Votes",
    "Over Votes": "Over Votes",
    "Misc. Write-in": None,  # duplicate of the (W) candidate column
    "Misc Write-in": None,
    "Vote By Mail Ballots Cast": None,
    "Total Ballots Cast": None,
    "Registered Voters": None,
    "Turnout Percentage": None,
    "Ballots Cast Blank": None,
}

PRECINCT_RE = re.compile(r"^\d[\d/]*\d$|^\d$")


def decode_header(cell):
    """Decode a rotated header cell: reverse each line, then the line order."""
    if not cell:
        return ""
    lines = [l for l in cell.split("\n") if l.strip()]
    return " ".join(reversed([l[::-1] for l in lines])).strip()


def classify_columns(header):
    """Map decoded header cells to (candidates, meta) column indexes.

    Returns (candidates, meta): candidates is [(idx, label)] in page
    order; meta maps 'Cast Votes'/'Under Votes'/'Over Votes' to indexes.
    """
    candidates = []
    meta = {}
    for idx, cell in enumerate(header):
        if idx == 0 or cell is None:
            continue
        label = decode_header(cell)
        if not label:
            continue
        norm = re.sub(r"\s+", " ", label)
        if norm in ("Misc. Write-in (W)", "Misc Write-in (W)", "Write-in (W)", "Write-in", "Write in"):
            candidates.append((idx, "Write-ins"))
            continue
        if norm in META_MAP or norm in (META_CAST,):
            key = META_CAST if norm == META_CAST else META_MAP[norm]
            if key is not None:
                meta[key] = idx
            continue
        # Yes/No measure options and regular candidate names.
        if norm.lower() in ("yes", "no"):
            candidates.append((idx, norm.title()))
        else:
            candidates.append((idx, format_candidate_name(norm.split())))
    return candidates, meta


def parse_title(line):
    """Title line -> (office, district, party) or None if unmapped/skipped."""
    m = TITLE_RE.match(line.strip())
    if not m:
        return None
    title, party_raw = m.group("title").strip(), m.group("party")
    if "Committee Person" in title or "Committee" in title:
        return None  # precinct committee pages: not part of the results set
    party = normalize_party(party_raw)

    if "County Commissioner" in title:
        office = "County Commissioner"
    else:
        office = normalize_office(title)
    if office is None:
        return None
    district = parse_district(title)
    return office, district, party


def parse_page(page, rows_out, stats):
    """Extract one page's table into rows_out; update the stats dict."""
    text = page.extract_text()
    title_line = None
    for line in text.splitlines():
        if TITLE_RE.match(line.strip()) or " - Nonpartisan Party" in line:
            title_line = line.strip()
            break
    if title_line is None:
        return
    parsed = parse_title(title_line)
    if parsed is None:
        return
    office, district, party = parsed

    tables = page.extract_tables()
    if not tables:
        return
    table = tables[0]
    if len(table) < 2:
        return
    candidates, meta = classify_columns(table[0])

    for row in table[1:]:
        if not row or not row[0]:
            continue
        precinct = row[0].strip()
        if not PRECINCT_RE.match(precinct) or precinct.lower() == "totals":
            continue
        stats["precincts"].add(precinct)
        key0 = (precinct, office, district, party)

        votes_by_label = defaultdict(int)
        cast = None
        for idx, label in candidates:
            if idx >= len(row) or row[idx] is None:
                continue
            v = row[idx].strip().replace(",", "")
            if re.fullmatch(r"\d+", v):
                votes_by_label[label] += int(v)
        for name, idx in meta.items():
            if idx >= len(row) or row[idx] is None:
                continue
            v = row[idx].strip().replace(",", "")
            if not re.fullmatch(r"\d+", v):
                continue
            if name == META_CAST:
                cast = int(v)
            else:
                rows_out[key0 + (name,)] += int(v)

        # Sum check: candidate votes (write-ins included) equal Cast Votes.
        if cast is not None:
            s = sum(votes_by_label.values())
            if s != cast:
                stats["mismatches"].append(
                    f"{precinct} {office} {district} {party}: sum={s} cast={cast}"
                )
            stats["checked"] += 1
        for label, votes in votes_by_label.items():
            rows_out[key0 + (label,)] += votes


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    import natural_pdf

    pdf = natural_pdf.PDF(pdf_path)
    rows_out = defaultdict(int)
    stats = {"precincts": set(), "mismatches": [], "checked": 0}

    for page in pdf.pages:
        parse_page(page, rows_out, stats)

    for m in stats["mismatches"][:20]:
        print(f"SUM MISMATCH: {m}", file=sys.stderr)
    print(
        f"precincts: {len(stats['precincts'])}; rows checked: {stats['checked']}; "
        f"mismatches: {len(stats['mismatches'])}",
        file=sys.stderr,
    )

    out_rows = [
        make_row(
            county=COUNTY,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=cand,
            votes=votes,
        )
        for (prec, office, district, party, cand), votes in sorted(rows_out.items())
    ]
    path = (
        Path("2026/counties")
        / f"{ELECTION_DATE}__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    )
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()