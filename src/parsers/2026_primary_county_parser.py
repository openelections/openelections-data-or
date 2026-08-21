#!/usr/bin/env python3
"""Convert the 2026 Oregon May Primary county-level results PDF to OpenElections CSV."""

import csv
import os
import re
from collections import namedtuple
from statistics import median

import pdfplumber

PDF_PATH = os.path.expanduser(
    "~/code/openelections-sources-or/2026/primary/2026 May Primary Election Official Results.PDF"
)
OUTPUT_PATH = "2026/20260519__or__primary__county.csv"

# OpenElections canonical office names
OFFICE_MAP = {
    "US Senator": "U.S. Senate",
    "US Representative": "U.S. House",
    "Governor": "Governor",
    "State Senator": "State Senate",
    "State Representative": "State House",
    "Judge of the Court of Appeals": "Judge of the Court of Appeals",
    "Judge of the Circuit Court": "Judge of the Circuit Court",
    "District Attorney": "District Attorney",
}

COUNTIES = {
    "Baker", "Benton", "Clackamas", "Clatsop", "Columbia", "Coos", "Crook", "Curry",
    "Deschutes", "Douglas", "Gilliam", "Grant", "Harney", "Hood River", "Jackson",
    "Jefferson", "Josephine", "Klamath", "Lake", "Lane", "Lincoln", "Linn", "Malheur",
    "Marion", "Morrow", "Multnomah", "Polk", "Sherman", "Tillamook", "Umatilla",
    "Union", "Wallowa", "Wasco", "Washington", "Wheeler", "Yamhill", "Total",
}

# Lines that are never part of a race block
TITLE_RE = re.compile(r"^May\s+\d{1,2},\s+\d{4},\s+Primary\s+Election\s+Abstract\s+of\s+Votes")
FOOTNOTE_RE = re.compile(r"^(\*\s*Nominee|\*\*\s*Elected|WI\s*=|" r"\*\s*Indicates\s*Passage)")

RaceBlock = namedtuple("RaceBlock", ["office", "district", "party", "header_lines", "data_lines"])


def normalize_party(text):
    """Return canonical party abbreviation; blank for nonpartisan."""
    if not text:
        return ""
    text = text.replace("(cont.)", "").strip()
    if text == "Democrat":
        return "D"
    if text == "Republican":
        return "R"
    return ""


def normalize_office(text):
    for known, canonical in OFFICE_MAP.items():
        if known in text:
            return canonical
    if text.startswith("Measure"):
        return text
    raise ValueError(f"Unrecognized office: {text}")


def parse_district(text):
    """Extract district/position info from a district line."""
    if not text:
        return ""
    # Judicial races with both district and position: keep full text
    if "District" in text and "Position" in text:
        return text
    # U.S. House / State Senate / State House: just the number
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+District", text)
    if m:
        return m.group(1)
    # Court of Appeals position
    if text.startswith("Position"):
        return text
    # District Attorney "X County" — the county is in the data rows, so leave district blank
    return ""


def is_number_token(text):
    return re.fullmatch(r"[0-9,]+", text) is not None


def parse_number(text):
    return int(text.replace(",", ""))


def split_data_row(words):
    """Return (county_name, vote_words) for a data row, handling multi-word counties."""
    max_county_words = 3
    for n in range(min(len(words), max_county_words), 0, -1):
        name = " ".join(words[i][0] for i in range(n))
        if name in COUNTIES:
            return name, words[n:]
    raise ValueError(f"Could not find county in data row: {words}")


def is_data_row(words):
    if not words:
        return False
    try:
        _, vote_words = split_data_row(words)
    except ValueError:
        return False
    return all(is_number_token(w[0]) for w in vote_words)


def classify_line(words, in_race=False):
    """Return a coarse type for a line based on its words."""
    if not words:
        return "EMPTY"

    text = " ".join(w[0] for w in words)

    if TITLE_RE.match(text):
        return "TITLE"
    if FOOTNOTE_RE.match(text):
        return "FOOTNOTE"
    if text == "Democrat" or text == "Republican" or "(cont.)" in text:
        return "PARTY"
    if any(known in text for known in OFFICE_MAP) or text.startswith("Measure"):
        return "OFFICE"
    if "District" in text or "Position" in text or text.endswith(" County"):
        return "DISTRICT"
    if re.fullmatch(r"\d+(?:st|nd|rd|th)", text):
        # District number split onto its own line (e.g., "18th" followed by "District")
        return "DISTRICT"
    if words[0][0] == "County":
        return "FIRST_NAME"
    if is_data_row(words):
        return "DATA"
    # Everything else in a race context is part of the candidate header block
    return "HEADER"


def group_words_into_lines(words, y_tolerance=3.0):
    """Group pdfplumber words into horizontal lines using their y position."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    current_line = [sorted_words[0]]
    current_top = sorted_words[0]["top"]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_top) <= y_tolerance:
            current_line.append(word)
        else:
            lines.append(current_line)
            current_line = [word]
            current_top = word["top"]
    lines.append(current_line)

    # Return words as (text, x0, x1, top) sorted left-to-right within each line
    return [
        [(w["text"], w["x0"], w["x1"], w["top"]) for w in sorted(line, key=lambda x: x["x0"])]
        for line in lines
    ]


def split_into_race_blocks(lines):
    """Split page lines into race blocks."""
    blocks = []
    current_block_lines = []
    current_office = None
    current_district = None
    current_party = None
    seen_data = False

    for words in lines:
        line_type = classify_line(words)
        text = " ".join(w[0] for w in words)

        if line_type in ("TITLE", "FOOTNOTE", "EMPTY"):
            continue

        if line_type == "OFFICE":
            if current_block_lines:
                blocks.append(make_block(current_block_lines, current_office, current_district, current_party))
            current_block_lines = [words]
            current_office = text
            current_district = None
            current_party = None
            seen_data = False
            continue

        # A new race starts when a DISTRICT or PARTY line appears after data has begun,
        # or when a PARTY line appears and the current block already has a party.
        if line_type == "DISTRICT":
            if seen_data:
                blocks.append(make_block(current_block_lines, current_office, current_district, current_party))
                current_block_lines = []
                current_district = text
                current_party = None
                seen_data = False
            elif current_block_lines and classify_line(current_block_lines[-1]) == "DISTRICT":
                # Split district line (e.g., "18th" / "District") — combine them
                current_district = current_district + " " + text if current_district else text
            else:
                current_district = text
            current_block_lines.append(words)
            continue

        if line_type == "PARTY":
            if seen_data or current_party:
                blocks.append(make_block(current_block_lines, current_office, current_district, current_party))
                current_block_lines = []
                current_party = text
                seen_data = False
            else:
                current_party = text
            current_block_lines.append(words)
            continue

        if line_type == "DATA":
            seen_data = True

        current_block_lines.append(words)

    if current_block_lines:
        blocks.append(make_block(current_block_lines, current_office, current_district, current_party))

    return blocks


def make_block(lines, office_context=None, district_context=None, party_context=None):
    """Convert a raw block of lines into a RaceBlock namedtuple."""
    office = office_context
    district = district_context
    party = party_context
    header_lines = []
    data_lines = []

    i = 0
    if i < len(lines) and classify_line(lines[0]) == "OFFICE":
        office = " ".join(w[0] for w in lines[0])
        i += 1

    # District Attorney and judicial races have a district/county/position line next.
    # Some PDFs split the district number and the word "District" onto separate lines.
    while i < len(lines) and classify_line(lines[i]) == "DISTRICT":
        part = " ".join(w[0] for w in lines[i])
        district = f"{district} {part}".strip() if district else part
        i += 1

    if i < len(lines) and classify_line(lines[i]) == "PARTY":
        party = " ".join(w[0] for w in lines[i])
        i += 1

    # Measures have a description line after the measure number; treat as district text
    if office and office.startswith("Measure") and i < len(lines):
        if classify_line(lines[i]) not in ("PARTY", "FIRST_NAME", "HEADER", "DATA"):
            district = " ".join(w[0] for w in lines[i])
            i += 1

    # Remaining lines are header + data; first data row marks the boundary
    first_data_idx = None
    for j in range(i, len(lines)):
        if is_data_row(lines[j]):
            first_data_idx = j
            break

    if first_data_idx is not None:
        header_lines = lines[i:first_data_idx]
        data_lines = lines[first_data_idx:]
    else:
        header_lines = lines[i:]

    return RaceBlock(office, district, party, header_lines, data_lines)


def extract_column_centers(data_lines):
    """Compute the median x-center for each vote column from data rows."""
    col_values = []
    for words in data_lines:
        if not is_data_row(words):
            continue
        _, vote_words = split_data_row(words)
        for idx, word in enumerate(vote_words, start=1):
            center = (word[1] + word[2]) / 2
            while len(col_values) <= idx:
                col_values.append([])
            col_values[idx].append(center)

    centers = []
    for values in col_values[1:]:  # index 0 is unused (county column)
        if values:
            centers.append(median(values))
        else:
            centers.append(None)
    return centers


def assign_words_to_columns(words, centers, max_distance=60):
    """Assign each word to the nearest vote column by x-center."""
    assignments = [[] for _ in range(len(centers))]
    for word in words:
        center = (word[1] + word[2]) / 2
        # Skip words that are clearly in the county-name column
        if word[2] < centers[0] - 80:
            continue
        best_idx = None
        best_dist = float("inf")
        for idx, c in enumerate(centers):
            if c is None:
                continue
            dist = abs(center - c)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx is not None and best_dist <= max_distance:
            assignments[best_idx].append(word)
    return assignments


def build_candidates(header_lines, centers, is_measure=False):
    """Return a list of candidate names, one per vote column."""
    if not header_lines:
        return []

    if is_measure:
        # Header line looks like "County Yes *No" — candidates are Yes/No
        words = header_lines[0]
        # Drop the "County" label and any asterisks
        return [strip_name_markers(w[0]) for w in words if w[0] != "County"]

    # First header line is last names; remaining lines are first/middle names
    last_name_words = header_lines[0]
    first_name_words = []
    for line in header_lines[1:]:
        first_name_words.extend(line)

    last_assignments = assign_words_to_columns(last_name_words, centers)
    first_assignments = assign_words_to_columns(first_name_words, centers)

    candidates = []
    for col_idx in range(len(centers)):
        last_parts = sorted(last_assignments[col_idx], key=lambda w: w[1])
        first_parts = sorted(first_assignments[col_idx], key=lambda w: w[1])

        last_name = " ".join(strip_name_markers(w[0]) for w in last_parts).strip()
        first_name = " ".join(format_first_token(w[0]) for w in first_parts).strip()

        if last_name == "Misc.":
            candidates.append("Misc.")
        elif first_name:
            candidates.append(f"{first_name} {last_name}".strip())
        else:
            candidates.append(last_name)

    return candidates


def strip_name_markers(text):
    """Remove leading nominee/elected asterisks."""
    return text.lstrip("*").strip()


def format_first_token(text):
    """Format a first/middle name token; add period to single-letter initials."""
    text = text.strip()
    # Drop write-in annotation entirely
    if text == "(WI)":
        return ""
    # Strip nominee/elected markers
    text = text.lstrip("*").strip()
    if re.fullmatch(r"[A-Z]", text):
        return text + "."
    return text


def parse_data_row(words):
    county, vote_words = split_data_row(words)
    votes = [parse_number(w[0]) for w in vote_words]
    return county, votes


def race_block_to_rows(block):
    """Generate CSV rows from a single race block."""
    if not block.office or not block.data_lines:
        return []

    office = normalize_office(block.office)
    district = parse_district(block.district) if block.district else ""
    party = normalize_party(block.party)
    is_measure = office.startswith("Measure")

    centers = extract_column_centers(block.data_lines)
    if not centers:
        return []

    candidates = build_candidates(block.header_lines, centers, is_measure=is_measure)
    if len(candidates) != len(centers):
        # Fallback: label by column index if name extraction failed
        candidates = [f"Candidate {i + 1}" for i in range(len(centers))]

    rows = []
    for words in block.data_lines:
        if not is_data_row(words):
            continue
        county, votes = parse_data_row(words)
        if len(votes) != len(candidates):
            # Mismatch; skip or warn
            continue
        for candidate, votes_val in zip(candidates, votes):
            rows.append([county, office, district, party, candidate, votes_val])

    return rows


def merge_continued_races(blocks):
    """Merge race blocks whose party line contains (cont.) with the previous matching race."""
    merged = []
    for block in blocks:
        if block.party and "(cont.)" in block.party and merged:
            prev = merged[-1]
            if (
                prev.office == block.office
                and prev.district == block.district
                and normalize_party(prev.party) == normalize_party(block.party)
            ):
                merged[-1] = RaceBlock(
                    office=prev.office,
                    district=prev.district,
                    party=prev.party,
                    header_lines=prev.header_lines + block.header_lines,
                    data_lines=prev.data_lines + block.data_lines,
                )
                continue
        merged.append(block)
    return merged


def main():
    all_blocks = []
    with pdfplumber.open(PDF_PATH) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines = group_words_into_lines(words)
            blocks = split_into_race_blocks(lines)
            all_blocks.extend(blocks)

    all_blocks = merge_continued_races(all_blocks)

    rows = []
    for block in all_blocks:
        rows.extend(race_block_to_rows(block))

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["county", "office", "district", "party", "candidate", "votes"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
