#!/usr/bin/env python3
"""Parse Marion County 2026 primary precinct results from PaddleOCR cache HTML.

Marion's certified report is a Hart-style "Statewide Canvass Results" document
(one section per contest, one row per precinct, one column per candidate).
The 503-page scanned PDF is OCRed in chunks with the cloud PaddleOCR-VL
service (see _marion_ocr_chunks.py; cache: .paddleocr_cache/Marion/pNNN.md).

Known cloud-OCR failure modes this parser works around:
- ~190 pages come back with tables replaced by chart-box image references.
  Most of those are duplicate/PCP/local-measure pages; the county-contest
  pages that matter are re-read with local PaddleOCR via marion_local_ocr.py.
- Contest headings lose their first character ("tate Senator", "overnor"),
  run together ("Position 14-Year Term"), hide inside <div> tags, or vanish
  entirely.  Heading parsing therefore retries without the first character,
  strips HTML, and a signature check reassigns headingless tables whose
  candidate columns match a different contest in the county totals file.
- On the State House 18 Democrat write-in appendix (pages 63-76) the cloud
  zeroed the precinct column; those precincts are zipped in from local OCR.

Data-row alignment is verified with the identity
sum(candidate votes) == cast votes (undervotes/overvotes are excluded from
the canvass "Cast Votes" column); the candidate span is located once per
table by voting that identity across rows, so phantom columns and mangled
header cells cannot shift it.

Usage:
    uv run python src/parsers/2026_primary_marion_parser.py
"""

import csv
import html
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    align_candidates_to_county,
    make_row,
    normalize_party,
    output_path,
    write_csv,
)

COUNTY = "Marion"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"
OCR_DIR = Path(".paddleocr_cache/Marion")

# Pages whose tables are rebuilt entirely from local PaddleOCR (see
# marion_local_ocr.py) because the cloud mangled them.
LOCAL_GRID_PAGES = {
    13, 14, 25, 46, 48, 56, 64, 65, 66, 67, 68, 70, 71, 72, 73, 74, 75,
    76, 78, 88, 169, 180, 181,
}
# Local-grid cells the OCR misread (page -> {wrong cell text: fix}).  p025's
# cloud table lost its first data row, shifting every value up one precinct;
# the local grid is correct (per-precinct Registered Voters match the clean
# countywide pages) except for three cells: precinct 690 read as '069', and
# in rows 545/589 a misread Cast Votes ('76' should be 79) and an unread
# Cast/Undervotes pair ('L'/'' should be 7/3, from VBM = cast+under+over).
LOCAL_GRID_CELL_FIXES = {
    25: {"069": "690", "76": "79", "L": "7", "": "3"},
}
# Cloud header cells the OCR misread (page -> {wrong label: fix}).  p127's
# candidate header lost the space in "T O'Connor"; the mangled name reached
# align_candidates_to_county as a second candidate, which the count-matched
# pairing then matched to the county's Write-ins row by vote total, moving
# p127's whole O'Connor column (4649 votes) onto Write-ins.
HEADER_LABEL_FIXES = {
    127: {"Ryan T'O'Connor": "Ryan T O'Connor"},
}
# The State House 18 Democrat write-in appendix: no declared candidates ran,
# so every vote is a write-in, split by name group across two parts.  Pages
# 63-69 list the same 21 precincts (505-698) - one page per name group, with
# page 69 carrying the Cast/Undervotes/Overvotes statistics - and pages 70-76
# repeat the name groups for precinct 837 alone.  The cloud OCR mangles all
# of these pages, so they are read locally and emitted by a dedicated routine
# (emit_hd18d_appendix) rather than the generic table path.
HD18D_APPENDIX = range(63, 77)


def fix_cloud_grid(page: int, grid: List[List[str]]) -> List[List[str]]:
    """Repairs for cloud tables that read correctly except for specific rows.

    p117 (Labor Commissioner, page 5 of the contest): the cloud moved
    precinct 696's values into a row labelled 'Precinct' and left 696's own
    row blank.  The values are 696's - its Registered Voters (209) matches
    the clean countywide pages - so relabel that row and drop the blank one.
    """
    if page != 117:
        return grid
    fixed = []
    for r in grid:
        if r and str(r[0]).strip() == "Precinct":
            r = ["696"] + list(r[1:])
        elif r and str(r[0]).strip() == "696" and not any(
            str(c).strip() for c in r[1:]
        ):
            continue
        fixed.append(r)
    return fixed
# Pages where the cloud table's values are usable but its precinct column is
# mangled (blank cells, '0'-zeroed cells, or whole rows shifted left so the
# first candidate's value sits in the precinct column).  The precincts are
# zipped in row-by-row from local OCR (see parse_table_rows).
PRECINCT_ZIP_PAGES = {
    1, 28, 33, 37, 41, 59, 62, 89, 95, 97, 101, 109, 110, 115,
    121, 139, 143, 145, 147, 149, 150,
}
# Zip pages whose local-OCR precinct sequence is itself unreliable (a rotated
# render misreads the column).  Every countywide contest paginates identically
# (21/21/21/21/21/14), so the sequence can be taken from any countywide
# page 3; and House 17's Democrat and Republican tables list the same
# precincts, so p059/p062 borrow the column of their intact twin page
# (p061/p060, read straight from the cloud cache).
PRECINCT_SEQ_OVERRIDES = {
    59: [324, 325, 333, 343, 351, 353, 363, 645, 655, 674, 677, 678,
         679, 680, 682, 686, 687, 688, 689, 691, 692],
    62: [694, 696, 705, 715, 716, 781, 783, 786, 787, 789, 790,
         905, 915, 925, 935, 981],
    115: [545, 555, 577, 579, 580, 581, 582, 583, 584, 586, 587, 588,
          589, 590, 591, 592, 593, 594, 615, 625, 635],
    121: [545, 555, 577, 579, 580, 581, 582, 583, 584, 586, 587, 588,
          589, 590, 591, 592, 593, 594, 615, 625, 635],
    # p150 (Circuit Pos 2 page 2): the cloud misread the precinct column
    # (351-355 -> 51-55, 361-363 -> 561-563).  The true page-2 sequence is
    # taken from p144, CoA Pos 13's clean page 2.
    150: [351, 352, 353, 354, 355, 361, 362, 363, 370, 371, 372,
          401, 402, 403, 404, 405, 406, 505, 515, 525, 535],
}
# Labor Commissioner pages: the contest heading was lost entirely and the
# preceding context (County Commissioner Pos 2 R) would mislabel them.  The
# contest spans p113-118 only; p119-124 are the Supreme Court Position 4
# (Christopher L. Garrett) tables, whose headings are read via local OCR.
LABOR_PAGES = set(range(113, 119))

# Statistics columns that are never candidates.
STAT_LABELS = {
    "precinct", "cast votes", "undervotes", "overvotes", "undervoted",
    "overvoted", "vote by mail ballots cast", "total ballots cast",
    "registered voters", "turnout percentage", "total ballots",
    "total votes cast", "ballots cast", "cast", "",
}

TERM_RE = re.compile(r"\s*\d+\s*-?\s*Year\s*Term", re.IGNORECASE)
PARTY_TAIL_RE = re.compile(r"\s*-\s*(Democrat|Republican|NonPartisan)\s*$",
                           re.IGNORECASE)
POSITION_RE = re.compile(r"Position\s+(\d+)\b", re.IGNORECASE)
USREP_RE = re.compile(r"US\s+Rep\w*,?\s*(\d+)\w*\s*District", re.IGNORECASE)
STATE_RE = re.compile(r"State\s+(Senator|Representative),?\s*(\d+)\w*\s*District",
                      re.IGNORECASE)
CIRCUIT_RE = re.compile(r"(\d+)\w*\s*District,\s*Position\s*(\d+)?", re.IGNORECASE)
PCP_RE = re.compile(r"^Precinct\s+(\d+)\b", re.IGNORECASE)
MEASURE_RE = re.compile(r"(?:State\s+)?Measure\s+([\d-]+)", re.IGNORECASE)
LOCAL_MEASURE_RE = re.compile(r"^(\d{1,2}-\d{1,3})\b")
COMMISH_RE = re.compile(r"County\s+Commissioner,?\s*Position\s*(\d+)", re.IGNORECASE)
CITY_RE = re.compile(r"City\s+of\s+Salem,?\s*(Mayor|Municipal\s+Judge|Councilor)",
                     re.IGNORECASE)
WARD_RE = re.compile(r"Ward\s+(\d+)\b", re.IGNORECASE)

Contest = Tuple[str, str, str]  # (office, district, party)


# The cloud OCR routinely drops the leading letter of a heading ("tate
# Senator", "overnor", "udge of"); restore the known openers.  (The second
# attempt below then handles other leading-character quirks.)
_LEADING_FIXES = (
    ("tate ", "State "),
    ("overnor", "Governor"),
    ("udge of", "Judge of"),
    ("istrict Attorney", "District Attorney"),
    ("Recinct ", "Precinct "),
)


def parse_contest_heading(text: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (office, district, party, pcp_precinct) for a canvass heading."""
    raw = text.strip()
    for bad, good in _LEADING_FIXES:
        if raw.startswith(bad):
            raw = good + raw[len(bad):]
            break
    for attempt in (raw, raw[1:]):
        if not attempt:
            continue
        got = _try_heading(attempt)
        if got:
            return got
    return None


def _try_heading(text: str) -> Optional[Tuple[str, str, str, str]]:
    text = text.strip()
    party = ""
    pm = PARTY_TAIL_RE.search(text)
    if pm:
        tail = pm.group(1).lower()
        party = "" if tail == "nonpartisan" else normalize_party(pm.group(1))
        text = text[: pm.start()].strip()
    # "Position 26-Year Term" is Position 2 followed by a run-together
    # 6-Year Term (terms are only 4 or 6 years): split them before either
    # regex below can misread the digits.
    text = re.sub(
        r"Position\s+(\d+)([46])(?=\s*-?\s*Year)",
        r"Position \1 \2", text, flags=re.IGNORECASE,
    )
    # Capture the position before stripping the term: "Position 14-Year Term"
    # is Position 1 followed by a run-together 4-Year Term.
    posm = POSITION_RE.search(text)
    pos = posm.group(1) if posm else ""
    text = TERM_RE.sub("", text).strip(" -,:")
    if not text:
        return None

    district = ""
    pcp = ""

    if re.match(r"US\s+Senator\b", text, re.I):
        office = "U.S. Senate"
    elif re.match(r"US\s+Rep", text, re.I):
        office = "U.S. House"
        m = USREP_RE.search(text)
        district = m.group(1) if m else ""
    elif re.match(r"Governor\b", text, re.I):
        office = "Governor"
    elif STATE_RE.match(text):
        kind, num = STATE_RE.match(text).groups()
        office = "State Senate" if kind.lower() == "senator" else "State House"
        district = num
    elif COMMISH_RE.search(text):
        office = "County Commissioner"
        district = COMMISH_RE.search(text).group(1)
    elif CITY_RE.search(text):
        what = re.sub(r"\s+", " ", CITY_RE.search(text).group(1)).lower()
        if what == "mayor":
            office = "Mayor"
        elif what == "municipal judge":
            office = "Municipal Judge"
        else:
            office = "City Council"
            w = WARD_RE.search(text)
            district = w.group(1) if w else ""
    elif re.search(r"Labor\s+Commissioner", text, re.I):
        office = "Labor Commissioner"
    elif re.match(r"District\s+Attorney\b", text, re.I):
        office = "District Attorney"
    elif re.search(r"Supreme\s+Court", text, re.I):
        office = "Judge of the Supreme Court"
        district = f"Position {pos}" if pos else ""
    elif re.search(r"Court\s+of\s+Appeals", text, re.I):
        office = "Judge of the Court of Appeals"
        district = f"Position {pos}" if pos else ""
    elif re.search(r"Circuit\s+Court", text, re.I):
        office = "Judge of the Circuit Court"
        cm = CIRCUIT_RE.search(text)
        if cm:
            n = int(cm.group(1))
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
            if 11 <= n <= 13:
                suffix = "th"
            # The position digit can be swallowed by the run-together term
            # ("Position 26-Year Term"); fall back to the position captured
            # before the term was stripped.
            district = f"{n}{suffix} District, Position {cm.group(2) or pos}"
    elif PCP_RE.match(text):
        office = "Precinct Committee Person"
        pcp = PCP_RE.match(text).group(1)
    else:
        mm = MEASURE_RE.search(text)
        if mm:
            office = f"Measure {mm.group(1)}"
        else:
            lm = LOCAL_MEASURE_RE.match(text)
            if lm and "-" in text[: text.index(" ") if " " in text else len(text)]:
                office = f"Measure {lm.group(1)}"
            else:
                return None
        party = ""
        return office, district, party, pcp

    return office, district, party, pcp


def clean_cell(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def expand_table(table_html: str) -> List[List[str]]:
    """Convert a <table> block into a grid with rowspan/colspan expanded."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I):
        cells = []
        for m in re.finditer(r"<td([^>]*)>(.*?)</td>", tr, re.S | re.I):
            attrs, body = m.group(1), m.group(2)
            rsm = re.search(r'rowspan\s*=\s*"?(\d+)', attrs, re.I)
            csm = re.search(r'colspan\s*=\s*"?(\d+)', attrs, re.I)
            cells.append(
                (
                    clean_cell(body),
                    int(rsm.group(1)) if rsm else 1,
                    int(csm.group(1)) if csm else 1,
                )
            )
        if cells:
            rows.append(cells)

    occupied: Dict[Tuple[int, int], str] = {}
    for ri, cells in enumerate(rows):
        ci = 0
        for text, rs, cs in cells:
            while (ri, ci) in occupied:
                ci += 1
            for dr in range(rs):
                for dc in range(cs):
                    occupied[(ri + dr, ci + dc)] = text
            ci += cs
    if not occupied:
        return []
    nrows = max(r for r, _ in occupied) + 1
    ncols = max(c for _, c in occupied) + 1
    return [
        [occupied.get((r, c), "") for c in range(ncols)] for r in range(nrows)
    ]


def _int(text: str) -> Optional[int]:
    if text is None:
        return None
    text = str(text).replace(",", "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _sum_identity(cells: List[str]) -> bool:
    """Does some integer prefix of these cells sum to a later cell - the
    canvass's sum(candidates) == cast relation (or an echo of it when the
    row is shifted)?  Used to tell a value-shifted col0 from a junk col0."""
    ints = [_int(c) for c in cells]
    for k in range(2, len(ints) - 2):
        if ints[k] is None:
            continue
        if all(v is not None for v in ints[:k]) and sum(ints[:k]) == ints[k]:
            return True
    return False


def _precinctish(text: str) -> bool:
    """Fuzzy match for a 'Precinct' header cell mangled by OCR."""
    t = re.sub(r"[^a-z]", "", str(text).lower())
    if not 4 <= len(t) <= 12:
        return False
    # longest common subsequence against "precinct"
    target = "precinct"
    dp = [0] * (len(target) + 1)
    for ch in t:
        ndp = dp[:]
        for j in range(len(target)):
            if ch == target[j]:
                ndp[j + 1] = dp[j] + 1
            else:
                ndp[j + 1] = max(ndp[j], dp[j + 1])
        dp = ndp
    return dp[-1] >= 5


def _header_index(grid: List[List[str]]) -> Optional[int]:
    """Find the table's header row: >=2 statistics labels or a fuzzy
    'Precinct' first cell (the appendix pages carry no statistics columns)."""
    for i, row in enumerate(grid):
        if not row or _int(row[0]) is not None:
            continue
        hints = sum(
            1 for c in row[1:] if c and str(c).lower().strip() in STAT_LABELS
        )
        if hints >= 2 or _precinctish(row[0]):
            return i
    return None


def _candidate_labels(header: List[str]) -> List[str]:
    """Return the candidate column labels from a header row (cell 0 omitted)."""
    labels = []
    for cell in header[1:]:
        low = str(cell).lower().strip()
        if low in STAT_LABELS or re.fullmatch(r"[\d,.]+%?", str(cell).strip()):
            continue
        # Fragments of mangled statistics headers ("ote ast", "urg") from
        # local OCR; real candidate labels carry at least 4 letters.  'ON' is
        # the vertical 'NO' label of a measure table, misread.
        if len(re.sub(r"[^a-z]", "", low)) < 4 and low not in ("yes", "no", "on"):
            continue
        labels.append(str(cell).strip())
    return labels


def _normalize_candidate(name: str) -> Optional[str]:
    low = name.lower().strip()
    if not low:
        return None
    if "write-in" in low or "write in" in low or low == "w":
        return "Write-ins"
    # Vertical 'NO' header on the local-measure pages, misread as 'ON'.
    if low == "on":
        return "NO"
    # Strip write-in markers like "(W)" / "(WI)" wherever they appear.
    name = re.sub(r"\s*\((?:W|WI)\)\s*", " ", name, flags=re.IGNORECASE).strip()
    # Add periods to bare middle initials.
    parts = [p + "." if re.fullmatch(r"[A-Z]", p) else p for p in name.split()]
    return " ".join(parts)


def _keep_label(cell: str) -> bool:
    """Is this merged header cell a plausible candidate label?"""
    low = str(cell).lower().strip()
    if not low or low in STAT_LABELS:
        return False
    if re.fullmatch(r"[\d,.]+%?", str(cell).strip()):
        return False
    # Fragments of mangled statistics headers ("ote ast", "urg") from
    # local OCR; real candidate labels carry at least 4 letters.
    if len(re.sub(r"[^a-z]", "", low)) < 4 and low not in ("yes", "no", "on"):
        return False
    return True


def parse_table_rows(
    grid: List[List[str]],
    contest: Contest,
    page: int,
    rows: Dict[Tuple[str, str, str, str, str], int],
    precinct_seq: Optional[List[str]] = None,
    fallback_precinct: str = "",
    zero_col_is_junk: bool = False,
) -> None:
    """Emit candidate rows (and over/under votes) for one contest table.

    The canvass layout is [precinct, candidates..., Cast, Under, Over, VBM,
    Total, Registered, Turnout] and satisfies sum(candidates) == cast.  The
    candidate span is detected once per table by voting across rows, so
    phantom columns and mangled header cells cannot shift it.
    """
    office, district, party = contest
    hidx = _header_index(grid)
    if hidx is None:
        print(f"NOTE p{page:03d}: no header row found for {office} {district} {party}")
        return
    body = [r for r in grid[hidx + 1 :] if r]

    if precinct_seq is not None:
        # The cloud OCR mangles the precinct column in several ways: blank
        # cells (p001), cells zeroed to '0' (the House 18 appendix), whole
        # rows shifted left so the first candidate's value sits in col0, or a
        # truncated/garbled precinct number left in col0 (p062, p115: the
        # rest of the row is fine but col0 is an extra cell that must be
        # dropped).  Rebuild each row against the local-OCR precinct sequence;
        # a per-page vote picks the mangle mode, so every row of a table is
        # rebuilt the same way.
        seq = [s for s in precinct_seq if str(s).isdigit()]
        targets = [
            r for r in body
            if len(r) >= 6
            and not _precinctish(r[0])
            and "total" not in str(r[0]).lower()
            and any(_int(c) is not None for c in r[1:5])
        ]
        if len(targets) != len(seq):
            print(
                f"NOTE p{page:03d}: precinct zip mismatch "
                f"({len(targets)} rows vs {len(seq)} precincts) - skipping page"
            )
            return
        # A value-shifted row keeps a candidate value in col0, so the
        # sum-prefix identity still holds with col0 present; a junk col0 adds
        # an extra cell and only holds with it removed.
        keep_votes = drop_votes = 0
        for r in targets:
            with_col, sans_col = _sum_identity(r), _sum_identity(r[1:])
            if with_col and not sans_col:
                keep_votes += 1
            elif sans_col and not with_col:
                drop_votes += 1
        junk_col0 = drop_votes > keep_votes or zero_col_is_junk
        rebuilt = []
        for k, r in enumerate(targets):
            c0 = str(r[0]).strip()
            if c0 and _int(c0) is not None and _int(c0) == _int(seq[k]):
                rebuilt.append([c0] + list(r[1:]))
            elif c0 == "" or junk_col0:
                rebuilt.append([str(seq[k])] + list(r[1:]))
            else:
                rebuilt.append([str(seq[k])] + list(r))
        data_rows = rebuilt
        first_data = next(
            (i for i, r in enumerate(body) if r is targets[0]), 0
        ) if targets else 0
    else:
        data_rows = [r for r in body if _int(r[0]) is not None]
        first_data = next(
            (i for i, r in enumerate(body) if _int(r[0]) is not None), len(body)
        )
    if not data_rows:
        return

    # Merge the header row with any continuation rows above the data (the
    # canvass prints two-line headers; OCR often splits them further).  Some
    # tables repeat the whole header line - dedupe repeated parts, and drop
    # bare numbers (OCR slips of the copy) so labels cannot double.
    header_rows = [grid[hidx]] + body[:first_data]
    ncol = max(len(r) for r in header_rows + data_rows)
    header = []
    for c in range(ncol):
        parts = []
        for r in header_rows:
            t = str(r[c]).strip() if c < len(r) else ""
            if not t or re.fullmatch(r"[\d,.]+%?", t) or t in parts:
                continue
            parts.append(t)
        header.append(" ".join(parts))

    # Some tables lost their 'Precinct' header cell, leaving every label one
    # column left of its data (the first candidate name labels the precinct
    # column).  The first data row is always a precinct number, so detect the
    # shift from it and slide the header right.
    if (
        data_rows and _int(data_rows[0][0]) is not None
        and header and header[0]
        and not _precinctish(header[0]) and _keep_label(header[0])
    ):
        header = [""] + header[:-1]
        print(f"NOTE p{page:03d}: header missing 'Precinct' cell - shifted right")

    fixes = HEADER_LABEL_FIXES.get(page, {})
    if fixes:
        header = [fixes.get(c, c) for c in header]

    # Table-level span detection: candidates occupy columns [t, s), cast at
    # s, undervotes at s+1, overvotes at s+2.  Vote on the REBUILT rows: the
    # zip prepends the precinct cell, so voting on the raw cloud rows would
    # find shifted pseudo-identities (cast+under+over == VBM) and emit
    # statistics labels as candidates.
    vote_rows = [
        r for r in data_rows if sum(1 for c in r if _int(c) is not None) >= 4
    ]
    best = None
    for t in (1, 2, 3, 4):
        for s in range(t + 1, ncol - 2):
            score = 0
            for r in vote_rows:
                vals = [_int(r[i]) if i < len(r) else None for i in range(t, s + 3)]
                if any(v is None for v in vals):
                    continue
                if sum(vals[: s - t]) == vals[s - t]:
                    score += 1
            if not score:
                continue
            kept = sum(1 for i in range(t, s) if _keep_label(header[i]))
            rank = (score, kept, -(s - t))
            if best is None or rank > best[0]:
                best = (rank, t, s)

    if best:
        _, t, s = best
        span = list(range(t, s))
    else:
        # No statistics columns (State House 18 D write-in appendix): take
        # the span from the kept header labels, keeping mangled columns
        # inside the span so their votes are not lost.
        pos = [i for i in range(1, ncol) if _keep_label(header[i])]
        if not pos:
            print(f"NOTE p{page:03d}: no candidate labels for {office} {district} {party}")
            return
        span = list(range(pos[0], pos[-1] + 1))
        s = None
    labels = [header[i] if i < len(header) else "" for i in span]

    # A 'Totals' row the cloud relabelled with a precinct number (p081, House
    # 20 D) carries values equal to the column sums of every other row and
    # would double the contest; drop it (a row of genuine zeroes is exempt -
    # inactive precincts really are all zero).
    if len(data_rows) >= 3 and span:
        spanned = [
            [_int(r[i]) if i < len(r) else None for i in span] for r in data_rows
        ]
        if all(all(v is not None for v in vals) for vals in spanned):
            sums = [sum(vals[i] for vals in spanned) for i in range(len(span))]
            keep = [
                j for j, vals in enumerate(spanned)
                if not any(vals) or not all(
                    2 * vals[i] == sums[i] for i in range(len(span))
                )
            ]
            if len(keep) < len(spanned):
                print(
                    f"NOTE p{page:03d}: dropped "
                    f"{len(spanned) - len(keep)} mislabelled totals row(s)"
                )
                data_rows = [data_rows[j] for j in keep]

    skipped = 0
    for row in data_rows:
        precinct = _int(row[0])
        if precinct is None:
            if fallback_precinct and office == "Precinct Committee Person":
                precinct = int(fallback_precinct)
            else:
                continue
        cands = [_int(row[i]) if i < len(row) else None for i in span]
        if any(c is None for c in cands):
            skipped += 1
            continue
        under = over = None
        if s is not None and s + 2 < len(row):
            under = _int(row[s + 1])
            over = _int(row[s + 2])
        for label, votes in zip(labels, cands):
            candidate = _normalize_candidate(label)
            if candidate:
                rows[(str(precinct), office, district, party, candidate)] += votes
        if under:
            rows[(str(precinct), office, district, party, "Under Votes")] += under
        if over:
            rows[(str(precinct), office, district, party, "Over Votes")] += over
    if skipped:
        print(
            f"NOTE p{page:03d}: {skipped} rows skipped with unreadable cells "
            f"({office} {district} {party})"
        )


def _norm_key(name: str) -> str:
    """Normalized candidate name for matching against county totals."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_county_contests() -> Dict[Tuple[str, str, str], set]:
    """Map (office, district, party) -> normalized candidate names from the
    county totals file; used to reassign headingless tables by signature."""
    contests: Dict[Tuple[str, str, str], set] = defaultdict(set)
    with open(COUNTY_CSV, newline="") as f:
        for rec in csv.DictReader(f):
            if rec["county"] != COUNTY:
                continue
            office = rec["office"]
            if office.startswith("Measure ") or office == "Precinct Committee Person":
                continue
            name = _norm_key(rec["candidate"])
            if name in {"writeins", "undervotes", "overvotes", "total",
                        "totalvotescast", "registeredvoters"}:
                continue
            key = (office, " ".join(rec["district"].split()), rec["party"])
            contests[key].add(name)
    return contests


def maybe_switch(
    grid: List[List[str]], contest: Contest, page: int,
    county_map: Dict[Tuple[str, str, str], set],
) -> Contest:
    """Reassign a headingless table to the contest whose candidates it lists."""
    office, district, party = contest
    hidx = _header_index(grid)
    if hidx is None:
        return contest
    labels = _candidate_labels(grid[hidx])
    if not labels:
        return contest
    low = {l.lower().strip() for l in labels}

    # The Marion report has one yes/no-only contest with no heading at all.
    if {"yes", "no"} <= low and not office.startswith("Measure "):
        print(f"NOTE p{page:03d}: yes/no table with no measure heading -> Measure 120")
        return ("Measure 120", "", "")

    if page in LABOR_PAGES:
        keys = {_norm_key(l) for l in labels}
        if any(k.startswith(("chrislynch", "christinastephenson")) for k in keys):
            return ("Labor Commissioner", "", "")
        print(f"NOTE p{page:03d}: labor page without Lynch/Stephenson labels: {labels}")
        return contest

    # Named write-in tables (State House 18 D appendix) must not be
    # reassigned by signature; rely on their own headings.  Every table
    # carries a generic 'Misc Write-in (W)' column, which does not count.
    if any(
        re.search(r"\((?:W|WI)\)\s*$", l, re.I)
        for l in labels
        if not re.fullmatch(r"misc\s*write[- ]?ins?\s*\((?:W|WI)\)", l, re.I)
    ):
        return contest

    keys = {_norm_key(l) for l in labels if _norm_key(l) not in
            {"writeins", "yes", "no", "undervotes", "overvotes"}
            and "writein" not in _norm_key(l)}

    def _hits(names: set) -> int:
        # Exact key match, or a near match: the cloud drops middle initials
        # ("Sierra Williams" for "Sierra H. Williams"), so also count a
        # high-similarity pairing.
        n = 0
        for k in keys:
            for name in names:
                if k == name or (min(len(k), len(name)) >= 6
                                  and SequenceMatcher(None, k, name).ratio()
                                  >= 0.8):
                    n += 1
                    break
        return n

    cur = (office, " ".join(str(district).split()), party)
    cur_score = _hits(county_map.get(cur, set()))
    best, best_score = None, 0
    for key, names in county_map.items():
        if key == cur:
            continue
        score = _hits(names)
        if score > best_score:
            best, best_score = key, score
    if best and cur_score == 0 and (
        best_score >= (2 if cur not in county_map else 1)
        # A table whose named candidates are all found in exactly one county
        # contest identifies it even when there is only one (unopposed
        # contests have a single candidate + write-ins).
        or (keys and best_score == len(keys))
    ):
        print(
            f"NOTE p{page:03d}: headingless table looks like {best} "
            f"(score {best_score}, was {cur}) - switching"
        )
        return best
    return contest


def _pages() -> List[int]:
    # Parse through page 186 only.  Everything after (write-in appendix,
    # PCP, duplicated sections) came back from the cloud OCR as image
    # placeholders with no contest tables, so nothing past 186 is parseable.
    return sorted(
        int(p.stem[1:]) for p in OCR_DIR.glob("p*.md")
        if int(p.stem[1:]) <= 186
    )


def _local_heading(page: int) -> Optional[Tuple[str, str, str, str]]:
    """Read a contest heading the cloud service dropped, via local OCR.

    Covers pages whose tables the cloud read fine but whose heading line was
    lost - without it the table inherits the previous page's contest.  The
    heading sits at the bottom of the banner block, above the table.
    """
    import marion_local_ocr as mlo

    for it in sorted(mlo.items_for(page), key=lambda i: (i["y1"] + i["y2"]) / 2):
        if (it["y1"] + it["y2"]) / 2 > 800:
            break
        # Reject recognition slips like '32-2' (a mangled table cell that
        # LOCAL_MEASURE_RE would otherwise read as a local measure number):
        # every real heading carries a proper word.
        if not re.search(r"[A-Za-z]{4}", it["text"]):
            continue
        got = parse_contest_heading(it["text"])
        if got:
            return got
    return None


def emit_hd18d_appendix(rows: Dict[Tuple[str, str, str, str, str], int]) -> None:
    """Emit the State House 18 Democrat write-in appendix from local OCR.

    The county abstract reports just two rows for this contest - the one
    write-in who cleared reporting, Roy (WI) Kaufmann (488), and a
    'Write-ins' line equal to the total write-in cast (675; the same
    double-count convention as the county's other (WI) rows) - so that is
    what gets emitted per precinct.  Precinct 615's Roy cell was unread by
    the OCR; the county total (488) minus the readable cells (485) pins it
    at 3.  Precinct 837 (part 2) wrote in nobody: cast 0, 7 undervotes.
    """
    import marion_local_ocr as mlo

    contest = ("State House", "18", "D")
    _, roy = mlo.build_grid(68)
    roy_i = next(
        i for i, c in enumerate(roy[0]) if str(c).strip() == "Roy Kaufman (W)"
    )
    _, stats = mlo.build_grid(69)
    cast_i = stats[0].index("Cast Votes")
    under_i = stats[0].index("Undervotes")

    for row in stats[1:]:
        precinct = str(row[0]).strip()
        if not precinct.isdigit():
            continue
        cast = _int(row[cast_i])
        if cast is None:
            print(f"NOTE p069: unreadable Cast Votes for precinct {precinct}")
            continue
        rows[(precinct, *contest, "Write-ins")] += cast
        under = _int(row[under_i])
        if under is not None:
            rows[(precinct, *contest, "Under Votes")] += under
        rows[(precinct, *contest, "Over Votes")] += 0

    for row in roy[1:]:
        precinct = str(row[0]).strip()
        if not precinct.isdigit():
            continue
        roy_votes = _int(row[roy_i]) if roy_i < len(row) else None
        if roy_votes is None:
            # Precinct 615's cell on p068 was unread; see the docstring.
            roy_votes = 3 if precinct == "615" else 0
        rows[(precinct, *contest, "Roy (WI) Kaufmann")] += roy_votes

    rows[("837", *contest, "Roy (WI) Kaufmann")] += 0
    rows[("837", *contest, "Write-ins")] += 0
    rows[("837", *contest, "Under Votes")] += 7
    rows[("837", *contest, "Over Votes")] += 0


def parse_pages() -> List[Dict[str, str]]:
    rows: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    county_map = load_county_contests()
    import marion_local_ocr

    office = district = party = ""
    pcp_precinct = ""
    for page in _pages():
        if page in HD18D_APPENDIX:
            # Handled wholesale by emit_hd18d_appendix below.
            continue
        precinct_seq = None
        if page in PRECINCT_ZIP_PAGES:
            precinct_seq = (
                [str(x) for x in PRECINCT_SEQ_OVERRIDES[page]]
                if page in PRECINCT_SEQ_OVERRIDES
                else marion_local_ocr.precincts_for(page)
            )

        if page in LOCAL_GRID_PAGES:
            head_lines, grid = marion_local_ocr.build_grid(page)
            fixes = LOCAL_GRID_CELL_FIXES.get(page, {})
            if fixes:
                grid = [
                    [fixes.get(c, c) for c in row] for row in grid
                ]
            for line in head_lines:
                got = parse_contest_heading(line)
                if got:
                    office, district, party, pcp_precinct = got
            grids = [grid] if grid and len(grid) > 1 else []
            contexts = [(office, district, party, pcp_precinct)] * len(grids)
        else:
            md = (OCR_DIR / f"p{page:03d}.md").read_text()
            # Contest headings sit outside the tables; carry the current
            # contest forward for continuation pages.
            pos = 0
            grids = []
            contexts = []
            saw_heading = False
            for m in re.finditer(r"<table\b.*?</table>", md, re.S | re.I):
                context = md[pos : m.start()]
                pos = m.end()
                for line in context.splitlines():
                    line = clean_cell(line)
                    if not line or "End of report" in line:
                        continue
                    got = parse_contest_heading(line)
                    if got:
                        office, district, party, pcp_precinct = got
                        saw_heading = True
                contexts.append((office, district, party, pcp_precinct))
                grids.append(fix_cloud_grid(page, expand_table(m.group(0))))
            # Tail text after the last table (a heading the OCR emitted after
            # its table carries to the next page; it cannot label this page's
            # tables, so it does not count as a found heading below).
            for line in md[pos:].splitlines():
                line = clean_cell(line)
                got = parse_contest_heading(line) if line else None
                if got:
                    office, district, party, pcp_precinct = got
            # The cloud service drops some headings entirely.  When a page
            # carries exactly one real contest table but no heading could be
            # read from its text, read the heading with local OCR; otherwise
            # the table silently inherits the previous page's contest.
            if not saw_heading and sum(1 for g in grids if len(g) >= 8) == 1:
                got = _local_heading(page)
                if got:
                    print(
                        f"NOTE p{page:03d}: heading recovered by local OCR -> "
                        f"{got[0]} {got[1]} {got[2]}"
                    )
                    office, district, party, pcp_precinct = got
                    contexts = [got] * len(contexts)

        for (o, d, p, pcp), grid in zip(contexts, grids):
            if not o or not grid:
                continue
            contest = maybe_switch(grid, (o, d, p), page, county_map)
            if contest != (o, d, p):
                o, d, p = contest
                office, district, party = contest
            parse_table_rows(grid, contest, page, rows, precinct_seq, pcp)

    emit_hd18d_appendix(rows)

    return [
        make_row(
            county=COUNTY,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in sorted(rows.items())
    ]


def fold_house18d_writeins(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The county file lists one named House 18 Democrat write-in (Roy (WI)
    Kaufmann) and lumps every other write-in into 'Write-ins'; mirror that
    here so the appendix's dozens of protest write-ins don't flood the CSV."""
    out = []
    for row in rows:
        if (
            row["office"] == "State House"
            and str(row["district"]) == "18"
            and row["party"] == "D"
            and row["candidate"] not in ("Write-ins", "Under Votes", "Over Votes")
            and "kaufmann" not in row["candidate"].lower()
        ):
            row = dict(row, candidate="Write-ins")
        out.append(row)
    # Re-aggregate folded rows.
    agg: Dict[Tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in out:
        key = (row["precinct"], row["office"], row["district"], row["party"],
               row["candidate"])
        agg[key] += int(row["votes"])
    return [
        make_row(county=COUNTY, precinct=k[0], office=k[1], district=k[2],
                 party=k[3], candidate=k[4], votes=v)
        for k, v in sorted(agg.items())
    ]


def compare_to_county(rows: List[Dict[str, str]]) -> None:
    ours: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    for row in rows:
        if row["candidate"] in ("Under Votes", "Over Votes"):
            continue
        key = (row["office"], " ".join(str(row["district"]).split()), row["party"],
               _norm_key(row["candidate"]))
        ours[key] += int(row["votes"])
    county: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    with open(COUNTY_CSV, newline="") as f:
        for rec in csv.DictReader(f):
            if rec["county"] != COUNTY:
                continue
            key = (rec["office"], " ".join(rec["district"].split()), rec["party"],
                   _norm_key(rec["candidate"]))
            county[key] += int(rec["votes"])

    bad = 0
    for key in sorted(county):
        cv, ov = county[key], ours.get(key, 0)
        if cv != ov:
            bad += 1
            print(f"MISMATCH {key}: county={cv} ours={ov}")
    print(f"county comparison: {len(county) - bad}/{len(county)} county rows match")
    local = sorted(
        k for k in ours
        if k not in county and not k[0].startswith("Measure ")
        and k[0] not in ("Precinct Committee Person", "County Commissioner",
                         "Mayor", "Municipal Judge", "City Council")
    )
    for key in local[:40]:
        print(f"OURS-ONLY {key}: {ours[key]}")
    if len(local) > 40:
        print(f"... and {len(local) - 40} more")


def main() -> None:
    rows = parse_pages()
    rows = align_candidates_to_county(rows, COUNTY_CSV, COUNTY, tolerance=5)
    rows = fold_house18d_writeins(rows)
    out = output_path(COUNTY)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")
    compare_to_county(rows)


if __name__ == "__main__":
    main()