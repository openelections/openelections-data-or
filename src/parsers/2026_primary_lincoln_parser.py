#!/usr/bin/env python3
"""Build the Lincoln County 2026 primary precinct CSV from PaddleOCR output.

Source: three image PDFs in openelections-sources-or, OCR'd with PaddleOCR
(PP-OCRv6) into ``.paddleocr_cache/Lincoln/<stem>_pNNN.json`` (each a list of
``{t, b:[x1,y1,x2,y2]}`` text boxes).  See CLAUDE.md / module docstring below.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CACHE = Path(".paddleocr_cache/Lincoln")
OUT = Path("2026/counties/20260519__or__primary__lincoln__precinct.csv")
COUNTY_CSV = Path("2026/20260519__or__primary__county.csv")
COUNTY = "Lincoln"

PRECINCTS = [
    "01 WALDPORT", "02 ALSEA", "03 SEAVIEW", "04 TIDEWATER", "05 YACHATS",
    "06 BAYVIEW", "07 SEAL ROCK", "08 SOUTH BEACH", "09 NYE CREEK",
    "10 NEWPORT BAY", "11 OCEANVIEW", "12 YAQUINA", "13 PACIFIC",
    "14 AGATE BEACH", "15 OTTER ROCK", "16 DEPOE BAY", "17 FOGARTY CREEK",
    "18 KERN", "19 SCHOONER CREEK", "20 DELAKE", "21 OCEANLAKE",
    "22 SUNSET WEST", "23 SUNSET EAST", "24 ROSE LODGE", "25 BIG ELK",
    "26 ELK CITY", "27 FRUITVALE", "28 EDDYVILLE", "29 SILETZ", "30 ROCK CREEK",
    "31 EAST TOLEDO", "32 SOUTH TOLEDO",
]
NAME_TO_ID = {re.sub(r"[^A-Z0-9]", "", p[3:]): p for p in PRECINCTS}

CANDIDATES = {
    ("U.S. Senate", "", "D"): ["Jeff Merkley", "Paul Damian Wells"],
    ("U.S. Senate", "", "R"): ["Brent Barker", "Deborah C Brown",
                               "David A Burch", "Russell McAlmond",
                               "Jo Rae Perkins", "Timothy Skelton",
                               "David Brock Smith"],
    ("U.S. House", "4", "D"): ["Daniel B. Bahlen", "Melissa Bird", "Val Hoyle"],
    ("U.S. House", "4", "R"): ["Monique DeSpain", "Stefan G. Strek"],
    ("Governor", "", "D"): ["Forest (Fora) Alexander", "James Atkinson IV",
                            "Cal Kishawi", "Tina Kotek", "Donnie M Beckwith",
                            "David W Beem", "Steve William Laible",
                            "Brittany Jones", "Tristan Sheppard",
                            "Miranda Weigler"],
    ("Governor", "", "R"): ["Danielle Bethell", "Hope A Dalrymple", "Ed Diehl",
                           "Christine Drazan", "Chris Dudley", "Kyle M Duyck",
                           "David Medina", "Robert Neuman", "Brad T Peters",
                           "Paul J. Romero Jr", "Wen Waddell",
                           "Martin Ward", "Tim O Youker", "DeAngelo Leroy Turner"],
    ("State House", "10", "D"): ["David Gomberg"],
    ("Labor Commissioner", "", ""): ["Chris Lynch", "Christina E Stephenson"],
    ("Judge of the Supreme Court", "Position 4", ""): ["Christopher L. Garrett"],
    ("Judge of the Court of Appeals", "Position 1", ""): ["Ryan T O'Connor"],
    ("Judge of the Court of Appeals", "Position 9", ""): ["Jacqueline Kamins"],
    ("Judge of the Court of Appeals", "Position 12", ""): ["Erin C Lagesen"],
    ("Judge of the Court of Appeals", "Position 13", ""): ["Doug Tookey"],
    ("Judge of the Circuit Court", "17th District, Position 1", ""):
        ["Sheryl M. Bachart"],
    ("Judge of the Circuit Court", "17th District, Position 2", ""):
        ["Marcia Buckley"],
    # County Commissioner (nonpartisan): names/seat order from the County
    # Candidates PDF headers, confirmed against the certified results.  The
    # columns run left-to-right Brubaker..Earls; OCR split some names across
    # two boxes (Carter McEntee, Eddie Townsend) so the canonical first-last
    # order is set explicitly here.
    ("County Commissioner", "Position 1", ""): ["Cheri Brubaker", "Casey Miller",
        "Nicholle Moody", "Carter McEntee", "Cathie Rigby", "Dru Earls"],
    ("County Commissioner", "Position 2", ""): ["Marci Baker", "Cristen Don",
        "Joe D Steere", "Eddie Townsend"],
    ("County Commissioner", "Position 3", ""): ["Walter Chuck Jr.", "Curtis Landers"],
}
# county-office contests are read from headers (not in CANDIDATES); their
# candidate names come straight from the OCR header boxes.

INT_RE = re.compile(r"^\d{1,6}$")
PREC_LABEL_RE = re.compile(r"^\d{0,2}\s+([A-Z][A-Z .'/-]+)$")
COL_GAP = 90          # within-band column clustering
ROW_TOL = 48          # y-tolerance for "same row" as a precinct label
ANCHOR_TOL = 70       # x-tolerance to assign a value to a column anchor
SLOT_TOL = 95         # x-tolerance for short-precinct nearest-slot fallback

# stat header text (normalized) -> emitted pseudo-candidate (or None=skip)
def _stat_map():
    d = {}
    for k in ("write-in totals", "writeintotals", "write in totals",
              "writeintotal"):
        d[k] = "Write-ins"
    for k in ("overvotes", "over votes", "overvotes"):
        d[k] = "Over Votes"
    for k in ("undervotes", "under votes", "undervotes"):
        d[k] = "Under Votes"
    for k in ("total votes cast", "totalvotescast", "total votes", "cast",
              "totalvotec", "contest total", "contesttotal"):
        d[k] = None
    return d
STAT_MAP = _stat_map()

NAV = ("certify", "abstract", "tally of votes", "dated this", "county clerk",
       "lincoln county, oregon", "primary election", "page ", "of 31",
       "of 11", "of 26", "candidates and measures", "official abstract")


def cx(b): return (b[0] + b[2]) // 2
def cy(b): return (b[1] + b[3]) // 2
def nkey(t): return re.sub(r"[^a-z]", "", t.lower())


def precinct_of(label):
    s = label.strip()
    # OCR sometimes prefixes a precinct label with a stray letter (e.g.
    # "D7 SEAL ROCK"); drop a leading run of non-digits that is immediately
    # followed by the precinct number.
    s = re.sub(r"^[^0-9]+(?=[0-9])", "", s)
    m = PREC_LABEL_RE.match(s)
    if m:
        pid = NAME_TO_ID.get(re.sub(r"[^A-Z0-9]", "", m.group(1)))
        if pid:
            return pid
    # Fallback: the precinct number may be lost or mangled (e.g. "DEPOE BAY"
    # with no number, or "O DELAKE" where "20" was read as the letter O).
    # Match the alphabetic part directly, then try dropping one leading
    # OCR-garbage letter.
    alpha = re.sub(r"[^A-Za-z]", "", s).upper()
    if alpha in NAME_TO_ID:
        return NAME_TO_ID[alpha]
    if len(alpha) > 3 and alpha[1:] in NAME_TO_ID:
        return NAME_TO_ID[alpha[1:]]
    return None


def load_page(stem, page):
    p = CACHE / f"{stem}_p{page:03d}.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------------------
# Page -> bands
# ---------------------------------------------------------------------------
def parse_page(boxes):
    """Return (precinct_rows, bands) for one page.

    precinct_rows: list of (y_center, precinct_id) sorted by y.
    bands: list of dict {wit_x, lo, hi, header:[boxes], cand_anchors:[x...],
    stat_anchors:{Write-ins/OVer Votes/Under Votes: x}} in x order.
    """
    precs = []
    for d in boxes:
        t = d["t"].strip()
        if d["b"][0] < 340 and t and not any(n in t.lower() for n in NAV):
            pid = precinct_of(t)
            if pid:
                precs.append((cy(d["b"]), pid, d["b"]))
    precs.sort()
    if not precs:
        return [], []
    first_row_y = precs[0][0]
    header = [d for d in boxes if d["t"].strip() and d["b"][1] < first_row_y
              and d["b"][0] > 340 and not any(n in d["t"].lower() for n in NAV)]
    votes = [d for d in boxes if d["b"][0] > 340 and d["b"][1] >= first_row_y
             and INT_RE.match(d["t"].strip())]
    # stat label header boxes
    stat_lbl = []  # (x, pseudo, text)
    for d in header:
        k = nkey(d["t"])
        if k in STAT_MAP:
            stat_lbl.append((cx(d["b"]), STAT_MAP[k], d["t"]))
    wits = sorted([s for s in stat_lbl if s[1] == "Write-ins"], key=lambda s: s[0])
    cts = sorted([s for s in stat_lbl if s[1] is None and "contest" in nkey(s[2])],
                 key=lambda s: s[0])
    # vote x-centers (used for per-band candidate-column clustering)
    vote_xs = sorted({cx(d["b"]) for d in votes})
    # stat-label x-centers (for excluding stat columns from candidate anchors)
    stat_xs = sorted(s[0] for s in stat_lbl)

    def cluster_cand(xmin, xmax):
        """Cluster vote x-centers in [xmin, xmax); return centers of clusters
        that are NOT within 90px of a stat label.

        The 90px tolerance absorbs the systematic rightward drift of the
        Contest-Total column of the *previous* contest, which bleeds into the
        left edge of this contest's candidate region (its data can sit ~85px
        right of its header label).  Real candidate columns sit well clear of
        the stat block, so they are not affected."""
        groups = []
        cl = None
        for x in vote_xs:
            if x < xmin or x >= xmax:
                continue
            if cl is not None and x - cl[-1] <= COL_GAP:
                cl.append(x)
            else:
                if cl:
                    groups.append(cl)
                cl = [x]
        if cl:
            groups.append(cl)
        centers = [sum(g) // len(g) for g in groups]
        return [c for c in centers
                if not any(abs(c - sx) <= 90 for sx in stat_xs)]

    # ---- contest delimiters ------------------------------------------------
    # Every contest ends with a "Contest Total" column, so the Contest-Total
    # header labels are the reliable contest boundaries (one per contest,
    # present even for write-in-less contests like ballot measures).  WIT
    # labels are NOT reliable delimiters because measures have no write-in
    # column.
    ct_lbl = cts  # (x, None, text) for "Contest Total", sorted by x

    def whole_width_band():
        all_centers = []
        cl = None
        for x in vote_xs:
            if cl is not None and x - cl[-1] <= COL_GAP:
                cl.append(x)
            else:
                if cl:
                    all_centers.append(cl)
                cl = [x]
        if cl:
            all_centers.append(cl)
        col_centers = [sum(g) // len(g) for g in all_centers]
        return {"wit_x": None, "lo": 340,
                "hi": col_centers[-1] + 80 if col_centers else 2000,
                "header": header, "cand_anchors": col_centers,
                "stat_anchors": {}, "stat_slots": []}

    if not ct_lbl:
        # No Contest-Total on this page.  Two cases:
        #  (a) A wide contest (Governor) whose Contest-Total column was pushed
        #      onto its own page -- here the page still has the WIT/TVC/OV/UV
        #      stat labels, so split into candidate columns + that stat block.
        #  (b) A candidate-only column-split page with no stats at all.
        if stat_lbl:
            cstats = sorted(stat_lbl, key=lambda s: s[0])
            first_stat_x = cstats[0][0]
            cand = cluster_cand(340, first_stat_x)
            stat_slots = [s[1] for s in cstats]
            sa = {p: x for x, p, _ in cstats if p is not None}
            bands = [{"wit_x": None, "lo": 340, "hi": cstats[-1][0] + 80,
                      "header": header, "cand_anchors": cand,
                      "stat_anchors": sa, "stat_slots": stat_slots}]
        else:
            bands = [whole_width_band()]
    else:
        bands = []
        start = 340
        for ci, (end_x, _, _) in enumerate(ct_lbl):
            # stat labels in (start, end_x] -- this contest's own stat block,
            # ending at its Contest Total.  Sorted by x gives the column order:
            # [WIT?, TVC, Overvotes, Undervotes, Contest Total].
            cstats = sorted([s for s in stat_lbl if start < s[0] <= end_x],
                            key=lambda s: s[0])
            first_stat_x = cstats[0][0] if cstats else end_x
            cand = cluster_cand(start, first_stat_x)
            stat_slots = [s[1] for s in cstats]
            sa = {}
            wit_x = None
            for x, pseudo, txt in cstats:
                if pseudo == "Write-ins":
                    wit_x = x
                if pseudo is not None:
                    sa.setdefault(pseudo, x)
            hh = [d for d in header if start - 80 <= cx(d["b"]) <= end_x + 80]
            bands.append({"wit_x": wit_x, "lo": start, "hi": end_x,
                          "header": hh, "cand_anchors": cand,
                          "stat_anchors": sa, "stat_slots": stat_slots})
            start = end_x

    # ---- orphan leading precinct rows ------------------------------------
    # On the wide Governor candidate pages the first precinct's label sits so
    # high it merges into the header and OCR drops it, but its vote row still
    # floats just above the first *labeled* precinct.  Detect such rows
    # (integer groups in the strip immediately above the first labeled row
    # whose column count matches the page's slot plan) and synthesize the
    # missing precinct ids as the predecessors of the first labeled precinct.
    n_slots = sum(len(b["cand_anchors"]) + len(b["stat_slots"]) for b in bands)
    if n_slots >= 3 and precs:
        idx0 = PRECINCTS.index(precs[0][1])
        ys = sorted(cy(d["b"]) for d in boxes
                    if d["b"][0] > 340
                    and first_row_y - 160 < cy(d["b"]) < first_row_y
                    and INT_RE.match(d["t"].strip()))
        rows = []  # (y, count) clustered by ROW_TOL gaps
        for y in ys:
            if rows and y - rows[-1][0] <= ROW_TOL:
                rows[-1] = (rows[-1][0], rows[-1][1] + 1)
            else:
                rows.append((y, 1))
        orph = [y for y, c in rows if c == n_slots]
        k = len(orph)
        if k and idx0 - k >= 0:
            for i, y in enumerate(orph):
                precs.append((y, PRECINCTS[idx0 - k + i], None))
            precs.sort()
    return precs, bands


def detect_contest(band):
    """Return (office, district, party) from a band's header text, or None."""
    text = " ".join(d["t"] for d in sorted(band["header"],
                  key=lambda d: (d["b"][1], cx(d["b"])))).lower()
    party = ""
    # Oregon's abstract labels party as "DEM"/"REP" (abbreviated), not the
    # full words.  OCR sometimes keeps the space ("DEM Governor") -- matched
    # by the word-boundary form -- and sometimes fuses it to the office
    # ("DEMGovernor"/"REPGovernor"), which has no boundary after dem/rep.
    # The fused form is caught by matching dem/rep directly glued to an
    # office keyword; this never fires on "representative"/"republican"
    # (those would need "rep"+"representative" = "reprepresentative").
    if re.search(r"\bdem(ocrat|ic)?\b|dem(?:governor|senator|representative|congress)", text):
        party = "D"
    elif re.search(r"\brep(ublican)?\b|rep(?:governor|senator|representative|congress)", text):
        party = "R"
    # nonpartisan has no party column text but the office is judicial/labor
    pos = re.search(r"position\s*(\d+)", text)
    posn = pos.group(1) if pos else ""
    dist = re.search(r"(\d+)(?:st|nd|rd|th)\s*district", text)
    distn = dist.group(1) if dist else ""
    if "supreme" in text:
        return ("Judge of the Supreme Court", f"Position {posn}" if posn else "", "")
    if "appeals" in text or "appeais" in text or "appea" in text:
        return ("Judge of the Court of Appeals", f"Position {posn}" if posn else "", "")
    if "circuit" in text or "chfcuit" in text or "circyit" in text:
        d = (f"{_ord(distn)} District, Position {posn}" if distn and posn
             else f"Position {posn}" if posn else "")
        return ("Judge of the Circuit Court", d, "")
    if "bureau" in text and "labor" in text:
        return ("Labor Commissioner", "", "")
    if "governor" in text:
        return ("Governor", "", party)
    if "measure" in text:
        m = re.search(r"measure\s*(\d+)", text)
        return (f"Measure {m.group(1)}" if m else "Measure 120", "", "")
    if "state senator" in text or ("senator" in text and "state" in text):
        return ("State Senate", distn, party)
    if "senator" in text:
        return ("U.S. Senate", "", party)
    if "state representative" in text or ("representative" in text and "state" in text):
        return ("State House", distn, party)
    if "representative" in text or "congress" in text:
        return ("U.S. House", distn, party)
    if "attorney general" in text:
        return ("Attorney General", "", party)
    if "secretary of state" in text:
        return ("Secretary of State", "", party)
    if "treasurer" in text and "state" in text:
        return ("State Treasurer", "", party)
    # county offices
    if "county commissioner" in text:
        return ("County Commissioner", f"Position {posn}" if posn else "", party)
    if "sheriff" in text:
        return ("Sheriff", "", party)
    if "county clerk" in text:
        return ("County Clerk", "", party)
    if "assessor" in text:
        return ("Assessor", "", party)
    if "treasurer" in text:
        return ("County Treasurer", "", party)
    if "surveyor" in text:
        return ("County Surveyor", "", party)
    if "district attorney" in text:
        return ("District Attorney", "", party)
    return None


def _ord(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else ['th','st','nd','rd'][min(n%10,4)]}"


# ---------------------------------------------------------------------------
# Value extraction (per-precinct positional assignment)
# ---------------------------------------------------------------------------
def row_values_all(boxes, lo_y, hi_y):
    """Integer values (x_center, value) whose y-center lies in [lo_y, hi_y]
    and that sit right of the precinct-label column (x>340).

    A precinct's data row drifts downward as you descend the page -- the
    rightmost candidate columns and the Write-ins/Over/Under stat block sit
    well below the precinct label -- so the window is asymmetric rather than
    a fixed symmetric band around the label.  A symmetric band drops the
    bottom-of-page precincts' rightmost columns and their stats.  The caller
    bounds ``hi_y`` to just before the next precinct's row (or above the
    Totals row for the last precinct) so a widened down-tolerance can never
    bleed into the next precinct's data.
    """
    out = []
    for d in boxes:
        b = d["b"]
        if b[0] > 340 and lo_y <= cy(b) <= hi_y \
           and INT_RE.match(d["t"].strip()):
            out.append((cx(b), int(d["t"].strip())))
    out.sort()
    return out


def _page_slot_plan(bands):
    """Ordered list of (band_index, slot) for every column on a page.

    Contests (bands) are laid out left-to-right; within a contest the
    candidate columns come first, then the stat columns in x-order.  A slot
    is either ('cand', idx) or ('stat', pseudo) or ('stat', None) (the
    TVC/Contest-Total columns, which are recognized but not emitted)."""
    plan = []
    for bi, b in enumerate(bands):
        for j in range(len(b["cand_anchors"])):
            plan.append((bi, ("cand", j)))
        for pseudo in b["stat_slots"]:
            plan.append((bi, ("stat", pseudo)))
    return plan


def extract_page(boxes, precs, bands):
    """Return {band_index: {precinct_id: rec}} where rec maps candidate
    index -> votes and stat pseudo ('Write-ins'/'Over Votes'/'Under Votes')
    -> votes.

    Columns drift right systematically with precinct row (y), so aggregating
    x across precincts makes adjacent columns overlap.  But within a single
    precinct row the columns are locally well-ordered and well-separated, so
    we assign values positionally: sort the precinct's values left-to-right
    and slot them across the page's column plan.  Precincts short of the full
    column count (OCR dropped a value) fall back to nearest-median-x, which a
    missing column leaves at 0.
    """
    plan = _page_slot_plan(bands)
    n_slots = len(plan)
    # Bottom summary "Totals" row y (left column, like a precinct label), if
    # this page carries one.  Used to bound the last precinct's down-window so
    # the widened tolerance cannot absorb the Totals row.
    totals_y = next((cy(d["b"]) for d in boxes
                     if d["b"][0] < 340
                     and d["t"].strip().lower() == "totals"), None)
    pys = [y for y, _, _ in precs]  # precs is sorted by y
    # Detect this page's data-row offset relative to the precinct labels.
    # The abstract uses two layouts: on most contest pages the data row sits a
    # little below the label and drifts further down across the row (rightmost
    # columns / stat block end up ~90px below the label); on some pages (e.g.
    # the side-by-side judicial contests) the data row sits ~10px ABOVE the
    # label and is compact.  A fixed symmetric band under-counts the first
    # layout (drops the drifted rightmost columns) and a widened down-band
    # double-counts the second (swallows the next precinct's row, which on
    # those pages sits ~80px below the label).  So we measure the signed
    # offset of each label's nearest integer-box row and only widen the
    # down-tolerance when the data is at or below the label.
    int_ys = sorted(cy(d["b"]) for d in boxes
                    if d["b"][0] > 340 and INT_RE.match(d["t"].strip()))
    row_clusters = []  # (y, count) of integer-box rows, clustered by 25px gaps
    for yy in int_ys:
        if row_clusters and yy - row_clusters[-1][0] <= 25:
            row_clusters[-1] = (row_clusters[-1][0], row_clusters[-1][1] + 1)
        else:
            row_clusters.append((yy, 1))
    data_rows = [y for y, c in row_clusters if c >= 3]
    offsets = []
    for ly in pys:
        near = min(data_rows, key=lambda ry: abs(ry - ly)) if data_rows else None
        if near is not None and abs(near - ly) <= 70:
            offsets.append(near - ly)
    import statistics
    med_off = statistics.median(offsets) if len(offsets) >= 3 else 0
    extend_down = med_off >= -5
    rows = {}
    n = len(precs)
    for i, (y, pid, _) in enumerate(precs):
        lo_y = y - ROW_TOL
        if extend_down:
            # data at/below label: capture the drifted rightmost columns + stat
            # block, capped below the next precinct's row and the Totals row.
            hi_y = y + 90
            if i < n - 1:
                hi_y = min(hi_y, pys[i + 1] - 10)
            elif totals_y is not None and totals_y > y:
                hi_y = min(hi_y, totals_y - 10)
        else:
            # data above/around label (compact rows): keep the symmetric band.
            hi_y = y + ROW_TOL
        rows[pid] = row_values_all(boxes, lo_y, hi_y)

    # Median x per slot, computed from full-count precincts (the majority).
    med_x = [0] * n_slots
    full = [v for v in rows.values() if len(v) == n_slots]
    if full:
        for j in range(n_slots):
            xs = sorted(v[j][0] for v in full)
            med_x[j] = xs[len(xs) // 2]

    out = {bi: {} for bi in range(len(bands))}
    for pid, vals in rows.items():
        if n_slots and len(vals) == n_slots:
            pairs = list(zip(vals, plan))
        elif n_slots:
            # short precinct: assign each value to nearest median-x slot
            pairs = []
            used = set()
            for x, v in vals:
                order = sorted(range(n_slots), key=lambda j: abs(x - med_x[j]))
                for j in order:
                    if j in used:
                        continue
                    if abs(x - med_x[j]) <= SLOT_TOL:
                        pairs.append(((x, v), plan[j]))
                        used.add(j)
                        break
        else:
            pairs = []
        for (x, v), (bi, slot) in pairs:
            rec = out[bi].setdefault(pid, {})
            kind, key = slot
            if kind == "cand":
                rec[key] = v
            elif key is not None:  # stat (skip None = TVC/Contest Total)
                rec[key] = v
    return out


# ---------------------------------------------------------------------------
# Contest assembly
# ---------------------------------------------------------------------------
def build_contests(stems):
    """Return {contest_key: [entry, ...]} where each entry is
    (page_index, stem, page, bi, band, contest_key, result) and result is
    {precinct_id: {cand_idx: v, 'Write-ins': v, ...}} for that band/page."""
    entries = []
    pi = 0
    for stem in stems:
        page = 1
        while True:
            boxes = load_page(stem, page)
            if boxes is None:
                break
            precs, bands = parse_page(boxes)
            per_band = extract_page(boxes, precs, bands)
            for bi, band in enumerate(bands):
                ck = detect_contest(band)
                # The County Candidates PDF repeats the state judicial offices
                # (Circuit Court) that the State PDF already carries; keep only
                # county-level offices from the County stem to avoid
                # double-counting the same contest from both PDFs.
                if "County" in stem and ck and ck[0].startswith("Judge of"):
                    continue
                # Skip Contest-Total-only pages (e.g. Governor CT pages): they
                # hold no candidate votes and no useful stats (only the
                # Contest Total, which is not emitted), but their precincts
                # overlap the candidate pages and would corrupt the merge.
                if not band["cand_anchors"] and not any(
                        s in ("Write-ins", "Over Votes", "Under Votes")
                        for s in band["stat_slots"]):
                    continue
                if ck is None:
                    print(f"WARN: {stem} p{page} band{bi}: could not detect "
                          f"contest; header= "
                          f"{' '.join(d['t'] for d in band['header'])[:160]}",
                          file=sys.stderr)
                    continue
                res = per_band.get(bi, {})
                entries.append((pi, stem, page, bi, band, ck, res))
            page += 1
            pi += 1
    # group by contest_key (office, district, party) preserving doc order
    contests = defaultdict(list)
    for e in entries:
        contests[e[5]].append(e)
    return contests


def merge_contest(ck, entries):
    """Merge a contest's band entries into per-precinct candidate+stat values.

    Pages of a contest come in two flavors:
      * precinct-split -- different precincts, the SAME candidate columns (the
        usual case: a 32-precinct contest split 1-17 / 18-32 across two pages).
      * column-split -- the SAME precincts, DIFFERENT candidate columns (wide
        contests like Governor, whose 10-13 candidates don't fit in one page
        width).

    Column-split pages have OVERLAPPING precinct sets; precinct-split pages
    have DISJOINT precinct sets.  So we group entries whose precinct sets
    intersect -- each such group is one "precinct region" whose pages are
    column-splits, ordered by page, and candidate indices accumulate across
    the column-splits in a region.  Disjoint regions (precinct-splits) share
    the same index ranges because their column structure is identical.
    """
    # group entries by precinct-set intersection (column-split partners)
    regions = []  # list of [entries]
    for e in sorted(entries, key=lambda e: e[0]):
        pset = set(e[6].keys())
        placed = False
        for reg in regions:
            if pset & set(reg[0][6].keys()):
                reg.append(e)
                placed = True
                break
        if not placed:
            regions.append([e])
    out = defaultdict(dict)
    max_idx = 0
    for reg in regions:
        start = 0
        for e in sorted(reg, key=lambda e: e[0]):
            _, stem, page, bi, band, _, res = e
            ncol = len(band["cand_anchors"])
            for pid, rec in res.items():
                for ai, v in rec.items():
                    if isinstance(ai, int):
                        out[pid][start + ai] = v
                    else:
                        out[pid][ai] = v
            start += ncol
        max_idx = max(max_idx, start)
    return out, max_idx


STAT_NAMES = ["Write-ins", "Over Votes", "Under Votes"]


def emit_contest(rows, ck, per_precinct, cand_total):
    office, district, party = ck
    canon = CANDIDATES.get(ck)
    is_measure = office.startswith("Measure ")
    if canon is not None:
        names = canon
    elif is_measure:
        names = ["Yes", "No"]
    else:
        names = None
    n_expected = len(names) if names else cand_total
    # A "No Candidate Filed" contest (e.g. State House 10 R) has only a
    # zero placeholder candidate column plus Write-in/Over/Under stats; emit
    # just the stat rows and no per-candidate rows.
    all_zero = names is None and n_expected > 0 and all(
        rec.get(i, 0) == 0 for rec in per_precinct.values()
        for i in range(n_expected))
    for pid, rec in per_precinct.items():
        if not all_zero:
            if names:
                for i, name in enumerate(names):
                    rows.append(_row(pid, office, district, party, name,
                                     rec.get(i, 0)))
            else:
                for i in range(n_expected):
                    rows.append(_row(pid, office, district, party,
                                     f"cand{i}", rec.get(i, 0)))
        for s in STAT_NAMES:
            v = rec.get(s)
            if v and v > 0:
                rows.append(_row(pid, office, district, party, s, v))


def _row(pid, office, district, party, cand, v):
    return {"county": COUNTY, "precinct": pid, "office": office,
            "district": district, "party": party, "candidate": cand,
            "votes": str(v)}


def main():
    # The third PDF ("Republican State Rep. Write-ins") is the per-precinct
    # breakdown of the State House 10 R write-in names behind the aggregate
    # "Write-ins" total that the State PDF already carries (p30/p31).  The
    # precinct CSV uses that aggregate, so the write-in detail is not parsed.
    stems = [
        "State Candidates & Measures",
        "County Candidates & Measures",
    ]
    contests = build_contests(stems)
    rows = []
    for ck, entries in contests.items():
        per, cand_total = merge_contest(ck, entries)
        emit_contest(rows, ck, per, cand_total)
    rows.sort(key=lambda r: (r["office"], r["district"], r["party"],
                             r["candidate"], r["precinct"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["county", "precinct", "office",
                                          "district", "party", "candidate",
                                          "votes"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")
    validate(rows)


def validate(rows):
    """Compare per-contest precinct totals to county.csv where available."""
    if not COUNTY_CSV.exists():
        return
    county = defaultdict(int)
    with COUNTY_CSV.open() as f:
        for r in csv.DictReader(f):
            if r["county"].strip() == COUNTY:
                county[(r["office"].strip(), r["district"].strip(),
                        r["party"].strip(), r["candidate"].strip())] += \
                        int(r["votes"])
    agg = defaultdict(int)
    for r in rows:
        agg[(r["office"], r["district"], r["party"], r["candidate"])] += \
            int(r["votes"])
    print("\n=== validation vs county.csv ===")
    keys = sorted(set(agg) | set(county))
    for k in keys:
        a = agg.get(k, 0)
        c = county.get(k, 0)
        if a != c:
            print(f"  {k}: parsed={a} county={c}  {'(not in county)' if c==0 else ''}")
    print("=== contest list ===")
    for k in sorted(agg):
        print("  ", k, agg[k])


if __name__ == "__main__":
    main()