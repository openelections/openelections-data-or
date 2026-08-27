#!/usr/bin/env python3
"""Map Lincoln OCR pages to contests.

For each page: precinct rows (by name), then contest BANDS detected via the
"Write-in Totals"/"Contest Total" stat labels.  Each band prints its x-range,
candidate-column count, stat labels, and the office/party header text inside
the band.  Output is a compact contest map for designing/verifying the parser.
"""
import glob
import json
import re
import sys
from pathlib import Path

CACHE = Path(".paddleocr_cache/Lincoln")

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
PREC_LABEL_RE = re.compile(r"^\d{0,2}\s+([A-Z][A-Z .'/-]+)$")
INT_RE = re.compile(r"^\d{1,6}$")

STAT_WORDS = {
    "write-in totals": "WIT", "writeintotals": "WIT",
    "total votes cast": "TVC", "totalvotescast": "TVC",
    "total votes": "TVC", "cast": "TVC",
    "overvotes": "OV", "undervotes": "UV",
    "contest total": "CT", "contesttotal": "CT",
}
OFFICE_WORDS = ("senator", "representative", "governor", "judge", "commissioner",
                "measure", "president", "treasurer", "attorney", "auditor",
                "labor", "supreme", "appeals", "circuit", "senate", "house",
                "clerk", "sheriff", "assessor", "surveyor", "coroner",
                "constable", "district attorney")


def cx(b): return (b[0] + b[2]) // 2
def cy(b): return (b[1] + b[3]) // 2
def nkey(t): return re.sub(r"[^a-z]", "", t.lower())


def precinct_of(label):
    m = PREC_LABEL_RE.match(label.strip())
    if not m:
        return None
    return NAME_TO_ID.get(re.sub(r"[^A-Z0-9]", "", m.group(1)))


def summarize(path):
    data = json.loads(Path(path).read_text())
    name = Path(path).name
    # precinct rows
    precs = []
    for d in data:
        t = d["t"].strip()
        if d["b"][0] < 340 and t and "Page" not in t and "Candidates" not in t \
           and "Abstract" not in t and "certify" not in t:
            pid = precinct_of(t)
            if pid:
                precs.append((cy(d["b"]), pid))
    precs.sort()
    if not precs:
        print(f"\n### {name}  (no precincts)")
        return
    first_row_y = precs[0][0]
    pnames = [p[1] for _, p in precs]
    # header + vote boxes
    header = [d for d in data if d["t"].strip() and d["b"][1] < first_row_y
              and d["b"][0] > 340]
    votes = [d for d in data if d["b"][0] > 340 and d["b"][1] >= first_row_y
             and INT_RE.match(d["t"].strip())]
    # stat label header boxes (by normalized key)
    stat_x = []  # (x, code, text)
    for d in header:
        k = nkey(d["t"])
        if k in STAT_WORDS:
            stat_x.append((cx(d["b"]), STAT_WORDS[k], d["t"]))
    # detect bands: each WIT starts a stat block.
    wits = sorted([s for s in stat_x if s[1] == "WIT"], key=lambda s: s[0])
    # vote column x-centers (fine cluster)
    xs = sorted({cx(d["b"]) for d in votes})
    cols = []
    for x in xs:
        if cols and x - cols[-1][-1] <= 90:
            cols[-1].append(x)
        else:
            cols.append([x])
    col_centers = [sum(g) // len(g) for g in cols]
    # band boundaries: prev CT (or left edge 340) to this WIT
    cts = sorted([s for s in stat_x if s[1] == "CT"], key=lambda s: s[0])
    print(f"\n### {name}  precincts {len(precs)}: {pnames[0]} .. {pnames[-1]}")
    print(f"  col x-centers: {col_centers}")
    print(f"  WIT x: {[s[0] for s in wits]}  CT x: {[s[0] for s in cts]}")
    if not wits:
        # no stats -> candidate-only page (or couldn't detect); treat as 1 band
        bands = [(340, col_centers[-1] if col_centers else 2000)]
    else:
        bands = []
        left = 340
        for w in wits:
            wx = w[0]
            bands.append((left, wx))
            # next left boundary = the CT belonging to this contest (rightmost
            # CT with x > wx but < next WIT)
            nxt = None
            for c in cts:
                if c[0] >= wx and (nxt is None or c[0] < nxt):
                    nxt = c[0]
            left = (nxt + 30) if nxt else wx
        # trailing columns after last CT (shouldn't happen)
    for bi, (lo, hi) in enumerate(bands):
        ccols = [x for x in col_centers if lo <= x <= hi]
        scols = [s for s in stat_x if lo - 60 <= s[0] <= hi + 800
                 and s[0] >= lo]
        # office/party text in this band's header (x within band, but office
        # name may sit right of candidate cols; widen to find it)
        band_header = [d for d in header
                       if lo - 60 <= cx(d["b"]) <= hi + 600]
        office_txt = " ".join(d["t"] for d in sorted(
            band_header, key=lambda d: (d["b"][1], cx(d["b"])))
            if not nkey(d["t"]) in STAT_WORDS)
        party = ""
        for d in band_header:
            lt = d["t"].lower()
            if "democrat" in lt:
                party = "D"
            elif "republican" in lt:
                party = "R"
            elif "nonpartisan" in lt:
                party = "NP"
        print(f"  band{bi}: x[{lo}-{hi}] cand_cols={len(ccols)} "
              f"party={party}")
        print(f"     office: {office_txt[:240]}")


def main():
    files = sorted(glob.glob(str(CACHE / "*.json")))
    if len(sys.argv) > 1:
        files = [f for f in files if any(k in f for k in sys.argv[1:])]
    for f in files:
        try:
            summarize(f)
        except Exception as e:
            print(f"\n### {Path(f).name}  ERROR: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()