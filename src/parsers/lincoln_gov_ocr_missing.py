#!/usr/bin/env python3
"""OCR the two missing Governor candidate pages (15, 26) at 600 DPI into
.paddleocr_cache/Lincoln_600/, using the same settings as lincoln_gov_reocr.py
so the boxes are in the same 6600px coordinate space."""
import json
import subprocess
import time
from pathlib import Path

from paddleocr import PaddleOCR

SRC = Path.home() / "code/openelections-sources-or/2026/primary" / \
    "Lincoln OR May 19, 2026 Primary - State Candidates & Measures.pdf"
STEM = "State Candidates & Measures"
CACHE600 = Path(".paddleocr_cache/Lincoln_600")
TMP = Path("/tmp/linc_ocr_600")
PAGES = [15, 26]
DPI = 600


def main():
    CACHE600.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    ocr = PaddleOCR(use_textline_orientation=False, lang="en",
                    text_det_limit_side_len=6800, text_det_limit_type="max")
    for pg in PAGES:
        jpath = CACHE600 / f"{STEM}_p{pg:03d}.json"
        if jpath.exists():
            print(f"p{pg:03d}: cached")
            continue
        t0 = time.time()
        pre = TMP / f"{STEM}_p{pg:03d}"
        subprocess.run(["pdftoppm", "-png", "-r", str(DPI), "-f", str(pg),
                        "-l", str(pg), str(SRC), str(pre)], check=True)
        pngs = sorted(TMP.glob(f"{STEM}_p{pg:03d}*.png"))
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
        print(f"p{pg:03d}: {len(data)} boxes ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()