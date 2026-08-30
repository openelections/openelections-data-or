"""Parser for Jefferson County 2026 primary "Custom Table Report" PDF.

The source is an Electionware custom-table matrix: candidates and stat
columns spread across the page, one row per precinct, one contest per
page -- except that wide contests continue their COLUMNS on the next page
(DEM/REP Governor), long contests continue their ROWS, and two short
contests can sit side by side.  The PDF has no usable text layer, so the
parser works from local PaddleOCR boxes (300-DPI renders, cached at
`.paddleocr_cache/Jefferson/boxes.json`).

Column model per contest, left to right: candidate names, any assigned
write-in candidate columns ("Write-in: <name>", a breakdown of the write-in
totals, not additional votes), "Write-in Totals", "Write-in: Not Assigned",
"Total Votes Cast", "Overvotes", "Undervotes", "Contest Total".  Invariants
validated per precinct row:

    Total Votes Cast == sum(candidates) + Write-in Totals
    Write-in Totals == sum(assigned write-ins) + Write-in: Not Assigned
    Contest Total    == Total Votes Cast + Overvotes + Undervotes

OCR drops and garbles cells (zeros, "L9", "09"), mangles precinct names
("ES (1)"), and merges first-row digits into header labels ("Overvotes0").
Recovery, in order: header-merged digits for the first row; per-column
single-gap fills validated against each page's "Totals" row (a zero
remainder fills all gaps with zero); row-invariant derivations; iterated
to a fixpoint.  County-baseline spellings fix OCR candidate names.

Usage:
    uv run python src/parsers/2026_primary_jefferson_parser.py [--probe]
"""

import difflib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    format_candidate_name,
    make_row,
    normalize_party,
    write_csv,
)

COUNTY = "Jefferson"
CACHE = Path(".paddleocr_cache/Jefferson/boxes.json")
TMP_BOXES = Path("/tmp/jefferson_ocr_boxes.json")
IMAGES = Path("/tmp/jefferson_imgs")
COUNTY_CSV = Path("2026/20260519__or__primary__county.csv")
SKIP_NAMES = {"Write-ins", "Yes", "No", "Over Votes", "Under Votes", "Total"}

# Ballot precinct number -> canonical precinct label (as printed).
PRECINCTS = {
    "01": "HAYSTACK (01)",
    "02": "CROOKED RIVER RANCH (02)",
    "05": "ASHWOOD (05)",
    "06": "KUTCHER (06)",
    "08": "LYLE GAP (08)",
    "11": "METOLIUS (11)",
    "13": "EAST MADRAS (13)",
    "14": "WARM SPRINGS (14)",
    "15": "SUTTLE LAKE (15)",
    "16": "CAMP SHERMAN (16)",
    "17": "CULVER (17)",
    "18": "ROUND BUTTE (18)",
    "19": "WEST MADRAS (19)",
    "20": "CIRCLE M (20)",
    "21": "CENTRAL (21)",
    "22": "SUNSET (22)",
    "23": "AGENCY PLAINS (23)",
}
ORDER = [PRECINCTS[p] for p in sorted(PRECINCTS, key=int)]
IDX = {label: i for i, label in enumerate(ORDER)}

# Page furniture, never contest titles or precinct rows.  Real contest
# titles may contain "Jefferson County" (e.g. "Jefferson County Assessor",
# "16-117 Jefferson County Library District"), so the furniture line is
# matched by its "Primary Election" tail instead.  OCR drops leading
# characters: "ustom Table Report", "Renort generated wit".
NOT_A_TITLE = re.compile(
    r"official|custom table|table report|report generated|generated with|"
    r"electionware|primary election|page \d|of \d+ precinct|generated|"
    r"all precincts|run date|run time|june \d+",
    re.I,
)
STATUS_LINE = re.compile(r"\d+\s+of\s+\d+\s+Precincts", re.I)

ROW_TOL = 32          # value boxes this close in y to a precinct name
COL_GAP = 80           # x-center gap that starts a new value column
HEADER_GAP = 70        # x-center gap that starts a new header column
HEADER_NEAR = 130      # max distance for a value column to claim a header

# canonical stat-column order, used to identify header-less columns
STAT_ORDER = ["WI_T", "WI_NA", "TVC", "OVER", "UNDER", "CT"]

# Declared/assigned write-in spellings not derivable from the county file.
WI_NAME_FIXES = {
    "Jim Doherty": "Jim E. (WI) Doherty",
    "Barbra J Lowe": "Barbra J. (WI) Lowe",
    "Jeremiah Johnson": "Jeremiah (WI) Johnson",
    "Julie Quaid": "Julie (WI) Quaid",
    "Arriana Adams": "Arriana (WI) Adams",
    "Mike Ahern": "Mike (WI) Ahern",
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


def county_rows():
    """County-level baseline: contest -> [(candidate, votes)]."""
    import csv

    out = defaultdict(list)
    with COUNTY_CSV.open() as f:
        for row in csv.DictReader(f):
            if row["county"] == COUNTY:
                cand = row["candidate"].strip()
                if cand and cand not in SKIP_NAMES:
                    out[(row["office"].strip(), row["district"].strip(),
                         row["party"].strip())].append((cand, int(row["votes"])))
    return out


def canonical_fixes(labels, preferred, threshold=0.85):
    """Map garbled candidate spellings to county baseline spellings."""
    fixes = {}
    for lab in labels:
        if lab in preferred:
            continue
        best, score = None, 0.0
        for p in preferred:
            r = difflib.SequenceMatcher(None, lab.lower(), p.lower()).ratio()
            if r > score:
                best, score = p, r
        if best is not None and score >= threshold:
            fixes[lab] = best
    return fixes


def num(text):
    """Parse a value box to int, or None when the OCR garbled it."""
    t = text.strip()
    if "," in t and not re.fullmatch(r"\d{1,3}(?:,\d{3})+", t):
        return None  # a number split by the OCR ("1," or bare "299")
    t = t.replace(",", "")
    if re.fullmatch(r"\d+", t) and not (len(t) > 1 and t.startswith("0")):
        return int(t)
    return None


def match_precinct(text):
    """Precinct label -> canonical key, or None when the OCR mangled it."""
    t = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"\((\d{1,2})\)", t)
    if m and m.group(1) in PRECINCTS:
        return PRECINCTS[m.group(1)]
    return None


# --- office mapping -------------------------------------------------------

MEASURE_RE = re.compile(r"(\d{1,2}-\d{1,3})")
BARE_MEASURE_RE = re.compile(r"\b(\d{2,3})\b")


def parse_office(title):
    """Contest title -> (office, district, party); (None, None, None) if unmapped."""
    t = re.sub(r"\s+", " ", title).strip()
    party = ""
    m = re.search(r"\b(DEM|REP)\b", t)
    if m:
        party = normalize_party(m.group(1))
    if re.fullmatch(r"\d{2,3}", t):
        # "State Measure 120" can OCR down to the bare number
        return f"Measure {t}", "", party
    if re.search(r"state representative", t, re.I):
        m = re.search(r"(\d+)(?:st|nd|rd|th)", t)
        return "State House", m.group(1) if m else "", party
    if re.search(r"us representative", t, re.I):
        m = re.search(r"(\d+)(?:st|nd|rd|th)", t)
        return "U.S. House", m.group(1) if m else "", party
    if re.search(r"us senator", t, re.I):
        return "U.S. Senate", "", party
    if re.search(r"governor", t, re.I):
        return "Governor", "", party
    if re.search(r"bureau of labor", t, re.I):
        return "Labor Commissioner", "", party
    if re.search(r"supreme court", t, re.I):
        m = re.search(r"Position (\d+)", t, re.I)
        return "Judge of the Supreme Court", f"Position {m.group(1)}" if m else "", party
    if re.search(r"court of appeals", t, re.I):
        m = re.search(r"Position (\d+)", t, re.I)
        return "Judge of the Court of Appeals", f"Position {m.group(1)}" if m else "", party
    if re.search(r"county assessor", t, re.I):
        return "County Assessor", "", party
    if re.search(r"county commissioner", t, re.I):
        m = re.search(r"Position #?(\d+)", t, re.I)
        return "County Commissioner", m.group(1) if m else "", party
    if "measure" in t.lower():
        m = MEASURE_RE.search(t) or BARE_MEASURE_RE.search(t)
        if m:
            return f"Measure {m.group(1)}", "", party
    m = MEASURE_RE.search(t)
    if m and re.search(r"district|rfpd|library|road", t, re.I):
        return f"Measure {m.group(1)}", "", party
    return None, None, None


def label_role(label):
    """Column header label -> (role, name, merged_first_row_value)."""
    lab = re.sub(r"\s+", " ", label).strip()
    merged = None
    # a trailing digit (with or without a separating space) is the first
    # precinct row's value merged up into the header; PaddleOCR renders a
    # rotated "8" as "∞"
    m = re.search(r"[\s]?(\d{1,4}|∞)$", lab)
    if m and len(lab) > len(m.group(1)) and not re.search(r"Position|#|District", lab):
        merged = 8 if m.group(1) == "∞" else int(m.group(1))
        lab = lab[: m.start()].strip()
    low = lab.lower()
    squished = low.replace(" ", "").replace("-", "")
    if "nocandidatefiled" in squished:
        return "NCF", None, merged
    if "write-in:not" in low or "writein:not" in squished or squished == "assigned":
        return "WI_NA", None, merged
    if "writeintotals" in squished:
        return "WI_T", None, merged
    if "totalvotescast" in squished:
        return "TVC", None, merged
    if squished.startswith("overvotes"):
        return "OVER", None, merged
    if squished.startswith("undervotes"):
        return "UNDER", None, merged
    if squished.startswith("contesttot"):
        return "CT", None, merged
    if "write-in:" in low or "writein:" in squished:
        # assigned write-in candidate column: a breakdown of Write-in Totals
        name = re.sub(r"write-?\s*in\s*:", "", lab, flags=re.I).strip(" ,")
        return "CAND_WI", re.sub(r"\s+", " ", name), merged
    if not lab:
        return None, None, merged
    return "CAND", lab, merged


# --- page parsing ---------------------------------------------------------

def items_of(page_boxes):
    return sorted(
        (
            (int((b["box"][1] + b["box"][3]) / 2), b["box"][0], b["box"][2], b["text"])
            for b in page_boxes
        ),
        key=lambda r: (r[0], r[1]),
    )


def page_groups(items):
    """Contest groups on a page: (title, vote_for_center_x, vote_for_cy)."""
    groups = []
    for cy, x0, x1, t in items:
        if "VOTE FOR" not in t.upper():
            continue
        vfx = (x0 + x1) / 2
        pieces = []
        for cy2, x20, x21, t2 in items:
            if cy - 300 < cy2 < cy - 20 and abs((x20 + x21) / 2 - vfx) < 1000:
                if NOT_A_TITLE.search(t2):
                    continue
                pieces.append((cy2, (x20 + x21) / 2, t2))
        pieces.sort(key=lambda p: (p[0], p[1]))
        title = " ".join(p[2] for p in pieces)
        groups.append((title, vfx, cy))
    return groups


def cluster_x(centers, gap):
    """Cluster scalar x-centers; returns list of cluster means, sorted."""
    if not centers:
        return []
    centers = sorted(centers)
    clusters = [[centers[0]]]
    for c in centers[1:]:
        if c - clusters[-1][-1] > gap:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    return [sum(c) / len(c) for c in clusters]


def resolve_anchors(name_rows, page_no, problems):
    """Map (cy, text) precinct name rows to precinct keys.

    Rows are in ballot order, so regex-mangled names are assigned by
    position: an unmatched run between two matched neighbors takes the
    unclaimed ballot precincts between them.
    """
    entries = [(cy, text, match_precinct(text)) for cy, text in name_rows]
    out = {}
    claimed = set()
    for cy, _t, k in entries:
        if k:
            out[cy] = k
            claimed.add(k)
    seq = [IDX[k] for _cy, _t, k in entries if k]
    if seq != sorted(seq):
        problems.append(
            f"p{page_no}: anchors out of ballot order: "
            + str([k for _c, _t, k in entries if k])
        )
        return out
    i, n = 0, len(entries)
    while i < n:
        if entries[i][2]:
            i += 1
            continue
        j = i
        while j < n and entries[j][2] is None:
            j += 1
        lo = IDX[entries[i - 1][2]] + 1 if i > 0 else 0
        hi = IDX[entries[j][2]] - 1 if j < n else len(ORDER) - 1
        avail = [label for label in ORDER[lo: hi + 1] if label not in claimed]
        run = entries[i:j]
        if len(avail) == len(run):
            for (cy, _t, _k), key in zip(run, avail):
                out[cy] = key
                claimed.add(key)
        else:
            problems.append(
                f"p{page_no}: unmatched name rows "
                + str([t for _c, t, _k in run])
                + f" vs available {avail}"
            )
        i = j
    return out


def parse_page(page_no, page_boxes, problems):
    """One page -> dict with groups, value columns and per-precinct values."""
    items = items_of(page_boxes)
    groups = page_groups(items)
    if not groups:
        return None
    vf_bottom = max(g[2] for g in groups) + 18

    name_rows = []  # (cy, text) for precinct rows
    totals_cy = None
    for cy, x0, x1, t in items:
        if x0 >= 400 or cy <= vf_bottom:
            continue
        tt = re.sub(r"\s+", " ", t).strip()
        if NOT_A_TITLE.search(tt):
            continue  # report footer/status lines are not precinct rows
        if re.fullmatch(r"[a-z ]{0,3}otals", tt.lower()):
            # "Totals", or squished OCR variants "otals" / "Cotals"
            totals_cy = cy
        else:
            name_rows.append((cy, tt))
    if not name_rows:
        return None
    name_rows.sort()
    anchor_keys = resolve_anchors(name_rows, page_no, problems)
    if not anchor_keys:
        return None
    anchors = sorted((cy, k) for cy, k in anchor_keys.items())
    first_row = anchors[0][0]
    anchor_cys = [cy for cy, _k in anchors] + ([totals_cy] if totals_cy else [])

    # numeric value boxes, each attached to its nearest row
    val_pts = []  # (row_cy, x_center, value)
    for cy, x0, x1, t in items:
        if x0 < 400:
            continue
        v = num(t)
        if v is None:
            continue
        near = min(anchor_cys, key=lambda ay: abs(cy - ay), default=None)
        if near is None or abs(cy - near) > ROW_TOL:
            continue
        val_pts.append((near, (x0 + x1) / 2, v))
    if not val_pts:
        return None
    vcols = cluster_x([p[1] for p in val_pts], COL_GAP)

    rows = defaultdict(dict)  # row_cy -> {vcol_index: value}
    for rcy, cx, v in val_pts:
        best = min(range(len(vcols)), key=lambda i: abs(vcols[i] - cx))
        if best not in rows[rcy]:
            rows[rcy][best] = v

    # header columns: boxes between the VOTE FOR line and the first row,
    # skipping the "N of N Precincts Reporting" status line
    header = [
        (cy, (x0 + x1) / 2, t)
        for cy, x0, x1, t in items
        if vf_bottom < cy < first_row - 12 and not STATUS_LINE.search(t)
    ]
    hcols = []
    for cy, cx, t in sorted(header, key=lambda h: h[1]):
        for col in hcols:
            if abs(col["x"] - cx) < HEADER_GAP and abs(col["y"] - cy) < 160:
                col["parts"].append((cy, t))
                col["x"] = (col["x"] + cx) / 2
                break
        else:
            hcols.append({"x": cx, "y": cy, "parts": [(cy, t)]})
    for col in hcols:
        label = " ".join(p[1] for p in sorted(col["parts"]))
        role, name, merged = label_role(label)
        col.update(role=role, name=name, merged=merged)

    # value columns claim the nearest header column
    vrole, vname, vmerged = {}, {}, {}
    for i, cx in enumerate(vcols):
        near = [c for c in hcols if abs(c["x"] - cx) < HEADER_NEAR]
        if near:
            c = min(near, key=lambda c: abs(c["x"] - cx))
            vrole[i], vname[i], vmerged[i] = c["role"], c["name"], c["merged"]
        else:
            vrole[i] = vname[i] = vmerged[i] = None

    # header-less stat columns: fill from the canonical stat order.  An
    # unknown column must sit between the nearest known stats to its left
    # and right; unknowns left of every stat column are candidates.  Two
    # contests can share a page, each with its own full stat run, so the
    # "already used" check is limited to columns since the nearest
    # candidate column to the left (the start of the current contest).
    known = sorted(
        (vcols[i], vrole[i]) for i in range(len(vcols)) if vrole[i] in STAT_ORDER
    )
    for i in range(len(vcols)):
        if vrole[i]:
            continue
        left = [r for x, r in known if x < vcols[i]]
        right = [r for x, r in known if x > vcols[i]]
        if not left:
            vrole[i] = "CAND"  # left of all stats: a candidate column
            continue
        cand_starts = [vcols[j] for j in range(len(vcols)) if vrole[j] == "CAND" and vcols[j] < vcols[i]]
        region = vcols[i] if not cand_starts else cand_starts[-1]
        used = {
            vrole[j]
            for j in range(len(vcols))
            if vrole[j] in STAT_ORDER and vcols[j] > region
        }
        lo = STAT_ORDER.index(left[-1]) + 1
        hi = STAT_ORDER.index(right[0]) if right else len(STAT_ORDER)
        options = [r for r in STAT_ORDER[lo:hi] if r not in used]
        if not options and not right and left[-1] == "UNDER":
            vrole[i] = "CT"  # Contest Total always closes the stat run
            continue
        if len(options) >= 1:
            vrole[i] = options[0]
        # multiple options with several unknowns in the gap: assign in
        # canonical order by x position
        peers = sorted(
            vcols[j] for j in range(len(vcols))
            if vrole[j] is None and left[-1] and vcols[j] > vcols[i]
        )
    # second pass for multi-unknown gaps: distribute remaining options by x
    unknowns = sorted(i for i in range(len(vcols)) if vrole[i] is None)
    if unknowns:
        problems.append(f"p{page_no}: unresolved columns at x={[int(vcols[i]) for i in unknowns]}")

    # header digits merged upward carry the first precinct row's values
    if anchors:
        first_cy = anchors[0][0]
        for i, merged in vmerged.items():
            if merged is not None and i not in rows.get(first_cy, {}):
                rows[first_cy][i] = merged

    return {
        "page": page_no,
        "groups": groups,
        "vcols": vcols,
        "vrole": vrole,
        "vname": vname,
        "rows": {k: dict(v) for k, v in rows.items()},
        "totals": dict(rows.get(totals_cy, {})) if totals_cy else {},
        "anchor_cys": anchor_cys[:-1] if totals_cy else anchor_cys,
        "anchor_keys": {cy: k for cy, k in anchors},
    }


def assign_groups(page):
    """Split page value columns between the page's contest groups."""
    groups = sorted(page["groups"], key=lambda g: g[1])
    titles = [g[0] for g in groups]
    n = len(vcols := page["vcols"])
    if n == 0:
        return []
    if len(groups) == 1:
        return [(titles[0], 0, n)]
    # two groups side by side: the left one ends at its Contest Total column
    ct = [i for i in range(n) if page["vrole"].get(i) == "CT"]
    if ct:
        cut = ct[0] + 1
        return [(titles[0], 0, cut), (titles[1], cut, n)]
    gaps = [(vcols[i + 1] - vcols[i], i) for i in range(n - 1)]
    cut = max(gaps)[1] + 1 if gaps else n // 2
    return [(titles[0], 0, cut), (titles[1], cut, n)]


def col_label(page, i):
    """Stable per-contest label for a page column."""
    role = page["vrole"].get(i)
    if role == "CAND" or role == "CAND_WI":
        return page["vname"].get(i)  # may be None (header dropped)
    return role


# Verified corrections for cached-OCR misreads (page, precinct, column
# label).  Every value is forced algebraically: the printed Totals row and
# the per-row invariants (TVC = candidates + WI_T; CT = TVC + over + under)
# admit exactly one consistent value once the corroborated cells are fixed,
# and each was cross-checked with a re-OCR of 300-DPI page bands.  Applied
# after contest assembly, before recovery, so the fills run off true values.
CELL_FIXES = {
    # p12 REP Governor: systematic 9<->6 confusion and dropped leading digits
    (12, "KUTCHER (06)", "Ed Diehl"): 9,               # read 6
    (12, "WEST MADRAS (19)", "Ed Diehl"): 85,           # read 55
    (12, "WEST MADRAS (19)", "Danielle Bethell"): 5,    # read 7
    (12, "CAMP SHERMAN (16)", "Christine Drazan"): 19,  # read 11
    (12, "CENTRAL (21)", "Christine Drazan"): 95,       # read 56
    (12, "CENTRAL (21)", "David Medina"): 9,            # read 6
    (12, "CENTRAL (21)", "Danielle Bethell"): 6,        # read 9
    (12, "CROOKED RIVER RANCH (02)", "Paul J Romero"): 6,  # read 9
    (12, "ROUND BUTTE (18)", "David Medina"): 6,        # read 9
    (12, "CULVER (17)", "Danielle Bethell"): 2,          # read 3
    (12, "SUNSET (22)", "Danielle Bethell"): 6,          # read 9
    # p13 REP Governor stats page: CRR Total Votes Cast misread (CT 1119 -
    # 0 over - 23 under = 1096; candidates + Turner write-in agree)
    (13, "CROOKED RIVER RANCH (02)", "TVC"): 1096,       # read 106
    # p3 U.S. House 2 D: KUT/MET Undervotes swapped (9/6), CENT TVC 66->99,
    # SUN Contest Total 68->89, WS Mueller/Rasmussen 6->9 (row + column forced)
    (3, "KUTCHER (06)", "UNDER"): 6,                     # read 9
    (3, "METOLIUS (11)", "UNDER"): 9,                    # read 6
    (3, "CENTRAL (21)", "TVC"): 99,                      # read 66
    (3, "SUNSET (22)", "CT"): 89,                       # read 68
    (3, "WARM SPRINGS (14)", "Rebecca Mueller"): 9,      # read 6
    (3, "WARM SPRINGS (14)", "Rasmussen Dawn"): 9,       # read 6
    # p5 DEM Governor: WS Jones 6, CS Kotek 60, RB TVC 37, WM Kotek 95 /
    # Weigler 6, CM Kotek 9, SUN WI_T/WI_NA 6 (all forced by row equations;
    # confirmed by 2x band re-OCR of the flagged rows). HAY's missing Jones/
    # WI_T/TVC/OVER cells are derived by the recovery engine once these land.
    (5, "WARM SPRINGS (14)", "Brittany Jones"): 6,      # read 9
    (5, "CAMP SHERMAN (16)", "Tina Kotek"): 60,         # read 09
    (5, "ROUND BUTTE (18)", "TVC"): 37,                 # read 27
    (5, "WEST MADRAS (19)", "Tina Kotek"): 95,           # read 55
    (5, "WEST MADRAS (19)", "Miranda Weigler"): 6,      # read 9
    (5, "CIRCLE M (20)", "Tina Kotek"): 9,              # read 6
    (5, "SUNSET (22)", "WI_T"): 6,                      # read 9
    (5, "SUNSET (22)", "WI_NA"): 6,                     # read 9
    # p16 Labor Commissioner: SUTTLE TVC, LYLE Contest Total (siblings fix CT,
    # raw Undervotes 4 then balances the row), AP Stephenson digits reversed,
    # EAST MADRAS write-ins (all row/column forced)
    (16, "SUTTLE LAKE (15)", "TVC"): 6,                 # read 9
    (16, "LYLE GAP (08)", "CT"): 9,                     # read 6
    (16, "AGENCY PLAINS (23)", "Stephenson Christina E"): 81,  # read 18
    (16, "EAST MADRAS (13)", "WI_T"): 6,                # read 5
    # p8 State House 59 D: CRR Undervotes 169 (row 248+0+169=417; col 519)
    (8, "CROOKED RIVER RANCH (02)", "UNDER"): 169,      # read 199
    # p9 U.S. Senate R: 9<->6 and dropped-digit reads (row + column forced)
    (9, "KUTCHER (06)", "David Brock Smith"): 6,        # read 9
    (9, "CULVER (17)", "Deborah C Brown"): 6,           # read 9
    (9, "CULVER (17)", "Jo Rae Perkins"): 66,            # read 99
    (9, "CULVER (17)", "CT"): 197,                       # read 167
    (9, "CENTRAL (21)", "UNDER"): 68,                    # read 89
    # p18/p19/p23 LYLE Contest Total read 6, true 9 on every nonpartisan
    # contest (all CT columns sum to 7174 only with 9; row TVC 4 + UNDER 5).
    # Page-level is safe: sibling contest on the page already reads 9.
    (18, "LYLE GAP (08)", "CT"): 9,                     # read 6
    (19, "LYLE GAP (08)", "CT"): 9,                     # read 6
    # p19 CoA Pos 13 / County Assessor (side-by-side): CAMP SHERMAN Contest
    # Total 160 fixes the Assessor side (col 7204->7174); CoA13 already 160.
    # Tookey 98 / TVC 99 / UNDER 61 fix the CoA13 row (cols 3911/4063 exact).
    (19, "CAMP SHERMAN (16)", "CT"): 160,               # Assessor read 190
    (19, "CAMP SHERMAN (16)", "Doug Tookey"): 98,        # read 86
    # p20 County Commissioner 1: CAMP SHERMAN Contest Total (121+0+39=160)
    (20, "CAMP SHERMAN (16)", "CT"): 160,               # read 190
}
# Fixes for side-by-side pages where two contests share the page: the plain
# (page, precinct, label) key cannot distinguish them, so these are keyed by
# the contest's (office, district, party) as well.  Applied in preference to
# CELL_FIXES.
CONTEST_FIXES = {
    # p19 CoA Pos 13 vs County Assessor (both have MET/CAMP SHERMAN/ASHWOOD
    # rows with different Undervotes per contest)
    (("Judge of the Court of Appeals", "Position 13", ""), 19, "METOLIUS (11)", "UNDER"): 93,   # read 135
    (("Judge of the Court of Appeals", "Position 13", ""), 19, "CAMP SHERMAN (16)", "TVC"): 99,    # read 66
    (("Judge of the Court of Appeals", "Position 13", ""), 19, "CAMP SHERMAN (16)", "UNDER"): 61,  # read 19
    (("County Assessor", "", ""), 19, "CAMP SHERMAN (16)", "UNDER"): 67,   # read 79
    (("County Assessor", "", ""), 19, "ASHWOOD (05)", "UNDER"): 9,         # read 6
    # p22 Measure 120 vs 9-182: SUTTLE appears in both with different No votes
    (("Measure 120", "", ""), 22, "SUTTLE LAKE (15)", "NN"): 7,            # raw 'L'
    # p22/p23 measure fixes are contest-scoped so they don't create phantom
    # rows in the side-by-side 9-182 / 16-116 contests (which share the
    # Yes/NN/TVC/UNDER/CT labels but only list SUTTLE + CAMP SHERMAN)
    (("Measure 120", "", ""), 22, "HAYSTACK (01)", "Yes"): 27,              # read 2
    (("Measure 120", "", ""), 22, "HAYSTACK (01)", "UNDER"): 28,            # read 2
    (("Measure 120", "", ""), 22, "LYLE GAP (08)", "TVC"): 9,               # read 6
    (("Measure 120", "", ""), 22, "LYLE GAP (08)", "CT"): 9,                # read 5
    (("Measure 120", "", ""), 22, "EAST MADRAS (13)", "NN"): 880,          # raw '088'
    (("Measure 16-117", "", ""), 23, "LYLE GAP (08)", "CT"): 9,             # read 6
}
# The Turner column (OCR dropped every cell except AGENCY's 0) is left for
# the recovery engine: with the fixes above in place every row ends up with
# exactly one missing candidate, so TVC - WI_T - others derives each
# precinct's Turner votes (CRR 2, CULVER 1, all others 0; total 3).


def main():
    probe_only = "--probe" in sys.argv
    boxes = load_boxes()
    problems = []

    pages = []
    for page_no in sorted(boxes):
        page = parse_page(page_no, boxes[page_no], problems)
        if page is None:
            if probe_only:
                print(f"p{page_no}: no groups/rows -- skipped")
            continue
        pages.append(page)
        if not probe_only:
            continue
        for title, c0, c1 in assign_groups(page):
            okey = parse_office(title)
            print(f"p{page_no}: {title!r} -> {okey} cols[{c0}:{c1}]")
        print(
            "   cols:",
            ", ".join(
                f"{page['vrole'].get(i) or '?'}"
                f"{'/' + str(page['vname'].get(i)) if page['vrole'].get(i) in ('CAND', 'CAND_WI') else ''}"
                f"@{int(page['vcols'][i])}"
                for i in range(len(page["vcols"]))
            ),
        )
        for cy, key in sorted(page["anchor_keys"].items()):
            r = {
                col_label(page, i) or f"col{i}": v
                for i, v in page["rows"].get(cy, {}).items()
            }
            print(f"   {key}: {r}")
        if page["totals"]:
            print("   Totals:", {
                col_label(page, i) or f"col{i}": v
                for i, v in page["totals"].items()
            })
        print()

    if probe_only:
        for p in problems:
            print(p)
        return

    # --- assemble contests ---------------------------------------------
    # contests[okey] -> {
    #   "cols": {label: (role, first_x)},
    #   "rows": {precinct: {label: value}},
    #   "totals": {label: value},
    # }
    contests = {}
    for page in pages:
        for title, c0, c1 in assign_groups(page):
            okey = parse_office(title)
            if okey[0] is None:
                problems.append(f"p{page['page']}: UNMAPPED TITLE {title!r}")
                continue
            con = contests.setdefault(
                okey, {"cols": {}, "rows": defaultdict(dict), "totals": defaultdict(int)}
            )
            live = []
            for i in range(c0, c1):
                role = page["vrole"].get(i)
                if role == "NCF":
                    continue
                label = col_label(page, i)
                if role in ("CAND", "CAND_WI") and label is None:
                    # candidate column whose header the OCR dropped entirely
                    label = f"__unknown_{int(page['vcols'][i])}"
                elif role not in (None, "NCF") and label is None:
                    continue  # a stat column that never got a role
                if label is None:
                    continue
                live.append((i, role, label))
                if label not in con["cols"]:
                    con["cols"][label] = (role, page["vcols"][i])
            for cy, key in page["anchor_keys"].items():
                for i, role, label in live:
                    if i in page["rows"].get(cy, {}):
                        con["rows"][key][label] = page["rows"][cy][i]
                    cfix = (okey, page["page"], key, label)
                    if cfix in CONTEST_FIXES:
                        con["rows"][key][label] = CONTEST_FIXES[cfix]
                    elif (page["page"], key, label) in CELL_FIXES:
                        con["rows"][key][label] = CELL_FIXES[(page["page"], key, label)]
            for i, role, label in live:
                if i in page["totals"]:
                    con["totals"][label] += page["totals"][i]

    # --- recovery ---------------------------------------------------------
    for okey, con in contests.items():
        rows = con["rows"]
        totals = con["totals"]
        precincts = list(rows)
        cand_labels = [l for l, (r, _x) in con["cols"].items() if r == "CAND"]
        wi_labels = [l for l, (r, _x) in con["cols"].items() if r == "CAND_WI"]
        for _ in range(12):
            changed = False
            # column fills against the Totals row
            for label, total in list(totals.items()):
                missing = [p for p in precincts if label not in rows[p]]
                if not missing:
                    continue
                present = sum(rows[p][label] for p in precincts if label in rows[p])
                rem = total - present
                if rem < 0:
                    problems.append(f"{okey} col {label}: rows over-sum by {-rem}")
                    continue
                if len(missing) == 1:
                    rows[missing[0]][label] = rem
                    changed = True
                elif rem == 0:
                    for p in missing:
                        rows[p][label] = 0
                    changed = True
            # row invariants
            for p in precincts:
                r = rows[p]
                def have(*ls):
                    return all(l in r for l in ls)
                cands_here = [l for l in cand_labels if l in r]
                if "TVC" in totals and "WI_T" not in r and have("TVC") and len(cands_here) == len(cand_labels):
                    v = r["TVC"] - sum(r[l] for l in cand_labels)
                    if v >= 0:
                        r["WI_T"] = v
                        changed = True
                if "TVC" in totals and "TVC" not in r and "WI_T" in r and len(cands_here) == len(cand_labels):
                    r["TVC"] = sum(r[l] for l in cand_labels) + r["WI_T"]
                    changed = True
                if "WI_T" in totals and "WI_T" not in r and have("WI_NA") and len([l for l in wi_labels if l in r]) == len(wi_labels):
                    v = r["WI_NA"] + sum(r[l] for l in wi_labels)
                    if v >= 0:
                        r["WI_T"] = v
                        changed = True
                if "WI_T" in totals and "WI_T" not in r and have("WI_NA") and not wi_labels:
                    # no assigned write-in columns: every write-in is unassigned
                    r["WI_T"] = r["WI_NA"]
                    changed = True
                if "CT" in totals and "CT" not in r and have("TVC", "OVER", "UNDER"):
                    r["CT"] = r["TVC"] + r["OVER"] + r["UNDER"]
                    changed = True
                for miss, other in (("OVER", "UNDER"), ("UNDER", "OVER")):
                    if miss not in r and have("CT", "TVC", other):
                        v = r["CT"] - r["TVC"] - r[other]
                        if v >= 0:
                            r[miss] = v
                            changed = True
                if "TVC" in totals and "TVC" not in r and have("CT", "OVER", "UNDER"):
                    r["TVC"] = r["CT"] - r["OVER"] - r["UNDER"]
                    changed = True
                if "TVC" in totals and "WI_T" in r:
                    miss_c = [l for l in cand_labels if l not in r]
                    if len(miss_c) == 1 and have("TVC"):
                        v = r["TVC"] - r["WI_T"] - sum(r[l] for l in cand_labels if l != miss_c[0])
                        if v >= 0:
                            r[miss_c[0]] = v
                            changed = True
                if "WI_T" in totals and have("WI_T", "WI_NA"):
                    miss_w = [l for l in wi_labels if l not in r]
                    if len(miss_w) == 1:
                        v = r["WI_T"] - r["WI_NA"] - sum(r[l] for l in wi_labels if l != miss_w[0])
                        if v >= 0:
                            r[miss_w[0]] = v
                            changed = True
            if not changed:
                break
        # final validation: row invariants, unrecovered cells, column sums
        for p in precincts:
            r = rows[p]
            cands_here = [l for l in cand_labels if l in r]
            if "TVC" in r and "WI_T" in r and len(cands_here) == len(cand_labels):
                if sum(r[l] for l in cand_labels) + r["WI_T"] != r["TVC"]:
                    problems.append(f"{okey} {p}: cands+WI != TVC")
            if all(k in r for k in ("TVC", "OVER", "UNDER", "CT")):
                if r["TVC"] + r["OVER"] + r["UNDER"] != r["CT"]:
                    problems.append(f"{okey} {p}: TVC+over+under != CT")
            # WI_NA is intentionally dropped from output; everything else
            # that the contest printed must be recovered
            for label in list(cand_labels) + list(wi_labels) + [
                "WI_T", "TVC", "OVER", "UNDER", "CT",
            ]:
                if label in totals and label not in r:
                    problems.append(f"{okey} {p}: UNRECOVERED {label}")
        for label, total in totals.items():
            present = sum(rows[p][label] for p in precincts if label in rows[p])
            complete = all(label in rows[p] for p in precincts)
            if complete and present != total:
                problems.append(
                    f"{okey} col {label}: precincts sum {present} != Totals {total} (misread cell?)"
                )
        if os.environ.get("JEFF_DEBUG"):
            print(f"DEBUG {okey} totals={dict(totals)}")
            for p in precincts:
                print(f"DEBUG {p} {dict(rows[p])}")

    # --- name canonicalization ------------------------------------------
    # Fixes are applied PER CONTEST against that contest's county-baseline
    # candidate list, so a local (non-county-file) candidate can never be
    # pulled onto a similar statewide name from another contest.
    county = county_rows()
    fixes = dict(WI_NAME_FIXES)
    fixes["Turner"] = "DeAngelo Leroy Turner"  # p12 header mostly dropped
    # four headers the OCR rendered surname-first; difflib can't see token
    # order (ratio ~0.5), so map them to the county-abstract spellings
    fixes["McAlmond Russell"] = "Russell McAlmond"
    fixes["Rasmussen Dawn"] = "Dawn Rasmussen"
    fixes["Stephenson Christina E"] = "Christina E. Stephenson"
    fixes["Dalrymple Hope A"] = "Hope A. Dalrymple"
    fixes["Rosenbaum Zeva"] = "Zeva Rosenbaum"
    fixes["Lockwood Jonathan"] = "Jonathan Lockwood"
    for okey, con in contests.items():
        labels = {
            l for l, (role, _x) in con["cols"].items()
            if role in ("CAND", "CAND_WI") and not l.startswith("__unknown")
        }
        if okey in county:
            fixes.update(canonical_fixes(labels, [c for c, _v in county[okey]]))

    # unknown candidate columns: match column totals to county candidates
    for okey, con in contests.items():
        unknown = sorted(
            (x, l) for l, (role, x) in con["cols"].items()
            if role == "CAND" and l.startswith("__unknown")
        )
        if not unknown or okey not in county:
            continue
        claimed = {fixes.get(l, l) for l in con["cols"] if not l.startswith("__unknown")}
        left = [(c, v) for c, v in county[okey] if c not in claimed]
        for _x, label in unknown:
            total = con["totals"].get(label)
            exact = [c for c, v in left if v == total]
            if len(exact) == 1:
                fixes[label] = exact[0]
                left = [(c, v) for c, v in left if c != exact[0]]
            elif len(left) == 1 and len(unknown) == 1:
                fixes[label] = left[0][0]
                left = []

    # --- emit --------------------------------------------------------------
    rows_out = []
    for okey in sorted(contests):
        office, district, party = okey
        con = contests[okey]
        labels_by_x = sorted(con["cols"], key=lambda l: con["cols"][l][1])
        for precinct in sorted(con["rows"]):
            r = con["rows"][precinct]
            for label in labels_by_x:
                role, _x = con["cols"][label]
                if role not in ("CAND", "CAND_WI"):
                    continue
                name = fixes.get(label, label)
                if name in ("NN", "N"):
                    name = "No"  # measure No columns OCR as NN / bare N
                if label not in r:
                    problems.append(f"{okey} {precinct}: MISSING {label!r}")
                    continue
                rows_out.append((precinct, office, district, party, name, r[label]))
            if "WI_T" in r and not office.startswith("Measure"):
                # measure pages have no write-in column; any WI_T there is an
                # engine-derived 0, and measure rows are Yes/No only
                rows_out.append((precinct, office, district, party, "Write-ins", r["WI_T"]))
            for label, role in (("Over Votes", "OVER"), ("Under Votes", "UNDER")):
                if r.get(role):
                    rows_out.append((precinct, office, district, party, label, r[role]))

    PSEUDO = {"Write-ins", "Over Votes", "Under Votes"}
    csv_rows = []
    for p, o, d, pa, c, v in rows_out:
        if c in PSEUDO or c in ("Yes", "No") or "(WI)" in c:
            # format_candidate_name strips (WI) markers; keep them
            name = c
        else:
            name = format_candidate_name(c.split())
        csv_rows.append(make_row(COUNTY, p, o, d, pa, name, v))

    path = "2026/counties/20260519__or__primary__jefferson__precinct.csv"
    write_csv(csv_rows, path)
    print(f"wrote {len(csv_rows)} rows to {path}")
    if problems:
        print(f"\n{len(problems)} problems:")
        for p in problems:
            print(" -", p)


if __name__ == "__main__":
    main()