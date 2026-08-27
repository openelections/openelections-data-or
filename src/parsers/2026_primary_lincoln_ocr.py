#!/usr/bin/env python3
"""Run PaddleOCR (PP-OCRv6) on the 3 Lincoln County 2026 primary PDFs.

Renders each PDF page to a 300-dpi PNG, runs PaddleOCR text detection +
recognition, and writes per-page JSON (text + bounding boxes) to
``.paddleocr_cache/Lincoln/<stem>_p001.json``.

We use plain text OCR (not the slow VL/structure pipelines, which hang or take
>4 min/page on CPU) and reconstruct tables geometrically from the bounding
boxes in the parser.

Re-runnable: existing .json files are skipped unless --force is passed.

Usage:
    uv run python3 src/parsers/2026_primary_lincoln_ocr.py [--force]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from paddleocr import PaddleOCR

SOURCES = [
    "Lincoln OR May 19, 2026 Primary - State Candidates & Measures.pdf",
    "Lincoln OR May 19, 2026 Primary - County Candidates & Measures.pdf",
    "Lincoln OR May 19, 2026 Primary - Republican State Rep. Write-ins.pdf",
]
SRC_DIR = Path.home() / "code/openelections-sources-or/2026/primary"
CACHE = Path(".paddleocr_cache/Lincoln")
TMP = Path("/tmp/linc_ocr")
DPI = 300


def page_count(pdf: Path) -> int:
    out = subprocess.check_output(["pdfinfo", str(pdf)], text=True)
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    raise RuntimeError(f"no page count for {pdf}")


def short_stem(name: str) -> str:
    if name.startswith("Lincoln OR May 19, 2026 Primary - "):
        name = name[len("Lincoln OR May 19, 2026 Primary - "):]
    return name[:-4]  # drop .pdf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    print("Initializing PaddleOCR (PP-OCRv6)…", flush=True)
    ocr = PaddleOCR(use_textline_orientation=False, lang="en")
    print("PaddleOCR ready.", flush=True)

    for name in SOURCES:
        pdf = SRC_DIR / name
        stem = short_stem(name)
        n = page_count(pdf)
        print(f"\n=== {name}  ({n} pages) ===", flush=True)
        for p in range(1, n + 1):
            jpath = CACHE / f"{stem}_p{p:03d}.json"
            if jpath.exists() and not args.force:
                print(f"  p{p:03d}: cached", flush=True)
                continue
            t0 = time.time()
            png_prefix = TMP / f"{stem}_p{p:03d}"
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(DPI), "-f", str(p), "-l", str(p),
                 str(pdf), str(png_prefix)],
                check=True,
            )
            pngs = sorted(TMP.glob(f"{stem}_p{p:03d}*.png"))
            if not pngs:
                print(f"  p{p:03d}: PNG render failed", flush=True)
                continue
            res = ocr.predict(str(pngs[0]))
            r = res[0]
            texts = [str(t) for t in r["rec_texts"]]
            raw_boxes = r["rec_boxes"]
            raw_scores = r["rec_scores"]
            boxes = [[int(v) for v in b] for b in (raw_boxes.tolist() if hasattr(raw_boxes, "tolist") else raw_boxes)]
            scores = [float(s) for s in (raw_scores.tolist() if hasattr(raw_scores, "tolist") else raw_scores)]
            data = [{"t": t, "b": b, "s": s}
                    for t, b, s in zip(texts, boxes, scores)]
            jpath.write_text(json.dumps(data))
            pngs[0].unlink(missing_ok=True)
            print(f"  p{p:03d}: {len(data)} boxes -> {jpath.name} "
                  f"({time.time()-t0:.1f}s)", flush=True)

    print("\nDone.", flush=True)


if __name__ == "__main__":
    sys.exit(main())