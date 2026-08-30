"""Parser for Crook County 2026 primary "Abstract of Votes" PDF.

The source PDF is image-only; the PaddleOCR markdown cache
(.paddleocr_cache/Crook/p001-p073.md) renders its contest tables as HTML
tables whose values are correctly paired (verified: candidate rows sum to
the printed Total, e.g. Merkley 48 + Wells 7 + Write-in 1 = 56).

Layout: one section per precinct ("Precinct NN", 17 sections, ~3-4 pages
each), contests in Choice/Votes/Vote% tables, and a county-wide "All
Precincts" summary starting at p062 (parsing breaks there).

Contest titles appear in three OCR renderings, all of which must be
recognized:

- ``## US Senator (X) (Vote for 1)`` (markdown heading),
- ``<div style="text-align: center;">US Senator (Y) (Vote for 1)</div>``
  (centered div),
- ``<td colspan="3">Crook County Assessor (Vote for 1)</td>`` (title row
  inside a table).

Partisan contests carry a ballot-style marker instead of a party name:
``(X)`` is the Democrat ballot and ``(Y)`` the Republican ballot (verified
by candidate sets: US Senator (X) lists Merkley/Wells; (Y) lists
Perkins/Brock Smith).  PCP titles spell the party out ("Precinct
Committee Person - Democrat - PRECINCT 02 (X)").

Crook numbers its questions with a county prefix: statewide Measure 120
prints as "Question 7-120" (the county-level file confirms "Measure 120"),
while local questions keep the prefix ("Question 7-184" -> "Measure 7-184").

Usage:
    uv run python src/parsers/2026_primary_crook_parser.py
"""

import html
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    format_candidate_name,
    make_row,
    normalize_party,
    write_csv,
)

COUNTY = "Crook"
CACHE_DIR = Path(".paddleocr_cache/Crook")

VOTE_FOR_RE = re.compile(r"\(Vote\s+for\s+(\d+)\)|\(Vote\s+f[cu]r\s+(\d+)\)", re.IGNORECASE)
PRECINCT_RE = re.compile(r"^Precinct\s+(\d+)\s*$", re.IGNORECASE)
STATS_RE = re.compile(r"^\d[\d,]*\s+ballots?\b")
PCP_RE = re.compile(r"Precinct\s+Committee\s+Person", re.IGNORECASE)
PCP_PARTY_RE = re.compile(r"Precinct\s+Committee\s+Person\s*-\s*(Democrat|Republican)", re.IGNORECASE)
PCP_PRECINCT_RE = re.compile(r"PRECINCT\s+(\d+)", re.IGNORECASE)
# Plain-line candidate rows: "Jeff Merkley 48 85.71%" (percent optional).
CANDIDATE_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z][^:]*?)\s+(?P<votes>[\d,]+)(?:\s+\d+(?:\.\d+)?%)?\s*$"
)
# Fragments of wrapped titles and page furniture that must never parse as
# candidate rows even though they match the line shape ("Position 12").
LINE_STOPWORDS = {
    "position", "district", "statewide", "nonpartisan", "federal", "partisan",
    "democrat", "republican", "contest", "votes", "choice",
    "election day", "ballots", "reporting", "precinct",
}


def parse_contest_title(text):
    """Map a contest title to (office, district, party) or None."""
    t = html.unescape(re.sub(r"\s+", " ", text)).strip()
    m = VOTE_FOR_RE.search(t)
    if not m:
        return None
    office_part = t[: m.start()].strip()
    vote_for = int(m.group(1) or m.group(2))

    party = ""
    if office_part.endswith("(X)"):
        party = "D"
        office_part = office_part[:-3].strip()
    elif office_part.endswith("(Y)"):
        party = "R"
        office_part = office_part[:-3].strip()
    else:
        pm = PCP_PARTY_RE.search(office_part)
        if pm:
            party = normalize_party(pm.group(1))

    if PCP_RE.search(office_part):
        return "Precinct Committee Person", "", party, vote_for

    # "US Representative, 2nd District US Representative 2nd District"
    district = ""
    dm = re.search(r"(\d+)(?:st|nd|rd|th)?\s+District", office_part)
    if dm:
        district = dm.group(1)
    pm = re.search(r"Position\s+(\d+)", office_part)
    position = f"Position {pm.group(1)}" if pm else ""

    lowered = office_part.lower()
    if "us senator" in lowered:
        return "U.S. Senate", "", party, vote_for
    if "us representative" in lowered:
        return "U.S. House", district, party, vote_for
    if "state representative" in lowered or "state senator" in lowered:
        office = "State House" if "state representative" in lowered else "State Senate"
        return office, district, party, vote_for
    if "governor" in lowered:
        return "Governor", "", party, vote_for
    if "bureau of labor" in lowered:
        return "Labor Commissioner", "", party, vote_for
    if "judge of the supreme court" in lowered:
        return "Judge of the Supreme Court", position, party, vote_for
    if "court of appeals" in lowered:
        return "Judge of the Court of Appeals", position, party, vote_for
    if "judge of the circuit court" in lowered:
        return "Judge of the Circuit Court", f"{district}th District, {position}" if district else position, party, vote_for
    if "district attorney" in lowered:
        return "District Attorney", "", party, vote_for

    # County offices: canonical names drop the county prefix.
    cm = re.match(r"^Crook County (Assessor|Clerk|Commissioner|Surveyor|Treasurer)", office_part)
    if cm:
        office = f"County {cm.group(1)}"
        if cm.group(1) == "Commissioner":
            return office, position, party, vote_for
        return office, "", party, vote_for

    qm = re.match(r"^Question\s+([\d-]+)", office_part)
    if qm:
        num = qm.group(1)
        # The county prefixes statewide measure numbers with "7-"; the
        # county-level file carries it as the plain statewide number.
        num = re.sub(r"^7-", "", num) if re.fullmatch(r"7-(1\d\d)", num) else num
        return f"Measure {num}", "", party, vote_for

    return None, None, None, vote_for


def label_candidate(name):
    n = html.unescape(re.sub(r"\s+", " ", name)).strip()
    lowered = n.lower()
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    if lowered in ("overvotes", "over votes", "over voted"):
        return "Over Votes"
    if lowered in ("undervotes", "under votes", "under voted"):
        return "Under Votes"
    # OCR garbles the rest of the variants ("Overtvotes", "Oversotes",
    # "Overvoles", "Overtotes"); fuzzy-match the two known labels.
    import difflib

    if lowered.startswith("o") and difflib.SequenceMatcher(None, lowered, "overvotes").ratio() >= 0.75:
        return "Over Votes"
    if lowered.startswith("u") and difflib.SequenceMatcher(None, lowered, "undervotes").ratio() >= 0.75:
        return "Under Votes"
    if lowered == "total":
        return "Total"
    if lowered in ("yes", "no"):
        return n.title()
    # A declared write-in candidate prints as "Name Write-in"; the votes
    # belong to the named candidate (Vikki Breese-Iverson, State House 59).
    stripped = re.sub(r"\s+write-?in[s]?$", "", n, flags=re.IGNORECASE)
    if stripped != n and stripped.strip():
        n = stripped.strip()
    # Strip OCR artifacts from the markdown cache's bold/italic markup
    # ("Turne $ ^{{*}} $" for "Turner") before canonical formatting.
    n = re.sub(r"[$^{}*]+", " ", n)
    return format_candidate_name(re.sub(r"\s+", " ", n).split())


def build_aliases(rows_out, threshold=0.85):
    """Cluster near-duplicate candidate names across pass-1 rows.

    OCR misreads of the same candidate ("Hope A. Dairymple" /
    "Hope A. Dalrymple") land as separate rows otherwise.  The most
    frequent spelling becomes canonical and every close variant is
    aliased to it; exact pseudocandidate labels are left alone.
    """
    import difflib

    freq = {}
    for key in rows_out:
        cand = key[-1]
        if cand in ("Over Votes", "Under Votes"):
            continue
        freq[cand] = freq.get(cand, 0) + 1
    canonical = []
    aliases = {}
    for name, _cnt in sorted(freq.items(), key=lambda kv: -kv[1]):
        match = None
        for canon in canonical:
            if (
                difflib.SequenceMatcher(None, name, canon).ratio() >= threshold
                and name != canon
            ):
                match = canon
                break
        if match is None:
            canonical.append(name)
        else:
            aliases[name] = match
    return aliases


def table_rows(table_html):
    """Yield the cell lists of one <table>...</table> block."""
    for tr in re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL):
        cells = [
            html.unescape(re.sub(r"\s+", " ", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        ]
        if cells:
            yield cells


def page_events(markdown):
    """Yield ('title'|'precinct'|'row'|'line', payload) in document order.

    The cache renders the same page as HTML tables in some places and as
    plain text lines in others, so plain lines between tables are scanned
    too — precinct labels, contest titles, and even whole candidate lists
    appear as plain lines on some pages.
    """
    pos = 0
    for m in re.finditer(r"<table.*?</table>", markdown, re.DOTALL):
        for line in markdown[pos : m.start()].splitlines():
            yield from single_cell(line)
        for cells in table_rows(m.group(0)):
            if len(cells) == 1:
                yield from single_cell(cells[0])
            else:
                yield ("row", cells)
        pos = m.end()
    for line in markdown[pos:].splitlines():
        yield from single_cell(line)


def single_cell(text):
    """Classify a standalone text line (heading, div, plain line, or cell)."""
    # Strip any html wrapper and markdown heading marks so div/heading
    # contents parse like plain lines.
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return
    if VOTE_FOR_RE.search(t):
        yield ("title", t)
        return
    pm = PRECINCT_RE.match(t)
    if pm:
        yield ("precinct", int(pm.group(1)))
        return
    if STATS_RE.match(t):
        return
    if "precincts reported" in t.lower():
        return
    cm = CANDIDATE_LINE_RE.match(t)
    if (
        cm
        and ":" not in t
        and "," not in cm.group("name")
        and cm.group("name").lower() not in LINE_STOPWORDS
    ):
        # Page headers like "Crook County, Oregon, ... May 19, 2026" match
        # the line shape with votes=2026; the comma guard rejects them
        # (candidate names in this report are never comma-separated).
        yield ("candline", (cm.group("name"), int(cm.group("votes").replace(",", ""))))
        return
    # Page furniture and split labels: keep for debugging.
    yield ("line", t)


# Fixed per-precinct contest order in the Crook report (observed in every
# precinct block).  Headerless contest rows are attributed to the contest
# that follows the last title seen.
EXPECTED_SEQUENCE = [
    ("U.S. Senate", "", "D"),
    ("U.S. Senate", "", "R"),
    ("U.S. House", "2", "D"),
    ("U.S. House", "2", "R"),
    ("Governor", "", "D"),
    ("Governor", "", "R"),
    ("State House", "59", "D"),
    ("State House", "59", "R"),
    ("Precinct Committee Person", "", "D"),
    ("Precinct Committee Person", "", "R"),
    ("Labor Commissioner", "", ""),
    ("Judge of the Supreme Court", "Position 4", ""),
    ("Judge of the Court of Appeals", "Position 1", ""),
    ("Judge of the Court of Appeals", "Position 9", ""),
    ("Judge of the Court of Appeals", "Position 12", ""),
    ("Judge of the Court of Appeals", "Position 13", ""),
    ("District Attorney", "", ""),
    ("County Assessor", "", ""),
    ("County Commissioner", "Position 2", ""),
    ("Measure 120", "", ""),
]


def parse_pages(pages, contest_index=None, candidate_sets=None, aliases=None):
    """Parse all precinct pages; returns (rows, report dict).

    contest_index maps a candidate-name tuple to (office, district, party);
    it is built by pass 1 and lets pass 2 resolve contests whose OCR title
    was dropped.  candidate_sets maps (office, district, party) to the set
    of candidate names seen for it, for subset matching when the OCR also
    mangled a row label.
    """
    rows_out = defaultdict(int)
    contest_index = contest_index if contest_index is not None else {}
    candidate_sets = candidate_sets if candidate_sets is not None else defaultdict(set)
    mismatches = []
    notes = []
    corrections = []
    contests_checked = 0
    new_index = {}
    new_sets = defaultdict(set)

    state = {
        "precinct": None,
        "office": None,
        "district": "",
        "party": "",
        "seq_idx": -1,  # index in EXPECTED_SEQUENCE of last title-matched contest
        "last_closed": None,  # (office, district, party) of last flushed contest
    }
    cands = defaultdict(int)
    over = under = 0
    total = None
    pending = []  # headerless (label, votes) rows
    deferred = []  # candidates whose label was merged into the row above

    def flush():
        nonlocal cands, over, under, total, contests_checked
        if state["office"] is None:
            return
        okey = (state["office"], state["district"], state["party"])
        s = sum(cands.values()) + over + under
        if total is not None and s != total:
            # A dropped candidate row leaves a deficit against the printed
            # Total; when exactly one known candidate is absent from the
            # parsed rows, the deficit is that candidate's votes.
            known = candidate_sets.get(okey, set())
            missing = known - set(cands.keys())
            if total > s and len(missing) == 1:
                cand = next(iter(missing))
                cands[cand] += total - s
                corrections.append(
                    f"{state['precinct']} {okey}: dropped row {cand} "
                    f"recovered as {total - s} (Total {total} - sum {s})"
                )
            else:
                mismatches.append(
                    f"{state['precinct']} {state['office']} {state['district']} "
                    f"{state['party']}: sum={s} total={total}"
                )
        contests_checked += 1
        names = tuple(cands.keys())
        if names and all(v is not None and v >= 0 for v in cands.values()):
            if names not in new_index:
                new_index[names] = okey
            new_sets[okey].update(cands.keys())
        for cand, votes in cands.items():
            rows_out[(state["precinct"],) + okey + (cand,)] += votes
        if over:
            rows_out[(state["precinct"],) + okey + ("Over Votes",)] += over
        if under:
            rows_out[(state["precinct"],) + okey + ("Under Votes",)] += under
        state["last_closed"] = okey
        state["office"] = None
        state["district"] = ""
        state["party"] = ""
        cands = defaultdict(int)
        over = under = 0
        total = None

    def resolve_pending(pend, total_votes):
        """Attribute headerless rows to a contest and emit them."""
        nonlocal contests_checked
        if not pend or state["precinct"] is None:
            return
        names = tuple(dict.fromkeys(label for label, _ in pend))
        # An all-write-in contest matches every candidate set that allows
        # write-ins, so the index and subset lookups are ambiguous: go
        # straight to the sequence fallback (p056's untitled PCP R block).
        ambiguous = set(names) == {"Write-ins"}
        okey = None if ambiguous else contest_index.get(names)
        if okey is None and not ambiguous:
            # Subset match: a contest whose candidate set contains all of
            # these names.
            subset = [k for k, s in candidate_sets.items() if names and set(names) <= s]
            if len(subset) == 1:
                okey = subset[0]
        if okey is None:
            # Sequence fallback: the dropped title is the contest right
            # after the last title seen.
            nxt = state["seq_idx"] + 1
            if 0 <= nxt < len(EXPECTED_SEQUENCE):
                okey = EXPECTED_SEQUENCE[nxt]
                corrections.append(
                    f"{state['precinct']}: headerless rows {names} -> {okey} (sequence)"
                )
        if okey is None:
            notes.append(f"{state['precinct']}: unresolved headerless rows {names}")
            return
        state["last_closed"] = okey
        agg = defaultdict(int)
        over_p = under_p = 0
        for label, votes in pend:
            if label == "Over Votes":
                over_p += votes
            elif label == "Under Votes":
                under_p += votes
            else:
                agg[label] += votes
        s = sum(agg.values()) + over_p + under_p
        if total_votes is not None and s != total_votes:
            mismatches.append(
                f"{state['precinct']} {okey} (headerless): sum={s} total={total_votes}"
            )
        contests_checked += 1
        for cand, votes in agg.items():
            rows_out[(state["precinct"],) + okey + (cand,)] += votes
        if over_p:
            rows_out[(state["precinct"],) + okey + ("Over Votes",)] += over_p
        if under_p:
            rows_out[(state["precinct"],) + okey + ("Under Votes",)] += under_p

    def record(label, votes):
        nonlocal total, over, under
        if aliases:
            label = aliases.get(label, label)
        if state["office"] is None:
            if label == "Total":
                # Headerless contest ends here: resolve its buffered rows.
                resolve_pending(pending, votes)
                pending.clear()
                return
            if label in ("Over Votes", "Under Votes"):
                if pending:
                    # Over/Under can print after the Total of a headerless
                    # contest; keep them with the pending rows.
                    pending.append((label, votes))
                elif state["last_closed"]:
                    # Over/Under print after the Total: they belong to the
                    # just-closed contest.
                    rows_out[(state["precinct"],) + state["last_closed"] + (label,)] += votes
                return
            pending.append((label, votes))
            return
        if label == "Total":
            total = votes
            flush()
        elif label == "Over Votes":
            over = votes
        elif label == "Under Votes":
            under = votes
        else:
            cands[label] += votes

    def open_contest(title):
        flush()
        if pending:
            resolve_pending(pending, None)
            pending.clear()
        parsed = parse_contest_title(title)
        if parsed[0] is None:
            notes.append(f"unmapped contest {title!r}")
            return
        state["office"], state["district"], state["party"] = parsed[:3]
        okey = (parsed[0], parsed[1], parsed[2])
        try:
            state["seq_idx"] = [
                (o, d, p) for o, d, p in EXPECTED_SEQUENCE
            ].index(okey)
        except ValueError:
            state["seq_idx"] = len(EXPECTED_SEQUENCE) - 1
        if parsed[0] == "Precinct Committee Person":
            ppm = PCP_PRECINCT_RE.search(title)
            if ppm:
                state["precinct"] = f"Precinct {int(ppm.group(1))}"

    for path in pages:
        markdown = path.read_text()
        if re.search(r"^## All Precincts", markdown, re.MULTILINE):
            break
        for kind, payload in page_events(markdown):
            if kind == "precinct":
                flush()
                if pending:
                    resolve_pending(pending, None)
                    pending.clear()
                state["precinct"] = f"Precinct {payload}"
                state["seq_idx"] = -1
                continue
            if kind == "title":
                open_contest(payload)
                continue
            if kind == "candline":
                if state["precinct"] is None:
                    continue
                record(label_candidate(payload[0]), payload[1])
                continue
            if kind == "row":
                cells = payload
                name = cells[0]
                pm = PRECINCT_RE.match(name)
                if pm:
                    flush()
                    if pending:
                        resolve_pending(pending, None)
                        pending.clear()
                    state["precinct"] = f"Precinct {int(pm.group(1))}"
                    state["seq_idx"] = -1
                    continue
                # Split label: ["Precinct", "03", ...]
                if (
                    name.strip().lower() == "precinct"
                    and len(cells) > 1
                    and re.fullmatch(r"\d+", cells[1].strip() or "")
                ):
                    flush()
                    if pending:
                        resolve_pending(pending, None)
                        pending.clear()
                    state["precinct"] = f"Precinct {int(cells[1])}"
                    state["seq_idx"] = -1
                    continue
                # Titles sometimes land in a two-cell row with the votes
                # cell empty (e.g. p031 renders every contest title that way).
                if VOTE_FOR_RE.search(name):
                    open_contest(name)
                    continue
                # The OCR occasionally merges a candidate row with the
                # Write-in row below it ("Doug Tookey\nWrite-in" in one
                # cell, the write-in's votes on the next row as a bare
                # number): split the labels and defer the write-in.
                if "\\n" in name:
                    parts = [p.strip() for p in name.split("\\n") if p.strip()]
                    if parts and len(cells) >= 2 and re.fullmatch(r"[\d,]+", cells[1] or ""):
                        record(label_candidate(parts[0]), int(cells[1].replace(",", "")))
                        deferred.extend(label_candidate(p) for p in parts[1:])
                    continue
                if (
                    deferred
                    and re.fullmatch(r"[\d,]+", name or "")
                    and len(cells) >= 2
                    and re.fullmatch(r"\d+(?:\.\d+)?%", cells[1].strip() or "")
                ):
                    record(deferred.pop(0), int(name.replace(",", "")))
                    continue
                if state["precinct"] is None:
                    continue
                # Choice/Votes/Vote% rows: name, votes, optional percent.
                if len(cells) >= 2 and re.fullmatch(r"[\d,]+", cells[1] or ""):
                    record(label_candidate(name), int(cells[1].replace(",", "")))
    flush()
    if pending:
        resolve_pending(pending, None)

    return rows_out, {
        "mismatches": mismatches,
        "notes": notes,
        "corrections": corrections,
        "contest_index": new_index,
        "candidate_sets": {k: set(v) for k, v in new_sets.items()},
        "checked": contests_checked,
    }


def main():
    pages = sorted(CACHE_DIR.glob("p*.md"))

    # Pass 1 builds the candidate-tuple index from contests whose titles
    # parsed; pass 2 uses it (plus the fixed sequence) to recover contests
    # whose titles OCR dropped.
    _rows1, report1 = parse_pages(pages)
    aliases = build_aliases(_rows1)
    for alias, canon in sorted(aliases.items()):
        print(f"CANDIDATE ALIAS: {alias!r} -> {canon!r}", file=sys.stderr)
    aliased_sets = {
        okey: {aliases.get(n, n) for n in names}
        for okey, names in report1["candidate_sets"].items()
    }
    aliased_index = {
        tuple(aliases.get(n, n) for n in names): okey
        for names, okey in report1["contest_index"].items()
    }
    rows, report = parse_pages(
        pages,
        contest_index=aliased_index,
        candidate_sets=aliased_sets,
        aliases=aliases,
    )

    for c in report["corrections"]:
        print(f"CORRECTION: {c}", file=sys.stderr)
    for m in report["mismatches"][:20]:
        print(f"SUM MISMATCH: {m}", file=sys.stderr)
    for n in report["notes"][:20]:
        print(f"NOTE: {n}", file=sys.stderr)
    print(
        f"contests checked: {report['checked']}; mismatches: {len(report['mismatches'])}; "
        f"notes: {len(report['notes'])}; corrections: {len(report['corrections'])}",
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
        for (prec, office, district, party, candidate), votes in sorted(rows.items())
    ]
    path = Path("2026/counties") / f"20260519__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    write_csv(out_rows, str(path))
    print(f"Wrote {len(out_rows)} rows to {path}")


if __name__ == "__main__":
    main()