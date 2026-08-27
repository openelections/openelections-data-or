#!/usr/bin/env python3
"""Re-OCR the 7 Governor pages of Lincoln's State PDF at 600 DPI and extract
per-precinct Governor D/R candidate+stat values, reusing the parser's own
extraction logic so the tuned constants still apply (600 DPI boxes are scaled
x0.5 back into the 300 DPI coordinate space the parser expects).

Outputs a comparison: for each (precinct, candidate) where 600 DPI differs from
the current CSV, prints both values; and prints 600 DPI candidate totals vs the
official county summary so we can confirm which reads are correct.

Only the Governor pages are re-OCRed; nothing else is touched.  The current CSV
is NOT modified by this script -- it only reports.  A separate step applies the
confirmed corrections.
"""
import csv
import importlib.util
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from paddleocr import PaddleOCR

SRC = Path.home() / "code/openelections-sources-or/2026/primary" / \
    "Lincoln OR May 19, 2026 Primary - State Candidates & Measures.pdf"
STEM = "State Candidates & Measures"
CACHE600 = Path(".paddleocr_cache/Lincoln_600")
TMP = Path("/tmp/linc_ocr_600")
DPI = 600
# Governor pages in the State PDF (from lincoln_inspect):
GOV_PAGES = [16, 17, 18, 27, 28, 29, 30]
CSV = Path("2026/counties/20260519__or__primary__lincoln__precinct.csv")

# Official Lincoln summary totals for the contested Governor candidates +
# OTTER ROCK is handled per-precinct below.
OFFICIAL = {
    ("Governor", "", "D"): {
        "Forest (Fora) Alexander": 123, "James Atkinson IV": 108,
        "Cal Kishawi": 34, "Tina Kotek": 6483, "Donnie M Beckwith": 38,
        "David W Beem": 66, "Steve William Laible": 61, "Brittany Jones": 174,
        "Tristan Sheppard": 86, "Miranda Weigler": 158,
    },
    ("Governor", "", "R"): {
        "Danielle Bethell": 121, "Hope A Dalrymple": 7, "Ed Diehl": 1628,
        "Christine Drazan": 2299, "Chris Dudley": 671, "Kyle M Duyck": 25,
        "David Medina": 258, "Robert Neuman": 7, "Brad T Peters": 43,
        "Paul J. Romero Jr": 27, "Wen Waddell": 3, "Martin Ward": 8,
        "Tim O Youker": 6, "DeAngelo Leroy Turner": 7,
    },
}


def ocr_pages():
    CACHE600.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    print("Initializing PaddleOCR (PP-OCRv6)…", flush=True)
    # Raise the detection side-length limit so the 600 DPI image (6600x5100)
    # is NOT downscaled to 4000px -- we want true 600 DPI recognition.  Boxes
    # are still returned in the original (6600px) coordinate space, which the
    # x0.5 scaling in boxes600() maps back to the parser's 300 DPI space.
    ocr = PaddleOCR(use_textline_orientation=False, lang="en",
                    text_det_limit_side_len=6800, text_det_limit_type="max")
    print("PaddleOCR ready.", flush=True)
    for p in GOV_PAGES:
        jpath = CACHE600 / f"{STEM}_p{p:03d}.json"
        if jpath.exists():
            print(f"  p{p:03d}: cached", flush=True)
            continue
        t0 = time.time()
        pre = TMP / f"{STEM}_p{p:03d}"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(DPI), "-f", str(p), "-l", str(p),
             str(SRC), str(pre)], check=True)
        pngs = sorted(TMP.glob(f"{STEM}_p{p:03d}*.png"))
        if not pngs:
            print(f"  p{p:03d}: PNG render failed", flush=True)
            continue
        res = ocr.predict(str(pngs[0]))
        r = res[0]
        texts = [str(t) for t in r["rec_texts"]]
        rb = r["rec_boxes"]
        rs = r["rec_scores"]
        boxes = [[int(v) for v in (b.tolist() if hasattr(b, "tolist") else b)]
                 for b in rb]
        scores = [float(s) for s in (rs.tolist() if hasattr(rs, "tolist") else rs)]
        data = [{"t": t, "b": b, "s": s} for t, b, s in zip(texts, boxes, scores)]
        jpath.write_text(json.dumps(data))
        pngs[0].unlink(missing_ok=True)
        print(f"  p{p:03d}: {len(data)} boxes -> {jpath.name} "
              f"({time.time()-t0:.1f}s)", flush=True)


def load_parser():
    spec = importlib.util.spec_from_file_location(
        "lincoln", "src/parsers/2026_primary_lincoln_parser.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def boxes600(page):
    p = CACHE600 / f"{STEM}_p{page:03d}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    # scale 600 DPI coordinates back to 300 DPI space (parser's constants)
    for d in data:
        d["b"] = [int(v / 2) for v in d["b"]]
    return data


def build_governor_entries(parser):
    """Replicate build_contests for just the Governor pages, reading 600 DPI
    boxes. Returns {contest_key: [entry, ...]}."""
    entries = []
    pi = 0
    for page in GOV_PAGES:
        boxes = boxes600(page)
        if boxes is None:
            continue
        precs, bands = parser.parse_page(boxes)
        per_band = parser.extract_page(boxes, precs, bands)
        for bi, band in enumerate(bands):
            ck = parser.detect_contest(band)
            if ck is None:
                print(f"  WARN p{page} band{bi}: no contest detected", file=sys.stderr)
                continue
            if ck[0] != "Governor":
                continue
            res = per_band.get(bi, {})
            entries.append((pi, STEM, page, bi, band, ck, res))
        pi += 1
    contests = defaultdict(list)
    for e in entries:
        contests[e[5]].append(e)
    return contests


def main():
    ocr_pages()
    parser = load_parser()
    contests = build_governor_entries(parser)
    # Merge each Governor contest -> per-precinct {cand_idx: v, stat: v}
    gov = {}  # (office,district,party) -> {pid: {name|stat: v}}
    for ck, entries in contests.items():
        per, cand_total = parser.merge_contest(ck, entries)
        names = parser.CANDIDATES.get(ck)
        office, district, party = ck
        pmap = {}
        for pid, rec in per.items():
            row = {}
            if names:
                for i, nm in enumerate(names):
                    row[nm] = rec.get(i, 0)
            for s in parser.STAT_NAMES:
                v = rec.get(s)
                if v and v > 0:
                    row[s] = v
            pmap[pid] = row
        gov[ck] = pmap

    # Current CSV Governor rows -> {(office,party,pid,cand): v}
    cur = defaultdict(dict)
    with CSV.open() as f:
        for r in csv.DictReader(f):
            if r["office"].strip() == "Governor":
                ck = (r["office"].strip(), r["district"].strip(), r["party"].strip())
                cur[ck][r["precinct"].strip()] = dict(cur[ck].get(r["precinct"].strip(), {}),
                                                       **{r["candidate"].strip(): int(r["votes"])})

    print("\n" + "=" * 70)
    print("PER-PRECINCT DIFFS (600 DPI vs current CSV), Governor")
    print("=" * 70)
    diffs = []  # (ck, pid, cand, csv_v, new_v)
    for ck in sorted(gov):
        office, district, party = ck
        print(f"\n--- {office} {party} ---")
        pids = sorted(set(gov[ck]) | set(cur.get(ck, {})),
                      key=lambda x: (len(x), x))
        for pid in pids:
            new = gov[ck].get(pid, {})
            old = cur.get(ck, {}).get(pid, {})
            cands = sorted(set(new) | set(old))
            for c in cands:
                nv = new.get(c, 0)
                ov = old.get(c, 0)
                if nv != ov:
                    print(f"  {pid:20s} {c:24s} csv={ov:5d} -> 600dpi={nv:5d}")
                    diffs.append((ck, pid, c, ov, nv))

    print("\n" + "=" * 70)
    print("CANDIDATE TOTALS: 600 DPI vs OFFICIAL SUMMARY")
    print("=" * 70)
    for ck in sorted(gov):
        office, district, party = ck
        off = OFFICIAL.get(ck, {})
        print(f"\n--- {office} {party} ---")
        tot = defaultdict(int)
        for pid, rec in gov[ck].items():
            for c, v in rec.items():
                tot[c] += v
        for c in sorted(set(tot) | set(off)):
            tv = tot.get(c, 0)
            ov = off.get(c, None)
            if ov is None:
                print(f"  {c:24s} 600dpi={tv:6d}  (no official ref)")
            else:
                mark = "OK" if tv == ov else "DIFF"
                print(f"  {c:24s} 600dpi={tv:6d} official={ov:6d}  {mark}")

    # Save diffs to a JSON for the apply step
    out = Path("/tmp/lincoln_gov_diffs.json")
    out.write_text(json.dumps([{"office": d[0][0], "party": d[0][2],
                                "precinct": d[1], "candidate": d[2],
                                "old": d[3], "new": d[4]} for d in diffs], indent=2))
    print(f"\n{len(diffs)} diffs written to {out}")


if __name__ == "__main__":
    main()