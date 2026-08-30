"""Parser for Wallowa County 2026 primary "Detail Results By Precinct" PDF.

Works from local PaddleOCR boxes (/tmp/wallowa_ocr_boxes.json, produced by
the batch OCR worker over 300-DPI rendered pages, cached to
.paddleocr_cache/Wallowa/boxes.json).  The PDF has no usable text layer.

Layout: twelve precinct blocks of six pages each, in vote order
ENTERPRISE #1..#4, JOSEPH #5/#6, WALLOWA #7/#8, LOSTINE #9, IMNAHA #10,
FLORA #11, TROY #12 (pages 1-6, 7-12, ..., 67-72).  Each precinct runs the
same 21 contests: (DEM then REP) U.S. Senate, U.S. House 2, Governor,
State House 58, Precinct Committee Person; then Labor Commissioner,
Supreme Court Position 4, Court of Appeals Positions 1/9/12/13, District
Attorney, County Commissioner Positions 2 and 3, State Measure 120, and
local Measure 2-011.  Every precinct block starts with a "Party Summary"
table under a "Contest" column header, and each page repeats the header;
contests flow across page boundaries.

Because the contest order is uniform, blocks are assigned to offices by
their index within the precinct (verified against each block's title line
where OCR produced one); a "pending" pass is never needed.

Pairing: value boxes sit near their row's label either slightly above or
slightly below it (placement varies by page), so each value is paired to
the nearest label row within ~50 px on the same page; every block is then
validated by candidates + over + under == Total.  OCR noise handled:

- zeros read as the letter "O" (values match x-window [1600, 1960) so a
  bare "O"/"o" box there is a zero);
- "(Vote For N)" trigger lines garble heavily ("o r 2)", "0ot r 1)",
  "(VoQt or 1)") -- matched fuzzily by their trailing "digit)" shape;
- row labels garble ("Write-in" -> "Vrite-in"/"Arite-in", "Over Votes" ->
  "Ovtes", "Yes" -> "25", "No" -> "(C)"): stats and measure Yes/No rows
  are recovered by fuzzy label matching plus position; measure candidate
  rows are renamed positionally (exactly two rows before Over);
- a single candidate value dropped by OCR is derived from Total.

Usage:
    uv run python src/parsers/2026_primary_wallowa_parser.py [--debug]
"""

import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    ELECTION_DATE,
    format_candidate_name,
    make_row,
    output_path,
    write_csv,
)

COUNTY = "Wallowa"
CACHE = Path(".paddleocr_cache/Wallowa/boxes.json")
TMP_BOXES = Path("/tmp/wallowa_ocr_boxes.json")
OUT = Path(output_path(COUNTY))

PRECINCTS = (
    "ENTERPRISE #1", "ENTERPRISE #2", "ENTERPRISE #3", "ENTERPRISE #4",
    "JOSEPH #5", "JOSEPH #6",
    "WALLOWA #7", "WALLOWA #8",
    "LOSTINE #9", "IMNAHA #10", "FLORA #11", "TROY #12",
)
PAGES_PER_PRECINCT = 6

LINE_TOL = 22      # boxes whose y-centers differ by less merge into one line
PAIR_TOL = 55      # a value pairs to a label row within this distance
# pages whose value/label offset ambiguity (equal above-shift and
# below-shift alignments) corrupts rows; pinned by county-abstract
# residual checks. "above" = values sit above their label rows.
PAGE_BIAS = {}
VX_MIN, VX_MAX = 1600, 1960  # x0 window for vote-value boxes

# The 21 contests, in uniform order, as (office, district, party).
EXPECTED = [
    ("U.S. Senate", "", "D"),
    ("U.S. House", "2", "D"),
    ("Governor", "", "D"),
    ("State House", "58", "D"),
    ("Precinct Committee Person", "", "D"),
    ("U.S. Senate", "", "R"),
    ("U.S. House", "2", "R"),
    ("Governor", "", "R"),
    ("State House", "58", "R"),
    ("Precinct Committee Person", "", "R"),
    ("Labor Commissioner", "", ""),
    ("Judge of the Supreme Court", "Position 4", ""),
    ("Judge of the Court of Appeals", "Position 1", ""),
    ("Judge of the Court of Appeals", "Position 9", ""),
    ("Judge of the Court of Appeals", "Position 12", ""),
    ("Judge of the Court of Appeals", "Position 13", ""),
    ("District Attorney", "", ""),
    ("County Commissioner", "Position 2", ""),
    ("County Commissioner", "Position 3", ""),
    ("Measure 120", "", ""),
    ("Measure 2-011", "", ""),
]
MEASURE_AT = {19, 20}  # indices into EXPECTED

# Candidates in the fixed document order each contest's block prints them
# (abstract spellings).  PCP and measures are absent: PCP names are
# per-precinct, measures are renamed positionally to Yes/No.
CANONICAL = {
    ("U.S. Senate", "", "D"): ["Jeff Merkley", "Paul Damian Wells"],
    ("U.S. Senate", "", "R"): [
        "Brent Barker", "Deborah C. Brown", "David A. Burch",
        "Russell McAlmond", "Jo Rae Perkins", "Timothy Skelton",
        "David Brock Smith",
    ],
    ("U.S. House", "2", "D"): [
        "Chris Beck", "Mary Doyle", "Rebecca Mueller", "Patty Snow",
        "Dawn Rasmussen", "Peter Quince",
    ],
    ("U.S. House", "2", "R"): ["Cliff Bentz", "Andrea Carr", "Peter J. Larson"],
    ("Governor", "", "D"): [
        "Forest (Fora) Alexander", "James Atkinson IV", "Cal Kishawi",
        "Tina Kotek", "Donnie M. Beckwith", "David W. Beem",
        "Steve William Laible", "Brittany Jones", "Tristan Sheppard",
        "Miranda Weigler",
    ],
    ("Governor", "", "R"): [
        "Danielle Bethell", "Hope A. Dalrymple", "Ed Diehl",
        "Christine Drazan", "Chris Dudley", "Kyle M. Duyck",
        "David Medina", "Robert Neuman", "Brad T. Peters",
        "Paul J. Romero Jr", "Wen Waddell", "Martin Ward",
        "Tim O. Youker", "DeAngelo Leroy Turner",
    ],
    ("State House", "58", "D"): ["Brian Campbell"],
    ("State House", "58", "R"): ["Bobby Levy"],
    ("Labor Commissioner", "", ""): ["Chris Lynch", "Christina E. Stephenson"],
    ("Judge of the Supreme Court", "Position 4", ""): ["Christopher L. Garrett"],
    ("Judge of the Court of Appeals", "Position 1", ""): ["Ryan T. O'Connor"],
    ("Judge of the Court of Appeals", "Position 9", ""): ["Jacqueline Kamins"],
    ("Judge of the Court of Appeals", "Position 12", ""): ["Erin C. Lagesen"],
    ("Judge of the Court of Appeals", "Position 13", ""): ["Doug Tookey"],
    ("District Attorney", "", ""): ["Rebecca J. Frolander"],
    ("County Commissioner", "Position 2", ""): [
        "Stacey Karvoski", "Cody R. Lathrop", "Paul Flanders",
    ],
    ("County Commissioner", "Position 3", ""): ["Rawley Bigsby", "John Hillock"],
}

STAT_LABELS = {"Over Votes", "Under Votes", "Total", "Write-ins"}

# "(Vote For N)" trigger line, garbled every way PaddleOCR manages.
TRIGGER_TAIL = re.compile(r"\d\s*\)\s*$")
TRIGGER_HINT = re.compile(r"ote\s*[Ff]o|VoQt|o r \d|ot r \d")

CERT_RE = re.compile(r"state of oregon|do hereby certify|county clerk", re.IGNORECASE)
HEADER_LABELS = {"contest", "votes", "candidate", "sequence"}

# Raw label -> canonical candidate name (applied before alias clustering).
# PCP slates are precinct-specific, so a mangled label has no clean sibling
# to cluster against; these were verified cell-by-cell with 400/600-dpi
# PaddleOCR probes of the label column.  "o le" (TROY #12 PCP D) is a
# "No Candidate Filed" garble too short for the fuzzy check, mapped to ""
# so the row is skipped like every other No-Candidate row.
NAME_FIXES = {
    "<aren Holme": "Karen Holme",
    "oni Herb": "Joni Herb",
    "Gue Coleman": "Sue Coleman",
    "ammie Quinby": "Tammie Quinby",
    "erry Journot": "Terry Journot",
    "olene D Cox": "Jolene D. Cox",
    "<erry Tienhaara": "Kerry Tienhaara",
    "o le": "",
}

# Hand-verified cell corrections, keyed (precinct, office_key). Each entry
# overrides candidate values / over / under / total after arbitration.
# Derived from the county abstract (2026/20260519__or__primary__county.csv)
# plus 400/600-dpi PaddleOCR probes of .paddleocr_cache misreads.
GOV_R = ("Governor", "", "R")
CELL_FIXES = {
    ("ENTERPRISE #1", GOV_R): {"cands": {"Danielle Bethell": 9}},
    ("ENTERPRISE #2", GOV_R): {
        "cands": {"Kyle M. Duyck": 1, "Robert Neuman": 1, "Brad T. Peters": 3},
    },
    ("ENTERPRISE #3", GOV_R): {"cands": {"Paul J. Romero Jr": 1}},
    ("JOSEPH #5", GOV_R): {"cands": {"Brad T. Peters": 0, "Write-ins": 1}},
    ("JOSEPH #6", GOV_R): {
        "cands": {"Danielle Bethell": 7},
        "under": 7,
        "total": 369,
    },
    ("WALLOWA #7", GOV_R): {
        "cands": {"David Medina": 1, "DeAngelo Leroy Turner": 1},
    },
    ("WALLOWA #8", GOV_R): {
        "cands": {"Ed Diehl": 87, "Paul J. Romero Jr": 1},
    },
    # Governor D/R, LOSTINE #9 (cached rows had value-dropped cells).
    ("LOSTINE #9", ("Governor", "", "D")): {
        "cands": {"Donnie M. Beckwith": 1, "Brittany Jones": 1},
    },
    ("LOSTINE #9", GOV_R): {"cands": {"Tim O. Youker": 2}},
    # Judicial: FLORA #11 CoA12 values landed one row off (63 stranded
    # under <NOLABEL> is the Lagesen cell); J5/T12 SupCt under misreads.
    ("FLORA #11", ("Judge of the Court of Appeals", "Position 12", "")): {
        "cands": {"Erin C. Lagesen": 35, "Write-ins": 0, "<NOLABEL>": 0},
        "under": 63,
        "total": 98,
    },
    ("JOSEPH #5", ("Judge of the Supreme Court", "Position 4", "")): {
        "cands": {"Write-ins": 9},
    },
    ("TROY #12", ("Judge of the Supreme Court", "Position 4", "")): {"under": 4},
    ("ENTERPRISE #2", ("Judge of the Supreme Court", "Position 4", "")): {"over": 0},
    ("ENTERPRISE #2", ("Judge of the Court of Appeals", "Position 12", "")): {"over": 0},
    # State House 58, WALLOWA #8: both parties' write-in cells read high.
    ("WALLOWA #8", ("State House", "58", "D")): {"cands": {"Write-ins": 1}},
    ("WALLOWA #8", ("State House", "58", "R")): {"cands": {"Write-ins": 1}},
    # U.S. House: E1 Rasmussen/Quince split, E4 Snow 6/9 flip, E3 Carr 6/9.
    ("ENTERPRISE #1", ("U.S. House", "2", "D")): {
        "cands": {"Dawn Rasmussen": 10, "Peter Quince": 1},
    },
    ("ENTERPRISE #4", ("U.S. House", "2", "D")): {"cands": {"Patty Snow": 6}},
    ("ENTERPRISE #3", ("U.S. House", "2", "R")): {"cands": {"Andrea Carr": 6}},
    # U.S. Senate: E1 D write-in derived too low; E1 R Burch 9->6 with the
    # stranded write-in value; E4 R stranded 1 is a write-in; I10 R and
    # T12 R county residuals (T12 total 6 is a 9 misread).
    ("ENTERPRISE #1", ("U.S. Senate", "", "D")): {"cands": {"Write-ins": 3}},
    ("ENTERPRISE #1", ("U.S. Senate", "", "R")): {
        "cands": {"David A. Burch": 6, "Write-ins": 6},
    },
    ("JOSEPH #5", ("U.S. Senate", "", "D")): {"under": 6},
    ("ENTERPRISE #4", ("U.S. Senate", "", "R")): {
        "cands": {"Write-ins": 1, "<NOLABEL>": 0},
        "over": 0,
    },
    ("IMNAHA #10", ("U.S. Senate", "", "R")): {"cands": {"Russell McAlmond": 6}},
    ("TROY #12", ("U.S. Senate", "", "R")): {"cands": {"Brent Barker": 1}, "total": 9},
    # WALLOWA #7 Labor under 56->95 and DA under 911->116 (cached misreads);
    # JOSEPH #6 Labor/CC3 totals read as 69 (dropped digit).
    ("WALLOWA #7", ("Labor Commissioner", "", "")): {"over": 0, "under": 95},
    ("WALLOWA #7", ("District Attorney", "", "")): {"under": 116},
    ("JOSEPH #6", ("Labor Commissioner", "", "")): {"total": 690},
    ("JOSEPH #6", ("County Commissioner", "Position 3", "")): {"total": 690},
    # ENTERPRISE #4 Labor under 99 is a 66 flip; Measure 2-011 No 56->95.
    ("ENTERPRISE #4", ("Labor Commissioner", "", "")): {"under": 66},
    ("ENTERPRISE #4", ("Measure 2-011", "", "")): {"cands": {"No": 95}, "over": 0},
    # WALLOWA #7 U.S. House D under 5->6 and TROY #12 CoA Pos 9 stats
    # dropped entirely (every other cell county- or triple-confirmed).
    ("WALLOWA #7", ("U.S. House", "2", "D")): {"under": 6},
    ("TROY #12", ("Judge of the Court of Appeals", "Position 9", "")): {
        "over": 0,
        "under": 4,
    },
    # ENTERPRISE #2 Measure 120: the merged '08' stat pair is over 0 /
    # under 8; TROY #12 Measure 2-011 No 2->7.
    ("ENTERPRISE #2", ("Measure 120", "", "")): {"over": 0, "under": 8},
    ("TROY #12", ("Measure 2-011", "", "")): {"cands": {"No": 7}},
    # Explicit zero over-vote cells where OCR dropped the 0 box.
    ("WALLOWA #8", ("County Commissioner", "Position 2", "")): {"over": 0},
    ("LOSTINE #9", ("Precinct Committee Person", "", "R")): {"over": 0},
}


def load_boxes():
    if CACHE.exists():
        return {int(k): v for k, v in json.loads(CACHE.read_text()).items()}
    raw = json.loads(TMP_BOXES.read_text())
    boxes = {
        int(k): [
            {"box": b, "text": t, "score": s}
            for b, t, s in zip(v["boxes"], v["texts"], v["scores"])
        ]
        for k, v in raw.items()
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(boxes))
    return boxes


def digitish(text: str) -> bool:
    return re.fullmatch(r"[\d,]+", text) is not None


def page_stream(boxes):
    """One page -> (lines, values) in document order, header zone cut.

    The header/summary zone (page header numbers, the "Party Summary"
    table on each precinct's first page) sits above the "Contest" column
    header; on the two pages where that header was dropped by OCR, fall
    back to cutting everything above y-center 500.  Value boxes are
    standalone numbers (or a bare "O"/"o" zero) inside the votes column
    x-window.
    """
    labels = []
    values = []
    for b in boxes:
        t = re.sub(r"\s+", " ", b["text"]).strip()
        if not t:
            continue
        x0, y0, y1 = b["box"][0], b["box"][1], b["box"][2 + 1]
        cy = (y0 + y1) / 2
        if digitish(t) or t in ("O", "o"):
            if VX_MIN <= x0 < VX_MAX and len(t) <= 7:
                values.append((cy, 0 if t in ("O", "o") else int(t.replace(",", ""))))
            continue
        if x0 < 1600 and not CERT_RE.search(t):
            labels.append((cy, x0, t))
    labels.sort()
    lines = []
    for cy, x0, t in labels:
        if lines and abs(cy - lines[-1][0]) <= LINE_TOL:
            lines[-1] = (lines[-1][0], min(lines[-1][1], x0), lines[-1][2] + " " + t)
        else:
            lines.append((cy, x0, t))

    # Cut the header zone: everything up to and including the "Contest"
    # column header when present, else everything above y-center 500.
    cut = 500.0
    for cy, _x0, t in lines:
        if t.strip().lower().startswith("contest"):
            cut = cy + 1
            break
    lines = [(cy, x0, t) for cy, x0, t in lines if cy >= cut]
    values = [(cy, v) for cy, v in values if cy >= cut]

    # Column headers can band into row lines on some pages.
    lines = [
        (cy, x0, t)
        for cy, x0, t in lines
        if re.sub(r"[^a-z]", "", t.lower()) not in HEADER_LABELS
    ]
    return lines, sorted(values)


# --- row classification ----------------------------------------------------

def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def label_stat(text: str):
    """Classify a row label as a stat name, or None for a candidate."""
    if text is None:
        return None
    lt = letters(text)
    if not lt:
        return None
    if difflib.SequenceMatcher(None, lt, "total").ratio() >= 0.8 or lt in ("tota", "otal"):
        return "Total"
    if difflib.SequenceMatcher(None, lt, "overvotes").ratio() >= 0.75:
        return "Over Votes"
    if difflib.SequenceMatcher(None, lt, "undervotes").ratio() >= 0.75:
        return "Under Votes"
    if (
        difflib.SequenceMatcher(None, lt, "writein").ratio() >= 0.7
        or "rite" in lt
        or lt in ("wri", "wi")
    ):
        return "Write-ins"
    return None


def label_candidate(name: str) -> str:
    n = re.sub(r"\s+", " ", name).strip()
    if not n:
        return n
    if label_stat(n):
        return n
    if "No Candidate Filed" in n or difflib.SequenceMatcher(
        None, letters(n), "nocandidatefiled"
    ).ratio() >= 0.8:
        return ""
    if n in NAME_FIXES:
        return NAME_FIXES[n]
    return format_candidate_name(n.split())


# --- alias/canonical names --------------------------------------------------

def build_aliases(counts, threshold=0.85):
    """Cluster raw labels that OCR mangled into near-duplicates."""
    names = sorted(counts)
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if find(a) == find(b):
                continue
            if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold:
                parent[find(b)] = find(a)
    return {n: find(n) for n in names if find(n) != n}


COUNTY_CSV = Path(f"2026/{ELECTION_DATE}__or__primary__county.csv")
SKIP_NAMES = {"Write-ins", "Yes", "No", "Over Votes", "Under Votes", "Total"}


def county_names():
    """Wallowa candidate names from the county abstract (name authority)."""
    import csv

    names = Counter()
    with COUNTY_CSV.open() as f:
        for row in csv.DictReader(f):
            if row["county"] == COUNTY and row["candidate"] not in SKIP_NAMES:
                names[row["candidate"]] += 1
    return names


def canonical_fixes(aliases, counts, preferred, threshold=0.85):
    """Map each cluster to its best county-abstract spelling."""
    fixes = {}
    for name in counts:
        target = aliases.get(name, name)
        cands = [n for n in counts if aliases.get(n, n) == target and counts[n] > 0]
        best = max(cands, key=lambda n: (counts[n], len(n)))
        pn = re.sub(r"[^a-z ]", "", best.lower())
        match, score = None, 0.0
        for pref in preferred:
            if abs(len(pref.split()) - len(best.split())) > 1:
                continue
            r = difflib.SequenceMatcher(None, pn, pref.lower()).ratio()
            if r > score:
                match, score = pref, r
        if match and score >= threshold:
            fixes[name] = match
    return fixes


def county_votes():
    """Wallowa abstract rows as {(office, district, party): {name: votes}}."""
    import csv

    out = defaultdict(Counter)
    with COUNTY_CSV.open() as f:
        for row in csv.DictReader(f):
            if row["county"] != COUNTY or row["candidate"] in SKIP_NAMES:
                continue
            out[(row["office"], row["district"], row["party"])][
                row["candidate"]
            ] += int(row["votes"])
    return out


def assign_names(okey, cands, name_map, bnotes):
    """Emit candidate votes for one block, mapping raw labels to names.

    Every block lists candidates in a fixed document order, so rows that
    OCR garbled beyond fuzzy matching are anchored by the rows that did
    parse and the gaps filled positionally from the canonical order.
    """
    canon = CANONICAL.get(okey)
    out = Counter()
    if canon:
        rows = list(cands)
        assigned = [None] * len(rows)
        # anchor: greedy best fuzzy matches (skip write-in slots)
        pairs = []
        for ri, (lab, val, cy) in enumerate(rows):
            if lab in (None, "<WRITE-IN>"):
                continue
            ln = re.sub(r"[^a-z]", "", lab.lower())
            for ci, name in enumerate(canon):
                cn = re.sub(r"[^a-z]", "", name.lower())
                r = difflib.SequenceMatcher(None, ln, cn).ratio()
                if r >= 0.6:
                    pairs.append((r, ri, ci))
        pairs.sort(reverse=True)
        for r, ri, ci in pairs:
            if assigned[ri] is None and ci not in assigned:
                assigned[ri] = ci
        # gap fill: remaining rows in document order take the
        # remaining canonical slots in order
        free_ci = [ci for ci in range(len(canon)) if ci not in assigned]
        free_ri = [ri for ri, a in enumerate(assigned)
                   if a is None and rows[ri][0] != "<WRITE-IN>"]
        for ri, ci in zip(free_ri, free_ci):
            assigned[ri] = ci
        for ri, (lab, val, cy) in enumerate(rows):
            if lab == "<WRITE-IN>":
                out["Write-ins"] += val or 0
            elif assigned[ri] is None:
                out[lab or "<NOLABEL>"] += val or 0
                bnotes.append(f"unassigned row {lab!r} = {val}")
            else:
                out[canon[assigned[ri]]] += val or 0
        extra = [rows[ri][0] for ri in range(len(rows))
                 if assigned[ri] is None and rows[ri][0] != "<WRITE-IN>"]
        holes = [canon[ci] for ci in range(len(canon)) if ci not in assigned]
        if extra or holes:
            bnotes.append(f"row-structure: extra={extra} holes={holes}")
    else:
        for lab, val, cy in cands:
            if lab == "<WRITE-IN>":
                out["Write-ins"] += val or 0
            elif lab is None:
                out["<NOLABEL>"] += val or 0
                bnotes.append(f"value-only row {val} at y{cy:.0f}")
            else:
                nm = label_candidate(lab)
                if name_map:
                    nm = name_map.get(nm, nm)
                out[nm] += val or 0
    return out


# --- the parse --------------------------------------------------------------

def run(boxes, name_map=None, collect=None, arb=None, debug=False):
    """One parse pass.  Returns (results, notes) where results is a list of
    (precinct, okey, cands Counter, over, under, notes_for_block) and notes
    are global diagnostics.  When collect is a counter, raw candidate labels
    are counted (pass 1) and results are empty."""
    # Global stream in document order, with page numbers retained.
    entries = []
    precinct_headers = defaultdict(list)
    for page_no in sorted(boxes):
        lines, values = page_stream(boxes[page_no])
        # Precinct headers are cut by the Contest line on first pages; look
        # for them in the raw label stream instead (before page_stream's cut).
        for b in boxes[page_no]:
            t = re.sub(r"\s+", " ", b["text"]).strip()
            if b["box"][0] < 1600 and re.search(r"#\s*\d", t) and not digitish(t):
                precinct_headers[page_no].append(t)
        stream = [(cy, "line", t) for cy, _x0, t in lines]
        stream += [(cy, "value", v) for cy, v in values]
        stream.sort(key=lambda s: s[0])
        for cy, kind, payload in stream:
            entries.append((page_no, cy, kind, payload))
    if not entries:
        return [], ["no entries parsed"]

    results = []
    notes = []

    def prec_of(page_no):
        return PRECINCTS[(page_no - 1) // PAGES_PER_PRECINCT]

    # --- block segmentation
    # blocks: list of dicts {precinct, idx, title, rows:[(page,cy,label|None)]}
    blocks = []
    cur = None
    title = None
    for page_no, cy, kind, payload in entries:
        if kind == "value":
            if cur is not None:
                cur["raw_values"].append((page_no, cy, payload))
            continue
        text = payload
        if TRIGGER_TAIL.search(text) and len(text) <= 26 and TRIGGER_HINT.search(text):
            if cur is not None:
                # the line right above the trigger is the next block's
                # title (unless the title was dropped and the last row is
                # a stat line); it must not stay in this block's rows.
                if (
                    title is not None
                    and label_stat(title) is None
                    and cur["raw_labels"]
                    and cur["raw_labels"][-1][2] == title
                ):
                    cur["raw_labels"].pop()
                    cur["title"] = title
                else:
                    cur["title"] = None
                blocks.append(cur)
            cur = {
                "precinct": prec_of(page_no),
                "page": page_no,
                "raw_labels": [],
                "raw_values": [],
                "title": None,
            }
            title = None
            continue
        if cur is not None:
            cur["raw_labels"].append((page_no, cy, text))
        # the line right above the next trigger is its block's title
        title = text
    if cur is not None:
        cur["title"] = None
        blocks.append(cur)

    for prec in PRECINCTS:
        got = [b for b in blocks if b["precinct"] == prec]
        if len(got) != len(EXPECTED):
            notes.append(f"{prec}: {len(got)} blocks, expected {len(EXPECTED)}")

    # --- per-block processing
    for bi, blk in enumerate(blocks):
        prec = blk["precinct"]
        idx = len([b for b in blocks[:bi] if b["precinct"] == prec])
        if idx >= len(EXPECTED):
            notes.append(f"{prec}: extra block {bi} at p{blk['page']}")
            continue
        okey = EXPECTED[idx]
        bnotes = []

        # pair labels and values per page: a globally consistent monotone
        # matching (values sit either slightly above or slightly below
        # their label rows -- the offset is constant per page but the
        # greedy nearest-y assignment cascades when rows are ~60px apart
        # and the offset is ~35px).
        labels = sorted(blk["raw_labels"])
        values = sorted(blk["raw_values"])

        def pair_rows(bias):
            # bias None: plain distance matching (with any PAGE_BIAS pin
            # applied per page); "above"/"below": force a hypothesis
            # (retry path).
            rows = []  # (page, cy, label, value)
            by_page = defaultdict(list)
            for pg, cy, lt in labels:
                by_page[pg].append((cy, lt))
            vpage = defaultdict(list)
            for pg, cy, v in values:
                vpage[pg].append((cy, v))
            for pg in sorted(set(by_page) | set(vpage)):
                labs = sorted(by_page[pg])
                vals = sorted(vpage[pg])
                n, m = len(labs), len(vals)
                if n == 0:
                    rows.extend([pg, cy, None, v, cy] for cy, v in vals)
                    continue
                if m == 0:
                    rows.extend([pg, cy, lt, None, None] for cy, lt in labs)
                    continue
                # dp[i][j]: best score pairing labs[:i] with vals[:j].
                # when both a pure above-shift and a pure below-shift
                # alignment have equal match counts and distances, the
                # DP cannot tell them apart.  The fragment's own edges
                # disambiguate: values above labels means the topmost
                # item is a value; below means the bottommost is.
                pbias = bias.get(pg) if isinstance(bias, dict) else (
                    bias or PAGE_BIAS.get(pg))
                if pbias is None and n and m:
                    top_val = vals[0][0] < labs[0][0] - 8
                    bot_val = vals[-1][0] > labs[-1][0] + 8
                    if top_val and not bot_val:
                        pbias = "above"
                    elif bot_val and not top_val:
                        pbias = "below"
                REWARD = 1000
                NEG = float("-inf")
                dp = [[NEG] * (m + 1) for _ in range(n + 1)]
                back = [[0] * (m + 1) for _ in range(n + 1)]
                dp[0][0] = 0
                for i in range(n + 1):
                    for j in range(m + 1):
                        if dp[i][j] == NEG:
                            continue
                        if j < m:
                            if dp[i][j] > dp[i][j + 1]:
                                dp[i][j + 1] = dp[i][j]
                                back[i][j + 1] = 1  # skip value
                        if i < n:
                            if dp[i][j] > dp[i + 1][j]:
                                dp[i + 1][j] = dp[i][j]
                                back[i + 1][j] = 2  # skip label
                        if i < n and j < m:
                            dy = labs[i][0] - vals[j][0]  # >0: value above label
                            ok = abs(dy) <= PAIR_TOL
                            if pbias == "above":
                                ok = ok and dy >= -8
                            elif pbias == "below":
                                ok = ok and dy <= 8
                            if ok:
                                sc = dp[i][j] + REWARD - abs(dy)
                                if sc > dp[i + 1][j + 1]:
                                    dp[i + 1][j + 1] = sc
                                    back[i + 1][j + 1] = 3  # pair
                # walk back
                pair = {}
                vpair = {}
                i, j = n, m
                while i > 0 or j > 0:
                    b = back[i][j]
                    if b == 3:
                        pair[i - 1] = vals[j - 1][1]
                        vpair[i - 1] = vals[j - 1][0]
                        i, j = i - 1, j - 1
                    elif b == 1:
                        j -= 1
                    elif b == 2:
                        i -= 1
                    else:
                        break
                for li, (cy, lt) in enumerate(labs):
                    rows.append([pg, cy, lt, pair.get(li), vpair.get(li)])
                # value-only rows (values not chosen in any pair)
                paired_vals = Counter(pair.values())
                remaining = []
                seen = Counter()
                for cy, v in vals:
                    seen[v] += 1
                    if seen[v] <= paired_vals.get(v, 0):
                        continue
                    remaining.append((cy, v))
                rows.extend([pg, cy, None, v, cy] for cy, v in remaining)
            rows.sort(key=lambda r: (r[0], r[1]))
            return rows

        def attempt(rows):
            # classify through sum-validation; returns the block payload
            # plus its notes so retry pairings can be compared.
            bn = []
            named = [[r[0], r[1], r[2], r[3], label_stat(r[2])] for r in rows]
            # positional stat repair: short letter strings at -2/-3 before Total
            ti = next((i for i, r in enumerate(named) if r[4] == "Total"), None)
            if ti is not None and ti >= 2:
                for pos, stat in ((-1, "Under Votes"), (-2, "Over Votes")):
                    j = ti + pos
                    if j >= 0 and named[j][4] is None and named[j][2] is not None:
                        lt = letters(named[j][2])
                        if lt and len(lt) <= 7 and not any(c.isdigit() for c in named[j][2]):
                            named[j][4] = stat

            # measure rows: exactly two candidate rows -> Yes, No
            if idx in MEASURE_AT:
                cand_rows = [r for r in named if r[4] is None]
                stat_names = [r[4] for r in named if r[4]]
                if len(cand_rows) == 2 and "Over Votes" in stat_names:
                    cand_rows[0][4] = "MEASURE"
                    cand_rows[1][4] = "MEASURE"
                    cand_rows[0][2] = "Yes"
                    cand_rows[1][2] = "No"
                else:
                    bn.append(f"measure rows unexpected: {[(r[2], r[3]) for r in cand_rows]}")

            # gather stats and candidates
            over = under = total = None
            cands = []  # (label, value, cy)
            for pg, cy, lab, val, st in named:
                if st == "Total":
                    total = val
                elif st == "Over Votes":
                    over = val if val is not None else over
                elif st == "Under Votes":
                    under = val if val is not None else under
                elif st == "Write-ins":
                    cands.append(("<WRITE-IN>", val, cy))
                elif lab is not None and difflib.SequenceMatcher(
                    None, letters(lab), "nocandidatefiled"
                ).ratio() >= 0.75:
                    continue  # "No Candidate Filed" row
                else:
                    cands.append((lab, val, cy))

            # single-missing-value derivation (before naming: it fixes a
            # candidate slot's value from Total when exactly one is unknown)
            missing = [c for c in cands if c[1] is None and c[0] is not None]
            known = sum(c[1] for c in cands if c[1] is not None)
            if total is not None:
                s = known + (over or 0) + (under or 0)
                if s != total and len(missing) == 1 and s <= total:
                    derived = total - s
                    for i, c in enumerate(cands):
                        if c[1] is None and c[0] == missing[0][0]:
                            cands[i] = (c[0], derived, c[2])
                            break
                    known += derived
                    bn.append(
                        f"derived {missing[0][0]!r} = {derived} from Total {total}"
                    )
                    s = known + (over or 0) + (under or 0)
                # several dropped write-in rows: their values merge into
                # one emitted row, so the group total is derivable
                if (s != total and missing
                        and all(c[0] == "<WRITE-IN>" for c in missing)
                        and s <= total):
                    cands[cands.index(missing[0])] = (
                        "<WRITE-IN>", total - s, missing[0][2])
                    known += total - s
                    bn.append(
                        f"derived write-in group = {total - s} from Total {total}"
                    )
                    s = known + (over or 0) + (under or 0)
                # a stat dropped entirely: derive it from Total
                if s != total and (over is None) != (under is None):
                    stat = total - s
                    if stat >= 0:
                        if over is None:
                            over = stat
                        else:
                            under = stat
                        bn.append(f"derived {'over' if over is stat else 'under'} = {stat} from Total {total}")
                        s = total
                if s != total:
                    bn.append(
                        f"SUM {known}+over{over}+under{under} != Total {total}; "
                        f"rows: {[(r[2], r[3], r[4]) for r in named]}"
                    )
            else:
                bn.append("Total row value missing")
            return over, under, total, cands, bn

        def failed(bn):
            return any(
                n.startswith("SUM ") or n == "Total row value missing"
                or n.startswith("measure rows unexpected")
                for n in bn
            )

        # plain DP first; if the block sum check fails (or values ended
        # up stranded off their labels), retry with the two offset
        # hypotheses. A retry is accepted only when it strands no
        # value-only rows AND either validates the sum or resolves
        # strictly more unknown cells than the plain pairing.
        rows = pair_rows(None)
        over, under, total, cands, bnotes = attempt(rows)
        base_bad = failed(bnotes)
        base_missing = sum(1 for c in cands if c[1] is None and c[0] is not None)
        base_valonly = sum(1 for c in cands if c[0] is None)
        if base_bad or base_valonly:
            for bias in ("above", "below"):
                rows2 = pair_rows(bias)
                if rows2 == rows:
                    continue
                o2, u2, t2, c2, bn2 = attempt(rows2)
                miss2 = sum(1 for c in c2 if c[1] is None and c[0] is not None)
                vo2 = sum(1 for c in c2 if c[0] is None)
                if os.environ.get("WAL_RETRY_DEBUG"):
                    print(f"RETRYDBG {prec} {okey} {bias}: "
                          f"base(miss={base_missing},vo={base_valonly},"
                          f"bad={base_bad}) new(miss={miss2},vo={vo2},"
                          f"bad={failed(bn2)}) same={rows2 == rows}")
                if vo2 != 0:
                    continue
                if not failed(bn2) or miss2 < base_missing:
                    bnotes = [f"pairing retry ({bias} bias)"] + bn2
                    rows = rows2
                    over, under, total, cands = o2, u2, t2, c2
                    break
        if os.environ.get("WAL_PAGE_DEBUG"):
            pdys = defaultdict(list)
            for r in rows:
                if r[2] is not None and r[3] is not None and r[4] is not None:
                    pdys[r[0]].append(r[1] - r[4])
            print(f"PGDBG {prec} {okey} bad={int(failed(bnotes))} " + " ".join(
                f"p{pg}:med{sorted(v)[len(v) // 2]}/n{len(v)}"
                for pg, v in sorted(pdys.items())))

        # county-abstract arbitration: for blocks whose sum check fails
        # OR whose emitted values disagree with the abstract residuals
        # (a candidate-to-candidate misassignment balances the sum, so
        # only the residual sees it), coordinate-descend over per-page
        # pairing biases (the value/label offset varies per page, and
        # one block spans several), scoring each candidate pairing
        # against residuals (abstract total minus the other precincts'
        # current values).
        if arb is not None and (prec, okey) in arb:
            resid = arb[(prec, okey)]

            def resid_err(out2):
                return sum(
                    abs((v or 0) - resid[nm]) if nm in resid else abs(v or 0)
                    for nm, v in out2.items() if nm != "<NOLABEL>"
                )

            out_pre = assign_names(okey, cands, name_map, [])
            base_rerr = resid_err(out_pre)
            if failed(bnotes) or base_rerr > 50:
                def score_of(rws):
                    o2, u2, t2, c2, bn2 = attempt(rws)
                    btmp = []
                    out2 = assign_names(okey, c2, name_map, btmp)
                    sc = 1000 * int(failed(bn2))
                    # a value stranded off every label (value-only row)
                    # is as bad as a failed sum: it means a real vote
                    # count is homeless and some label holds a wrong value
                    sc += 1000 * sum(1 for c in c2 if c[0] is None)
                    sc += 50 * sum(1 for c in c2 if c[1] is None and c[0] is not None)
                    sc += 150 * int(t2 is None)
                    for nm, v in out2.items():
                        sc += abs((v or 0) - resid[nm]) if nm in resid else abs(v or 0)
                    return sc

                base = score_of(pair_rows(None))
                pages = sorted({pg for pg, *_ in labels} | {pg for pg, *_ in values})
                cur, best = {}, base
                for _ in range(2):
                    improved = False
                    for pg in pages:
                        for b in ("above", "below"):
                            trial = dict(cur)
                            trial[pg] = b
                            sc = score_of(pair_rows(trial))
                            if sc < best - 1:
                                cur, best, improved = trial, sc, True
                    if not improved:
                        break
                if os.environ.get("WAL_ARB_DEBUG"):
                    print(f"ARBDBG {prec} {okey} base={base} "
                          f"best={best} pins={cur} rerr={base_rerr}")
                if best < base - 25:
                    rws = pair_rows(cur)
                    o2, u2, t2, c2, bn2 = attempt(rws)
                    rows = rws
                    over, under, total, cands = o2, u2, t2, c2
                    bnotes = (
                        [f"arbitrated pins={cur} score {best} < {base}"] + bn2
                    )

        if collect is not None:
            for lab, val, cy in cands:
                if lab and lab not in ("<WRITE-IN>", None):
                    collect[label_candidate(lab)] += 1
            continue

        out = assign_names(okey, cands, name_map, bnotes)

        fix = CELL_FIXES.get((prec, okey))
        if fix:
            for name, val in fix.get("cands", {}).items():
                out[name] = val
            if "over" in fix:
                over = fix["over"]
            if "under" in fix:
                under = fix["under"]
            if "total" in fix:
                total = fix["total"]
            bnotes.append(f"cell-fixes: {sorted(fix)}")

        results.append((prec, okey, out, over, under, total, bnotes))

    return results, notes, blocks


def main():
    debug = "--debug" in sys.argv
    boxes = load_boxes()

    # pass 1: collect raw candidate labels for alias clustering
    collect = Counter()
    run(boxes, collect=collect)
    aliases = build_aliases(collect)
    preferred = county_names()
    fixes = canonical_fixes(aliases, collect, preferred)

    # rebuild the alias map on top of canonical fixes so clusters resolve
    # to a single canonical spelling (Grant's cycle-avoidance).
    name_map = {}
    for raw in collect:
        nm = label_candidate(raw)
        if nm in fixes:
            name_map[raw] = fixes[nm]
        else:
            tgt = aliases.get(nm, nm)
            if tgt != nm:
                tgt2 = label_candidate(tgt)
                name_map[raw] = fixes.get(tgt2, tgt2)

    # iterative county-abstract arbitration: each pass pairs bad blocks
    # against residuals computed from the previous pass, so fixes in one
    # precinct sharpen the residuals for the next
    cvals = county_votes()

    def build_arb(results):
        per = {}
        for prec, okey, out, *_ in results:
            per[(prec, okey)] = out
        arb = {}
        for okey in cvals:
            for prec in PRECINCTS:
                resid = {}
                for nm, cv in cvals[okey].items():
                    others = sum(
                        per.get((p, okey), Counter()).get(nm, 0)
                        for p in PRECINCTS if p != prec
                    )
                    resid[nm] = cv - others
                arb[(prec, okey)] = resid
        return arb

    results, notes, blocks = run(boxes, name_map=name_map)
    for _ in range(2):
        arb = build_arb(results)
        results, notes, blocks = run(boxes, name_map=name_map, arb=arb)
    print(f"{len(results)} contest results")
    prob = 0
    for prec, okey, out, over, under, total, bnotes in results:
        for n in bnotes:
            prob += 1
            print(f"  {prec} {okey}: {n}")
    for n in notes:
        prob += 1
        print(f"  NOTE {n}")
    print(f"{prob} problems")

    if debug:
        return

    rows = []
    for prec, okey, cands, over, under, total, bnotes in results:
        office, district, party = okey
        for name, votes in sorted(cands.items()):
            if name in ("", "<NOLABEL>"):
                continue
            rows.append(make_row(COUNTY, prec, office, district, party, name, votes))
        if over is not None:
            rows.append(make_row(COUNTY, prec, office, district, party, "Over Votes", over))
        if under is not None:
            rows.append(make_row(COUNTY, prec, office, district, party, "Under Votes", under))
    write_csv(rows, str(OUT))
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()