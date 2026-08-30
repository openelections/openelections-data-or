#!/usr/bin/env python3
"""Chunked OCR driver for the 2026 Marion primary canvass PDF.

The full 503-page / 156 MB PDF fails on the PaddleOCR AI Studio service
(server-side 500 partway through the job), so the PDF is split into
/tmp/marion_chunks/chunk-*.pdf (PAGES_PER_CHUNK pages each) with pypdf and each
chunk is OCRed separately. Per-page markdown is written to
.paddleocr_cache/Marion/p{N:03d}.md with global page numbering, then a
.complete marker is dropped. Idempotent: chunks whose first page is already
cached are skipped.

Usage:
    uv run python src/parsers/_marion_ocr_chunks.py split   # one-time split
    uv run python src/parsers/_marion_ocr_chunks.py         # OCR all chunks
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import _fetch_pages, _poll_job, _submit_job, _token

PDF = "/Users/dwillis/code/openelections-sources-or/2026/primary/Marion.pdf"
CHUNK_DIR = Path("/tmp/marion_chunks")
CACHE = Path(".paddleocr_cache/Marion")
PAGES_PER_CHUNK = 10


def split() -> int:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(PDF)
    n = len(reader.pages)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    for start in range(0, n, PAGES_PER_CHUNK):
        cno = start // PAGES_PER_CHUNK + 1
        out = CHUNK_DIR / f"chunk-{cno:02d}.pdf"
        if out.exists():
            print(f"{out.name}: exists, skipping", flush=True)
            continue
        w = PdfWriter()
        for p in reader.pages[start : start + PAGES_PER_CHUNK]:
            w.add_page(p)
        with open(out, "wb") as f:
            w.write(f)
        print(f"{out.name}: pages {start + 1}-{min(start + PAGES_PER_CHUNK, n)} "
              f"({out.stat().st_size // 1024} KB)", flush=True)
    print(f"split {n} pages into chunks", flush=True)
    return 0


def ocr() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not (CACHE / ".complete").exists() and not any(CACHE.glob("p*.md")):
        pass
    token = _token()
    total = 0
    chunks = sorted(CHUNK_DIR.glob("chunk-*.pdf"))
    for ci, chunk in enumerate(chunks, start=1):
        start_page = (ci - 1) * PAGES_PER_CHUNK + 1
        if (CACHE / f"p{start_page:03d}.md").exists():
            print(f"chunk {ci:02d}: already cached, skipping", flush=True)
            total += PAGES_PER_CHUNK
            continue
        print(f"chunk {ci:02d} ({chunk.name}, {chunk.stat().st_size // 1024} KB): "
              "submitting...", flush=True)
        for attempt in range(1, 31):
            try:
                job_id = _submit_job(str(chunk), token)
                break
            except Exception as e:
                print(f"  attempt {attempt} failed: {e}", flush=True)
                if attempt == 30:
                    return 1
                # The service queue fills up ("任务提交队列已满"); back off.
                time.sleep(30)
        pages = None
        for attempt in range(1, 6):
            try:
                jsonl_url = _poll_job(job_id, token)
                pages = _fetch_pages(jsonl_url)
                break
            except Exception as e:
                print(f"  poll/fetch attempt {attempt} failed: {e}", flush=True)
                if attempt == 5:
                    return 1
                time.sleep(30)
                # The service sometimes fails a job near completion; resubmit.
                try:
                    job_id = _submit_job(str(chunk), token)
                except Exception as e2:
                    print(f"  resubmit failed: {e2}", flush=True)
        if pages is None:
            return 1
        for j, md in enumerate(pages, start=1):
            (CACHE / f"p{start_page + j - 1:03d}.md").write_text(md)
        total += len(pages)
        print(f"chunk {ci:02d}: got {len(pages)} pages "
              f"(global {start_page}..{start_page + len(pages) - 1})", flush=True)
    (CACHE / ".complete").write_text(str(total))
    print(f"DONE: {total} pages cached to {CACHE}", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "split":
        sys.exit(split())
    sys.exit(ocr())