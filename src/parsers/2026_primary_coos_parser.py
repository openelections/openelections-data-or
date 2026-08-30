"""Parser for Coos County 2026 primary "Abstract of Votes" PDF.

Coos.pdf is an image-only PDF (no text layer), so the source of record is the
PaddleOCR markdown cache at .paddleocr_cache/Coos/p*.md (this parser does not
re-run OCR).  The layout is the ES&S abstract, like Douglas: one section per
precinct ("Precinct NN" label), contests listed vertically inside each
section, candidates as rows.  Differences from the Douglas text PDF:

- Contests split across page boundaries: the Total/Overvotes/Undervotes rows
  of a contest can land on the next page after its candidate rows, so parsing
  runs over one flat line stream for the whole report, not per page.
- The countywide summary section (pages 75-88) has *no* "All Precincts"
  precinct label to stop on; instead parsing stops at the first per-contest
  statistics line reporting >= 10,000 ballots (the largest precinct reports
  ~3,600 registered voters; the summary reports 22,016 ballots).
- Measure headers: "M120 Transportation Tax" -> "Measure 120" and
  "6 -228 City of North Bend ..." -> "Measure 6-228" (OCR spaces inside the
  measure number are tolerated).
- PCP headers read "Precinct Committeepersons - Democrat - 01 - NAME (DEM)
  (Vote for 8)"; the office is "Precinct Committee Person" and the precinct
  number comes from the header itself.  PCP contests list several separate
  "Write-in" rows, which are summed into a single "Write-ins" row.
- OCR variants of "Overvotes" ("Overtotes", "Oversvotes") are normalized.

Usage:
    uv run python src/parsers/2026_primary_coos_parser.py \
        [path/to/Coos.pdf]
"""

import re
import sys
import html
import difflib
from collections import defaultdict
from pathlib import Path

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

COUNTY = "Coos"
CACHE = Path(".paddleocr_cache") / COUNTY

VOTE_FOR_RE = re.compile(r"\(Vote for \d+\)", re.IGNORECASE)
PARTY_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
PCP_RE = re.compile(r"Precinct\s+Committee\s*Person", re.IGNORECASE)
PCP_PRECINCT_RE = re.compile(
    r"Precinct\s+Committee\s*Persons?\s+-\s+(?:Democrat|Republican)\s+-\s+(\d+)",
    re.IGNORECASE,
)
# "M120 Transportation Tax" or "6 -228 City of North Bend ...".
MEASURE_RE = re.compile(
    r"^(?:M(?P<m>\d{1,3})|(?P<n>\d{1,2})\s*-\s*(?P<n2>\d{1,3}))\s+\S",
    re.IGNORECASE,
)
PRECINCT_RE = re.compile(r"^Precinct\s+(\d+)$", re.IGNORECASE)
STATS_RE = re.compile(r"^(\d+)\s+ballots", re.IGNORECASE)
# A candidate row that OCR merged onto the end of the contest statistics
# line: "..., 575 registered voters, turnout 60.70% Jeff Merkley 288 87.80%"
# (the trailing percent can be truncated: "... Danielle Bethell 273 2.8").
STATS_TAIL_RE = re.compile(
    r"turnout\s+[\d.]+%\s+(?P<name>.+?)\s+(?P<votes>\d+)\s+[\d.]*%?\s*$"
)
# A candidate row whose name, votes and percent merged into one line
# ("Troy Cribbins 198 27.89%"), or name+votes with the percent on the
# next line.
MERGED_ROW_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z .()&'\-]*?)\s+(?P<votes>\d+)\s+[\d.]+%$"
)
MERGED_ROW_NOPCT_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z .()&'\-]*?)\s+(?P<votes>\d+)$"
)
PCT_LINE_RE = re.compile(r"^[\d.]+%$")
INT_RE = re.compile(r"\d+")

# Countywide summary sections report ~22,000 ballots; no precinct reports
# more than ~3,600 registered voters.
SUMMARY_BALLOT_THRESHOLD = 10_000

# Page-frame lines to ignore (the OCR often pre-joins header table cells, so
# these match on prefixes rather than exact text).
def is_junk_line(text: str) -> bool:
    lowered = text.lower()
    if text.startswith("Certified - Final Official Totals"):
        return True
    if text == "May 19, 2026 Primary":
        return True
    if text.startswith("Page:"):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text):
        return True
    if re.fullmatch(r"\d{2}:\d{2}:\d{2}", text):
        return True
    if text.startswith("All Precincts,"):
        return True
    if text.startswith("Total Ballots Cast:"):
        return True
    if "precincts reported out of" in lowered:
        return True
    if text in ("Choice", "Votes", "Vote %"):
        return True
    return False


def extract_lines(cache: Path):
    """Yield the report as a flat stream of text lines.

    HTML tags become line separators (every <td> cell is its own line), HTML
    entities are decoded, and markdown heading markers are stripped.  Cell
    text that OCR pre-joined ("Page: 75 of 88 2026-06-10") stays joined.
    """
    for path in sorted(cache.glob("p*.md")):
        text = path.read_text()
        text = re.sub(r"<[^>]+>", "\n", text)
        text = html.unescape(text)
        for raw in text.split("\n"):
            line = re.sub(r"^#{1,6}\s*", "", raw.strip())
            if line:
                yield line


def parse_contest_header(text: str):
    m = VOTE_FOR_RE.search(text)
    office_part = text[: m.start()].strip() if m else text.strip()

    party = ""
    pm = PARTY_RE.search(office_part)
    if pm:
        party = normalize_party(pm.group(1))
        office_part = (office_part[: pm.start()] + office_part[pm.end():]).strip()

    district = parse_district(office_part)
    office = normalize_office(office_part)

    if office is None:
        mm = MEASURE_RE.match(office_part)
        if mm:
            num = mm.group("m") or f"{mm.group('n')}-{mm.group('n2')}"
            office = f"Measure {num}"
            district = ""

    if office is None:
        # Unmapped county office: keep the name, but move the position or
        # district fragment ("Coos County Commissioner, Position 2") into
        # the district column.
        office = office_part
        if district:
            idx = office_part.find(district)
            if idx > 0:
                office = office_part[:idx].rstrip(" ,")
        # Canonical county-office names drop the county prefix ("Coos
        # County Clerk" -> "County Clerk"); the county column already
        # disambiguates.
        cm = re.match(
            r"^[A-Za-z .']+ County "
            r"(Commissioner|Clerk|Surveyor|Assessor|Treasurer|Legal Counsel)$",
            office,
        )
        if cm:
            office = f"County {cm.group(1).title()}"

    return office, district, party


def label_candidate(name: str) -> str:
    n = name.strip()
    lowered = n.lower()
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    # OCR mangles "Overvotes"/"Undervotes" freely ("Overtotes", "Oversvotes",
    # "Overtoves", "Overtvotes", "Oversotes", "Overvoles", "Undervoles",
    # "Undervotates"); any single mangled word starting over-/under- is one.
    if re.fullmatch(r"over[a-z]{2,9}s", lowered) or lowered == "over votes":
        return "Over Votes"
    if re.fullmatch(r"under[a-z]{2,9}s", lowered) or lowered == "under votes":
        return "Under Votes"
    if lowered in ("yes", "no"):
        return n.title()
    return format_candidate_name(n.split())


def parse_cache(cache: Path, contest_index=None, candidate_sets=None, name_fixes=None):
    rows = defaultdict(int)
    precinct: str | None = None
    office: str | None = None
    district = ""
    party = ""
    rows_list = []  # [(candidate, votes, pct)] contest rows, in report order
    orphans = []  # [(votes, pct)] rows whose candidate name OCR lost
    pending = []  # (name, votes) buffered rows of a contest with no header
    vote_for_n = 1  # "Vote for N" of the current contest
    stats_bo = None  # (ballots, overvotes, undervotes) from the stats line
    last_closed = None  # (office, district, party) of the contest just closed
    corrections = []
    mismatches = []
    infos = []
    contests_checked = []
    if contest_index is None:
        contest_index = {}  # ordered candidate tuple -> (office, district, party)
    if candidate_sets is None:
        candidate_sets = defaultdict(set)  # (office, district, party) -> names
    if name_fixes is None:
        name_fixes = {}  # OCR-mangled candidate name -> canonical name

    lines = list(extract_lines(cache))
    i = 0

    def reset_contest():
        nonlocal rows_list, orphans, pending, vote_for_n, stats_bo
        rows_list = []
        orphans = []
        pending = []
        vote_for_n = 1
        stats_bo = None

    def _emit(candidate, votes):
        key = (precinct, office, district, party, candidate)
        rows[key] += votes

    def _pct_offenders(denom):
        """Rows whose printed percent disagrees with votes/denom."""
        if not denom:
            return None
        return [
            (c, v, p)
            for c, v, p in rows_list
            if p is not None and abs(p - 100.0 * v / denom) > 0.006
        ]

    def close_contest(total_votes):
        """Validate the contest against its Total row and repair OCR damage.

        Three independent numbers must agree: the candidate rows, the Total
        row, and the statistics line (vote-for N x ballots - overvotes -
        undervotes).  When they disagree, the per-row percents say which is
        wrong, and pin down the exact misread row: a row whose votes disagree
        with its own printed percent is corrected to round(pct * authority).
        A deficit no row accounts for is a row OCR lost entirely; when the
        percent deficits of exactly one candidate add up to the missing
        share, the deficit belongs to that candidate.
        """
        nonlocal contests_checked, office, district, party, last_closed
        if office is None:
            # A Total row with no header and no buffered rows: the contest's
            # rows were lost entirely (resolve_pending handles the buffered
            # case).  Nothing to validate or emit.
            if total_votes:
                mismatches.append(
                    f"{precinct} <no header>: Total {total_votes} with no rows"
                )
            reset_contest()
            return
        last_closed = (office, district, party)
        # Orphan rows (name lost, e.g. "719 48.32%" at a page break): if
        # exactly one expected candidate has not appeared, the orphan is it.
        while orphans:
            missing = (
                candidate_sets.get((office, district, party), set())
                - {c for c, _, _ in rows_list}
                - {"Over Votes", "Under Votes"}
            )
            if len(missing) != 1:
                mismatches.append(
                    f"{precinct} {office} {party}: {len(orphans)} orphan row(s) "
                    f"unresolved (missing candidates: {sorted(missing)})"
                )
                break
            v, p = orphans.pop(0)
            rows_list.append((list(missing)[0], v, p))
        contests_checked.append(1)

        s = sum(v for _, v, _ in rows_list)
        expected = None
        if stats_bo is not None:
            b, o, u = stats_bo
            cand = vote_for_n * b - o - u
            if cand > 0 and abs(cand - total_votes) <= max(10, total_votes // 50):
                expected = cand
        # The authority is whichever of the stats-derived and printed totals
        # the row percents best support.  A misread row contradicts both, so
        # this counts offending rows instead of requiring a perfect match;
        # an authority with more than a few offenders is not trusted.
        off_e = _pct_offenders(expected)
        off_t = _pct_offenders(total_votes)
        if off_e is not None and len(off_e) <= len(off_t or []) and len(off_e) <= 3:
            authority = expected
        elif off_t is not None and len(off_t) <= 3:
            authority = total_votes
        else:
            authority = None

        if (
            authority is not None
            and s != authority
            and any(p is not None for _, _, p in rows_list)
        ):
            # Correct rows whose votes contradict their own printed percent.
            for j, (c, v, p) in enumerate(rows_list):
                if p is None:
                    continue
                derived = round(p * authority / 100.0)
                if derived != v and abs(derived - v) >= 2:
                    corrections.append(
                        f"{precinct} {office} {party} {c}: {v} -> {derived} (pct {p}%)"
                    )
                    rows_list[j] = (c, derived, p)
            s2 = sum(v for _, v, _ in rows_list)
            if s2 != authority:
                # The remaining deficit is a row OCR lost entirely (its
                # percent is missing from the pct column) or a row read low
                # (its printed percent exceeds its votes).  Either way the
                # candidate whose pct column diverges from its votes carries
                # the deficit, signed.
                deficit = authority - s2
                by_cand = defaultdict(lambda: [0.0, 0.0, 0])  # pct_sum, votes, rows
                for c, v, p in rows_list:
                    by_cand[c][1] += v
                    if p is not None:
                        by_cand[c][0] += p
                    by_cand[c][2] += 1
                short = []
                for c, (psum, v, cnt) in by_cand.items():
                    if c in ("Over Votes", "Under Votes"):
                        continue
                    gap = 100.0 * v / authority - psum
                    # percent rounding alone is bounded by 0.005 per row
                    if abs(gap) > 0.005 * cnt + 0.002:
                        short.append((c, gap))
                share = 100.0 * deficit / authority
                fits = [c for c, gap in short if abs(gap - share) <= 0.05]
                if len(fits) == 1:
                    corrections.append(
                        f"{precinct} {office} {party} {fits[0]}: {deficit:+d} "
                        f"(pct column off by {share:+.2f}%)"
                    )
                    rows_list.append((fits[0], deficit, None))
                    s2 += deficit
                else:
                    # PCP write-in rows carry 1-3 vote values whose percents
                    # print at 0.05% granularity; a one-vote error is below
                    # pct resolution, so the deficit is unattributable.
                    infos.append(
                        f"{precinct} {office} {party}: {deficit} votes unresolvable "
                        f"(share {share:+.2f}%, pct gaps: {short})"
                    )
            s = s2
        if s != total_votes:
            if authority == s:
                infos.append(
                    f"{precinct} {office} {party}: rows sum={s}; Total row "
                    f"misread as {total_votes} (not emitted)"
                )
            else:
                mismatches.append(
                    f"{precinct} {office} {party}: sum={s} total={total_votes}"
                )

        for c, v, _ in rows_list:
            _emit(c, v)
        # Register the contest so later precincts can resolve missing
        # headers and orphan rows.
        names = []
        for c, _, _ in rows_list:
            if c not in ("Over Votes", "Under Votes") and c not in names:
                names.append(c)
        okey = (office, district, party)
        if office is not None and names and tuple(names) not in contest_index:
            contest_index[tuple(names)] = okey
        if office is not None:
            candidate_sets[okey] |= set(names)
        reset_contest()
        # The contest is closed; clear the header so that any rows arriving
        # before the next header are buffered as a headerless contest
        # (resolve_pending) instead of silently joining this one.  In several
        # precincts OCR dropped a judicial header entirely (p003: the Court
        # of Appeals Position 12 section), and the candidate rows then
        # validated against their own Total under the *previous* contest's
        # name -- invisible to the sum check.
        office = None
        district = ""
        party = ""

    def record(name, votes, pct=""):
        """Record one candidate/Total/orphan row; used by every row variant."""
        if name is None:  # orphan votes row ("719 48.32%")
            try:
                p = float(pct.rstrip("%")) if pct else None
            except ValueError:
                p = None
            orphans.append((votes, p))
            return
        if name.strip().lower() == "total":
            close_contest(votes)
            return
        candidate = label_candidate(name)
        candidate = name_fixes.get(candidate, candidate)
        if candidate in ("Over Votes", "Under Votes"):
            if office is None:
                # The Over/Undervotes lines of the contest that just closed
                # (its header has been cleared) belong to last_closed.
                if last_closed:
                    rows[
                        (precinct, last_closed[0], last_closed[1], last_closed[2], candidate)
                    ] += votes
            else:
                _emit(candidate, votes)
            return
        if office is None:
            # OCR dropped this contest's header entirely (p17: the US
            # Senator (DEM) section of Precinct 05; p003: Court of Appeals
            # Position 12).  Buffer the rows; the Total row closes the
            # contest and the candidate list resolves the office from
            # identical contests in other precincts.
            pending.append((name, votes))
            return
        try:
            p = float(pct.rstrip("%")) if pct else None
        except ValueError:
            p = None
        rows_list.append((candidate, votes, p))

    def resolve_pending(total_votes):
        """Close a buffered headerless contest once its Total row arrives."""
        nonlocal office, district, party
        if not pending:
            close_contest(total_votes)
            return
        names = []
        for n, _ in pending:
            c = label_candidate(n)
            c = name_fixes.get(c, c)
            if c not in ("Over Votes", "Under Votes") and c not in names:
                names.append(c)
        found = contest_index.get(tuple(names))
        if found is None:
            # Fallback: the unique known contest whose candidate set
            # contains every buffered name.
            cands = set(names)
            subset_matches = [
                k for k, s in candidate_sets.items() if cands and cands <= s
            ]
            if len(subset_matches) == 1:
                found = subset_matches[0]
        if found is None:
            mismatches.append(
                f"{precinct} <missing header> {party}: unresolved contest, "
                f"candidates={names}, total={total_votes}"
            )
            reset_contest()
            return
        office, district, party = found
        for n, v in pending:
            record(n, v, "")
        close_contest(total_votes)

    while i < len(lines):
        text = lines[i]

        if is_junk_line(text):
            i += 1
            continue

        # Countywide summary section starts here; everything after it is
        # aggregates, not per-precinct data.
        if text == "All Precincts":
            break

        # Precinct labels like "Precinct 01".
        pm = PRECINCT_RE.match(text)
        if pm:
            precinct = f"Precinct {int(pm.group(1))}"
            office = None
            district = ""
            party = ""
            reset_contest()
            i += 1
            continue

        if not precinct:
            i += 1
            continue

        # Contest header.
        if VOTE_FOR_RE.search(text):
            if pending:
                mismatches.append(
                    f"{precinct}: headerless contest never closed: {pending[:3]}"
                )
                reset_contest()
            nfm = re.search(r"\(Vote for (\d+)\)", text, re.IGNORECASE)
            vote_for_n = int(nfm.group(1)) if nfm else 1
            new_office, district, party = parse_contest_header(text)
            office = new_office
            if PCP_RE.match(text):
                # PCP sections do not repeat the "Precinct NN" label; the
                # header itself carries the precinct number.
                office = "Precinct Committee Person"
                district = ""
                pcp_pm = PCP_PRECINCT_RE.search(text)
                if pcp_pm:
                    precinct = f"Precinct {int(pcp_pm.group(1))}"
            reset_contest()
            # reset_contest cleared the per-contest state; restore the
            # values parsed from this header.
            vote_for_n = int(nfm.group(1)) if nfm else 1
            i += 1
            continue

        # Per-contest statistics line ("NNN ballots (O over voted ballots,
        # O overvotes, U undervotes), M registered voters, turnout N%").
        # OCR sometimes merges the first candidate row onto its end; a
        # ballot count this large is the countywide summary section
        # (backstop for a page whose label was lost).
        sm = STATS_RE.match(text)
        if sm:
            if int(sm.group(1)) >= SUMMARY_BALLOT_THRESHOLD:
                break
            bm = re.match(
                r"^(\d+)\s+ballots\s*\(\s*(\d+)\s+over\s+\S+\s+ballots"
                r",\s*(\d+)\s+over\w+\s*,\s*(\d+)\s+under\w+",
                text,
                re.IGNORECASE,
            )
            if bm:
                stats_bo = (int(bm.group(1)), int(bm.group(3)), int(bm.group(4)))
            tm = STATS_TAIL_RE.search(text)
            if tm:
                record(tm.group("name"), int(tm.group("votes")), "")
            i += 1
            continue

        # A rowspan cell merged two candidate names into one line with a
        # literal "\n" ("Cynthia L. Beaman\nWrite-in"); their vote values
        # follow as alternating (votes, pct) pairs.
        if "\\n" in text:
            names = [x.strip() for x in text.split("\\n") if x.strip()]
            vals = []
            pcts = []
            j = i + 1
            while j < len(lines) and len(vals) < len(names):
                if INT_RE.fullmatch(lines[j]):
                    vals.append(int(lines[j]))
                elif PCT_LINE_RE.fullmatch(lines[j]):
                    pcts.append(lines[j])
                else:
                    break
                j += 1
            if len(vals) == len(names):
                for k, nm in enumerate(names):
                    record(nm, vals[k], pcts[k] if k < len(pcts) else "")
                i = j
                continue
            # Not this pattern; fall through to the other checks.

        # Candidate / Total / Overvotes / Undervotes row: a name line
        # followed by a pure-integer votes line, then usually a percent
        # line.
        if i + 1 < len(lines) and INT_RE.fullmatch(lines[i + 1]):
            pct = lines[i + 2] if i + 2 < len(lines) and PCT_LINE_RE.fullmatch(lines[i + 2]) else ""
            if text.strip().lower() == "total" and office is None:
                resolve_pending(int(lines[i + 1]))
            else:
                record(text, int(lines[i + 1]), pct)
            i += 3 if pct else 2
            continue

        # Orphan votes row ("719 48.32%"): the candidate name was lost.
        om = re.match(r"^(\d+)\s+([\d.]+)%$", text)
        if om:
            record(None, int(om.group(1)), om.group(2))
            i += 1
            continue

        # Merged row variants: "Troy Cribbins 198 27.89%" in one line, or
        # "Troy Cribbins 198" with the percent on the next line.
        mp = MERGED_ROW_RE.match(text)
        if mp:
            record(mp.group("name"), int(mp.group("votes")), "")
            i += 1
            continue
        mp = MERGED_ROW_NOPCT_RE.match(text)
        if mp and i + 1 < len(lines) and PCT_LINE_RE.fullmatch(lines[i + 1]):
            record(mp.group("name"), int(mp.group("votes")), lines[i + 1])
            i += 2
            continue

        i += 1

    return rows, {
        "contest_index": contest_index,
        "candidate_sets": candidate_sets,
        "corrections": corrections,
        "mismatches": mismatches,
        "infos": infos,
        "checked": len(contests_checked),
    }


def build_name_fixes(rows):
    """Map OCR-mangled candidate names to their canonical form.

    Within one (office, district, party) group, a candidate whose rows
    appear in at most two precincts while a near-identical name covers at
    least three times as many precincts is a misread of that name
    ("Ryan TO'Connor", "Doug Tockey", "Court Bolce").
    """
    groups = defaultdict(lambda: defaultdict(set))
    for (prec, o, d, p, cand) in rows:
        groups[(o, d, p)][cand].add(prec)
    fixes = {}
    for key, cands in groups.items():
        counts = {c: len(ps) for c, ps in cands.items()}
        if max(counts.values(), default=0) < 5:
            # Small groups (PCP: every candidate is precinct-local) have no
            # reliable frequency signal.
            continue
        for cand, n in counts.items():
            if n > 2:
                continue
            best = None
            for other, m in counts.items():
                if other == cand or m < 3 * n or m < 5:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, cand.lower(), other.lower()
                ).ratio()
                if ratio >= 0.85 and (best is None or ratio > best[1]):
                    best = (other, ratio)
            if best:
                fixes[cand] = best[0]
    return fixes


def main():
    if len(sys.argv) > 1:
        # The PDF path is accepted for symmetry with the other parsers; the
        # OCR cache is the actual input.
        if not CACHE.exists():
            print(f"PaddleOCR cache not found: {CACHE}", file=sys.stderr)
            sys.exit(1)

    # Pass 1 indexes every contest (candidate lists per office) but cannot
    # resolve headerless contests on their first occurrence -- a contest
    # whose header OCR dropped in Precinct 1 can only be identified once
    # Precinct 2's copy has been parsed.  Pass 2 re-parses with the
    # completed index and name fixes, and is the run whose rows are kept.
    pass1_rows, pass1 = parse_cache(CACHE)
    fixes = build_name_fixes(pass1_rows)
    index = {
        tuple(fixes.get(n, n) for n in names): okey
        for names, okey in pass1["contest_index"].items()
    }
    sets = defaultdict(set)
    for okey, names in pass1["candidate_sets"].items():
        sets[okey] = {fixes.get(n, n) for n in names}
    rows, report = parse_cache(
        CACHE, contest_index=index, candidate_sets=sets, name_fixes=fixes
    )

    for cand, canonical in sorted(fixes.items()):
        print(f"CORRECTION: candidate name {cand!r} -> {canonical!r}", file=sys.stderr)
    for m in report["corrections"]:
        print(f"CORRECTION: {m}", file=sys.stderr)
    if report["mismatches"]:
        for m in report["mismatches"][:20]:
            print(f"SUM MISMATCH: {m}", file=sys.stderr)
    if report["infos"]:
        for m in report["infos"][:20]:
            print(f"NOTE: {m}", file=sys.stderr)
        if len(report["infos"]) > 20:
            print(f"... {len(report['infos']) - 20} more notes", file=sys.stderr)
    print(
        f"contests checked against Total: {report['checked']}; "
        f"mismatches: {len(report['mismatches'])}; "
        f"corrections: {len(report['corrections']) + len(fixes)}; "
        f"notes: {len(report['infos'])}",
        file=sys.stderr,
    )
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
    path = Path("2026/counties") / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()