#!/usr/bin/env python3
"""Chunked OCR driver for Malheur.pdf.

The full 28 MB PDF times out on upload, so this OCRs the 10-page chunks in
/tmp/malheur_chunks/chunk-*.pdf and writes the per-page markdown into
.paddleocr_cache/Malheur/p{N:03d}.md with global page numbering, then drops a
.complete marker. Idempotent: chunks already present in the cache are skipped.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import _submit_job, _poll_job, _fetch_pages, _token  # type: ignore

CACHE = Path(".paddleocr_cache/Malheur")
CHUNKS = sorted(Path("/tmp/malheur_chunks").glob("chunk-*.pdf"))
PAGES_PER_CHUNK = 10


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    token = _token()
    total = 0
    for ci, chunk in enumerate(CHUNKS, start=1):
        start_page = (ci - 1) * PAGES_PER_CHUNK + 1
        # Skip a chunk if its first page is already cached (resume support).
        if (CACHE / f"p{start_page:03d}.md").exists():
            print(f"chunk {ci:02d} ({chunk.name}): already cached, skipping", flush=True)
            n = PAGES_PER_CHUNK
            total += n
            continue
        print(f"chunk {ci:02d} ({chunk.name}, {chunk.stat().st_size//1024} KB): submitting...",
              flush=True)
        for attempt in range(1, 4):
            try:
                job_id = _submit_job(str(chunk), token)
                break
            except Exception as e:
                print(f"  attempt {attempt} failed: {e}", flush=True)
                if attempt == 3:
                    return 1
                time.sleep(5)
        jsonl_url = _poll_job(job_id, token)
        pages = _fetch_pages(jsonl_url)
        for j, md in enumerate(pages, start=1):
            (CACHE / f"p{start_page + j - 1:03d}.md").write_text(md)
        n = len(pages)
        total += n
        print(f"chunk {ci:02d}: got {n} pages (global {start_page}..{start_page+n-1})", flush=True)
    (CACHE / ".complete").write_text(str(total))
    print(f"DONE: {total} pages cached to {CACHE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())