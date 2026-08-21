"""PaddleOCR-VL extraction helper for Oregon 2026 primary PDFs.

Mirrors the approach used in openelections-data-al/convert_canvass_pdfs_paddleocr.py:
- Submit the whole PDF to the PaddleOCR AI Studio endpoint.
- Poll until done.
- Cache per-page markdown under .paddleocr_cache/.
- Yield (page_number, markdown) on every call.

Auth: the helper checks $PADDLEOCR_TOKEN first, then the git-ignored
.paddleocr_token files in the current directory or home directory. For this
repo it also falls back to the token stored in the Alabama repo that was
referenced by the project instructions.
"""

import glob
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable, Tuple

import requests

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
DEFAULT_CACHE_DIR = ".paddleocr_cache"
OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
POLL_SECONDS = 5


def _token() -> str:
    tok = os.environ.get("PADDLEOCR_TOKEN")
    if tok:
        return tok.strip()
    for path in (".paddleocr_token", os.path.expanduser("~/.paddleocr_token")):
        if os.path.exists(path):
            return open(path).read().strip()
    # Fallback to the token kept in the Alabama repo referenced by CLAUDE.md.
    al_token = os.path.expanduser(
        "~/code/openelections-data-al/.paddleocr_token"
    )
    if os.path.exists(al_token):
        return open(al_token).read().strip()
    raise SystemExit(
        "No PaddleOCR token: set $PADDLEOCR_TOKEN or create .paddleocr_token"
    )


def _submit_job(pdf_path: str, token: str) -> str:
    headers = {"Authorization": f"bearer {token}"}
    data = {"model": MODEL, "optionalPayload": json.dumps(OPTIONAL_PAYLOAD)}
    # Large PDFs (20+ MB) can exceed the default write timeout on a slow
    # uplink; retry the upload a few times with a generous timeout.
    last_err = None
    for attempt in range(1, 4):
        try:
            with open(pdf_path, "rb") as f:
                r = requests.post(
                    JOB_URL, headers=headers, data=data,
                    files={"file": f}, timeout=600,
                )
            if r.status_code != 200:
                raise RuntimeError(
                    f"PaddleOCR submit failed ({r.status_code}): {r.text[:400]}"
                )
            return r.json()["data"]["jobId"]
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            print(f"    paddleocr submit attempt {attempt} failed: {e}; retrying...",
                  flush=True)
            time.sleep(5)
    raise RuntimeError(f"PaddleOCR submit failed after retries: {last_err}")


def _poll_job(job_id: str, token: str) -> str:
    headers = {"Authorization": f"bearer {token}"}
    while True:
        r = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
        r.raise_for_status()
        d = r.json()["data"]
        state = d["state"]
        if state == "done":
            return d["resultUrl"]["jsonUrl"]
        if state == "failed":
            raise RuntimeError(f"PaddleOCR job failed: {d.get('errorMsg')}")
        prog = d.get("extractProgress") or {}
        if state == "running" and "totalPages" in prog:
            print(
                f"    paddleocr running {prog.get('extractedPages')}/{prog['totalPages']} pages",
                flush=True,
            )
        time.sleep(POLL_SECONDS)


def _fetch_pages(jsonl_url: str) -> list:
    r = requests.get(jsonl_url, timeout=120)
    r.raise_for_status()
    pages = []
    for line in r.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        for res in obj["result"]["layoutParsingResults"]:
            pages.append(res["markdown"]["text"])
    return pages


def extract_pages(
    pdf_path: str, cache_dir: str = DEFAULT_CACHE_DIR
) -> Iterable[Tuple[int, str]]:
    """Yield (page_number, markdown) for every page of the PDF."""
    pdf_path = os.path.abspath(pdf_path)
    token = _token()
    stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(pdf_path).stem)
    cache = Path(cache_dir) / stem
    cache.mkdir(parents=True, exist_ok=True)
    done_marker = cache / ".complete"

    if done_marker.exists():
        for md_path in sorted(cache.glob("p*.md")):
            page = int(re.search(r"p(\d+)\.md$", str(md_path)).group(1))
            yield page, md_path.read_text()
        return

    print(f"    paddleocr submitting {os.path.basename(pdf_path)} ...", flush=True)
    job_id = _submit_job(pdf_path, token)
    jsonl_url = _poll_job(job_id, token)
    pages = _fetch_pages(jsonl_url)
    for i, raw in enumerate(pages, start=1):
        (cache / f"p{i:03d}.md").write_text(raw)
    done_marker.write_text(str(len(pages)))
    print(f"    paddleocr got {len(pages)} pages", flush=True)
    for i, raw in enumerate(pages, start=1):
        yield i, raw


def plain_text(md: str) -> str:
    """Return a plain-text rendering of PaddleOCR markdown, preserving line breaks."""
    # Convert HTML line breaks to newlines.
    text = re.sub(r"<br\s*/?>", "\n", md, flags=re.IGNORECASE)
    # Drop remaining HTML tags.
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text


def table_rows(md: str) -> list:
    """Extract rows from any <table> blocks as lists of cell texts.

    Returns an empty list if the markdown contains no tables.
    """
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", md, flags=re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)
        # Normalize whitespace inside each cell.
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in cells
        ]
        if cells:
            rows.append(cells)
    return rows
