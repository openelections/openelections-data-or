#!/usr/bin/env python3
"""Convert the 2026 Oregon May Primary county-level results PDF to OpenElections CSV.

Uses natural-pdf for text extraction with word-level positions.
"""

import csv
import os
import re
from statistics import median

import natural_pdf

PDF_PATH = os.path.expanduser(
    "~/code/openelections-sources-or/2026/primary/"
    "2026 May Primary Election Official Results.PDF"
)
OUTPUT_PATH = "2026/20260519__or__primary__county.csv"

COUNTIES = {
    "Baker", "Benton", "Clackamas", "Clatsop", "Columbia", "Coos", "Crook",
    "Curry", "Deschutes", "Douglas", "Gilliam", "Grant", "Harney",
    "Hood River", "Jackson", "Jefferson", "Josephine", "Klamath", "Lake",
    "Lane", "Lincoln", "Linn", "Malheur", "Marion", "Morrow", "Multnomah",
    "Polk", "Sherman", "Tillamook", "Umatilla", "Union", "Wallowa", "Wasco",
    "Washington", "Wheeler", "Yamhill",
}

OFFICE_MAP = {
    "US Senator": "U.S. Senate",
    "US Representative": "U.S. House",
    "Governor": "Governor",
    "State Senator": "State Senate",
    "State Representative": "State House",
    "Commissioner of the Bureau of Labor and Industries": "Labor Commissioner",
    "Judge of the Supreme Court": "Judge of the Supreme Court",
    "Judge of the Court of Appeals": "Judge of the Court of Appeals",
    "Judge of the Circuit Court": "Judge of the Circuit Court",
    "District Attorney": "District Attorney",
}

TITLE_RE = re.compile(
    r"^May\s+\d{1,2},\s+\d{4},\s+Primary\s+Election\s+Abstract"
)
FOOTNOTE_RE = re.compile(
    r"^(\*\s*Nominee|\*\*\s*Elected|WI\s*=|\*\s*Indicates)"
)


def ordinal(n):
    s = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
    if 11 <= n <= 13:
        s = "th"
    return f"{n}{s}"


def normalize_office(text):
    for raw, canonical in OFFICE_MAP.items():
        if text.startswith(raw) or raw in text:
            return canonical
    if text.startswith("Measure"):
        return text
    return None


def strip_markers(text):
    return text.lstrip("*").strip()


def is_number(text):
    return bool(re.fullmatch(r"[0-9,]+", text))


def parse_number(text):
    return int(text.replace(",", ""))


def group_words_by_line(words, y_tol=3.0):
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w.top, w.x0))
    lines = []
    cur = [sorted_words[0]]
    cur_top = sorted_words[0].top
    for w in sorted_words[1:]:
        if abs(w.top - cur_top) <= y_tol:
            cur.append(w)
        else:
            lines.append(sorted(cur, key=lambda w: w.x0))
            cur = [w]
            cur_top = w.top
    lines.append(sorted(cur, key=lambda w: w.x0))
    return lines


def classify_line_text(text):
    if TITLE_RE.match(text):
        return "TITLE"
    if FOOTNOTE_RE.match(text):
        return "FOOTNOTE"
    if text in ("Democrat", "Republican") or "(cont.)" in text:
        return "PARTY"
    off = normalize_office(text)
    if off:
        return "OFFICE"
    if re.match(r"^\d+(?:st|nd|rd|th)\s+District", text):
        return "DISTRICT"
    if re.match(r"^Position\s+\d+$", text):
        return "DISTRICT"
    if re.fullmatch(r"\d+(?:st|nd|rd|th)", text):
        return "ORDINAL"
    if text == "District":
        return "DISTRICT_WORD"
    if re.match(r"^\w[\w\s]*?\s+County$", text) and text != "Hood River County":
        if text.replace(" County", "") in COUNTIES or text.replace(" County", "") == "Hood River":
            return "DA_COUNTY"
    if text == "Hood River County":
        return "DA_COUNTY"
    return None


def is_data_line(line_words):
    texts = [w.text for w in line_words if w.text.strip()]
    if len(texts) < 2:
        return False
    county_part = texts[0]
    if county_part == "Hood" and len(texts) > 1 and texts[1] == "River":
        county_part = "Hood River"
        num_start = 2
    else:
        num_start = 1
    if county_part not in COUNTIES and county_part != "Total":
        return False
    return all(is_number(t) for t in texts[num_start:])


def parse_data_line(line_words):
    texts = [w.text for w in line_words if w.text.strip()]
    positions = [w for w in line_words if w.text.strip()]
    if texts[0] == "Hood" and len(texts) > 1 and texts[1] == "River":
        county = "Hood River"
        vote_words = positions[2:]
    elif texts[0] == "Total":
        county = "Total"
        vote_words = positions[1:]
    else:
        county = texts[0]
        vote_words = positions[1:]
    votes = [(parse_number(w.text), w.x1) for w in vote_words]
    return county, votes


def compute_column_centers(data_lines):
    col_x1s = {}
    for dl in data_lines:
        _, votes = parse_data_line(dl)
        for idx, (_, x1) in enumerate(votes):
            col_x1s.setdefault(idx, []).append(x1)
    centers = []
    for idx in sorted(col_x1s.keys()):
        centers.append(median(col_x1s[idx]))
    return centers


def assign_to_column(word_x1, centers, tolerance=30):
    best = None
    best_dist = float("inf")
    for idx, c in enumerate(centers):
        d = abs(word_x1 - c)
        if d < best_dist:
            best_dist = d
            best = idx
    if best is not None and best_dist <= tolerance:
        return best
    return None


def build_candidates_from_words(last_name_words, first_name_words, col_centers, is_measure=False):
    if is_measure:
        cands = []
        for w in sorted(last_name_words, key=lambda w: w.x0):
            if w.text.strip() == "County":
                continue
            cands.append(strip_markers(w.text))
        return cands

    n_cols = len(col_centers)
    last_by_col = [""] * n_cols
    first_by_col = [""] * n_cols

    for w in last_name_words:
        if not w.text.strip():
            continue
        col = assign_to_column(w.x1, col_centers)
        if col is not None:
            existing = last_by_col[col]
            if existing:
                last_by_col[col] = existing + " " + w.text
            else:
                last_by_col[col] = w.text

    for w in first_name_words:
        if not w.text.strip() or w.text.strip() == "County":
            continue
        col = assign_to_column(w.x1, col_centers)
        if col is not None:
            existing = first_by_col[col]
            if existing:
                first_by_col[col] = existing + " " + w.text
            else:
                first_by_col[col] = w.text

    candidates = []
    for i in range(n_cols):
        last = strip_markers(last_by_col[i]).strip()
        first = strip_markers(first_by_col[i]).strip()
        if first == "(WI)":
            first = ""
        first = re.sub(r"\b([A-Z])\b(?![\.\w])", r"\1.", first)
        if last == "Misc.":
            candidates.append("Write-ins")
        elif first:
            candidates.append(f"{first} {last}")
        else:
            candidates.append(last)
    return candidates


def parse_pdf(pdf_path):
    pdf = natural_pdf.PDF(pdf_path)
    all_rows = []

    current_office = None
    current_district = ""
    current_party = ""

    pending_last_words = []
    pending_first_words = []
    pending_data_lines = []
    pending_is_measure = False
    seen_county_line = False
    pending_ordinal = None

    def flush():
        nonlocal pending_last_words, pending_first_words, pending_data_lines, seen_county_line
        if not pending_data_lines:
            pending_last_words = []
            pending_first_words = []
            return

        col_centers = compute_column_centers(pending_data_lines)
        if not col_centers:
            pending_last_words = []
            pending_first_words = []
            pending_data_lines = []
            return

        candidates = build_candidates_from_words(
            pending_last_words, pending_first_words, col_centers,
            is_measure=pending_is_measure,
        )

        if len(candidates) != len(col_centers):
            candidates = [f"Candidate {i+1}" for i in range(len(col_centers))]

        for dl in pending_data_lines:
            county, votes = parse_data_line(dl)
            if county == "Total":
                continue
            if len(votes) != len(candidates):
                continue
            for cand, (v, _) in zip(candidates, votes):
                all_rows.append({
                    "county": county,
                    "office": current_office,
                    "district": current_district,
                    "party": current_party,
                    "candidate": cand,
                    "votes": v,
                })

        pending_last_words = []
        pending_first_words = []
        pending_data_lines = []
        seen_county_line = False

    for page in pdf.pages:
        words = [w for w in page.words if w.text.strip()]
        lines = group_words_by_line(words)

        for line_words in lines:
            full_text = " ".join(w.text for w in line_words).strip()
            ltype = classify_line_text(full_text)

            if ltype == "TITLE" or ltype == "FOOTNOTE":
                continue

            if ltype == "OFFICE":
                flush()
                current_office = normalize_office(full_text)
                current_district = ""
                current_party = ""
                seen_county_line = False
                pending_is_measure = bool(
                    current_office and current_office.startswith("Measure")
                )
                if pending_is_measure:
                    current_district = ""
                    current_party = ""
                continue

            if ltype == "PARTY":
                is_cont = "(cont.)" in full_text
                new_party = "D" if "Democrat" in full_text else "R"
                if pending_data_lines:
                    flush()
                elif not is_cont:
                    pending_last_words = []
                    pending_first_words = []
                    pending_data_lines = []
                current_party = new_party
                seen_county_line = False
                continue

            if ltype == "ORDINAL":
                pending_ordinal = full_text
                continue

            if ltype == "DISTRICT_WORD":
                if pending_ordinal:
                    combined = f"{pending_ordinal} District"
                    pending_ordinal = None
                    if pending_data_lines:
                        flush()
                    m = re.match(r"^(\d+)", combined)
                    if m:
                        current_district = m.group(1)
                    current_party = ""
                    seen_county_line = False
                    pending_last_words = []
                    pending_first_words = []
                    pending_data_lines = []
                continue

            pending_ordinal = None

            if ltype == "DISTRICT":
                if pending_data_lines:
                    flush()
                m = re.match(
                    r"^(\d+)(?:st|nd|rd|th)\s+District(?:,\s+Position\s+(\d+))?$",
                    full_text,
                )
                if m:
                    dist_num = int(m.group(1))
                    if m.group(2):
                        current_district = (
                            f"{ordinal(dist_num)} District, Position {m.group(2)}"
                        )
                    else:
                        current_district = str(dist_num)
                else:
                    pm = re.match(r"^Position\s+(\d+)$", full_text)
                    if pm:
                        current_district = f"Position {pm.group(1)}"
                current_party = ""
                seen_county_line = False
                pending_last_words = []
                pending_first_words = []
                pending_data_lines = []
                continue

            if ltype == "DA_COUNTY":
                if pending_data_lines:
                    flush()
                current_district = ""
                current_party = ""
                seen_county_line = False
                pending_last_words = []
                pending_first_words = []
                pending_data_lines = []
                continue

            if is_data_line(line_words):
                pending_data_lines.append(line_words)
                continue

            if current_office and pending_is_measure:
                if full_text.startswith("County "):
                    pending_last_words = list(line_words)
                elif full_text.startswith("Increases") or full_text.startswith("Amends"):
                    continue
                continue

            if full_text.startswith("County "):
                pending_first_words = [
                    w for w in line_words if w.text.strip() != "County"
                ]
                seen_county_line = True
                continue

            if current_office and not pending_data_lines:
                if seen_county_line:
                    pending_first_words.extend(line_words)
                else:
                    pending_last_words.extend(line_words)
                continue

    flush()
    return all_rows


def main():
    print(f"Loading PDF: {PDF_PATH}")
    rows = parse_pdf(PDF_PATH)
    print(f"Extracted {len(rows)} rows")

    csv_rows = []
    for r in rows:
        csv_rows.append([
            r["county"], r["office"], r["district"],
            r["party"], r["candidate"], r["votes"],
        ])

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["county", "office", "district", "party", "candidate", "votes"])
        writer.writerows(csv_rows)

    print(f"Wrote {len(csv_rows)} rows to {OUTPUT_PATH}")
    offices = sorted(set(r["office"] for r in rows))
    print(f"Offices ({len(offices)}): {offices}")
    counties = sorted(set(r["county"] for r in rows))
    print(f"Counties ({len(counties)})")


if __name__ == "__main__":
    main()
