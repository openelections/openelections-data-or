#!/usr/bin/env python3
"""Resolve Lincoln Governor minor-candidate undercounts using the 600 DPI OCR.

The parser's band/anchor detection misfires on the 600 DPI boxes, so a wholesale
re-extraction is wrong.  Instead we use a hybrid:

  * 300 DPI boxes drive the *structure* -- precinct y-windows, column slot
    plans, and the x-position of every cell (mirrors extract_page exactly);
  * the 600 DPI boxes (scaled x0.5 into the 300 DPI coordinate space) supply
    only the *recognized value* for each cell, found by nearest-x match within
    the precinct's y-window.

So 600 DPI's better recognition replaces a cell's value only where it reads a
number at the cell's known x-position.  Spurious 600 DPI boxes (split digits,
stray marks) at wrong x-positions are ignored because nothing matches them.

For each Governor candidate we then compare the 600 DPI per-cell values to the
current CSV and to the official county summary.  A fix is recommended only when
the cells where 600 DPI reads higher than the CSV sum exactly to the candidate's
official undercount -- so we never regress a candidate below its official total.

This script does NOT modify the CSV.  It writes recommended cell fixes to
/tmp/lincoln_gov_fixes.json.
"""
import csv
import importlib.util
import json
import statistics
from collections import defaultdict
from pathlib import Path

spec = importlib.util.spec_from_file_location("p", "src/parsers/2026_primary_lincoln_parser.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

STEM = "State Candidates & Measures"
CACHE300 = Path(".paddleocr_cache/Lincoln")
CACHE600 = Path(".paddleocr_cache/Lincoln_600")
# Governor D candidates: p15 (precincts 01-17), p16 (18-32); stat continuation
# pages p17 (01-16), p18 (17-32).  Governor R: p26 (01-17), p27 (18-32); stat
# pages p28 (01-17), p29 (18-32).  All 8 pages are needed so merge_contest
# reconstructs the full 32-precinct contest with candidate indices accumulating
# across the column-split candidate pages.
GOV_PAGES = [15, 16, 17, 18, 26, 27, 28, 29]
CSV = Path("2026/counties/20260519__or__primary__lincoln__precinct.csv")
ROW_TOL = p.ROW_TOL
SLOT_TOL = p.SLOT_TOL
INT_RE = p.INT_RE

OFFICIAL = {
    ("Governor", "", "D"): {
        "Forest (Fora) Alexander": 123, "James Atkinson IV": 108, "Cal Kishawi": 34,
        "Tina Kotek": 6483, "Donnie M Beckwith": 38, "David W Beem": 66,
        "Steve William Laible": 61, "Brittany Jones": 174, "Tristan Sheppard": 86,
        "Miranda Weigler": 158,
    },
    ("Governor", "", "R"): {
        "Danielle Bethell": 121, "Hope A Dalrymple": 7, "Ed Diehl": 1628,
        "Christine Drazan": 2299, "Chris Dudley": 671, "Kyle M Duyck": 25,
        "David Medina": 258, "Robert Neuman": 7, "Brad T Peters": 43,
        "Paul J. Romero Jr": 27, "Wen Waddell": 3, "Martin Ward": 8,
        "Tim O Youker": 6, "DeAngelo Leroy Turner": 7,
    },
}


def load300(page):
    return json.loads((CACHE300 / f"{STEM}_p{page:03d}.json").read_text())


def load600(page):
    d = json.loads((CACHE600 / f"{STEM}_p{page:03d}.json").read_text())
    for b in d:
        b["b"] = [int(v / 2) for v in b["b"]]
    return d


def page_windows(boxes, precs):
    """Exact mirror of extract_page's per-precinct (lo_y, hi_y) windows."""
    totals_y = next((p.cy(d["b"]) for d in boxes
                     if d["b"][0] < 340 and d["t"].strip().lower() == "totals"), None)
    pys = [y for y, _, _ in precs]
    int_ys = sorted(p.cy(d["b"]) for d in boxes
                    if d["b"][0] > 340 and INT_RE.match(d["t"].strip()))
    clusters = []
    for yy in int_ys:
        if clusters and yy - clusters[-1][0] <= 25:
            clusters[-1] = (clusters[-1][0], clusters[-1][1] + 1)
        else:
            clusters.append((yy, 1))
    data_rows = [y for y, c in clusters if c >= 3]
    offsets = []
    for ly in pys:
        near = min(data_rows, key=lambda ry: abs(ry - ly)) if data_rows else None
        if near is not None and abs(near - ly) <= 70:
            offsets.append(near - ly)
    med_off = statistics.median(offsets) if len(offsets) >= 3 else 0
    extend_down = med_off >= -5
    out = {}
    n = len(precs)
    for i, (y, pid, _) in enumerate(precs):
        lo_y = y - ROW_TOL
        if extend_down:
            hi_y = y + 90
            if i < n - 1:
                hi_y = min(hi_y, pys[i + 1] - 10)
            elif totals_y is not None and totals_y > y:
                hi_y = min(hi_y, totals_y - 10)
        else:
            hi_y = y + ROW_TOL
        out[pid] = (lo_y, hi_y)
    return out


def extract_xtracked(boxes, precs, bands):
    """Mirror extract_page but return {bi: {pid: {slot: (x300, v300)}}} so each
    slotted cell carries the x-position of the 300 DPI box that produced it.
    For short-precinct median-x assignments the matched box's own x is kept."""
    plan = p._page_slot_plan(bands)
    n_slots = len(plan)
    win = page_windows(boxes, precs)
    rows = {pid: p.row_values_all(boxes, lo, hi) for pid, (lo, hi) in win.items()}
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
            rec[slot] = (x, v)
    return out, win


def read_600_at(d600, lo_y, hi_y, x300, slot):
    """Find the 600 DPI integer box nearest x300 within the precinct y-window;
    return its value, or None if nothing is within SLOT_TOL of x300."""
    best = None
    bestd = None
    for d in d600:
        b = d["b"]
        if b[0] > 340 and lo_y <= p.cy(b) <= hi_y and INT_RE.match(d["t"].strip()):
            x = p.cx(b)
            dd = abs(x - x300)
            if dd <= SLOT_TOL and (bestd is None or dd < bestd):
                bestd = dd
                best = int(d["t"].strip())
    return best


def build_governor_600():
    """Return {contest_key: {pid: {cand_name|stat: v600}}} using 600 DPI values
    slotted by 300 DPI geometry."""
    entries = []  # (pi, page, bi, band, ck, {pid: {slot: (x,v)}}, win)
    for pi, page in enumerate(GOV_PAGES):
        b300 = load300(page)
        b600 = load600(page)
        precs, bands = p.parse_page(b300)
        tracked, win = extract_xtracked(b300, precs, bands)
        for bi, band in enumerate(bands):
            ck = p.detect_contest(band)
            if not ck or ck[0] != "Governor":
                continue
            # For each cell, read the 600 DPI value at the 300 DPI x-position.
            # Keys follow the parser convention: int for candidates, the stat
            # pseudo string for stats (so merge_contest slots them correctly).
            res600 = {}
            for pid, rec in tracked[bi].items():
                lo_y, hi_y = win[pid]
                r = {}
                for slot, (x300, v300) in rec.items():
                    kind, key = slot
                    if kind == "stat" and key is None:
                        continue  # TVC / Contest Total -- not emitted
                    v600 = read_600_at(b600, lo_y, hi_y, x300, slot)
                    # keep 300 DPI value where 600 DPI found nothing nearby
                    r[key] = v600 if v600 is not None else v300
                res600[pid] = r
            entries.append((pi, page, bi, band, ck, res600))
    contests = defaultdict(list)
    for e in entries:
        contests[e[4]].append(e)
    gov = {}
    for ck, es in contests.items():
        # adapt to merge_contest's expected entry shape:
        # (pi, stem, page, bi, band, ck, {pid: {slot: v}})
        adapted = [(pi, STEM, page, bi, band, ck, res) for pi, page, bi, band, ck, res in es]
        per, _ = p.merge_contest(ck, adapted)
        names = p.CANDIDATES.get(ck)
        pmap = {}
        for pid, rec in per.items():
            row = {}
            if names:
                for i, nm in enumerate(names):
                    row[nm] = rec.get(i, 0)
            for s in p.STAT_NAMES:
                v = rec.get(s)
                if v and v > 0:
                    row[s] = v
            pmap[pid] = row
        gov[ck] = pmap
    return gov


def main():
    gov = build_governor_600()

    # Current CSV Governor rows
    cur = defaultdict(dict)
    with CSV.open() as f:
        for r in csv.DictReader(f):
            if r["office"].strip() == "Governor":
                ck = (r["office"].strip(), r["district"].strip(), r["party"].strip())
                cur[ck][r["precinct"].strip()] = dict(
                    cur[ck].get(r["precinct"].strip(), {}),
                    **{r["candidate"].strip(): int(r["votes"])})

    print("=" * 72)
    print("CANDIDATE TOTALS: CSV vs 600 DPI vs OFFICIAL")
    print("=" * 72)
    all_fixes = []
    for ck in sorted(gov):
        office, district, party = ck
        off = OFFICIAL.get(ck, {})
        print(f"\n--- {office} {party} ---")
        csv_tot = defaultdict(int)
        new_tot = defaultdict(int)
        for pid, rec in cur.get(ck, {}).items():
            for c, v in rec.items():
                csv_tot[c] += v
        for pid, rec in gov[ck].items():
            for c, v in rec.items():
                new_tot[c] += v
        for c in sorted(set(csv_tot) | set(new_tot) | set(off)):
            cv = csv_tot.get(c, 0)
            nv = new_tot.get(c, 0)
            ov = off.get(c)
            tag = ""
            if ov is not None:
                tag = "OK" if nv == ov else f"DIFF(official={ov})"
            print(f"  {c:24s} csv={cv:6d} 600dpi={nv:6d} {tag}")

    print("\n" + "=" * 72)
    print("PER-CELL DIFFS where 600 DPI > CSV (candidate-level validation)")
    print("=" * 72)
    for ck in sorted(gov):
        office, district, party = ck
        off = OFFICIAL.get(ck, {})
        print(f"\n--- {office} {party} ---")
        for c in sorted(set(off) | set(gov.get(ck, {}).get(next(iter(gov[ck])), {}))):
            csv_tot = sum(cur.get(ck, {}).get(pid, {}).get(c, 0) for pid in gov[ck])
            new_tot = sum(gov[ck].get(pid, {}).get(c, 0) for pid in gov[ck])
            ov = off.get(c)
            if ov is None:
                continue
            under = ov - csv_tot
            if under == 0 and new_tot == csv_tot:
                continue
            print(f"  {c:24s} csv={csv_tot:5d} 600dpi={new_tot:5d} official={ov:5d} "
                  f"under={under:+d}")
            # enumerate cells where 600dpi > csv
            inc = []
            for pid in sorted(gov[ck], key=lambda x: (len(x), x)):
                cv = cur.get(ck, {}).get(pid, {}).get(c, 0)
                nv = gov[ck].get(pid, {}).get(c, 0)
                if nv != cv:
                    print(f"      {pid:20s} csv={cv:4d} 600dpi={nv:4d} d={nv-cv:+d}")
                    if nv > cv:
                        inc.append((pid, cv, nv))
            inc_sum = sum(n - cv for _, cv, n in inc)
            print(f"      -> increases sum={inc_sum} vs official undercount={under} "
                  f"{'MATCH' if inc_sum == under else 'NO MATCH'}")
            if ov is not None and inc_sum == under and inc:
                for pid, cv, n in inc:
                    all_fixes.append(dict(office=office, district=district,
                                          party=party, precinct=pid,
                                          candidate=c, old=cv, new=n))

    Path("/tmp/lincoln_gov_fixes.json").write_text(json.dumps(all_fixes, indent=2))
    print(f"\n{len(all_fixes)} recommended fixes -> /tmp/lincoln_gov_fixes.json")


if __name__ == "__main__":
    main()