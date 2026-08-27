#!/usr/bin/env python3
"""Build the Malheur County 2026 primary precinct CSV from the PaddleOCR cache.

The source is the Oregon state abstract ``Malheur.pdf`` (image PDF, OCR'd into
``.paddleocr_cache/Malheur/p*.md`` as HTML tables).  The abstract lays each
contest out as one page: precincts as rows, candidates as columns.  Malheur's
copy has several layout wrinkles that this parser handles explicitly:

  * The candidate header row sits above the precinct rows but its columns do
    NOT line up with the data columns (meta cells like "County"/"Malheur"/
    "Election" push the header text right of where the votes land).  So columns
    are classified by *position from the known candidate count*, not by header
    text.  Vote cells are the pure-integer cells after the precinct label.
  * Governor spans two pages (p03+p04 Dem, p13+p14 Rep): the first page holds
    the first 10 candidates, the second page the remaining candidates plus the
    Write-ins/Over/Under stat columns.  The two pages share precincts, so they
    are merged per precinct.
  * p19 and p20 each carry TWO Court of Appeals contests side by side, split
    by an empty separator column.  Each contest is one candidate + 3 stats.
  * p06 is a no-candidate-filed County Commissioner contest (write-in carousel):
    one write-in column + Under votes, no Over column.
  * PCP "Committee Person" pages (p23-p70) and write-in detail pages
    ("Name | Prec 1 | Prec 2 ...", p07-p10) are skipped.

Candidate names follow the spelling used by the other 2026 Oregon county
precinct files (e.g. "Ryan T O'Connor" with "-Incumbent" stripped, matching
Benton).  Write-ins/Over/Under are emitted only when non-zero, matching the
established convention.
"""

import csv
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

CACHE = Path(".paddleocr_cache/Malheur")
OUT = Path("2026/counties/20260519__or__primary__malheur__precinct.csv")

COUNTY = "Malheur"

INT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*$|^-?\d+$")
PRECINCT_RE = re.compile(r"^\d+\s*-\s+")
NAV_MARKERS = ("Vice President", "ABSTRACT OF VOTES", "STATE OF OREGON",
               "SER 900", "1. President", "2. President")

# ---------------------------------------------------------------------------
# Canonical candidate lists (spelling matches other 2026 OR county files).
# ---------------------------------------------------------------------------
SENATE_D = ["Jeff Merkley", "Paul Damian Wells"]
SENATE_R = ["Brent Barker", "Deborah C Brown", "David A Burch",
            "Russell McAlmond", "Jo Rae Perkins", "Timothy Skelton",
            "David Brock Smith"]
HOUSE_D = ["Chris Beck", "Mary Doyle", "Rebecca Mueller", "Patty Snow",
           "Dawn Rasmussen", "Peter Quince"]
HOUSE_R = ["Cliff Bentz", "Andrea Carr", "Peter J. Larson"]
GOV_D = ["Forest (Fora) Alexander", "James Atkinson IV", "Cal Kishawi",
         "Tina Kotek", "Donnie M Beckwith", "David W Beem",
         "Steve William Laible", "Brittany Jones", "Tristan Sheppard",
         "Miranda Weigler"]
GOV_R = ["Danielle Bethell", "Hope A Dalrymple", "Ed Diehl", "Christine Drazan",
         "Chris Dudley", "Kyle M Duyck", "David Medina", "Robert Neuman",
         "Brad T Peters", "Paul J. Romero Jr", "Wen Waddell",
         "Martin Ward", "Tim O Youker", "DeAngelo Leroy Turner"]
COA_1 = "Ryan T O'Connor"
COA_9 = "Jacqueline Kamins"
COA_12 = "Erin C Lagesen"
COA_13 = "Doug Tookey"

STATS = ("Write-ins", "Over Votes", "Under Votes")


def expand_table(html_text: str):
    """Expand an OCR'd HTML table into a rectangular grid of cell strings."""
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    grid = []
    spans = {}  # col -> (text, rows remaining) carried into the next row
    for tr in table.find_all("tr"):
        row = []
        c = 0
        for td in tr.find_all(["td", "th"]):
            while c in spans:
                text, left = spans[c]
                row.append(text)
                if left > 1:
                    spans[c] = (text, left - 1)
                else:
                    del spans[c]
                c += 1
            text = td.get_text(" ", strip=True)
            colspan = int(td.get("colspan", 1) or 1)
            rowspan = int(td.get("rowspan", 1) or 1)
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    spans[c] = (text, rowspan - 1)
                c += 1
        while c in spans:  # trailing rowspan cells
            text, left = spans[c]
            row.append(text)
            if left > 1:
                spans[c] = (text, left - 1)
            else:
                del spans[c]
            c += 1
        grid.append(row)
    if not grid:
        return []
    width = max(len(r) for r in grid)
    return [r + [""] * (width - len(r)) for r in grid]


def strip_nav_columns(grid):
    """Drop trailing navigation-sidebar columns (the abstract's right rail).

    The nav column is the rightmost one whose cells are dominated by nav marker
    text ("Vice President", "STATE OF OREGON", etc.).  Only trailing columns are
    removed so real data is never touched.
    """
    if not grid:
        return grid
    width = len(grid[0])
    nav_cols = set()
    for j in range(width):
        cells = [r[j] for r in grid if j < len(r) and r[j].strip()]
        if not cells:
            continue
        if sum(1 for c in cells if any(m in c for m in NAV_MARKERS)) >= max(1, len(cells) // 2):
            nav_cols.add(j)
    # Keep only trailing nav columns (a nav column left of data would be a bug).
    if nav_cols:
        max_nav = max(nav_cols)
        # Only strip if every column from max_nav to width-1 is nav (trailing run).
        if all(j in nav_cols for j in range(max_nav, width)):
            grid = [[c for j, c in enumerate(r) if j not in nav_cols] for r in grid]
    return grid


def page_text(page: int) -> str:
    return (CACHE / f"p{page:03d}.md").read_text()


def is_pcp(grid) -> bool:
    flat = " ".join(" ".join(r) for r in grid)
    return "Committee Person" in flat or "Committeeperson" in flat


def is_writein_detail(grid) -> bool:
    for r in grid[:6]:
        if any("Name" in c for c in r) and sum("Prec" in c for c in r) >= 3:
            return True
    return False


def _clean_precinct(name: str) -> str:
    """Normalize a precinct label: drop OCR literal backslash-n line breaks and
    collapse whitespace.  PaddleOCR writes in-cell line breaks as the two
    characters ``\\n`` (bytes 5c 6e), not a real newline."""
    name = name.replace("\\n", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def precinct_number(name: str):
    m = re.match(r"^(\d+)\s*-\s*", name)
    return int(m.group(1)) if m else None


def build_precinct_canon():
    """Scan all contest pages and return {precinct_number: canonical_name}.

    OCR reads precinct 24's name inconsistently (truncated on some pages, full
    on others).  The longest variant per precinct number is the canonical name.
    """
    canon = {}
    for page in range(1, 23):
        grid = strip_nav_columns(expand_table(page_text(page)))
        for name, _ in precinct_rows(grid):
            num = precinct_number(name)
            if num is None:
                continue
            cur = canon.get(num)
            if cur is None or len(name) > len(cur):
                canon[num] = name
    return canon


def precinct_rows(grid):
    """Yield (precinct_name, [int votes...]) for each precinct data row.

    The precinct label is col0.  Votes are every pure-integer cell from col1
    onward; duplicate precinct labels (colspan=2), "Separate sheet" text, and
    empty separator columns are non-integer and so skipped automatically.
    The "Totals" row is excluded.
    """
    for row in grid:
        if not row:
            continue
        first = _clean_precinct(row[0])
        if not PRECINCT_RE.match(first):
            continue
        votes = []
        for cell in row[1:]:
            v = cell.strip().replace(",", "")
            if v and INT_RE.match(v):
                votes.append(int(v))
        yield first, votes


def emit(rows, precinct, party, office, district, candidates, votes, stats_start):
    """Append candidate + stat rows for one precinct.

    ``votes[:stats_start]`` are the candidate columns; ``votes[stats_start:]``
    are the Write-ins/Over/Under stat columns (in that order).
    """
    for cand, v in zip(candidates, votes[:stats_start]):
        rows.append({"county": COUNTY, "precinct": precinct, "office": office,
                     "district": district, "party": party,
                     "candidate": cand, "votes": str(v)})
    stat_cols = votes[stats_start:]
    for name, v in zip(STATS, stat_cols):
        if v and v > 0:
            rows.append({"county": COUNTY, "precinct": precinct, "office": office,
                         "district": district, "party": party,
                         "candidate": name, "votes": str(v)})


def emit_measure(rows, precinct, votes):
    """Measure 120: [Yes, No, Over, Under]."""
    for cand, v in zip(["Yes", "No"], votes[:2]):
        rows.append({"county": COUNTY, "precinct": precinct, "office": "Measure 120",
                     "district": "", "party": "", "candidate": cand,
                     "votes": str(v)})
    for name, v in zip(("Over Votes", "Under Votes"), votes[2:4]):
        if v and v > 0:
            rows.append({"county": COUNTY, "precinct": precinct, "office": "Measure 120",
                         "district": "", "party": "", "candidate": name,
                         "votes": str(v)})


def emit_carousel(rows, precinct, party, office, district, votes):
    """No-candidate-filed contest: [write-ins, under_votes] (no over column)."""
    # votes = [write_in_total, under_votes]
    if len(votes) >= 1 and votes[0] > 0:
        rows.append({"county": COUNTY, "precinct": precinct, "office": office,
                     "district": district, "party": party,
                     "candidate": "Write-ins", "votes": str(votes[0])})
    if len(votes) >= 2 and votes[1] > 0:
        rows.append({"county": COUNTY, "precinct": precinct, "office": office,
                     "district": district, "party": party,
                     "candidate": "Under Votes", "votes": str(votes[1])})


def collect_page(page: int):
    """Return the list of (precinct, votes) for a page.

    Only the known contest pages (1-22) are ever passed here; PCP pages
    (p23-p70) and write-in detail pages (p07-p10) are never requested, so no
    content-based skip guard is needed (the nav sidebar on contest pages
    lists "Committeeperson" among contests, which would false-positive a
    PCP guard).
    """
    grid = strip_nav_columns(expand_table(page_text(page)))
    return list(precinct_rows(grid))


def main():
    rows = []
    precinct_canon = build_precinct_canon()

    # ---- single-contest pages -------------------------------------------
    def single(page, party, office, district, candidates):
        data = collect_page(page)
        if not data:
            print(f"WARN: p{page} {office} produced no precinct rows", file=sys.stderr)
            return
        n_cand = len(candidates)
        for precinct, votes in data:
            if len(votes) < n_cand:
                print(f"WARN p{page} {precinct}: {len(votes)} votes < {n_cand} cands",
                      file=sys.stderr)
                continue
            emit(rows, precinct, party, office, district, candidates, votes, n_cand)

    single(1, "D", "U.S. Senate", "", SENATE_D)
    single(2, "D", "U.S. House", "2", HOUSE_D)
    single(5, "D", "State House", "60", ["Beth Spell"])
    single(11, "R", "U.S. Senate", "", SENATE_R)
    single(12, "R", "U.S. House", "2", HOUSE_R)
    single(15, "R", "State House", "60", ["Mark Owens"])
    single(16, "R", "County Commissioner", "Position 1",
           ["Tom Vialpando", "Jim Mendiola"])
    single(17, "", "Labor Commissioner", "",
           ["Chris Lynch", "Christina E Stephenson"])
    single(18, "", "Judge of the Supreme Court", "Position 4",
           ["Christopher L. Garrett"])
    single(21, "", "District Attorney", "", ["David M Goldthorpe"])

    # ---- County Commissioner Pos 1 Dem (no candidate filed) -------------
    data = collect_page(6)
    if data:
        for precinct, votes in data:
            emit_carousel(rows, precinct, "D", "County Commissioner", "Position 1", votes)

    # ---- Governor: two-page merge (p03+p04 Dem, p13+p14 Rep) ------------
    def gov_pages(p_cand, p_stat, party, candidates):
        """page p_cand holds candidates[0:n1]; page p_stat holds candidates[n1:]
        followed by the 3 stat columns.  Both pages list the same precincts in
        the same order, so they are merged by position (not by name: OCR can
        truncate a precinct name on one page, e.g. p13 precinct 24)."""
        cands_data = collect_page(p_cand)
        stat_data = collect_page(p_stat)
        if not cands_data or not stat_data:
            print(f"WARN: governor pages {p_cand}/{p_stat} missing data", file=sys.stderr)
            return
        if len(cands_data) != len(stat_data):
            print(f"WARN: governor pages {p_cand}/{p_stat} precinct count mismatch "
                  f"{len(cands_data)} vs {len(stat_data)}", file=sys.stderr)
        n_cand = len(candidates)
        for (precinct, cvotes), (precinct2, svotes) in zip(cands_data, stat_data):
            n1 = len(cvotes)  # candidates on the first page
            n_cand2 = n_cand - n1  # remaining candidates on the second page
            # Prefer the longer precinct name (OCR sometimes truncates one page).
            name = max(precinct, precinct2, key=len)
            if len(svotes) < n_cand2 + 3:
                print(f"WARN gov {name}: stat page has {len(svotes)} cols, "
                      f"need {n_cand2 + 3}", file=sys.stderr)
            combined = cvotes + svotes
            emit(rows, name, party, "Governor", "", candidates, combined, n_cand)

    gov_pages(3, 4, "D", GOV_D)
    gov_pages(13, 14, "R", GOV_R)

    # ---- Court of Appeals: two contests per page (p19, p20) -------------
    def coa_page(page, cand1, dist1, cand2, dist2):
        data = collect_page(page)
        if not data:
            print(f"WARN: p{page} Court of Appeals produced no rows", file=sys.stderr)
            return
        for precinct, votes in data:
            # each contest = 1 candidate + 3 stats = 4 columns
            if len(votes) < 8:
                print(f"WARN p{page} {precinct}: {len(votes)} votes < 8", file=sys.stderr)
                continue
            emit(rows, precinct, "", "Judge of the Court of Appeals", dist1,
                 [cand1], votes[0:4], 1)
            emit(rows, precinct, "", "Judge of the Court of Appeals", dist2,
                 [cand2], votes[4:8], 1)

    coa_page(19, COA_1, "Position 1", COA_9, "Position 9")
    coa_page(20, COA_12, "Position 12", COA_13, "Position 13")

    # ---- Measure 120 (p22) ----------------------------------------------
    data = collect_page(22)
    if data:
        for precinct, votes in data:
            if len(votes) < 4:
                print(f"WARN p22 {precinct}: {len(votes)} votes < 4", file=sys.stderr)
                continue
            emit_measure(rows, precinct, votes)

    # ---- write -----------------------------------------------------------
    # Normalize precinct names to the canonical (longest) variant per number,
    # so OCR truncation on some pages does not split one precinct into two.
    for r in rows:
        num = precinct_number(r["precinct"])
        if num is not None and num in precinct_canon:
            r["precinct"] = precinct_canon[num]
    rows.sort(key=lambda r: (r["office"], r["district"], r["party"],
                             r["candidate"], r["precinct"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["county", "precinct", "office",
                                          "district", "party", "candidate",
                                          "votes"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()