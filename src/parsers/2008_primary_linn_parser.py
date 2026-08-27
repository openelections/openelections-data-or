#!/usr/bin/env python3
"""Build the Linn County 2008 primary precinct CSV from the text-based
"office precinct report" canvass.

Source:
  /Users/dwillis/code/openelections-sources-or/2008/
      Linn OR may_20_2008_office_precinct_report.txt
  /Users/dwillis/code/openelections-sources-or/2008/
      Linn OR summary may_20_2008.pdf  (candidate names / order / totals)

The report is a fixed-width plain-text canvass (not an image), so no OCR is
required; the summary PDF has a text layer (pdftotext).  Each contest is laid
out as:

    NAME HEADING CANVASS ... LINN COUNTY, OREGON ... FINAL AND OFFICIAL
    PRIMARY ELECTION
    <party>                          <- "Democrat" / "Republican" / (blank)
    <OFFICE>      WITH N OF N PRECINCTS REPORTING
    Vote For  1
    <vertically-spelled candidate column headers>
    -----   -----   -----   -----      <- one dash group per vote column
    0001 001    <vote> <vote> ...      <- precinct rows
    ...
    CANDIDATE TOTALS   <totals>        <- county totals (verification)

A contest may span several printed pages; page breaks are form-feeds and each
page repeats the office header + vertical candidate headers + dash line.  There
is no `====` separator between pages of the same contest -- only after the
final CANDIDATE PERCENT line -- so each contest is exactly one block.

Candidate NAMES and column ORDER come from the summary PDF (cross-checked
against the report's CANDIDATE TOTALS rows, which match exactly).  The dash
line marks each vote column's fixed character position; precinct rows are
sliced by those spans so blank (0) cells are handled.

Per the user's instruction ("do the same" as the 2006 Linn primary, which did
all offices), every contest in the report is emitted.  Office naming follows
Linn's established convention (judge positions and commissioner seats embedded
in the office name with an empty district column; "Justice of the Peace" with
district "4").  Parties are the three-letter codes "DEM"/"REP"; nonpartisan
contests have a blank party (a known verifier false positive that CI accepts).

For contests where no candidate filed (Republican U.S. House, Attorney General,
State Representative 11th), the report renders a leading "NO CANDIDATE FILED"
column of zeros; that column is dropped so only Write-ins / Over / Under are
emitted, matching the existing Oregon convention.
"""

import csv
import re
import sys
from pathlib import Path

SRC = Path("/Users/dwillis/code/openelections-sources-or/2008/"
           "Linn OR may_20_2008_office_precinct_report.txt")
OUT = Path("2008/counties/20080520__or__primary__linn__precinct.csv")
COUNTY = "Linn"

# ---------------------------------------------------------------------------
# Contest definitions, in the exact order they appear in the report.
#
# Each spec carries: the report's office header text (for a sanity check),
# the canonical office name, district, party, the ordered candidate list, the
# trailing stat columns, and the expected county-level totals (candidates ++
# stats, in column order) taken from the summary PDF -- used only to verify
# the parsed precinct sums.  For no-candidate contests the leading "NO
# CANDIDATE FILED" report column is dropped before the totals are compared,
# so `totals` lists only the Write-in / Over / Under columns.
# ---------------------------------------------------------------------------
STD = ["Write-ins", "Over Votes", "Under Votes"]       # most contests
MEASURE_STATS = ["Over Votes", "Under Votes"]          # ballot measures

CONTESTS = [
    # ---- Democrat ----------------------------------------------------------
    dict(raw="United States President PRESIDENT OF THE UNITED STATES",
         office="President", district="", party="DEM",
         candidates=["Hillary Clinton", "Barack Obama"], stats=STD,
         totals=[7514, 7352, 184, 2, 473]),
    dict(raw="United States Senator", office="U.S. Senate", district="",
         party="DEM",
         candidates=["Pavel Goberman", "Jeff Merkley", "Roger S. Obrist",
                     "David Loera", "Candy Neville", "Steve Novick"], stats=STD,
         totals=[338, 5908, 339, 160, 1191, 4930, 85, 11, 2563]),
    dict(raw="UNITED STATES REPRESENTATIVE IN CONGRESS",
         office="U.S. House", district="4", party="DEM",
         candidates=["Peter A. DeFazio"], stats=STD,
         totals=[12885, 84, 0, 2556]),
    dict(raw="Secretary of State", office="Secretary of State", district="",
         party="DEM",
         candidates=["Kate Brown", "Rick Metsger", "Vicki L. Walker",
                     "Paul Damian Wells"], stats=STD,
         totals=[5688, 4838, 1709, 448, 57, 5, 2780]),
    dict(raw="State Treasurer", office="State Treasurer", district="",
         party="DEM", candidates=["Ben Westlund"], stats=STD,
         totals=[10069, 67, 0, 5389]),
    dict(raw="Attorney General", office="Attorney General", district="",
         party="DEM", candidates=["John R. Kroger", "Greg Macpherson"],
         stats=STD, totals=[6105, 6097, 27, 1, 3295]),
    dict(raw="State Senator, 9th District", office="State Senate", district="9",
         party="DEM", candidates=["Steven H. Frank", "Bob McDonald"], stats=STD,
         totals=[1630, 2227, 20, 1, 1905]),
    dict(raw="State Senator, 12th District", office="State Senate",
         district="12", party="DEM", candidates=["Kevin C. Nortness"],
         stats=STD, totals=[419, 9, 0, 308]),
    dict(raw="STATE REPRESENTATIVE, 11TH DISTRICT", office="State House",
         district="11", party="DEM", candidates=["Phil Barnhart"], stats=STD,
         totals=[1105, 9, 1, 626]),
    dict(raw="STATE REPRESENTATIVE, 15TH DISTRICT", office="State House",
         district="15", party="DEM", candidates=["Dick Olsen"], stats=STD,
         totals=[4674, 97, 0, 2494]),
    dict(raw="STATE REPRESENTATIVE, 16TH DISTRICT", office="State House",
         district="16", party="DEM", candidates=["Sara A. Gelser"], stats=STD,
         totals=[0, 0, 0, 0]),
    dict(raw="STATE REPRESENTATIVE, 17TH DISTRICT", office="State House",
         district="17", party="DEM", candidates=["Dan Thackaberry"], stats=STD,
         totals=[3769, 81, 3, 1930]),
    dict(raw="STATE REPRESENTATIVE, 23RD DISTRICT", office="State House",
         district="23", party="DEM", candidates=["Jason Brown", "Wesley West"],
         stats=STD, totals=[325, 148, 6, 1, 256]),
    dict(raw="County Commissioner, Position 2",
         office="County Commissioner, Position 2", district="", party="DEM",
         candidates=["Pete Boucot"], stats=STD, totals=[9240, 90, 1, 6194]),
    dict(raw="County Commissioner, Position 3",
         office="County Commissioner, Position 3", district="", party="DEM",
         candidates=["Gordon Kirbey, Jr."], stats=STD,
         totals=[9228, 109, 3, 6185]),

    # ---- Republican --------------------------------------------------------
    dict(raw="United States President PRESIDENT OF THE UNITED STATES",
         office="President", district="", party="REP",
         candidates=["John McCain", "Ron Paul"], stats=STD,
         totals=[9742, 1470, 442, 4, 599]),
    dict(raw="United States Senator", office="U.S. Senate", district="",
         party="REP", candidates=["Gordon H. Smith", "Gordon Leitch"],
         stats=STD, totals=[9454, 1862, 65, 2, 874]),
    dict(raw="UNITED STATES REPRESENTATIVE IN CONGRESS",
         office="U.S. House", district="4", party="REP",
         candidates=[], stats=STD, totals=[325, 0, 11932]),
    dict(raw="Secretary of State", office="Secretary of State", district="",
         party="REP", candidates=["Rick Dancer"], stats=STD,
         totals=[9004, 77, 1, 3175]),
    dict(raw="State Treasurer", office="State Treasurer", district="",
         party="REP", candidates=["Allen Alley"], stats=STD,
         totals=[7903, 62, 2, 4290]),
    dict(raw="Attorney General", office="Attorney General", district="",
         party="REP", candidates=[], stats=STD, totals=[279, 0, 11978]),
    dict(raw="State Senator, 9th District", office="State Senate", district="9",
         party="REP",
         candidates=["Herman Joseph Baurer", "Sarah Arcune", "Fred Girod"],
         stats=STD, totals=[243, 388, 3257, 12, 0, 773]),
    dict(raw="State Senator, 12th District", office="State Senate",
         district="12", party="REP", candidates=["Brian J. Boquist"],
         stats=STD, totals=[496, 0, 0, 220]),
    dict(raw="STATE REPRESENTATIVE, 11TH DISTRICT", office="State House",
         district="11", party="REP", candidates=[], stats=STD,
         totals=[27, 0, 1590]),
    dict(raw="STATE REPRESENTATIVE, 15TH DISTRICT", office="State House",
         district="15", party="REP", candidates=["Andy Olson"], stats=STD,
         totals=[4203, 25, 1, 1021]),
    dict(raw="STATE REPRESENTATIVE, 16TH DISTRICT", office="State House",
         district="16", party="REP", candidates=["Rockne Roll"], stats=STD,
         totals=[1, 0, 0, 0]),
    dict(raw="STATE REPRESENTATIVE, 17TH DISTRICT", office="State House",
         district="17", party="REP",
         candidates=["Sherrie Sprenger", "Bruce Cuff", "Marc Lucca",
                     "Cliff Wooten"], stats=STD,
         totals=[2122, 730, 498, 859, 5, 4, 455]),
    dict(raw="STATE REPRESENTATIVE, 23RD DISTRICT", office="State House",
         district="23", party="REP", candidates=["Jim Thompson", "Craig Pope"],
         stats=STD, totals=[343, 246, 2, 1, 124]),
    dict(raw="County Commissioner, Position 2",
         office="County Commissioner, Position 2", district="", party="REP",
         candidates=["Roger Nyquist"], stats=STD, totals=[8458, 105, 0, 3694]),
    dict(raw="County Commissioner, Position 3",
         office="County Commissioner, Position 3", district="", party="REP",
         candidates=["Michael P. Spasaro", "William C. (Will) Tucker"],
         stats=STD, totals=[3520, 5993, 34, 9, 2701]),

    # ---- Nonpartisan -------------------------------------------------------
    dict(raw="Judge of the Supreme Court, Position 1",
         office="Judge of the Supreme Court, Position 1", district="",
         party="", candidates=["Thomas A. Balmer"], stats=STD,
         totals=[17936, 171, 1, 13042]),
    dict(raw="Judge of the Court of Appeals, Pos. 1",
         office="Judge of the Court of Appeals, Position 1", district="",
         party="", candidates=["David Schuman"], stats=STD,
         totals=[17763, 158, 1, 13228]),
    dict(raw="Judge of the Court of Appeals, Pos. 2",
         office="Judge of the Court of Appeals, Position 2", district="",
         party="", candidates=["Walt Edmonds"], stats=STD,
         totals=[17459, 151, 2, 13538]),
    dict(raw="Judge of the Oregon Tax Court",
         office="Judge of the Oregon Tax Court", district="", party="",
         candidates=["Henry C. Breithaupt"], stats=STD,
         totals=[17414, 145, 2, 13589]),
    dict(raw="District Attorney, Linn County", office="District Attorney",
         district="", party="", candidates=["Jason Carlile"], stats=STD,
         totals=[19373, 224, 0, 11553]),
    dict(raw="County Assessor", office="County Assessor", district="",
         party="", candidates=["Mark J. Noakes"], stats=STD,
         totals=[19057, 121, 0, 11972]),
    dict(raw="County Surveyor", office="County Surveyor", district="",
         party="", candidates=["Charles W. Gibbs"], stats=STD,
         totals=[18123, 122, 3, 12902]),
    dict(raw="Justice of the Peace, District 4A",
         office="Justice of the Peace", district="4", party="",
         candidates=["Jad Lemhouse"], stats=STD,
         totals=[11738, 114, 0, 8716]),

    # ---- Ballot measures (Yes / No / Over / Under, no write-in) -------------
    dict(raw="51", office="Measure 51", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[20068, 7072, 14, 3996]),
    dict(raw="52", office="Measure 52", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[19876, 7112, 10, 4152]),
    dict(raw="53", office="Measure 53", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[12541, 14130, 15, 4464]),
    dict(raw="22-77 CITY OF ALBANY", office="Measure 22-77 City of Albany",
         district="", party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[5882, 3173, 3, 1524]),
    dict(raw="22-78 CITY OF ALBANY", office="Measure 22-78 City of Albany",
         district="", party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[5875, 3193, 2, 1512]),
    dict(raw="22-75 CITY OF SCIO", office="Measure 22-75 City of Scio",
         district="", party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[85, 83, 0, 7]),
    dict(raw="22-76 CITY OF LEBANON", office="Measure 22-76 City of Lebanon",
         district="", party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[2318, 1080, 0, 397]),
    dict(raw="24-245 CHEMEKETA COMMUNITY COLLEGE",
         office="Measure 24-245 Chemeketa Community College", district="",
         party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[465, 661, 0, 92]),
]

PRECINCT_RE = re.compile(r"^(\d{4}) (\d{3})\s+(.*)$")
OFFICE_RE = re.compile(r"^\s*(\S.+?)\s+WITH \d+ OF \d+ PRECINCTS REPORTING\s*$")
DASH_RE = re.compile(r"-{3,}")
SEP_RE = re.compile(r"^={5,}")


def normalize_text(s: str) -> str:
    """Collapse internal whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", s).strip()


def parse_report():
    """Return the list of contest blocks.

    Each block is a dict with the office header text and the list of raw
    lines belonging to the contest (between two `====` separators).  Contests
    that span several pages are a single block: page breaks are form-feeds,
    not `====` separators.
    """
    raw = SRC.read_bytes().decode("latin-1")
    # Normalize line endings (the file mixes CR, LF, CRLF); form-feeds are left
    # inline on the following page-header line, which is harmless.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    blocks = []
    cur = None
    for line in lines:
        if SEP_RE.match(line):
            if cur is not None:
                blocks.append(cur)
                cur = None
            continue
        m = OFFICE_RE.match(line)
        if m and cur is None:
            cur = {"office": normalize_text(m.group(1)), "lines": []}
        if cur is not None:
            cur["lines"].append(line.rstrip())
    if cur is not None:
        blocks.append(cur)
    return blocks


def dash_columns(block_lines):
    """Return the (start, end) character spans of each vote column from the
    first `-----` separator line in the block."""
    for line in block_lines:
        if DASH_RE.search(line) and not SEP_RE.match(line):
            starts = [m.start() for m in re.finditer(r"-{3,}", line)]
            if len(starts) >= 1:
                spans = []
                for i, s in enumerate(starts):
                    e = starts[i + 1] if i + 1 < len(starts) else len(line)
                    spans.append((s, e))
                return spans
    return []


def slice_row(line: str, spans):
    """Return the list of ints in each column span of `line` (blank -> 0)."""
    vals = []
    for s, e in spans:
        cell = line[s:e].strip()
        vals.append(int(cell) if cell else 0)
    return vals


def align(values, ncol):
    """Align a row to `ncol` columns: drop leading surplus columns (the "NO
    CANDIDATE FILED" zero column) and pad short rows with trailing zeros."""
    if len(values) > ncol:
        values = values[len(values) - ncol:]
    elif len(values) < ncol:
        values = values + [0] * (ncol - len(values))
    return values


def parse_block(block):
    """Return (precinct_rows, totals_row) for a contest block.

    `precinct_rows` is a list of (precinct_number:int, [vote ints]).
    `totals_row` is the list of column ints from CANDIDATE TOTALS, or None.
    """
    spans = dash_columns(block["lines"])
    precincts = []
    totals = None
    for line in block["lines"]:
        m = PRECINCT_RE.match(line)
        if m:
            precincts.append((int(m.group(2)), slice_row(line, spans)))
            continue
        if "CANDIDATE TOTAL" in line and spans:
            totals = slice_row(line, spans)
    return precincts, totals


def main():
    blocks = parse_report()

    # The first block is the STATISTICS section (no office header line); skip
    # it.  The remaining blocks are the contests, in order, matching CONTESTS.
    contest_blocks = [b for b in blocks if any(
        OFFICE_RE.match(l) for l in b["lines"])]

    if len(contest_blocks) != len(CONTESTS):
        print(f"WARN: {len(contest_blocks)} contest blocks found, "
              f"{len(CONTESTS)} expected", file=sys.stderr)

    rows = []
    all_precincts = set()
    warnings = 0

    for spec, block in zip(CONTESTS, contest_blocks):
        # Sanity check: the block's office header must match the spec.
        office_matches = any(
            normalize_text(l).upper().startswith(spec["raw"].upper())
            for l in block["lines"] if OFFICE_RE.match(l))
        if not office_matches:
            print(f"WARN: office mismatch -- expected '{spec['raw']}', "
                  f"block office='{block['office']}'", file=sys.stderr)
            warnings += 1

        precincts, totals = parse_block(block)
        columns = spec["candidates"] + spec["stats"]
        ncol = len(columns)

        # Drop the leading "NO CANDIDATE FILED" column / pad short rows.
        precincts = [(p, align(v, ncol)) for p, v in precincts]
        if totals is not None:
            totals = align(totals, ncol)

        # Verify county totals against the summary.
        if totals is not None:
            if len(totals) != ncol:
                print(f"WARN: {spec['office']} ({spec['party']}) totals have "
                      f"{len(totals)} cols, expected {ncol}: {totals}",
                      file=sys.stderr)
                warnings += 1
            elif totals != spec["totals"]:
                print(f"WARN: {spec['office']} ({spec['party']}) totals mismatch "
                      f"report {totals} vs summary {spec['totals']}",
                      file=sys.stderr)
                warnings += 1

        # Verify precinct sums equal the county totals.
        if totals is not None and len(totals) == ncol:
            sums = [0] * ncol
            for _, votes in precincts:
                if len(votes) != ncol:
                    print(f"WARN: {spec['office']} precinct vote count "
                          f"{len(votes)} != {ncol}", file=sys.stderr)
                    warnings += 1
                    continue
                for i, v in enumerate(votes):
                    sums[i] += v
            if sums != spec["totals"]:
                print(f"WARN: {spec['office']} ({spec['party']}) precinct sums "
                      f"{sums} != summary {spec['totals']}", file=sys.stderr)
                warnings += 1

        # Emit rows: one per precinct per candidate/stat, in column order.
        for prec_num, votes in precincts:
            all_precincts.add(prec_num)
            for ci, name in enumerate(columns):
                rows.append({
                    "county": COUNTY,
                    "precinct": str(prec_num),
                    "office": spec["office"],
                    "district": spec["district"],
                    "party": spec["party"],
                    "candidate": name,
                    "votes": str(votes[ci]),
                    "_pidx": prec_num,
                    "_cidx": ci,
                })

    # Sort: office, district, party, precinct (numeric), candidate column order.
    rows.sort(key=lambda r: (r["office"], r["district"], r["party"],
                             r["_pidx"], r["_cidx"]))
    for r in rows:
        del r["_pidx"]
        del r["_cidx"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["county", "precinct", "office",
                                          "district", "party", "candidate",
                                          "votes"])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUT}")
    print(f"  contests: {len(CONTESTS)}, precincts: {len(all_precincts)}, "
          f"warnings: {warnings}")


if __name__ == "__main__":
    main()