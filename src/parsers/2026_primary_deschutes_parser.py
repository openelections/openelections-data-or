#!/usr/bin/env python3
"""Parse Deschutes County 2026 Oregon primary precinct PDF.

Reads cached PaddleOCR-VL-1.6 markdown files under
``.paddleocr_cache/Deschutes/p*.md`` and writes
``2026/counties/20260519__or__primary__deschutes__precinct.csv``.

Verification results (``src/verify_precinct_totals.py --county Deschutes``):

* ``duplicate_entries``, ``file_format``, ``missing_values``, and
  ``vote_breakdown_totals`` all pass for the generated precinct CSV.
* Precinct/county comparison reports **1 mismatch**, **1 county total missing
  in precincts**, and **55 precinct sums missing in county CSV**.

Known data-quality notes (these are explainable when verifying against
``2026/20260519__or__primary__county.csv``):

* The county-level CSV conflates several candidate names in the Governor
  contest (e.g., ``Forest Steve (Fora) William Laible Alexander Atkinson``,
  ``Youker Tim O. Chris DeAngelo Dudley``). The precinct PDF lists the
  individual names; after matching by vote total where possible the remaining
  Governor rows are left as parsed, so those names are reported as
  ``NOT_IN_COUNTY``.
* State Measure 120 is listed in the county CSV as ``Candidate 1`` and
  ``Candidate 2``. The precinct PDF lists ``Yes``/``No``. The alignment helper
  maps ``Yes`` to ``Candidate 1`` and ``No`` to ``Candidate 2`` by sorted vote
  total.
* ``Ballots Cast`` and ``Registered Voters`` rows are produced for every
  precinct because the PDF provides them per contest. The county CSV does not
  contain these rows, so the verifier reports them as ``NOT_IN_COUNTY``.
* The county CSV does not include any ``Judge of the Supreme Court``,
  ``Labor Commissioner``, or ``District Attorney`` rows, so those contests are
  reported as missing from the county CSV.
* The PDF's write-in abstract pages for State Representative 54 (Rep) are
  skipped. The county CSV contains an ``Andrew Caruana`` Republican row (27
  votes) that is not present on the ordinary contest pages, so that county total
  is reported as both ``NOT_IN_PRECINCTS`` and a ``MISMATCH``.
* Over/Under vote rows and write-in rows are emitted for every contest where
  the PDF supplies them. The county CSV only records ``Misc.`` for write-ins,
  and only for some contests, so write-in/Over/Under rows are often reported as
  ``NOT_IN_COUNTY``. Write-ins are mapped to ``Misc.`` when totals match.
"""

import argparse
import csv
import html
import re
from typing import Dict, List, Optional, Tuple

from paddleocr_extract import extract_pages, plain_text
from precinct_2026_common import (
    align_candidates_to_county,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    recover_missing_precincts,
    write_csv,
)


COUNTY_NAME = "Deschutes"
COUNTY_CSV = "2026/20260519__or__primary__county.csv"

_FIXED_LABELS = {
    "precinct",
    "ballots",
    "ballots cast",
    "cast",
    "reg.",
    "reg",
    "registered",
    "registered voters",
    "voters",
    "total",
    "total votes",
    "votes",
    "vote %",
}

_PSEUDO_LABELS = {"write-in", "write in", "over votes", "under votes"}


def _clean_int(text: str) -> Optional[int]:
    text = text.strip().replace(",", "")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


def _is_percent(text: str) -> bool:
    return "%" in text


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_contest(text: str) -> Tuple[Optional[str], Optional[str], str]:
    """Return (office, district, party) from the page text.

    Tries the administrative line first, then falls back to a table title like
    ``Governor (REP) (Vote for 1)``.
    """
    # Administrative line: between ScanStations and All Boxes.
    m = re.search(
        r"All Counter Groups[,\s]*All ScanStations[,\s]*(.+?)\s*[,\s]*All Boxes",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        m = re.search(
            r"All Counter Groups[,\s]*All ScanStations[,\s]*(.+?)(?:\n\n|\Z)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    contest_text = ""
    if m:
        contest_text = _normalize_ws(m.group(1))
        # p015 uses periods instead of commas and lacks All Boxes.
        contest_text = re.sub(r"Total Ballots Cast:.*", "", contest_text).strip()
    if not contest_text:
        # Fall back to a title row in the table text.  The title may include a
        # party code, e.g. ``Governor (REP) (Vote for 1)``, or be nonpartisan,
        # e.g. ``Judge of the Court of Appeals, Position 9 (Vote for 1)``.
        # On some pages the title is one long line that also contains the
        # table headers, so we split on the ``(Vote for ...)`` marker.
        for line in text.splitlines():
            line = line.strip()
            if re.search(r"\(?Vote\s+for", line, re.IGNORECASE):
                parts = re.split(r"\(?Vote\s+for", line, flags=re.IGNORECASE)
                contest_text = _normalize_ws(parts[0])
                break
    if not contest_text:
        return None, None, ""

    party = ""
    party_match = re.search(r"\(([A-Z]+)\)", contest_text, re.IGNORECASE)
    if party_match:
        party = normalize_party(party_match.group(1))
        contest_text = contest_text[: party_match.start()] + contest_text[party_match.end() :]
        contest_text = re.sub(r"\(\s*\)", "", contest_text).strip()

    # Some pages put the party only in the table title (e.g. State House 59),
    # so fall back to a party code anywhere in the page text.
    if not party:
        any_party = re.search(r"\(([A-Z]{3})\)", text, re.IGNORECASE)
        if any_party:
            party = normalize_party(any_party.group(1))

    office = normalize_office(contest_text)
    district = parse_district(contest_text)
    return office, district, party


def _parse_html_table(md: str) -> List[List[Tuple[str, int]]]:
    """Return table rows as lists of (cell_text, colspan).

    Also expands ``rowspan`` headers by duplicating the spanning cell into the
    following rows, and splits cells like ``"1 100.00%"`` into two columns.
    """
    rows: List[List[Tuple[str, int]]] = []
    pending_rowspans: Dict[int, Tuple[str, int, int]] = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", md, flags=re.S | re.I):
        cells: List[Tuple[str, int]] = []
        col = 0
        # First, place any pending rowspans into this row.
        pending_copy = dict(pending_rowspans)
        for c in sorted(pending_copy.keys()):
            while col < c:
                col += 1
            text, span, remaining = pending_rowspans[c]
            cells.insert(c, (text, span))
            if remaining <= 1:
                del pending_rowspans[c]
            else:
                pending_rowspans[c] = (text, span, remaining - 1)
        # Now process the actual cells in this row.
        for m in re.finditer(r"<td([^>]*)>(.*?)</td>", tr, flags=re.S | re.I):
            attrs = m.group(1)
            raw_content = m.group(2)
            span = 1
            sm = re.search(r'colspan=["\']?(\d+)', attrs, re.IGNORECASE)
            if sm:
                span = int(sm.group(1))
            rowspan = 1
            rm = re.search(r'rowspan=["\']?(\d+)', attrs, re.IGNORECASE)
            if rm:
                rowspan = int(rm.group(1))
            content = re.sub(r"<[^>]+>", " ", raw_content)
            content = html.unescape(content)
            content = _normalize_ws(content)
            # Split merged number-percentage cells.
            split_m = re.fullmatch(
                r"(\d{1,6}(?:,\d{3})*)\s+(\d{1,3}(?:\.\d+)?%)", content
            )
            if split_m:
                cells.append((split_m.group(1), span))
                cells.append((split_m.group(2), span))
            else:
                cells.append((content, span))
            if rowspan > 1:
                pending_rowspans[col] = (content, span, rowspan - 1)
            col += span
        if cells:
            rows.append(cells)
    return rows


def _find_header_row(rows: List[List[Tuple[str, int]]]) -> Tuple[int, int]:
    """Return (row_index, fixed_len) for the per-precinct header row."""
    for i, row in enumerate(rows):
        if not row or not row[0][0].strip().lower().startswith("precinct"):
            continue
        # Skip title-only rows such as ['Precinct', '50 precincts reported...'].
        if len(row) <= 2:
            continue
        # Find Total Votes / Total to fix the prefix length.
        fixed_len = None
        col = 0
        for text, span in row:
            if re.search(r"^(total votes|total)$", text.strip(), re.IGNORECASE):
                fixed_len = col + span
            col += span
        if fixed_len:
            # Ensure the next cell is not a fixed label; otherwise keep scanning.
            return i, fixed_len
    # Fallback: find the first row whose first cell is a fixed column header.
    for i, row in enumerate(rows):
        if not row:
            continue
        first = row[0][0].strip().lower()
        if first in {"ballots cast", "ballots", "reg. voters", "reg.", "total votes", "total"}:
            col = 0
            for text, span in row:
                if re.search(r"^(total votes|total)$", text.strip(), re.IGNORECASE):
                    return i, col + span
                col += span
    raise ValueError("No Precinct header row found")


def _header_cells_after_fixed(
    rows: List[List[Tuple[str, int]]], header_idx: int, fixed_len: int
) -> List[Tuple[str, int, int]]:
    """Return header cells after the fixed prefix as (text, start, end)."""
    out = []
    col = 0
    for text, span in rows[header_idx]:
        end = col + span - 1
        if col >= fixed_len:
            out.append((text, col - fixed_len, end - fixed_len))
        col += span
    return out


def _metric_columns(data_rows: List[List[str]], fixed_len: int) -> List[int]:
    """Return offsets (after the fixed prefix) that contain integer vote totals."""
    if not data_rows:
        return []
    max_len = max(len(r) for r in data_rows)
    metric = []
    for col in range(fixed_len, max_len):
        values = [
            r[col].strip()
            for r in data_rows
            if col < len(r) and r[col].strip()
        ]
        if not values:
            continue
        if any(_is_percent(v) for v in values):
            continue
        if any(_clean_int(v) is not None for v in values):
            metric.append(col - fixed_len)
    return metric


def _ordered_label_assignment(
    labels: List[str], metric_offsets: List[int], pct_offsets: List[int]
) -> Dict[int, str]:
    """Assign labels to metric offsets for span-1 headers with percentages.

    A metric that is followed by a percentage column forms a two-column group
    representing one candidate/pseudo-candidate.  We pair header labels to
    those groups in order, which handles the common OCR layout where the first
    candidate's percentage column has no explicit header cell.
    """
    # Build metric groups: each group starts at a metric offset and includes any
    # immediately following percentage columns.
    metric_offsets = sorted(metric_offsets)
    pct_offsets = set(pct_offsets)
    groups: List[List[int]] = []
    for m in metric_offsets:
        if groups and m in groups[-1]:
            continue
        group = [m]
        nxt = m + 1
        while nxt in pct_offsets:
            group.append(nxt)
            nxt += 1
        groups.append(group)

    mapping: Dict[int, str] = {}
    used: set[int] = set()
    label_idx = 0
    for group in groups:
        m = group[0]
        # Advance past empty/end labels until we find a plausible label for
        # this group, but never use a label that comes after the group start
        # unless necessary.
        while label_idx < len(labels) and (
            not labels[label_idx].strip() or labels[label_idx].strip().lower() in _FIXED_LABELS
        ):
            used.add(label_idx)
            label_idx += 1
        # Prefer a label at or before the metric. If none, use the next unused.
        chosen = None
        for idx in range(label_idx, len(labels)):
            if idx in used:
                continue
            if idx > m and chosen is not None:
                break
            chosen = idx
            if idx <= m:
                break
        if chosen is None:
            continue
        mapping[m] = labels[chosen].strip()
        used.add(chosen)
        label_idx = chosen + 1
    return mapping


def _map_labels(
    header_cells: List[Tuple[str, int, int]],
    labels_flat: List[str],
    metric_offsets: List[int],
    pct_offsets: List[int],
) -> Dict[int, str]:
    """Map metric offsets to header labels by grouping metrics with percentages."""
    # Ignore the actual colspan values in the parsed header: OCR frequently omits
    # the percentage-only header cell for the first candidate and shifts the
    # remaining labels. Grouping the metric columns with their following
    # percentage columns and pairing those groups with labels in order is robust
    # across both colspan and non-colspan layouts.
    return _ordered_label_assignment(labels_flat, metric_offsets, pct_offsets)


def _candidate_name(label: str) -> str:
    clean = label.strip()
    lower = clean.lower()
    if lower in {"write-in", "write in"}:
        return "Write-ins"
    if lower == "over votes":
        return "Over Votes"
    if lower == "under votes":
        return "Under Votes"
    name = format_candidate_name(clean.split())
    # Normalise to title case while preserving initials like "O'Connor".
    return name.title()


def _load_county_totals(
    path: str,
) -> Dict[Tuple[str, str, str, str], int]:
    totals: Dict[Tuple[str, str, str, str], int] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row["county"].strip() != COUNTY_NAME:
                continue
            key = (
                row["office"].strip(),
                row["district"].strip(),
                row["party"].strip(),
                row["candidate"].strip(),
            )
            totals[key] = totals.get(key, 0) + int(row["votes"])
    return totals


def _normalize_name_key(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ").strip()
    return name


def _assign_by_county(
    metric_cols: List[Dict],
    office: str,
    district: str,
    party: str,
    county_totals: Dict[Tuple[str, str, str, str], int],
    used_county_names: Optional[Dict[Tuple[str, str, str], set]] = None,
) -> List[Tuple[int, str, Dict[str, int]]]:
    """Return (column_index, assigned_name, values) using county totals.

    ``metric_cols`` items have keys ``values`` (precinct->votes), ``label``,
    ``total``. The county totals include ``Misc.`` for write-ins. Over/Under
    columns are inferred from the remaining data.

    ``used_county_names`` is a mapping from ``(office, district, party)`` to a
    set of county candidate names already used for that contest. Names are
    tracked per-contest so that multi-page contests do not map several distinct
    PDF candidates to the same county name (essential for Governor, where the
    county CSV conflates names). Write-in ``Misc.`` matching is also per-contest,
    so a write-in column on a later page of the same contest is still emitted.
    """
    contest_key = (office, district, party)
    expected = {
        cand: total
        for (o, d, p, cand), total in county_totals.items()
        if (o, d, p) == contest_key
    }
    if not expected:
        return []

    if used_county_names is None:
        used_county_names = {}
    used_names = used_county_names.setdefault(contest_key, set())

    # Compute expected Under Votes from each page's Ballots/Total data.
    under_expected = 0
    seen_precincts: set[str] = set()
    for col in metric_cols:
        for precinct, ballots, total in col.get("bt_pairs", []):
            if precinct in seen_precincts:
                continue
            seen_precincts.add(precinct)
            if ballots is not None and total is not None:
                under_expected += max(0, ballots - total)

    misc_total = expected.pop("Misc.", None)

    assignments: List[Tuple[int, str, Dict[str, int]]] = []
    used: set[int] = set()

    # Write-ins: match to Misc. total if present.
    if misc_total is not None and "Misc." not in used_names:
        best_idx = None
        best_diff = float("inf")
        for idx, col in enumerate(metric_cols):
            if idx in used:
                continue
            if col["label"].lower() in {"write-in", "write ins"}:
                diff = abs(col["total"] - misc_total)
                if diff <= 5 and diff < best_diff:
                    best_idx = idx
                    best_diff = diff
        if best_idx is None:
            for idx, col in enumerate(metric_cols):
                if idx in used:
                    continue
                diff = abs(col["total"] - misc_total)
                if diff <= 5 and diff < best_diff:
                    best_idx = idx
                    best_diff = diff
        if best_idx is not None:
            assignments.append((best_idx, "Write-ins", metric_cols[best_idx]["values"]))
            used.add(best_idx)
            used_names.add("Misc.")

    # Candidate columns: match totals to expected candidate totals.
    expected_items = sorted(expected.items(), key=lambda x: x[1], reverse=True)
    for cand, cand_total in expected_items:
        if cand in used_names:
            continue
        best_idx = None
        best_diff = float("inf")
        for idx, col in enumerate(metric_cols):
            if idx in used:
                continue
            if col["label"].lower() in _PSEUDO_LABELS:
                continue
            diff = abs(col["total"] - cand_total)
            if diff <= 5 and diff < best_diff:
                best_idx = idx
                best_diff = diff
        if best_idx is not None:
            assignments.append((best_idx, cand, metric_cols[best_idx]["values"]))
            used.add(best_idx)
            used_names.add(cand)

    # Under Votes: remaining column whose total matches the computed under total.
    remaining = [idx for idx in range(len(metric_cols)) if idx not in used]
    if remaining and under_expected:
        best_idx = min(
            remaining,
            key=lambda idx: abs(metric_cols[idx]["total"] - under_expected),
        )
        if abs(metric_cols[best_idx]["total"] - under_expected) <= 5:
            assignments.append((best_idx, "Under Votes", metric_cols[best_idx]["values"]))
            used.add(best_idx)
            remaining.remove(best_idx)

    # Over Votes: a remaining small column, or one labeled Over.
    for idx in remaining:
        col = metric_cols[idx]
        if col["label"].lower() == "over votes":
            assignments.append((idx, "Over Votes", col["values"]))
            used.add(idx)

    # Anything left over is probably Under Votes if not already assigned.
    remaining = [idx for idx in range(len(metric_cols)) if idx not in used]
    for idx in remaining:
        col = metric_cols[idx]
        if col["label"].lower() == "under votes":
            assignments.append((idx, "Under Votes", col["values"]))
            used.add(idx)

    return assignments


def _parse_page(
    markdown: str,
    county_totals: Dict[Tuple[str, str, str, str], int],
    used_county_names: Optional[Dict[Tuple[str, str, str], set]] = None,
) -> List[Dict[str, str]]:
    text = plain_text(markdown)
    if "Abstract of Write-In Votes" in text or "Write-In Candidate Name" in text:
        return []

    rows = _parse_html_table(markdown)
    if not rows:
        return []

    try:
        header_idx, fixed_len = _find_header_row(rows)
    except ValueError:
        return []

    office, district, party = _extract_contest(text)
    if office is None:
        return []

    header_cells = _header_cells_after_fixed(rows, header_idx, fixed_len)
    labels_flat = [text for text, _, _ in header_cells]

    # Flatten data rows to plain strings (colspan already expanded).
    data_rows: List[List[str]] = []
    for r in rows[header_idx + 1 :]:
        if not r:
            continue
        first = r[0][0].strip().lower()
        if first.startswith("precinct") and first != "total":
            data_rows.append([text for text, _ in r])

    if not data_rows:
        return []

    max_len = max(len(r) for r in data_rows)
    # Ensure every row has the same number of columns.
    for r in data_rows:
        while len(r) < max_len:
            r.append("")

    metric_offsets = _metric_columns(data_rows, fixed_len)
    pct_offsets = set()
    for col in range(fixed_len, max_len):
        vals = [r[col].strip() for r in data_rows if r[col].strip()]
        if any(_is_percent(v) for v in vals):
            pct_offsets.add(col - fixed_len)

    mapping = _map_labels(header_cells, labels_flat, metric_offsets, pct_offsets)

    # Build metric column objects.
    metric_cols: List[Dict] = []
    for m in metric_offsets:
        values: Dict[str, int] = {}
        for r in data_rows:
            val = _clean_int(r[fixed_len + m])
            if val is not None:
                values[r[0].strip()] = val
        label = mapping.get(m, "")
        metric_cols.append(
            {
                "values": values,
                "label": label.lower() if label else "",
                "total": sum(values.values()),
                "bt_pairs": [],
            }
        )

    # Record Ballots/Total pairs per precinct for Under-Vote computation.
    seen: set[str] = set()
    for r in data_rows:
        precinct = r[0].strip()
        if precinct in seen:
            continue
        seen.add(precinct)
        ballots = _clean_int(r[1]) if len(r) > 1 else None
        total = _clean_int(r[3]) if len(r) > 3 else None
        for col in metric_cols:
            col["bt_pairs"].append((precinct, ballots, total))

    # County-level assignment matches columns to canonical county names and
    # maps write-ins to ``Misc.``. Used names are tracked per contest so that
    # multi-page contests do not reuse a county name once it has been assigned
    # to an earlier page (essential for Governor, where the county CSV
    # conflates several candidate names). Write-in matching is also per-contest,
    # so later pages of a contest still emit their write-in rows.
    assignments = _assign_by_county(
        metric_cols, office, district, party, county_totals, used_county_names
    )
    if not assignments:
        assignments = [
            (idx, _candidate_name(mapping.get(m, "")), metric_cols[idx]["values"])
            for idx, m in enumerate(metric_offsets)
        ]

    out: List[Dict[str, str]] = []
    seen_rows: set[tuple] = set()

    def add(row: Dict[str, str]) -> None:
        key = tuple(row.values())
        if key in seen_rows:
            return
        seen_rows.add(key)
        out.append(row)

    # Emit Ballots Cast and Registered Voters rows for every precinct on the
    # page. They are consolidated globally later, choosing the maximum per
    # precinct across all contests.
    for r in data_rows:
        precinct = r[0].strip()
        ballots = _clean_int(r[1]) if len(r) > 1 else None
        registered = _clean_int(r[2]) if len(r) > 2 else None
        if ballots is not None:
            add(make_row(COUNTY_NAME, precinct, "Ballots Cast", "", "", "", ballots))
        if registered is not None:
            add(make_row(COUNTY_NAME, precinct, "Registered Voters", "", "", "", registered))

    for idx, name, values in assignments:
        if not name:
            continue
        for precinct, votes in values.items():
            add(make_row(COUNTY_NAME, precinct, office, district, party, name, votes))

    return out


def _dedup_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[tuple] = set()
    out: List[Dict[str, str]] = []
    for row in rows:
        key = tuple(row.values())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _consolidate_pseudo_offices(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep one Ballots Cast and one Registered Voters row per precinct.

    The PDF reports these values per contest, so a precinct may have many
    values. The canonical format requires a single value per precinct; we use
    the maximum (which corresponds to the county-wide / total-ballot figure).
    """
    best: Dict[Tuple[str, str, str, str, str], int] = {}
    for row in rows:
        if row["office"] not in {"Ballots Cast", "Registered Voters"}:
            continue
        key = (
            row["county"],
            row["precinct"],
            row["office"],
            row["district"],
            row["party"],
            row["candidate"],
        )
        votes = int(row["votes"])
        if key not in best or votes > best[key]:
            best[key] = votes

    consolidated: List[Dict[str, str]] = []
    for row in rows:
        if row["office"] in {"Ballots Cast", "Registered Voters"}:
            key = (
                row["county"],
                row["precinct"],
                row["office"],
                row["district"],
                row["party"],
                row["candidate"],
            )
            if int(row["votes"]) == best.get(key):
                consolidated.append(row)
                del best[key]
        else:
            consolidated.append(row)
    return consolidated


def _consolidate_candidate_duplicates(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep a single row per candidate/precinct key.

    Multi-page contests and county-level name alignment can produce multiple
    rows with the same (county, precinct, office, district, party,
    candidate) key. If any row for a key is non-zero, drop the zero rows and
    keep the maximum non-zero value. This eliminates the zero-vote duplicates
    that appear when a candidate column spans multiple pages while preserving
    the actual precinct votes.
    """
    from collections import defaultdict

    groups: Dict[Tuple[str, str, str, str, str, str], List[int]] = defaultdict(list)
    for row in rows:
        if row["office"] in {"Ballots Cast", "Registered Voters"}:
            continue
        key = (
            row["county"],
            row["precinct"],
            row["office"],
            row["district"],
            row["party"],
            row["candidate"],
        )
        groups[key].append(int(row["votes"]))

    best: Dict[Tuple[str, str, str, str, str, str], int] = {}
    for key, vals in groups.items():
        positive = [v for v in vals if v > 0]
        best[key] = max(positive) if positive else 0

    out: List[Dict[str, str]] = []
    seen: set[Tuple[str, str, str, str, str, str]] = set()
    for row in rows:
        if row["office"] in {"Ballots Cast", "Registered Voters"}:
            out.append(row)
            continue
        key = (
            row["county"],
            row["precinct"],
            row["office"],
            row["district"],
            row["party"],
            row["candidate"],
        )
        if key in seen:
            continue
        row = dict(row)
        row["votes"] = str(best[key])
        seen.add(key)
        out.append(row)
    return out


def parse(pdf_path: str) -> List[Dict[str, str]]:
    county_totals = _load_county_totals(COUNTY_CSV)
    all_rows: List[Dict[str, str]] = []
    used_county_names: Dict[Tuple[str, str, str], set] = {}
    for page_num, markdown in extract_pages(pdf_path):
        if page_num <= 7:
            continue
        page_rows = _parse_page(markdown, county_totals, used_county_names)
        if page_rows:
            all_rows.extend(page_rows)
    all_rows = _consolidate_pseudo_offices(all_rows)
    return _dedup_rows(all_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf_path", help="path to the Deschutes County PDF (cache is used)")
    args = ap.parse_args()

    rows = parse(args.pdf_path)
    # Governor names are deliberately left as parsed from the PDF because the
    # county CSV conflates several candidates together; aligning to it would
    # create duplicate rows and merge distinct candidates.
    gov_rows = [r for r in rows if r["office"] == "Governor"]
    other_rows = [r for r in rows if r["office"] != "Governor"]
    other_rows = align_candidates_to_county(other_rows, COUNTY_CSV, COUNTY_NAME, tolerance=5)
    rows = gov_rows + other_rows
    rows = _consolidate_candidate_duplicates(rows)
    rows = recover_missing_precincts(rows, COUNTY_CSV, COUNTY_NAME, missing_precincts={})

    out_path = output_path(COUNTY_NAME)
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
