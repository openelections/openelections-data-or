"""Parser for Washington County 2026 primary "Ballots Cast per Contest with
Precincts" PDF (ES&S, 1275 pages).

Layout is a matrix: one contest header at the top of each page, a two-line
column-header block (candidate names wrap), then one row per precinct.
Candidate value columns are right-aligned, each followed by a left-aligned
percent column; precincts paginate down the page and candidate groups across
pages (the contest header repeats on every page with a different subset of
candidate columns).  PCP contests are one precinct per contest, a single data
row per page, and several "Write-in" columns.

Because natural_pdf's word tokenizer splits words when glyph baselines
jitter (see the Douglas parser), lines are rebuilt from characters: chars are
clustered into visual lines by y-center, and tokens (with x extents) are
split on the explicit space characters.

Usage:
    uv run python src/parsers/2026_primary_washington_parser.py \
        '/path/to/Washington.pdf'
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import natural_pdf

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY = "Washington"

VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)
PARTY_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
PCP_RE = re.compile(r"^Precinct\s+Committee\s+Person", re.IGNORECASE)
PCP_PRECINCT_RE = re.compile(
    r"^Precinct\s+Committee\s+Person\s+-\s+(?:Democrat|Republican)\s+-\s+(\d+)",
    re.IGNORECASE,
)
PRECINCT_ROW_RE = re.compile(r"^Precinct\s+(\d+)$", re.IGNORECASE)


def _row_label(toks):
    """Classify a data line: 'Total', 'Precinct NNN', or None."""
    if not toks:
        return None
    if toks[0][2] == "Total":
        return "Total"
    if PRECINCT_ROW_RE.match(toks[0][2]):
        return toks[0][2]
    if (
        toks[0][2] == "Precinct"
        and len(toks) > 1
        and re.fullmatch(r"\d+", toks[1][2])
    ):
        return f"Precinct {int(toks[1][2])}"
    return None
MEASURE_RE = re.compile(r"\b(Measure\s+(\d{1,2}-\d{1,3}|\d{1,3}))\b", re.IGNORECASE)

# Column-header names that are not candidates.
NON_CANDIDATE_COLUMNS = {
    "Ballots Cast",
    "Reg. Voters",
    "Reg Voters",
    "Total Votes",
    "Over Votes",
    "Under Votes",
}

# Report frame lines (top of every page) to ignore.
JUNK_PREFIXES = (
    "Ballots Cast per Contest",
    "Washington County, May 19",
    "All Precincts,",
    "Total Ballots Cast:",
)
JUNK_EXACT = {"Official Results", "Precinct Cast Voters Votes"}

# Pages whose contest header is one of these are parsed the same way; there is
# no countywide summary section in this report (each page repeats its own
# contest header), so no stop marker is needed.


def _page_lines(page):
    """Yield visual lines as token lists: [(x0, x1, text), ...] left to right.

    Chars are clustered by y-center (3pt tolerance); tokens are split on the
    explicit space characters, with a defensive split on any x-gap > 1.5pt.
    The page frame (stamp/page numbers, x > 470) never reaches the table.
    """
    chars = [c for c in page.chars if c.x0 < 600]
    chars.sort(key=lambda c: ((c.top + c.bottom) / 2, c.x0))
    band = []
    prev_center = None
    for c in chars:
        center = (c.top + c.bottom) / 2
        if band and prev_center is not None and center - prev_center > 3.0:
            yield _band_tokens(band)
            band = []
        band.append(c)
        prev_center = center
    if band:
        yield _band_tokens(band)


def _band_tokens(band):
    """Split a char band into tokens [(x0, x1, text), ...] in x order."""
    tokens = []
    cur = []
    x0 = x1 = None
    for c in sorted(band, key=lambda c: c.x0):
        is_space = c.text == " "
        if cur and (is_space or c.x0 - x1 > 1.5):
            tokens.append((x0, x1, "".join(cur)))
            cur = []
        if is_space:
            continue
        if not cur:
            x0 = c.x0
        cur.append(c.text)
        x1 = c.x1
    if cur:
        tokens.append((x0, x1, "".join(cur)))
    return [(a, b, t) for a, b, t in tokens if t.strip()]


def _is_int_token(text: str) -> bool:
    # This report writes numbers without comma separators ("35991"), so a
    # plain digit run must be accepted too.
    return re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", text) is not None


def _is_pct_token(text: str) -> bool:
    return text.endswith("%")


# Within a candidate name ("Jeff Merkley", "Paul Damian Wells") consecutive
# words sit ~2px apart.  Between two *candidate* names the gap is >= 14px
# once the frame tokens below are claimed first, so 8px separates them.
NAME_GAP_THRESHOLD = 8.0

# Frame columns whose names are known in advance.  Every word of these names
# right-aligns exactly at the column's value edge, which distinguishes them
# from candidate names (centered over their column): a long candidate name
# can start only ~7px right of the "Total Votes" edge, so gap clustering
# alone cannot tell the two apart ("Total Tammy Carpenter" otherwise merges).
FRAME_NAMES = (
    ("Ballots", "Cast"),
    ("Reg.", "Voters"),
    ("Reg", "Voters"),
    ("Total", "Votes"),
    ("Over", "Votes"),
    ("Under", "Votes"),
)


def _claim_frame_columns(header_lines, edges):
    """Identify frame columns by their known, edge-right-aligned names.

    Returns (columns, claimed) where columns maps edge -> frame name and
    claimed is a set of (line_index, token_index) consumed by them.
    """
    columns = {}
    claimed = set()
    for edge in edges:
        near = []  # tokens whose right edge sits on this column edge
        for li, line in enumerate(header_lines):
            for ti, t in enumerate(line):
                if abs(t[1] - edge) <= 1.5 and t[0] >= 60:
                    near.append((li, ti, t[2]))
        for w1, w2 in FRAME_NAMES:
            a = next((n for n in near if n[2] == w1), None)
            b = next(
                (n for n in near if n[2] == w2 and (a is None or n[:2] != a[:2])),
                None,
            )
            if a and b:
                columns[edge] = f"{w1} {w2}"
                claimed.add((a[0], a[1]))
                claimed.add((b[0], b[1]))
                break
    return columns, claimed


def _cluster_fragments(line_tokens):
    """Group one header line's tokens into name fragments [(x0, x1, words)]."""
    frags = []
    for x0, x1, text in sorted(line_tokens):
        if x0 < 60:  # "Precinct" row-label column of the header block
            continue
        if frags and x0 - frags[-1][1] <= NAME_GAP_THRESHOLD:
            frags[-1][1] = x1
            frags[-1][2].append(text)
        else:
            frags.append([x0, x1, [text]])
    return frags


def _header_names(header_lines, edges):
    """Assemble candidate column names into (x0, x1, name) triples.

    Frame tokens are claimed first (see _claim_frame_columns); the remaining
    tokens are candidate names, which wrap onto the second header line
    ("Paul Damian" / "Wells") — a continuation fragment's center falls inside
    its first-line fragment's x-span, so later-line fragments merge into
    earlier ones by center containment.
    """
    _, claimed = _claim_frame_columns(header_lines, edges)
    frags = []  # merged fragments from earlier lines
    for li, line in enumerate(header_lines):
        new_frags = []
        for x0, x1, words in _cluster_fragments(
            [t for ti, t in enumerate(line) if (li, ti) not in claimed]
        ):
            center = (x0 + x1) / 2
            for f in frags:
                if f[0] - 2 <= center <= f[1] + 2:
                    f[0] = min(f[0], x0)
                    f[1] = max(f[1], x1)
                    f[2].extend(words)
                    break
            else:
                new_frags.append([x0, x1, words])
        frags.extend(new_frags)
    frags.sort(key=lambda f: f[0])
    return [(f[0], f[1], " ".join(f[2])) for f in frags]


def parse_contest_header(text: str):
    """Return (office, district, party, is_pcp, pcp_precinct)."""
    m = VOTE_FOR_RE.search(text)
    office_part = text[: m.start()].strip() if m else text.strip()

    party = ""
    pm = PARTY_RE.search(office_part)
    if pm:
        party = normalize_party(pm.group(1))
        office_part = (office_part[: pm.start()] + office_part[pm.end():]).strip()

    is_pcp = bool(PCP_RE.match(office_part))
    pcp_precinct = None
    if is_pcp:
        pcp_m = PCP_PRECINCT_RE.match(office_part)
        if pcp_m:
            pcp_precinct = int(pcp_m.group(1))
        if not party:
            # Long PCP headers drop the "(DEM)"/"(REP)" marker; the party
            # still appears as "- Democrat -" / "- Republican -".
            pm2 = re.search(r"-\s*(Democrat|Republican)\s*-", office_part, re.IGNORECASE)
            if pm2:
                party = normalize_party("DEM" if pm2.group(1).lower() == "democrat" else "REP")
        return "Precinct Committee Person", "", party, True, pcp_precinct

    # Measures keep just their number ("State Measure 120" -> "Measure 120",
    # "City of Lake Oswego Measure 3-635" -> "Measure 3-635").  A district-
    # style number embedded in the office name ("Tualatin Hills Park &
    # Recreation District 34-350") is a measure number too.
    mm = MEASURE_RE.search(office_part)
    if mm:
        return f"Measure {mm.group(2)}", "", party, False, None
    nm = re.search(r"\b(\d{1,2}-\d{1,3})\b", office_part)
    if nm:
        return f"Measure {nm.group(1)}", "", party, False, None

    district = parse_district(office_part)
    # Metro office names stay verbatim (normalize_office would collapse
    # "Metro Auditor" into the canonical "Auditor"); the unmapped branch
    # below splits the district out of them.
    office = (
        None
        if re.match(r"Metro\b", office_part, re.IGNORECASE)
        else normalize_office(office_part)
    )

    if office is None:
        office = office_part

        # "Metro Councilor, District 2" / "County Commissioner, District 2":
        # split a plain "District N" out of unmapped office names.
        dm = re.search(r",\s*District\s+(\d+|At-Large)\s*$", office_part, re.IGNORECASE)
        if dm:
            district = dm.group(1)
            if district.lower() == "at-large":
                district = "At-Large"
            office = office_part[: dm.start()].strip().rstrip(",")

        # "City of Beaverton, Council Member, Position 1" -> "City Council".
        cm = re.match(r"City of [A-Z][a-zA-Z .']+?, Council Member", office_part)
        if cm:
            office = "City Council"

        # Strip a position fragment from any remaining unmapped office name.
        pm2 = re.search(r"(Position\s+\d+)", office, re.IGNORECASE)
        if pm2:
            district = pm2.group(1)
            office = office[: pm2.start()].strip().rstrip(",")

    return office, district, party, False, None


def label_candidate(name: str) -> str:
    lowered = name.lower().strip()
    if lowered in ("write-in", "write in", "write-ins"):
        return "Write-ins"
    if lowered == "over votes":
        return "Over Votes"
    if lowered == "under votes":
        return "Under Votes"
    if lowered in ("yes", "no"):
        return name.title()
    return format_candidate_name(name.split())


def parse_page(page):
    """Parse one page.

    Returns (office, district, party, rows, totals) where rows maps
    (precinct, candidate) -> votes and totals maps candidate -> the page's
    Total row (None when the page has no Total row).
    """
    lines = list(_page_lines(page))
    texts = [" ".join(t[2] for t in line) for line in lines]

    # Contest header: the line carrying "(Vote for N)"; merge a wrapped
    # "(Vote for" tail with the following line.
    hdr_idx = None
    for i, text in enumerate(texts):
        if VOTE_FOR_RE.search(text):
            hdr_idx = i
            break
        if text.endswith("(Vote for") and i + 1 < len(texts):
            texts[i] = text + " " + texts[i + 1]
            lines[i] = lines[i] + lines[i + 1]
            texts[i + 1] = None
            hdr_idx = i
            break
    if hdr_idx is None:
        return None
    texts = [t for t in texts if t is not None]

    office, district, party, is_pcp, pcp_precinct = parse_contest_header(
        texts[hdr_idx]
    )

    # Header block: everything between the contest header and the first
    # data row; data rows start with a "Precinct NNN" or "Total" label token.
    data_start = None
    for i in range(hdr_idx + 1, len(lines)):
        if _row_label(lines[i]):
            data_start = i
            break
    if data_start is None:
        return None

    # Data rows.
    row_values = defaultdict(dict)  # precinct label -> {col_edge: value}
    totals = {}
    for i in range(data_start, len(lines)):
        toks = lines[i]
        label = _row_label(toks)
        if label is None:
            continue
        rest = toks[1:] if toks[0][2] == "Total" else (
            toks[2:] if len(toks) > 1 and toks[1][2].isdigit() else toks[1:]
        )
        vals = {}
        for x0, x1, text in rest:
            if _is_pct_token(text):
                continue
            if _is_int_token(text):
                vals[round(x1, 1)] = int(text.replace(",", ""))
        if label == "Total":
            totals = vals
        else:
            row_values[label] = vals

    # Column right edges from the value tokens themselves (right-aligned).
    edge_counts = defaultdict(int)
    for vals in row_values.values():
        for edge in vals:
            edge_counts[edge] += 1
    if totals:
        for edge in totals:
            edge_counts[edge] += 1
    if not edge_counts:
        return office, district, party, [], None
    edges = sorted(edge_counts)

    # Frame columns are identified by their known edge-aligned names; the
    # remaining header names are candidates, centered over their column edge.
    header_block = lines[hdr_idx + 1 : data_start]
    columns, _claimed = _claim_frame_columns(header_block, edges)
    names = {edge: [] for edge in edges}
    for x0, x1, name in _header_names(header_block, edges):
        center = (x0 + x1) / 2
        best = min(edges, key=lambda e: abs(center - e))
        names[best].append(name)
    for edge, edge_names in names.items():
        if edge not in columns and edge_names:
            columns[edge] = " ".join(edge_names)
    for edge in edges:
        columns.setdefault(edge, "")

    columns = {}
    for edge in edges:
        col_name = " ".join(names[edge]).strip()
        columns[edge] = col_name

    # Emit candidate values.
    out = []
    for label, vals in row_values.items():
        pm = PRECINCT_ROW_RE.match(label)
        precinct = f"Precinct {pm.group(1)}" if pm else label
        for edge, value in vals.items():
            col_name = columns.get(edge, "")
            if col_name in NON_CANDIDATE_COLUMNS or not col_name:
                continue
            out.append((precinct, label_candidate(col_name), value))
    return office, district, party, out, totals or None


def parse_pdf(pdf_path: str):
    rows = defaultdict(int)
    pdf = natural_pdf.PDF(pdf_path)
    n_pages = len(pdf.pages)
    for i, page in enumerate(pdf.pages):
        if (i + 1) % 200 == 0:
            print(f"  ... page {i + 1}/{n_pages}", file=sys.stderr, flush=True)
        parsed = parse_page(page)
        if parsed is None:
            continue
        office, district, party, out, totals = parsed
        for precinct, candidate, value in out:
            rows[(precinct, office, district, party, candidate)] += value

    out_rows = [
        make_row(
            county=COUNTY,
            precinct=prec,
            office=office,
            district=district,
            party=party,
            candidate=candidate,
            votes=votes,
        )
        for (prec, office, district, party, candidate), votes in rows.items()
    ]
    return out_rows


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python src/parsers/2026_primary_washington_parser.py '/path/to/Washington.pdf'")
        sys.exit(1)
    out_rows = parse_pdf(sys.argv[1])
    path = Path("2026/counties") / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()