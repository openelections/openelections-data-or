#!/usr/bin/env python3
"""Build the Linn County 2006 primary precinct CSV from the text-based
"office precinct report" canvass.

Source:
  /Users/dwillis/code/openelections-sources-or/2006/
      Linn OR may_16_2006_office_precinct_report.txt

This is a fixed-width plain-text report (the county's official canvass),
NOT an image PDF, so no OCR is required.  Each contest is laid out as:

    NAME HEADING CANVASS ... LINN COUNTY, OREGON ... FINAL AND OFFICIAL
    PRIMARY ELECTION
    <party>                          <- "Democrat" / "Republican" / (blank)
    ...
    <OFFICE>      WITH N OF N PRECINCTS REPORTING
    Vote For  1
    <vertically-spelled candidate column headers>
    -----   -----   -----   -----      <- one dash group per vote column
    0001 001    <vote> <vote> ...      <- precinct rows (precinct code then votes)
    ...
    CANDIDATE TOTALS   <totals>        <- county totals (verification)

The candidate column headers are spelled vertically (one letter per line),
which is awkward to parse, so candidate NAMES and their column ORDER come
from the text-based summary PDF (cross-checked against this report's
CANDIDATE TOTALS rows, which match exactly).  The dash-group line marks
each vote column's fixed character position; precinct rows are sliced by
those ranges so that blank cells (a 0 rendered as blank space) are handled
correctly.

Per the user's instruction ("do all offices"), every contest in the report
is emitted -- not just the partisan statewide subset that the existing 2006
Oregon primary files contain.  Office naming follows the established Oregon
convention (judge positions and commissioner seats embedded in the office
name with an empty district column; "Commissioner of the Bureau of Labor
and Industries"; "County Sheriff").  Precincts are unpadded integers and
parties are the three-letter codes used by the existing 2006 Linn general
file ("DEM"/"REP"; blank for nonpartisan contests).
"""

import csv
import re
import sys
from pathlib import Path

SRC = Path("/Users/dwillis/code/openelections-sources-or/2006/"
            "Linn OR may_16_2006_office_precinct_report.txt")
OUT = Path("2006/20060516__or__primary__linn__precinct.csv")
COUNTY = "Linn"

# ---------------------------------------------------------------------------
# Contest definitions, in the exact order they appear in the report.
#
# Each spec carries: the report's office header text (for a sanity check),
# the canonical office name, district, party, the ordered candidate list,
# the trailing stat columns, and the expected county-level totals
# (candidates ++ stats, in column order) taken from the summary PDF -- these
# are used only to verify the parsed precinct sums.
# ---------------------------------------------------------------------------
STD = ["Write-ins", "Over Votes", "Under Votes"]   # most contests
MEASURE_STATS = ["Over Votes", "Under Votes"]       # ballot measures (no write-in)

CONTESTS = [
    # ---- Democrat ----------------------------------------------------------
    dict(raw="UNITED STATES CONGRESS REPRESENTATIVE 4TH DISTRICT",
         office="U.S. House", district="4", party="DEM",
         candidates=["Peter A. DeFazio"], stats=STD,
         totals=[8363, 84, 4, 1224]),
    dict(raw="GOVERNOR", office="Governor", district="", party="DEM",
         candidates=["Pete Sorenson", "Ted Kulongoski", "Jim Hill"], stats=STD,
         totals=[1717, 4257, 2956, 144, 6, 595]),
    dict(raw="STATE SENATOR, 8TH DISTRICT", office="State Senate", district="8",
         party="DEM", candidates=["Mario E. Magaña"], stats=STD,
         totals=[2590, 46, 2, 1828]),
    dict(raw="STATE SENATOR, 6TH DISTRICT", office="State Senate", district="6",
         party="DEM", candidates=["Bill Morrisette"], stats=STD,
         totals=[641, 13, 0, 381]),
    dict(raw="STATE REPRESENTATIVE, 11TH DISTRICT", office="State House",
         district="11", party="DEM", candidates=["Phil Barnhart"], stats=STD,
         totals=[653, 8, 0, 374]),
    dict(raw="STATE REPRESENTATIVE, 15TH DISTRICT", office="State House",
         district="15", party="DEM", candidates=["Sam H. W. Sappington"],
         stats=STD, totals=[2581, 43, 0, 1842]),
    dict(raw="STATE REPRESENTATIVE, 16TH DISTRICT", office="State House",
         district="16", party="DEM", candidates=["Sara A. Gelser"], stats=STD,
         totals=[0, 0, 0, 0]),
    dict(raw="STATE REPRESENTATIVE, 17TH DISTRICT", office="State House",
         district="17", party="DEM", candidates=["Dan Thackaberry"], stats=STD,
         totals=[2317, 47, 0, 1312]),
    dict(raw="STATE REPRESENTATIVE, 23RD DISTRICT", office="State House",
         district="23", party="DEM", candidates=["Jason Brown"], stats=STD,
         totals=[270, 2, 0, 226]),
    dict(raw="COUNTY COMMISSIONER, POSITION 1", office="County Commissioner, Position 1",
         district="", party="DEM", candidates=["Glenda Fleming"], stats=STD,
         totals=[5756, 96, 1, 3822]),

    # ---- Republican --------------------------------------------------------
    dict(raw="UNITED STATES CONGRESS REPRESENTATIVE 4TH DISTRICT",
         office="U.S. House", district="4", party="REP",
         candidates=["Jim Feldkamp", "Monica Johnson"], stats=STD,
         totals=[7018, 1769, 68, 5, 2706]),
    dict(raw="GOVERNOR", office="Governor", district="", party="REP",
         candidates=["Kevin Mannix", "W. Ames Curtright", "David W. Beem",
                     "Bob Leonard Forthan", "Jason A. Atkinson", "Ron Saxton",
                     "William E. Spidal", "Gordon Leitch"], stats=STD,
         totals=[3165, 281, 65, 33, 1672, 5347, 89, 112, 81, 19, 702]),
    dict(raw="STATE SENATOR, 8TH DISTRICT", office="State Senate", district="8",
         party="REP", candidates=["Frank Morse"], stats=STD,
         totals=[3471, 122, 2, 1548]),
    dict(raw="STATE SENATOR, 6TH DISTRICT", office="State Senate", district="6",
         party="REP", candidates=["Renee Lindsey"], stats=STD,
         totals=[787, 9, 0, 628]),
    dict(raw="STATE REPRESENTATIVE, 11TH DISTRICT", office="State House",
         district="11", party="REP", candidates=[], stats=STD,
         totals=[46, 0, 1378]),                  # no candidate filed
    dict(raw="STATE REPRESENTATIVE, 15TH DISTRICT", office="State House",
         district="15", party="REP", candidates=["Andy Olson"], stats=STD,
         totals=[4126, 29, 0, 986]),
    dict(raw="STATE REPRESENTATIVE, 16TH DISTRICT", office="State House",
         district="16", party="REP", candidates=["Robin M. Brown"], stats=STD,
         totals=[2, 0, 0, 0]),
    dict(raw="STATE REPRESENTATIVE, 17TH DISTRICT", office="State House",
         district="17", party="REP", candidates=["Jeff Kropf"], stats=STD,
         totals=[3517, 43, 0, 701]),
    dict(raw="STATE REPRESENTATIVE, 23RD DISTRICT", office="State House",
         district="23", party="REP", candidates=["Brian Boquist"], stats=STD,
         totals=[462, 3, 0, 273]),
    dict(raw="COUNTY COMMISSIONER, POSITION 1", office="County Commissioner, Position 1",
         district="", party="REP", candidates=["Bill Tacy", "John Lindsey"],
         stats=STD, totals=[2249, 6802, 42, 4, 2469]),

    # ---- Nonpartisan -------------------------------------------------------
    dict(raw="COMMISSIONER OF THE BUREAU OF LABOR AND INDUSTRIES",
         office="Commissioner of the Bureau of Labor and Industries",
         district="", party="", candidates=["Dan Gardner"], stats=STD,
         totals=[13988, 138, 1, 10838]),
    dict(raw="SUPERINTENDENT OF PUBLIC INSTRUCTION",
         office="Superintendent of Public Instruction", district="", party="",
         candidates=["Susan Castillo", "Deborah L. Andrews"], stats=STD,
         totals=[10605, 8338, 96, 9, 5917]),
    dict(raw="JUDGE OF THE SUPREME COURT, POSITION 6",
         office="Judge of the Supreme Court, Position 6", district="", party="",
         candidates=["Jack Roberts", "Virginia L. Linder", "W. Eugene (Gene) Hallman"],
         stats=STD, totals=[9569, 5748, 3698, 41, 16, 5893]),
    dict(raw="JUDGE OF THE SUPREME COURT, POSITION 2",
         office="Judge of the Supreme Court, Position 2", district="", party="",
         candidates=["Paul De Muniz"], stats=STD,
         totals=[13473, 114, 2, 11376]),
    dict(raw="JUDGE OF THE SUPREME COURT, POSITION 3",
         office="Judge of the Supreme Court, Position 3", district="", party="",
         candidates=["Robert D. (Skip) Durham"], stats=STD,
         totals=[13298, 96, 1, 11570]),
    dict(raw="JUDGE OF THE COURT OF APPEALS, POS. 5",
         office="Judge of the Court of Appeals, Position 5", district="", party="",
         candidates=["Rick Haselton"], stats=STD,
         totals=[13464, 92, 1, 11408]),
    dict(raw="JUDGE OF THE COURT OF APPEALS, POS. 6",
         office="Judge of the Court of Appeals, Position 6", district="", party="",
         candidates=["David V. Brewer"], stats=STD,
         totals=[13317, 89, 1, 11558]),
    dict(raw="JUDGE OF THE COURT OF APPEALS, POS. 8",
         office="Judge of the Court of Appeals, Position 8", district="", party="",
         candidates=["Jack L. Landau"], stats=STD,
         totals=[13256, 92, 4, 11613]),
    dict(raw="JUDGE OF THE COURT OF APPEALS, POS. 10",
         office="Judge of the Court of Appeals, Position 10", district="", party="",
         candidates=["Rex Armstrong"], stats=STD,
         totals=[13021, 85, 1, 11858]),
    dict(raw="JUDGE OF THE CIRC. CT., DIST. 23, POS. 2",
         office="Judge of the Circuit Court, 23rd District, Position 2",
         district="", party="", candidates=["Rick J. McCormick"], stats=STD,
         totals=[14398, 114, 0, 10453]),
    dict(raw="JUDGE OF THE CIRC. CT., DIST. 23, POS. 3",
         office="Judge of the Circuit Court, 23rd District, Position 3",
         district="", party="", candidates=["Daniel R. Murphy"], stats=STD,
         totals=[13949, 98, 1, 10917]),
    dict(raw="JUDGE OF THE CIRC. CT., DIST. 23, POS. 4",
         office="Judge of the Circuit Court, 23rd District, Position 4",
         district="", party="", candidates=["John A. McCormick"], stats=STD,
         totals=[14335, 100, 0, 10530]),
    dict(raw="JUDGE OF THE CIRC. CT., DIST. 23, POS. 5",
         office="Judge of the Circuit Court, 23rd District, Position 5",
         district="", party="", candidates=["Glen D. Baisinger"], stats=STD,
         totals=[14352, 113, 0, 10500]),
    dict(raw="COUNTY SHERIFF", office="County Sheriff", district="", party="",
         candidates=["Tim Mueller", "Michael P. Spasaro", "Keith Leopard"],
         stats=STD, totals=[17697, 4476, 753, 60, 5, 1974]),
    dict(raw="Directors PROPOSED EAGLE PARK & REC DIST",
         office="Proposed Eagle Park & Rec District, Director", district="",
         party="",
         candidates=["Jack McClure", "Pat McKibben", "Roger Raven",
                     "Tim E. Bunnell", "Susanne Isom", "Jim Gilligan"],
         stats=STD, totals=[323, 372, 299, 280, 401, 298, 154, 25, 1928]),

    # ---- Ballot measures (Yes / No / Over / Under, no write-in) ------------
    dict(raw="22-52 PROPOSED EAGLE PARK & REC DIST",
         office="Measure 22-52 Proposed Eagle Park & Rec District", district="",
         party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[173, 624, 0, 19]),
    dict(raw="22-53 GREATER ALBANY SCHOOL DISTRICT #8J",
         office="Measure 22-53 Greater Albany School District #8J", district="",
         party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[5836, 4424, 3, 663]),
    dict(raw="22-54 CITY OF SWEET HOME",
         office="Measure 22-54 City of Sweet Home", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[1003, 749, 3, 47]),
    dict(raw="22-55 CITY OF SWEET HOME",
         office="Measure 22-55 City of Sweet Home", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[1095, 658, 1, 48]),
    dict(raw="22-56 CITY OF TANGENT",
         office="Measure 22-56 City of Tangent", district="", party="",
         candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[208, 92, 0, 15]),
    dict(raw="22-57 LEBANON RURAL FIRE PROTECTION DISTRICT",
         office="Measure 22-57 Lebanon Rural Fire Protection District",
         district="", party="", candidates=["Yes", "No"], stats=MEASURE_STATS,
         totals=[3068, 2520, 3, 539]),
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
    lines belonging to the contest (between two `====` separators).
    """
    raw = SRC.read_bytes().decode("latin-1")
    # Normalize line endings (the file mixes CR, LF, CRLF).
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
    first `-----` separator line in the block.  Each vote column is the
    region from one dash group's start to the next dash group's start (or to
    the end of the line for the last column), so right-aligned values are
    captured cleanly even when blank (0) cells leave the column empty.
    """
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

    # The first block is the STATISTICS section (no "WITH ... PRECINCTS
    # REPORTING" office line); skip it.  The remaining blocks are the
    # contests, in order, matching CONTESTS.
    contest_blocks = [b for b in blocks if OFFICE_RE.match(
        (b["lines"][0] if b["lines"] else "")) or any(
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
            for l in block["lines"]
            if OFFICE_RE.match(l))
        if not office_matches:
            print(f"WARN: office mismatch -- expected '{spec['raw']}', "
                  f"block office='{block['office']}'", file=sys.stderr)
            warnings += 1

        precincts, totals = parse_block(block)
        columns = spec["candidates"] + spec["stats"]
        ncol = len(columns)

        # Verify county totals against the summary.
        if totals is not None:
            if len(totals) != ncol:
                print(f"WARN: {spec['office']} ({spec['party']}) totals have "
                      f"{len(totals)} cols, expected {ncol}: {totals}",
                      file=sys.stderr)
                warnings += 1
            elif totals != spec["totals"]:
                print(f"WARN: {spec['office']} ({spec['party']}) totals mismatch "
                      f"report {totals} vs summary {spec['totals']}", file=sys.stderr)
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
            if len(votes) < ncol:
                votes = votes + [0] * (ncol - len(votes))
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