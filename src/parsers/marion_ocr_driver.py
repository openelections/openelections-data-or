#!/usr/bin/env python3
"""OCR driver: submit the 2026 Marion primary SOVC PDF to PaddleOCR-VL.

Populates .paddleocr_cache/Marion/ with per-page markdown so the Marion
parser can consume it. Run once per source-PDF replacement:

    uv run python src/parsers/marion_ocr_driver.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import extract_pages

PDF = "/Users/dwillis/code/openelections-sources-or/2026/primary/Marion.pdf"

n = 0
for page, md in extract_pages(PDF, cache_dir=".paddleocr_cache"):
    n += 1
    if page % 25 == 0:
        print(f"cached page {page}", flush=True)
print(f"done: {n} pages")