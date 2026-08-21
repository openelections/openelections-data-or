#!/usr/bin/env python3
"""Parse Hart/VSAP-style "Detail Results By Precinct" PDFs.

Works with cached PaddleOCR-VL-1.6 markdown for Sherman, Wallowa, and Grant
counties in the 2026 Oregon primary.
"""

import argparse
import csv
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from paddleocr_extract import extract_pages, plain_text, table_rows
from precinct_2026_common import (
    COUNTIES,
    DISTRICTED_OFFICES,
    PSEUDO_CANDIDATES,
    PSEUDO_OFFICES,
    align_candidates_to_county,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    output_path,
    parse_district,
    parse_number,
    recover_missing_precincts,
    write_csv,
)

# ---------------------------------------------------------------------------
# Common text helpers
# ---------------------------------------------------------------------------


def _flatten(text: str) -> str:
    text = text.replace("\\n", " ")
    text = text.replace("\\r", " ")
    text = text.replace("\\t", " ")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _to_int(text: str) -> Optional[int]:
    text = text.replace(",", "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _all_text_blocks(md: str) -> List[str]:
    """Return every logical text block from markdown tables and plain text."""
    blocks = []
    # Pull text from table cells.
    for row in table_rows(md):
        for cell in row:
            t = _flatten(cell)
            if t:
                blocks.append(t)
    # Also pull any plain paragraphs outside tables.
    text = plain_text(md)
    for line in text.split("\n"):
        t = _flatten(line)
        if t:
            blocks.append(t)
    return blocks


def _looks_like_office_line(text: str) -> bool:
    if normalize_office(text):
        return True
    if re.search(r"\bMeasure\b", text, re.IGNORECASE):
        return True
    if re.search(r"\bPrecinct\s+Committee\s+Person\b", text, re.IGNORECASE):
        return True
    if re.search(r"\bDistrict\s+Attorney\b", text, re.IGNORECASE):
        return True
    if re.search(r"\bJudge\s+of\s+(the\s+)?(Supreme|Circuit|Court\s+of\s+Appeals)\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(County\s+Commissioner|County\s+Assessor|Justice\s+of\s+the\s+Peace|Sherman\s+County)", text, re.IGNORECASE):
        return True
    return False


def _extract_party_from_line(text: str) -> str:
    up = text.upper()
    # Common prefixes.
    if re.search(r"\b(DEM|DEMOCRAT)\b", up):
        return "D"
    if re.search(r"\b(REP|REPUBLICAN)\b", up):
        return "R"
    if re.search(r"\bNON\s*PARTISAN|NONPARTISAN\b", up):
        return ""
    return ""


def _clean_candidate_name(text: str) -> str:
    text = re.sub(r"\(Vote\s+For\s+\d+\)", "", text, flags=re.IGNORECASE)
    text = text.replace("(WI)", "")
    text = re.sub(r"\bWrite-in:?\b", "Write-in", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNo\s+Candidate\s+Filed\b", "No Candidate Filed", text, flags=re.IGNORECASE)
    parts = text.split()
    return format_candidate_name(parts)


def _normalize_candidate(text: str) -> str:
    t = text.lower().strip()
    if "write-in" in t or "write in" in t:
        return "Write-ins"
    if "over vote" in t or "overvote" in t:
        return "Over Votes"
    if "under vote" in t or "undervote" in t:
        return "Under Votes"
    # Common OCR misread of "Under Votes".
    if t == "write votes":
        return "Under Votes"
    if t == "total" or t == "contest total":
        return ""
    return _clean_candidate_name(text)


def _parse_office_line(text: str) -> Tuple[str, str, str]:
    """Return (office, district, party) from a contest header line."""
    text = _flatten(text)

    party = _extract_party_from_line(text)

    # Build a version of the line suitable for office/district detection.
    # Remove metadata tokens, but keep the office/district text itself.
    detect = text
    detect = re.sub(r"\(Vote\s+For\s+\d+\)", "", detect, flags=re.IGNORECASE)
    detect = re.sub(r"\(WI\)", "", detect, flags=re.IGNORECASE)
    detect = re.sub(r"\bFederal\b", "", detect, flags=re.IGNORECASE)
    detect = re.sub(r"\bStatewide\s+(Partisan|Nonpartisan)\b", "", detect, flags=re.IGNORECASE)
    detect = re.sub(r"^\s*(DEM|REP)\s+", "", detect, flags=re.IGNORECASE)
    detect = re.sub(r"\b(Democrat|Republican|Nonpartisan)\b", "", detect, flags=re.IGNORECASE)
    # Some office lines have the first candidate's vote total merged onto them.
    # Strip a trailing number unless it is part of the office/district label
    # (e.g. "Measure 120", "Position 9", "District 58", "#2").
    tail = re.search(r"(\d+)\s*$", detect)
    if tail:
        before = detect[: tail.start()].split()
        preceding = before[-1].lower() if before else ""
        keep = preceding in {
            "measure", "position", "district", "#",
        } or bool(re.search(r"#\s*$", detect[: tail.start()]))
        if not keep:
            detect = detect[: tail.start()].strip()
    detect = detect.strip(" -,")

    office = normalize_office(detect)
    if office is None:
        # Measures.
        m = re.search(r"(State\s+)?Measure\s+\d+", detect, re.IGNORECASE)
        if m:
            office = m.group(0).title()
        elif re.search(r"\bPrecinct\s+Committee\s+Person\b", detect, re.IGNORECASE):
            office = "Precinct Committee Person"
        elif re.search(r"\bCounty\s+Commissioner\b", detect, re.IGNORECASE):
            office = "County Commissioner"
        elif re.search(r"\bDistrict\s+Attorney\b", detect, re.IGNORECASE):
            office = "District Attorney"
        elif re.search(r"\bJustice\s+of\s+the\s+Peace\b", detect, re.IGNORECASE):
            office = "Justice of the Peace"
        else:
            office = detect

    district = parse_district(detect)
    # For county commissioner races the position number is not a district.
    if office == "County Commissioner":
        m = re.search(r"#\s*(\d+)", detect)
        if m:
            district = m.group(1)

    return office, district, party


# ---------------------------------------------------------------------------
# Per-page contest extraction
# ---------------------------------------------------------------------------


def _table_row_text(row: List[str]) -> str:
    """Join non-empty cells of a table row into a single logical text line."""
    parts = []
    for c in row:
        t = _flatten(c)
        if t:
            parts.append(t)
    return " ".join(parts)


def _looks_like_meta(text: str) -> bool:
    # Keep this narrow: skip only boilerplate lines, not contest headers that
    # happen to contain the county name.
    if re.search(r"Detail Results|^Machine|First Ballot|Last Ballot|Total Sheets|Sheets Processed|Ballot Style|^Seq:|Reporting|Primary Election|Contest\s+Votes", text, re.IGNORECASE):
        return True
    if re.search(r"^Party$|^Ballots Cast$|^Contest$|^Votes$", text, re.IGNORECASE):
        return True
    # PaddleOCR sometimes hallucinates a run of "YYYY Votes" lines; drop them.
    if re.search(r"\bVotes\s+\d{4}\b", text, re.IGNORECASE):
        return True
    # Stand-alone county/"May 2026" banner lines are meta, but lines that also
    # contain an office/candidate name are not.
    if re.fullmatch(r"\s*Wallowa County\s*|\s*Sherman County\s*|\s*Grant County\s*|\s*Wallowa County May 2026 Primary Election\s*", text, re.IGNORECASE):
        return True
    return False


def _extract_lines(md: str) -> List[str]:
    """Return logical text lines from a page of markdown."""
    lines: List[str] = []
    for row in table_rows(md):
        line = _table_row_text(row)
        if line:
            lines.append(line)
    text = plain_text(md)
    for line in text.split("\n"):
        t = _flatten(line)
        if t and t not in lines:
            lines.append(t)
    return lines


def _process_lines(
    lines: List[str],
    county_name: str,
    precinct_name: str,
    include_county: bool = True,
    start_state: Optional[Tuple[str, str, str]] = None,
) -> Tuple[List[Dict[str, str]], Tuple[str, str, str]]:
    """Parse logical lines into CSV rows, optionally carrying contest state over.

    Returns the parsed rows and the final (office, district, party) state so
    multi-page contests can continue on the next page.
    """
    rows: List[Dict[str, str]] = []
    current_office, current_district, current_party = start_state or ("", "", "")

    for line in lines:
        if _looks_like_meta(line):
            continue

        if re.search(r"Total Ballots Cast|Blank Sheets Cast|Registered Voters", line, re.IGNORECASE):
            continue

        # Some pages (especially the second PC-person page in a precinct) lose the
        # office line to OCR noise. The "(Vote For N)" line with N > 1 is a
        # reliable marker for a PC-person block; set a placeholder office and
        # fix the party later from the precinct's other PC-person block.
        orphan_pcp = re.fullmatch(
            r"\(Vote\s+For\s+([2-9])\)", line.strip(), flags=re.IGNORECASE
        )
        if orphan_pcp and current_office != "Precinct Committee Person":
            current_office = "Precinct Committee Person"
            current_district = ""
            current_party = "?"
            continue

        if _looks_like_office_line(line):
            current_office, current_district, current_party = _parse_office_line(line)
            continue

        if not current_office:
            continue

        line = re.sub(r"\(Vote\s+For\s+\d+\)", "", line, flags=re.IGNORECASE).strip()
        m = re.match(r"^(.*?)\s+(\d[\d,]*|0)\s*$", line)
        if not m:
            continue

        name_part = m.group(1).strip()
        votes = _to_int(m.group(2))
        if votes is None:
            continue

        if name_part.lower() in {"total", "contest total", "total votes cast"}:
            continue

        candidate = _normalize_candidate(name_part)
        if not candidate:
            continue

        office = current_office
        district = current_district
        party = current_party

        if office.startswith("Measure"):
            if candidate.lower() in {"candidate 1", "yes"}:
                candidate = "Yes"
            elif candidate.lower() in {"candidate 2", "no"}:
                candidate = "No"

        if not include_county:
            if office not in {
                "U.S. Senate", "U.S. House", "Governor", "State Senate", "State House",
                "Measure 120",
            } and not office.startswith("Measure"):
                continue

        rows.append(
            make_row(
                county=county_name,
                precinct=precinct_name,
                office=office,
                district=district,
                party=party,
                candidate=candidate,
                votes=votes,
            )
        )

    return rows, (current_office, current_district, current_party)


def _extract_page_rows(
    md: str,
    county_name: str,
    precinct_name: str,
    include_county: bool = True,
) -> List[Dict[str, str]]:
    """Parse one page of markdown into CSV rows (state does not carry over)."""
    rows, _ = _process_lines(
        _extract_lines(md), county_name, precinct_name, include_county
    )
    return rows


# ---------------------------------------------------------------------------
# County-specific page grouping and precinct naming
# ---------------------------------------------------------------------------


def _find_pc_person_precinct(md: str) -> Optional[str]:
    """Return a precinct name found in a Precinct Committee Person contest."""
    text = plain_text(md)
    # Try several formats seen in the cached markdown.
    patterns = [
        r"Precinct\s+Committee\s+Person\s+.*?-\s+(?:Democrat|Republican)\s+(.+?)(?:\s+\(Vote\s+For|\s+\d|$)",
        r"Precinct\s+Committee\s+Person\s+(?:Democrat|Republican)\s+(.+?)(?:\s+\(Vote\s+For|\s+\d|$)",
        r"DEM\s+Precinct\s+Committee\s+Person\s+Democrat\s+(.+?)(?:\s+\(Vote\s+For|\s+\d|$)",
        r"REP\s+Precinct\s+Committee\s+Person\s+Republican\s+(.+?)(?:\s+\(Vote\s+For|\s+\d|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # Drop trailing repeated word (e.g. "Rufus Rufus").
            parts = name.split()
            if len(parts) >= 2 and parts[-1].lower() == parts[-2].lower():
                name = " ".join(parts[:-1])
            # Clean artifacts.
            name = re.sub(r"\s+", " ", name).strip()
            if name:
                return name
    return None


def _find_header_precinct(md: str) -> Optional[str]:
    """Return a standalone precinct name from the page header (Wallowa/Grant)."""
    rows = table_rows(md)
    # Look in the first few rows for patterns like "ENTERPRISE #1".
    for r in rows[:6]:
        for c in r:
            c = _flatten(c)
            if re.search(r"^(Enterprise|Joseph|Wallowa|Lostine|Imnaha|Flora|Troy)\s*#?\s*\d+", c, re.IGNORECASE):
                m = re.search(r"(Enterprise|Joseph|Wallowa|Lostine|Imnaha|Flora|Troy)\s*#?\s*\d+", c, re.IGNORECASE)
                return m.group(0).strip()
            # Grant-style names.
            if re.search(r"^(John Day Valley|Auwii Catoo|Union|South Fork|Laurie Cates|Laura Cates)", c, re.IGNORECASE):
                m = re.search(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", c)
                if m:
                    return m.group(0).strip()
    return None


def _find_ballots_cast(md: str) -> Optional[int]:
    rows = table_rows(md)
    for r in rows:
        joined = " | ".join(r)
        if "Total Ballots Cast" in joined:
            for c in r:
                v = _to_int(c)
                if v is not None:
                    return v
    text = plain_text(md)
    m = re.search(r"Total Ballots Cast[:\s]+(\d+)", text)
    if m:
        return _to_int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Wallowa
# ---------------------------------------------------------------------------


def _normalise_precinct(name: str) -> str:
    """Return a stable precinct name (uppercase Wallowa town names)."""
    return name.strip().upper()


def _assign_pcp_parties(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Fill in missing party codes for orphaned PC-person blocks.

    Primary PC-person contests are party-specific.  When OCR drops the office
    line for one of the two blocks in a precinct, we mark it with a placeholder
    party.  This routine assigns the missing party by looking at the precinct's
    other PC-person block.
    """
    out = []
    by_precinct: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        by_precinct.setdefault(row["precinct"], []).append(row)

    for precinct, prs in by_precinct.items():
        # Identify PC-person blocks by scanning the rows in source order.
        blocks: List[Tuple[int, int]] = []
        i = 0
        while i < len(prs):
            if prs[i]["office"] == "Precinct Committee Person":
                start = i
                while i < len(prs) and prs[i]["office"] == "Precinct Committee Person":
                    i += 1
                blocks.append((start, i))
            else:
                i += 1

        known_parties = {
            prs[j]["party"]
            for start, end in blocks
            for j in range(start, end)
            if prs[j]["party"] not in {"", "?"}
        }

        for start, end in blocks:
            block_known = {
                prs[j]["party"]
                for j in range(start, end)
                if prs[j]["party"] not in {"", "?"}
            }
            if block_known:
                continue
            missing = ({"D", "R"} - known_parties).pop() if known_parties else "D"
            for j in range(start, end):
                prs[j]["party"] = missing
            known_parties.add(missing)

        out.extend(prs)
    return out


def _find_seq(md: str) -> Optional[str]:
    text = plain_text(md)
    m = re.search(r"Seq:(\d+)", text)
    if m:
        return m.group(1)
    return None


def parse_wallowa(pdf_path: str) -> Tuple[List[Dict[str, str]], Dict]:
    county_name = "Wallowa"
    all_rows: List[Dict[str, str]] = []
    missing_precincts: Dict = {}

    # Wallowa's precinct reports are grouped by ballot-style "Seq:XXXXX" cover
    # pages. A change in sequence number starts a new precinct block.
    groups: List[Tuple[Optional[str], List[str]]] = []
    current_seq: Optional[str] = None
    current_name: Optional[str] = None
    current_pages: List[Tuple[int, str]] = []

    for page_num, md in extract_pages(pdf_path):
        seq = _find_seq(md)
        header_precinct = _find_header_precinct(md)
        pc_precinct = _find_pc_person_precinct(md)
        page_name = None
        if header_precinct:
            page_name = _normalise_precinct(header_precinct)
        elif pc_precinct:
            page_name = _normalise_precinct(pc_precinct)

        new_group = False
        if seq and seq != current_seq:
            current_seq = seq
            new_group = True
        if page_name and current_name and page_name != current_name:
            new_group = True

        if new_group:
            if current_pages:
                groups.append((current_name, [md for _, md in current_pages]))
            current_pages = [(page_num, md)]
            current_name = page_name
        else:
            current_pages.append((page_num, md))
            if not current_name and page_name:
                current_name = page_name

    if current_pages:
        groups.append((current_name, [md for _, md in current_pages]))

    # Process each precinct group. Carry contest state across pages within the
    # group so multi-page contests keep the correct office/district/party.
    prev_state: Optional[Tuple[str, str, str]] = None
    for name, mds in groups:
        lines: List[str] = []
        for md in mds:
            lines.extend(_extract_lines(md))
        precinct = name if name else "UNKNOWN"
        rows, prev_state = _process_lines(lines, county_name, precinct, start_state=prev_state)
        all_rows.extend(rows)

    all_rows = _assign_pcp_parties(all_rows)
    return all_rows, missing_precincts


# ---------------------------------------------------------------------------
# Sherman
# ---------------------------------------------------------------------------


def parse_sherman(pdf_path: str) -> Tuple[List[Dict[str, str]], Dict]:
    county_name = "Sherman"
    all_rows: List[Dict[str, str]] = []
    missing_precincts: Dict = {}

    current_precinct: Optional[str] = None
    prev_ballots: Optional[int] = None

    for page_num, md in extract_pages(pdf_path):
        # Sherman names mostly come from PC person contests or a few standalone
        # headers.  A header page has party/ballot-style statistics.
        pc_precinct = _find_pc_person_precinct(md)
        header_precinct = _find_header_precinct(md)
        ballots = _find_ballots_cast(md)

        # Pages with ballot-style statistics usually mark a new precinct.
        # Look for "Seq:" rows or Party/Democrat lines in tables.
        has_stats = bool(re.search(r"Seq:\d+", plain_text(md)))

        if pc_precinct:
            current_precinct = pc_precinct
        elif header_precinct:
            current_precinct = header_precinct
        elif has_stats and ballots != prev_ballots:
            # First page of a new precinct when no name is visible.
            # The first such block is the un-named "Biggs" precinct.
            if current_precinct is None:
                current_precinct = "Biggs"

        prev_ballots = ballots

        if not current_precinct:
            continue

        page_rows = _extract_page_rows(md, county_name, current_precinct)
        all_rows.extend(page_rows)

    return all_rows, missing_precincts


# ---------------------------------------------------------------------------
# Grant
# ---------------------------------------------------------------------------


def _grant_header_name(md: str) -> Optional[str]:
    """Grant header pages put the precinct name as a plain-text line."""
    text = plain_text(md)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # Common known Grant precincts.
    known = {
        "John Day Valley", "Union", "Auwii Catoo", "South Fork",
        "Laurie Cates", "Laura Cates", "Monument", "Long Creek",
        "Mt. Vernon", "Mt Vernon", "Prairie City", "Seneca",
        "Canyon City", "Granite", "Bates", "Dayville", "Fox",
    }
    for line in lines[:20]:
        for k in known:
            if k.lower() in line.lower():
                return k
        # Generic: first line that is a plausible place name after the first few lines.
    return None


def parse_grant(pdf_path: str) -> Tuple[List[Dict[str, str]], Dict]:
    county_name = "Grant"
    all_rows: List[Dict[str, str]] = []
    missing_precincts: Dict = {}

    current_precinct: Optional[str] = None

    for page_num, md in extract_pages(pdf_path):
        header_precinct = _grant_header_name(md)
        pc_precinct = _find_pc_person_precinct(md)

        if header_precinct:
            current_precinct = header_precinct
        elif pc_precinct and (not current_precinct or current_precinct != pc_precinct):
            current_precinct = pc_precinct

        if not current_precinct:
            continue

        page_rows = _extract_page_rows(md, county_name, current_precinct)
        all_rows.extend(page_rows)

    return all_rows, missing_precincts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("county", choices=["Sherman", "Wallowa", "Grant"])
    ap.add_argument("pdf", help="path to precinct PDF")
    args = ap.parse_args()

    parsers = {
        "Sherman": parse_sherman,
        "Wallowa": parse_wallowa,
        "Grant": parse_grant,
    }
    rows, missing_precincts = parsers[args.county](args.pdf)

    county_csv = "2026/20260519__or__primary__county.csv"
    rows = align_candidates_to_county(rows, county_csv, args.county, tolerance=5)
    rows = recover_missing_precincts(rows, county_csv, args.county, missing_precincts)

    out = output_path(args.county)
    write_csv(rows, out)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
