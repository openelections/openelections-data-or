#!/usr/bin/env python3
"""Rebuild the Benton County 2026 primary precinct CSV from the PaddleOCR cache.

The cached pages (.paddleocr_cache/Benton/p*.md) contain HTML tables, each
split into per-contest column blocks (one block per contest, side by side).
Known OCR quirks handled here:

  * Long candidate names split across two header cells
    ("Alkinson IV" + "James"), sometimes in reverse order.
  * A block's header row buried in the first precinct row (names where
    precinct 01's numbers should be) — the row is used as headers and its
    precinct's detail is simply absent from the page.
  * Yes/No measure headers shifted or blank — measures are mapped
    positionally to [Yes, No, Total Votes Cast, Overvotes, Undervotes,
    Contest Total].
  * Continuation pages carrying only trailing statistic columns, sometimes
    with garbled headers — assigned positionally to the contest's missing
    statistic columns (Write-in Totals, Write-in: Not Assigned, Total Votes
    Cast, Overvotes, Undervotes, Contest Total, in document order).
  * Phantom duplicate columns (e.g. a second "Overvotes" of all zeros) —
    positionally-filled columns have lower confidence than header-labeled
    ones, so real values override them on conflict.

Validation (reported to stderr):
  * per precinct:  sum(candidate columns incl. Write-in Totals)
                   == Total Votes Cast
  * per precinct:  Total Votes Cast + Overvotes + Undervotes == Contest Total
  * per column:    sum over precincts == the contest's Totals row

Itemized "Write-in: Name" columns are not emitted; the Write-in Totals
column is emitted as "Write-ins".
"""

import csv
import re
import sys
from collections import defaultdict
from difflib import get_close_matches
from pathlib import Path

from bs4 import BeautifulSoup

CACHE = Path(".paddleocr_cache/Benton")
OUT = Path("2026/counties/20260519__or__primary__benton__precinct.csv")

PRECINCT_RE = re.compile(r"^\d{1,3}$")
NUM_RE = re.compile(r"^-?[\d,]+$")

# normalized (letters/digits only) header text -> canonical display name;
# "__SPLIT__" marks cells that must merge with a neighboring header cell.
NAME_OVERRIDES = {
    "atkinsonivjames": "James Atkinson IV",
    "alkinsonivjames": "James Atkinson IV",
    "alkinsoniv": "__SPLIT__",
    "dallymple": "__SPLIT__",
    "dallymplehopea": "Hope A Dalrymple",
    "sheppard": "Tristan Sheppard",
    "neuman": "Robert Neuman",
    "gomberg": "David Gomberg",
    "pauljromerojr": "Paul J Romero DeAngelo Jr",
    "deangeloleroyturner": "Leroy Turner",
}

# PaddleOCR misread cells on p020's REP Governor grid (an extra cell was
# inserted after Peters on rows 18-20, and Peters' 2 votes on row 17 landed
# under Romero). Values below come from the county's official "Precinct
# Results by Contest" report (2026/sources/2026May19-benton-precinct-results.pdf,
# REP Governor section, page 19), keyed by precinct then canonical name.
GOV_R_CELL_FIXES = {
    "17": {"Brad T Peters": 2, "Paul J Romero DeAngelo Jr": 0},
    "18": {"Paul J Romero DeAngelo Jr": 3, "Wen Waddell": 0},
    "19": {"Wen Waddell": 0, "Leroy Turner": 1},
    "20": {"Wen Waddell": 0},
}

STAT_ORDER = [
    "writein_totals", "writein_not_assigned", "total_votes_cast",
    "overvotes", "undervotes", "contest_total",
]

# Blocks whose header row is missing/garbled beyond repair: manual per-column
# labels, keyed by (page, title prefix). Tokens: "cand:Name", any stat key,
# "skip", or "assigned" (itemized write-in column).
MANUAL_LABELS = {
    ("p030.md", "REP State Representative, 15"): [
        "cand:Shelly Boshart Davis", "writein_totals", "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total", "skip",
    ],
    ("p012.md", "DEM State Representative, 15"): [
        "cand:Joanna Robinson", "writein_totals", "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total",
    ],
    # p016's header row garbled candidate names from column 4 onward
    # ("McAlmond", "Russell Perkins", "Jo Rae", ...); the data is aligned
    # exactly like p015 (verified: p015+p016 column sums == Totals row).
    ("p016.md", "REP US Senator"): [
        "cand:Brent Barker", "cand:Deborah C Brown", "cand:David A Burch",
        "cand:Russell McAlmond", "cand:Jo Rae Perkins", "cand:Timothy Skelton",
        "cand:David Brock Smith", "writein_totals", "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total", "skip",
    ],
    # p011 senate block: headers shifted so that "Write-in: Not Cast"/"Cast"/
    # "Total Votes" land one column early; col 8 is a phantom seen only in the
    # Totals row. Verified: 13+0+5=18, Totals 10775+1+1940=12716.
    ("p011.md", "DEM State Senator, 8"): [
        "cand:Sara Gelser Blouin", "writein_totals", "skip",
        "total_votes_cast", "overvotes", "undervotes", "contest_total", "skip",
    ],
    # p023 REP State Senator, 8: headers carry literal "\n" line breaks
    # ("Valerie Draper\nWoldeit", "Assigned\nWrite-in: Not\nAssigned",
    # "Total Votes\nCast") that stat_label can't normalize, so the candidate
    # and two stat columns leaked as bogus candidates. Verified against the
    # official report: Draper Woldeit totals 3,059 (== county CSV);
    # Write-in Totals == Assigned Write-in: Not Assigned per precinct.
    ("p023.md", "REP State Senator, 8"): [
        "cand:Valerie Draper Woldeit", "writein_totals", "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total",
    ],
    # p011 HD-10 block: leading "Contest Total" header is really Gomberg's
    # column; the Totals row lost its first cell (shift right by one).
    # Verified: Totals 1686+1+599=2286.
    ("p011.md", "DEM State Representative, 10"): [
        "TOTALS_SHIFT_RIGHT",
        "cand:David Gomberg", "writein_totals", "skip", "total_votes_cast",
        "overvotes", "undervotes", "contest_total", "skip",
    ],
    # p012 HD-16 block: precinct rows 01-09 lost their first cell (values sit
    # one column right, Contest Total absent); rows 12+ and Totals are normal.
    ("p012.md", "DEM State Representative, 16"): [
        "SHIFT_IF_FIRST_EMPTY",
        "cand:Sarah Finger McDonald", "writein_totals", "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total",
    ],
    # p013: "Write-in: Not Cast" through "Contest Total" headers each sit one
    # column left of their data. Verified: 677+680+3=1360, col9 empty.
    ("p013.md", "DEM County Commissioner"): [
        "cand:Pat Malone", "cand:John Wilson", "writein_totals",
        "writein_not_assigned",
        "total_votes_cast", "overvotes", "undervotes", "contest_total", "skip",
    ],
    # p022: "Total Votes"/Over/Under/CT headers shifted one column left;
    # col 7 is a phantom. Verified: Totals 7131+3+213=7347.
    ("p022.md", "REP Governor"): [
        "writein_totals", "skip", "total_votes_cast", "overvotes",
        "undervotes", "contest_total", "skip",
    ],
    # Measure 2-143: every data row lost its first cell (values sit one
    # column right, Contest Total absent); the Totals row is normal.
    # Verified: 1819+379=2198; Totals 12846+1+1607=14454.
    ("p094.md", "2-143"): [
        "SHIFT_IF_FIRST_EMPTY",
        "cand:Yes", "cand:No", "total_votes_cast", "overvotes", "undervotes",
        "contest_total",
    ],
}

MEASURE_TEMPLATE = {
    6: ["Yes", "No", "total_votes_cast", "overvotes", "undervotes", "contest_total"],
    5: ["Yes", "No", "total_votes_cast", "overvotes", "undervotes"],
    4: ["Yes", "No", "total_votes_cast", "overvotes"],
}


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def stat_label(text: str):
    """Map an OCR'd pseudo-column header to a canonical stat key (or None)."""
    n = norm_key(text)
    if not n:
        return None
    if n.startswith("writein"):
        if "totals" in n:
            return "writein_totals"
        # "Write-in: Not Assigned", "Write-in: Not Cast", and the bare
        # "Assigned"/"Write-in: Assigned" roll-ups all sit in the same
        # position between Write-in Totals and Total Votes Cast; in these
        # abstracts their values equal Write-in Totals, so they validate
        # identically under the wna slot.
        if "notassigned" in n or "notcast" in n or n == "writeinassigned":
            return "writein_not_assigned"
        return "writein_assigned"  # itemized "Write-in: Name" column
    fixed = {
        "totalvotescast": "total_votes_cast",
        "totalvotes": "total_votes_cast",
        "contesttotal": "contest_total",
        "overvotes": "overvotes",
        "ovenotes": "overvotes",
        "ovenvotes": "overvotes",
        "overtoves": "overvotes",
        "undervotes": "undervotes",
        "nocandidatefiled": "no_candidate_filed",
        "assigned": "writein_not_assigned",
    }
    return fixed.get(n)


def expand_table(html_text: str):
    """Expand an OCR'd HTML table into a rectangular grid of strings."""
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    grid = []
    spans = {}  # col -> (text, remaining_rows) for the NEXT row
    for tr in table.find_all("tr"):
        row = []
        c = 0
        for td in tr.find_all(["td", "th"]):
            while c in spans:
                text, left = spans[c]
                row.append(text)
                if left > 1:
                    spans[c] = (text, left - 1)
                else:
                    del spans[c]
                c += 1
            text = td.get_text(strip=True)
            colspan = int(td.get("colspan", 1))
            rowspan = int(td.get("rowspan", 1))
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    spans[c] = (text, rowspan - 1)
                c += 1
        while c in spans:  # trailing rowspan cells
            text, left = spans[c]
            row.append(text)
            if left > 1:
                spans[c] = (text, left - 1)
            else:
                del spans[c]
            c += 1
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    return [r + [""] * (width - len(r)) for r in grid]


def segment_blocks(grid):
    """Yield (start_col, end_col, title) per contest block using row 0 titles."""
    if not grid:
        return []
    titles = [c.strip() for c in grid[0]]
    runs = []
    cur, start = "", 1
    for c in range(1, len(titles)):
        if titles[c] != cur:
            if cur:
                runs.append((start, c - 1, cur))
            cur, start = titles[c], c
    if cur:
        runs.append((start, len(titles) - 1, cur))
    return runs


class Block:
    def __init__(self, page, title, start, end):
        self.page = page
        self.title = title
        self.start = start
        self.end = end
        self.headers = []
        self.header_unusable = False
        self.rows = []       # (precinct, [int|None])
        self.totals = None   # [int|None]
        self.totals_shift_right = False   # Totals row lost its first cell
        self.shift_if_first_empty = False  # rows with an empty first cell shifted right
        self.skip_totals = False           # Totals row fails its own identity


def parse_block(grid, page, title, start, end):
    blk = Block(page, title, start, end)
    data_start = None
    for i, row in enumerate(grid):
        first = row[0].strip()
        if PRECINCT_RE.match(first) or first.lower() == "totals":
            data_start = i
            break
    if data_start is None:
        return blk

    def value_cells(row):
        return [row[c].strip() for c in range(start, end + 1)]


    # Header row normally sits just above the first data row, but on some
    # pages the first precinct row IS the header row (names in place of
    # precinct 01's numbers).
    header_row = grid[data_start - 1] if data_start > 0 else None
    first_vals = value_cells(grid[data_start])
    nonnum = sum(1 for v in first_vals if v and not NUM_RE.match(v))
    if first_vals and nonnum >= max(1, (len(first_vals) + 1) // 2):
        header_row = grid[data_start]
        data_start += 1
    if header_row is not None:
        blk.headers = [header_row[c].strip() for c in range(start, end + 1)]
        # A header row made of filler text means the OCR lost the real one.
        filler = sum(1 for h in blk.headers
                     if re.search(r"precincts|vote\s*for", h, re.I))
        if blk.headers and filler >= max(1, len(blk.headers) // 2):
            blk.header_unusable = True
    else:
        blk.headers = [""] * (end - start + 1)
        blk.header_unusable = True

    for row in grid[data_start:]:
        first = row[0].strip()
        if not (PRECINCT_RE.match(first) or first.lower() == "totals"):
            continue
        vals = []
        for c in range(start, end + 1):
            v = row[c].strip().replace(",", "")
            if v == "":
                vals.append(None)
            elif re.fullmatch(r"-?\d+", v):
                vals.append(int(v))
            else:
                vals.append(None)
                print(f"WARN {page} {title} p{first}: non-numeric cell {row[c]!r}",
                      file=sys.stderr)
        if first.lower() == "totals":
            blk.totals = vals
        else:
            blk.rows.append((first, vals))
    return blk


def parse_cache_blocks():
    blocks = []
    for page in sorted(CACHE.glob("p*.md")):
        text = page.read_text()
        for html_part in re.split(r"(?=<table)", text):
            if "<table" not in html_part:
                continue
            grid = expand_table(html_part)
            for start, end, title in segment_blocks(grid):
                blk = parse_block(grid, page.name, title, start, end)
                if blk.rows or blk.totals is not None or blk.headers:
                    blocks.append(blk)
    return blocks


def contest_meta(title: str):
    """Return (party, office, district) for a block title, or None for stats."""
    t = title.strip()
    party = ""
    m = re.match(r"^(DEM|REP)\s+", t)
    if m:
        party = {"DEM": "D", "REP": "R"}[m.group(1)]
        t = t[m.end():]
    m = re.match(r"State Measure\s+(\d+)", t)
    if m:
        return ("", f"Measure {m.group(1)}", "")
    m = re.match(r"(\d+-\d+)\s", t)
    if m:
        return ("", f"Measure {m.group(1)}", "")
    m = re.match(r"(\d+-\d+)$", t)
    if m:
        return ("", f"Measure {m.group(1)}", "")
    if t.startswith("US Senator"):
        return (party, "U.S. Senate", "")
    m = re.match(r"US Representative,\s*(\d+)(?:st|nd|rd|th) District", t)
    if m:
        return (party, "U.S. House", m.group(1))
    if t.startswith("Governor"):
        return (party, "Governor", "")
    m = re.match(r"State Senator,\s*(\d+)(?:st|nd|rd|th) District", t)
    if m:
        return (party, "State Senate", m.group(1))
    m = re.match(r"State Representative,\s*(\d+)(?:st|nd|rd|th) District", t)
    if m:
        return (party, "State House", m.group(1))
    if t.startswith("County Commissioner"):
        suffix = t.split(",", 1)[1].strip() if "," in t else ""
        return (party, "County Commissioner", suffix)
    if t.startswith("Commissioner of the Bureau of Labor"):
        return (party, "Labor Commissioner", "")
    if t.startswith("Judge of the"):
        office, _, suffix = t.partition(",")
        return (party, office.strip(), suffix.strip())
    if t.startswith("STATISTICS"):
        return None
    return ("", t, "")


class Contest:
    def __init__(self, party, office, district):
        self.party = party
        self.office = office
        self.district = district
        self.carousel = False             # no-candidate-filed write-in carousel
        self.candidates = []                 # canonical display names in order
        self.canon = {}                      # norm_key -> canonical name
        self.cells = defaultdict(dict)       # precinct -> key -> (votes, conf)
        self.assigned = defaultdict(int)     # precinct -> sum of itemized write-ins
        self.totals_by_key = {}
        self.covered_stats = set()           # header-labeled stats seen so far

    def canonical(self, header: str) -> str:
        merged = norm_key(header)
        if merged in NAME_OVERRIDES and NAME_OVERRIDES[merged] != "__SPLIT__":
            name = NAME_OVERRIDES[merged]
            nk = norm_key(name)
            if nk not in self.canon:
                self.canon[nk] = name
                if name not in self.candidates:
                    self.candidates.append(name)
            return self.canon[nk]
        if merged in self.canon:
            return self.canon[merged]
        # Fuzzy match on sorted token sets against known canonical names.
        candidates = {" ".join(sorted(c.lower().split())): c for c in self.candidates}
        probe = " ".join(sorted(header.lower().split()))
        close = get_close_matches(probe, candidates, n=1, cutoff=0.8) if candidates else []
        if close:
            name = candidates[close[0]]
            self.canon[merged] = name
            return name
        name = header.strip()
        self.canon[merged] = name
        self.candidates.append(name)
        return name

    def set_cell(self, precinct, key, value, conf, where):
        if value is None:
            return
        cur = self.cells[precinct].get(key)
        if cur is None or conf > cur[1]:
            if cur is not None and cur[0] != value:
                print(f"CONFLICT {self.office}/{self.district}/{self.party} "
                      f"p{precinct} {key}: keep {value} over {cur[0]} ({where})",
                      file=sys.stderr)
            self.cells[precinct][key] = (value, conf)
        elif cur[0] != value:
            print(f"CONFLICT {self.office}/{self.district}/{self.party} "
                  f"p{precinct} {key}: keep {cur[0]} over {value} ({where})",
                  file=sys.stderr)


def merge_split_headers(headers):
    """Merge adjacent header cells marked as halves of one split name.

    A "__SPLIT__" override always marks the FIRST fragment of a name split
    across two cells, so merging only ever looks forward from the marked
    cell itself.
    """
    out = []
    i = 0
    while i < len(headers):
        h = headers[i]
        if NAME_OVERRIDES.get(norm_key(h)) == "__SPLIT__" and \
                i + 1 < len(headers) and headers[i + 1]:
            merged = h + " " + headers[i + 1]
            if norm_key(merged) in NAME_OVERRIDES:
                merged = NAME_OVERRIDES[norm_key(merged)]
            out.append(merged)
            i += 2
            continue
        out.append(h)
        i += 1
    return out


def label_block(blk: Block, contest: Contest, is_measure: bool):
    """Return per-column (kind, key, conf); kind in cand|stat|assigned|skip."""
    width = blk.end - blk.start + 1

    for (page, title_prefix), manual in MANUAL_LABELS.items():
        if blk.page == page and blk.title.startswith(title_prefix):
            tokens = list(manual)
            if tokens and tokens[0] == "TOTALS_SHIFT_RIGHT":
                blk.totals_shift_right = True
                tokens.pop(0)
            elif tokens and tokens[0] == "SHIFT_IF_FIRST_EMPTY":
                blk.shift_if_first_empty = True
                tokens.pop(0)
            if len(tokens) != width:
                print(f"WARN manual labels for {page} {blk.title}: "
                      f"{len(tokens)} vs width {width}", file=sys.stderr)
                return None
            return [manual_token(t, contest) for t in tokens]

    if contest.carousel:
        return carousel_labels(blk, contest, width)

    if is_measure:
        template = MEASURE_TEMPLATE.get(width)
        if template is None:
            if width < 4:
                print(f"WARN {blk.page} {blk.title}: measure width {width}",
                      file=sys.stderr)
                return None
            template = MEASURE_TEMPLATE[6] + ["skip"] * (width - 6)
        return [("skip", "extra", 0) if t == "skip"
                else ("cand", contest.canonical(t), 0) if t in ("Yes", "No")
                else ("stat", t, 0) for t in template]

    if blk.header_unusable:
        # All columns are itemized write-ins we only aggregate; names lost.
        print(f"WARN {blk.page} {blk.title}: header row lost, treating "
              f"{width} columns as itemized write-ins", file=sys.stderr)
        return [("assigned", f"{blk.page}:{i}", 0) for i in range(width)]

    headers = merge_split_headers(blk.headers)
    if len(headers) != width:
        # A split name can leave one header fewer than columns; the final
        # column's header is then missing — fill it positionally (conf 0).
        if len(headers) == width - 1:
            headers.append("")
        else:
            print(f"WARN {blk.page} {blk.title}: {len(headers)} headers vs "
                  f"width {width}", file=sys.stderr)
            return None
    labels = []
    seen_stats = set()
    for h in headers:
        sk = stat_label(h)
        if sk is not None:
            if sk == "writein_assigned":
                labels.append(("assigned", h, 1))
            elif sk == "no_candidate_filed":
                labels.append(("skip", sk, 1))
            elif sk in seen_stats:
                labels.append(("skip", "phantom", 0))  # duplicate stat column
            else:
                seen_stats.add(sk)
                labels.append(("stat", sk, 1))
        elif h and not NUM_RE.match(h.replace(",", "")):
            labels.append(("cand", contest.canonical(h), 1))
        else:
            labels.append((None, None, 0))  # empty or stray numeric header
    contest.covered_stats |= seen_stats
    # Fill unlabeled columns from the contest's not-yet-seen stats, in order.
    missing = [s for s in STAT_ORDER if s not in contest.covered_stats]
    pool = list(missing) + ["unlabeled"] * len(labels)
    it = iter(pool)
    labels = [l if l[0] is not None else ("stat", next(it), 0) for l in labels]
    return labels


def carousel_labels(blk: Block, contest: Contest, width: int):
    """Label a block of a no-candidate-filed (write-in carousel) contest.

    Every non-statistic column is an itemized "Write-in: Name" column, even
    when OCR dropped the prefix.  Statistic columns are kept only when the
    block's rows satisfy TV+Over+Under=CT wherever all four are populated;
    some carousel pages have shifted stat headers that fail this identity.
    """
    real_stats = {"total_votes_cast", "overvotes", "undervotes", "contest_total"}
    labels = []
    seen = set()
    for i in range(width):
        h = blk.headers[i] if i < len(blk.headers) else ""
        sk = stat_label(h) if h else None
        if sk in STAT_ORDER and sk not in seen and sk != "writein_not_assigned":
            seen.add(sk)
            labels.append(("stat", sk, 1))
        else:
            labels.append(("assigned", f"{blk.page}:{i}", 1))

    idx = {k: i for i, (kind, k, _) in enumerate(labels)
           if kind == "stat" and k in real_stats}
    if {"total_votes_cast", "contest_total"} <= idx.keys():
        checked = 0
        for _, vals in blk.rows:
            row = {k: vals[i] for k, i in idx.items() if i < len(vals)}
            if any(row.get(k) is None for k in
                   ("total_votes_cast", "overvotes", "undervotes", "contest_total")):
                continue
            checked += 1
            if (row["total_votes_cast"] + row["overvotes"] + row["undervotes"]
                    != row["contest_total"]):
                checked = -1
                break
        if checked <= 0:
            print(f"WARN {blk.page} {blk.title}: carousel stat columns fail "
                  "the TV+Over+Under=CT identity; dropping them", file=sys.stderr)
            labels = [("skip", "untrusted_stat", 1)
                      if kind == "stat" and k in real_stats else (kind, k, c)
                      for kind, k, c in labels]
        elif blk.totals is not None:
            # Rows pass but the Totals row can itself be shifted; if its own
            # identity fails, keep the row values but don't record Totals.
            idx2 = {k: i for i, (kind, k, _) in enumerate(labels)
                    if kind == "stat" and k in real_stats}
            row = {k: blk.totals[i] for k, i in idx2.items()
                   if i < len(blk.totals)}
            if not any(v is None for v in row.values()) and \
                    len(row) == 4 and \
                    row["total_votes_cast"] + row["overvotes"] + \
                    row["undervotes"] != row["contest_total"]:
                print(f"WARN {blk.page} {blk.title}: carousel Totals row "
                      "fails the identity; ignoring it", file=sys.stderr)
                blk.skip_totals = True
    contest.covered_stats |= {k for kind, k, _ in labels if kind == "stat"}
    return labels


def manual_token(tok: str, contest: Contest):
    if tok.startswith("cand:"):
        return ("cand", contest.canonical(tok[5:]), 1)
    if tok == "skip":
        return ("skip", "manual", 1)
    if tok == "assigned":
        return ("assigned", "manual", 1)
    contest.covered_stats.add(tok)
    return ("stat", tok, 1)


def assemble(blocks):
    # Pre-scan: contests with any "No Candidate Filed" column are write-in
    # carousels; every non-statistic column in their blocks is an itemized
    # write-in name.
    carousel_meta = set()
    for blk in blocks:
        meta = contest_meta(blk.title)
        if meta is None:
            continue
        if any(stat_label(h) == "no_candidate_filed" for h in blk.headers if h):
            carousel_meta.add(meta)

    contests = {}
    order = []
    stats_blocks = []
    for blk in blocks:
        meta = contest_meta(blk.title)
        if meta is None:
            if blk.rows:
                stats_blocks.append(blk)
            continue
        if not blk.rows and blk.totals is None:
            continue
        if meta not in contests:
            contests[meta] = Contest(*meta)
            contests[meta].carousel = meta in carousel_meta
            order.append(meta)
        contest = contests[meta]
        labels = label_block(blk, contest, meta[1].startswith("Measure "))
        if labels is None:
            continue
        shifted_labels = None
        if blk.shift_if_first_empty:
            # rows whose first cell is empty sit one column to the right and
            # lack the final column
            shifted_labels = [("skip", "shifted", 1)] + labels[:-1]
        for precinct, vals in blk.rows:
            if all(v is None for v in vals):
                continue
            row_labels = labels
            if shifted_labels is not None and vals[0] is None:
                row_labels = shifted_labels
            if len(vals) != len(row_labels):
                print(f"WARN {blk.page} {blk.title} p{precinct}: "
                      f"{len(vals)} values vs {len(row_labels)} labels", file=sys.stderr)
                continue
            for (kind, key, conf), v in zip(row_labels, vals):
                if kind in ("skip",) or key == "unlabeled":
                    continue
                if kind == "assigned":
                    if v:
                        contest.assigned[precinct] += v
                    continue
                contest.set_cell(precinct, key, v, conf, blk.page)
        if blk.totals is not None:
            totals = blk.totals
            if blk.totals_shift_right:
                totals = [None] + totals[:-1]
            if len(totals) != len(labels):
                print(f"WARN {blk.page} {blk.title} Totals: "
                      f"{len(totals)} values vs {len(labels)} labels", file=sys.stderr)
            else:
                for (kind, key, conf), v in zip(labels, totals):
                    if blk.skip_totals:
                        break
                    if blk.shift_if_first_empty and key == "contest_total":
                        continue  # shifted rows lack CT, so the sum can't match
                    if contest.carousel and kind == "stat" and \
                            key != "writein_totals":
                        continue  # carousel Totals rows shift unpredictably
                    if kind in ("cand", "stat") and key != "unlabeled" and v is not None:
                        prev = contest.totals_by_key.get(key)
                        if prev is not None and prev != v:
                            print(f"CONFLICT-TOTAL {contest.office} {key}: "
                                  f"{prev} vs {v} ({blk.page})", file=sys.stderr)
                        else:
                            contest.totals_by_key[key] = v
    return [contests[k] for k in order], stats_blocks


def validate(contest):
    problems = []
    keys = contest.candidates + ["writein_totals"]
    for precinct, cells in sorted(contest.cells.items(), key=lambda kv: int(kv[0])):
        got = {k: v for k, (v, _) in cells.items()}
        cand_sum = sum(got.get(k, 0) for k in keys)
        tvc = got.get("total_votes_cast")
        if tvc is not None and cand_sum != tvc:
            problems.append(f"{contest.office}|{contest.district}|{contest.party} "
                            f"p{precinct}: candidates {cand_sum} != TotalCast {tvc}")
        over, under, ct = got.get("overvotes"), got.get("undervotes"), got.get("contest_total")
        if None not in (tvc, over, under, ct) and tvc + over + under != ct:
            problems.append(f"{contest.office}|{contest.district}|{contest.party} "
                            f"p{precinct}: {tvc}+{over}+{under} != ContestTotal {ct}")
    for key, total in contest.totals_by_key.items():
        colsum = sum(c.get(key, (0, 0))[0] for c in contest.cells.values())
        if colsum != total:
            problems.append(f"{contest.office}|{contest.district}|{contest.party} "
                            f"col {key}: sum {colsum} != Totals {total}")
    return problems


def statistics_rows(stats_blocks):
    wanted = [
        (re.compile(r"voters\s*-\s*total", re.I), "Registered Voters"),
        (re.compile(r"ballo?its\s*cast\s*-\s*tota", re.I), "Ballots Cast"),
        (re.compile(r"ballo?its\s*cast\s*-\s*blan", re.I), "Ballots Cast Blank"),
    ]
    out = {}
    totals_seen = {}
    for blk in stats_blocks:
        for idx, h in enumerate(blk.headers):
            for rx, office in wanted:
                if not rx.search(h):
                    continue
                for precinct, vals in blk.rows:
                    if idx < len(vals) and vals[idx] is not None:
                        prev = out.get((precinct, office))
                        if prev is not None and prev != vals[idx]:
                            print(f"CONFLICT STAT {office} p{precinct}: "
                                  f"{prev} vs {vals[idx]} ({blk.page})", file=sys.stderr)
                        else:
                            out[(precinct, office)] = vals[idx]
                break
    return [dict(county="Benton", precinct=p, office=o, district="", party="",
                 candidate=o, votes=str(v))
            for (p, o), v in sorted(out.items(), key=lambda kv: (kv[0][1], int(kv[0][0])))]


def contest_rows(contest):
    out = []
    precincts = sorted(contest.cells, key=int)
    for precinct in precincts:
        cells = {k: v for k, (v, _) in contest.cells[precinct].items()}
        for cand in contest.candidates:
            if cand in cells:
                out.append((precinct, cand, cells[cand]))
        wit = cells.get("writein_totals")
        if wit is None:
            wit = contest.assigned.get(precinct) or None
        if wit:
            out.append((precinct, "Write-ins", wit))
        for sk, name in (("overvotes", "Over Votes"), ("undervotes", "Under Votes")):
            if cells.get(sk):
                out.append((precinct, name, cells[sk]))
    return [dict(county="Benton", precinct=p, office=contest.office,
                 district=contest.district, party=contest.party,
                 candidate=c, votes=str(v)) for p, c, v in out]


def main():
    blocks = parse_cache_blocks()
    contests, stats_blocks = assemble(blocks)
    # Apply official-value cell fixes before validation re-confirms the rows.
    for contest in contests:
        if contest.office == "Governor" and contest.party == "R":
            for precinct, fixes in GOV_R_CELL_FIXES.items():
                for cand, votes in fixes.items():
                    contest.cells[precinct][cand] = (votes, 1)
    problems = []
    for contest in contests:
        problems.extend(validate(contest))
    rows = statistics_rows(stats_blocks)
    for contest in contests:
        rows.extend(contest_rows(contest))
    with OUT.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["county", "precinct", "office", "district",
                           "party", "candidate", "votes"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")
    if problems:
        print(f"\n{len(problems)} validation problems:", file=sys.stderr)
        for p in problems[:100]:
            print("  " + p, file=sys.stderr)
        if len(problems) > 100:
            print(f"  ... and {len(problems) - 100} more", file=sys.stderr)
    else:
        print("All validation checks passed.")


if __name__ == "__main__":
    main()
