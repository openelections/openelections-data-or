#!/usr/bin/env python3
"""Local PaddleOCR (PP-OCRv6) fallback for Marion County 2026 canvass pages.

The cloud PaddleOCR-VL service (paddleocr_extract.py) mangles some pages of the
Marion canvass PDF deterministically: whole tables come back as chart-box image
references, and on the State House 18 write-in appendix pages it zeroes out the
precinct column.  This module renders those pages at 300 dpi with pdftoppm and
runs the local PaddleOCR engine, then reconstructs the contest tables from the
recognition boxes.

Two outputs per page, both cached under .paddleocr_cache/Marion_local/:
- pNNN.items.json  raw recognition boxes and texts
- pNNN.grid.json   {"head_lines": [...], "grid": [[...]]} in the same shape the
                   cloud parser's expand_table() produces (header row + data
                   rows), plus "precincts": the left-column precinct sequence.

Usage:
    uv run python src/parsers/marion_local_ocr.py 46 48 ...
    uv run python src/parsers/marion_local_ocr.py --all
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PDF = "/Users/dwillis/code/openelections-sources-or/2026/primary/Marion.pdf"
CACHE = Path(".paddleocr_cache/Marion_local")
DPI = 300

# Pages whose tables are rebuilt entirely from local OCR.
LOCAL_GRID_PAGES = [
    13, 14, 25, 46, 48, 56, 64, 65, 66, 67, 68, 70, 71, 72, 73, 74, 75,
    76, 78, 88, 169, 180, 181,
]
# Pages where the cloud table is good but the precinct column was mangled
# (blank, zeroed, or value-shifted); only the left-column precinct sequence
# is needed from local OCR.
PRECINCT_ZIP_PAGES = [
    1, 28, 33, 37, 41, 59, 62, 63, 69, 89, 95, 97, 101, 109, 110,
    115, 121, 139, 143, 145, 147, 149, 150,
]

_engine = None


def engine():
    global _engine
    if _engine is None:
        from paddleocr import PaddleOCR

        _engine = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            lang="en",
        )
    return _engine


def _yc(it):
    return (it["y1"] + it["y2"]) / 2


def _xc(it):
    return (it["x1"] + it["x2"]) / 2


def _render(page: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    png = CACHE / f"p{page:03d}-{DPI}.png"
    if not png.exists():
        subprocess.run(
            [
                "pdftoppm", "-f", str(page), "-l", str(page), "-r", str(DPI),
                "-png", "-singlefile", PDF, str(png.with_suffix("")),
            ],
            check=True,
        )
    return png


def items_for(page: int) -> list:
    cache = CACHE / f"p{page:03d}.items.json"
    if cache.exists():
        return json.loads(cache.read_text())
    png = _render(page)
    result = engine().predict(str(png))[0]
    items = []
    for box, text in zip(result["rec_boxes"].tolist(), result["rec_texts"]):
        x1, y1, x2, y2 = box
        items.append(
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "text": text.strip()}
        )
    cache.write_text(json.dumps(items))
    return items


def precincts_for(page: int) -> list:
    """Left-column precinct numbers/labels in reading order."""
    items = items_for(page)
    out = []
    for it in sorted(items, key=_yc):
        if _xc(it) < 100 and _yc(it) > 650 and re.fullmatch(
            r"\d{1,4}|Totals?", it["text"], re.IGNORECASE
        ):
            out.append(it["text"])
    return out


def _cluster_columns_x(items, xf):
    """Cluster data items into columns by an x accessor; return centers."""
    xs = sorted(xf(it) for it in items)
    if not xs:
        return []
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] < 70:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [sum(c) / len(c) for c in clusters]


def _nearest(items, y, window=48):
    best, best_d = None, None
    for it in items:
        d = abs(_yc(it) - y)
        if d <= window and (best_d is None or d < best_d):
            best, best_d = it, d
    return best


def build_grid(page: int):
    """Return (head_lines, grid) reconstructed from local OCR boxes."""
    items = items_for(page)
    cache = CACHE / f"p{page:03d}.grid.json"
    if cache.exists():
        d = json.loads(cache.read_text())
        return d["head_lines"], d["grid"]

    head_lines = [
        it["text"]
        for it in sorted(items, key=lambda i: (_yc(i), _xc(i)))
        if _yc(it) < 650 and it["text"] and re.search(r"[A-Za-z]{3}", it["text"])
    ]
    # On some scans the contest heading sits below y=650; keep it out of the
    # table region but route it to the heading lines.
    is_heading = re.compile(r"\d\s*-?\s*Year\s+Term|NonPartisan", re.I)
    deep = [
        it for it in items
        if _yc(it) >= 650 and it["text"] and is_heading.search(it["text"])
    ]
    head_lines += [it["text"] for it in sorted(deep, key=_yc)]

    below = [
        it for it in items
        if _yc(it) > 650 and it["text"]
        and not is_heading.search(it["text"])
        # Drop non-ASCII junk recognitions (stray CJK glyphs etc.): a single
        # such box between two columns can bridge the clustering gap.
        and re.search(r"[A-Za-z0-9]", it["text"])
    ]
    precinct_col = [
        it for it in below
        if _xc(it) < 100 and re.fullmatch(r"\d{1,4}|Totals?", it["text"], re.I)
    ]
    precinct_col.sort(key=_yc)
    # The left margin (x < 100) holds only the precinct column and its
    # 'Precinct' header; mangled header fragments there ('renct') must not
    # become phantom data bands.
    others = [it for it in below if it not in precinct_col and _xc(it) >= 100]

    # Remove scan skew before clustering: on the appendix scans the precinct
    # column's x drifts linearly with y (~0.8 degrees of rotation), which
    # otherwise chains neighbouring columns into single clusters.  De-skewed
    # x also keeps each vertical name header beside its own value column
    # (names sit ~60px left of their values by design).
    theta, ybar = 0.0, 0.0
    if len(precinct_col) >= 5:
        ys = [_yc(it) for it in precinct_col]
        xs = [_xc(it) for it in precinct_col]
        ybar = sum(ys) / len(ys)
        xbar = sum(xs) / len(xs)
        num = sum((y - ybar) * (x - xbar) for y, x in zip(ys, xs))
        den = sum((y - ybar) ** 2 for y in ys)
        if den and abs(num / den) < 0.05:
            theta = num / den

    def _dxc(it):
        return _xc(it) - theta * (_yc(it) - ybar)

    centers = _cluster_columns_x(others, _dxc)
    columns = [[] for _ in centers]
    for it in others:
        best = min(range(len(centers)), key=lambda i: abs(_dxc(it) - centers[i]))
        columns[best].append(it)
    for col in columns:
        col.sort(key=_yc)

    # Split header from data.  Two anchors, in order of preference:
    # 1. The largest vertical gap in the densest column (its header label
    #    sits far above its first data value) - only gaps separating a short
    #    header run (<=4 lines) from a long body count; a mid-table gap is a
    #    missed OCR value, not a boundary.
    # 2. The first precinct number: data values never ride more than ~115px
    #    above their precinct, so everything higher up is header.  (On some
    #    appendix pages the precinct numbers sit ~77px below their row's
    #    values, so the precinct column cannot anchor the ROW pairing, but it
    #    still bounds the header zone.)
    densest = max(columns, key=len, default=None)
    split_y = None
    if densest and len(densest) >= 6:
        ys = [_yc(it) for it in densest]
        gaps = [(ys[i + 1] - ys[i], i) for i in range(len(ys) - 1)]
        pitch = sorted(g for g, _ in gaps)[len(gaps) // 2] or max(gaps)[0]
        head_gaps = [
            (g, i) for g, i in gaps
            if i + 1 <= 4 and len(ys) - (i + 1) >= 4 and g > max(1.6 * pitch, 90)
        ]
        if head_gaps:
            gmax, gi = max(head_gaps)
            split_y = (ys[gi] + ys[gi + 1]) / 2
    if split_y is None and precinct_col:
        split_y = _yc(precinct_col[0]) - 115
    if split_y is None:
        split_y = 650.0

    header_items = [[] for _ in centers]
    data_cols = [[] for _ in centers]
    for ci, col in enumerate(columns):
        for it in col:
            (header_items if _yc(it) <= split_y else data_cols)[ci].append(it)

    # Vertical column headers (YES/NO, the statistics labels on the local
    # measure pages) are tall boxes that extend below the split line, so the
    # y split alone dumps them into the data.  Data cells are always numeric,
    # so any item carrying two consecutive letters belongs to the header.
    for ci, col in enumerate(data_cols):
        moved = [it for it in col if re.search(r"[A-Za-z]{2}", it["text"])]
        if moved:
            data_cols[ci] = [it for it in col if it not in moved]
            header_items[ci].extend(moved)

    # Row count: the modal data-column length, not the max.  A single column
    # can hold extra items (two stats recognised separately in one row and
    # merged in another), which would otherwise mint phantom rows.
    lens = [len(c) for c in data_cols if c] + ([len(precinct_col)] if precinct_col else [])
    n_rows = max(set(lens), key=lambda L: (lens.count(L), L)) if lens else 0
    full = [c for c in data_cols if len(c) == n_rows]
    # Band centres averaged over all full-length columns: steadier than any
    # single column's spacing.
    band_ys = [
        sum(_yc(col[k]) for col in full) / len(full)
        for k in range(n_rows)
    ] if full else []

    rows = []
    for k in range(n_rows):
        row = []
        if len(precinct_col) == n_rows:
            row.append(precinct_col[k]["text"])
        else:
            got = _nearest(precinct_col, band_ys[k]) if k < len(band_ys) else None
            row.append(got["text"] if got else "")
        for col in data_cols:
            if len(col) == n_rows:
                row.append(col[k]["text"])
            else:
                got = _nearest(col, band_ys[k]) if k < len(band_ys) else None
                row.append(got["text"] if got else "")
        rows.append(row)

    header = ["" for _ in centers]
    for ci, col in enumerate(header_items):
        header[ci] = " ".join(it["text"] for it in col)
    grid = [["Precinct"] + header] + rows

    cache.write_text(json.dumps({"head_lines": head_lines, "grid": grid}))
    return head_lines, grid


def main(argv):
    pages = []
    if len(argv) > 1 and argv[1] == "--all":
        pages = LOCAL_GRID_PAGES + PRECINCT_ZIP_PAGES
    else:
        pages = [int(a) for a in argv[1:]]
    for page in sorted(pages):
        items_for(page)
        head_lines, grid = build_grid(page)
        print(
            f"p{page:03d}: {len(grid) - 1} data rows x {len(grid[0]) if grid else 0} cols; "
            f"head={head_lines[:2]}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))