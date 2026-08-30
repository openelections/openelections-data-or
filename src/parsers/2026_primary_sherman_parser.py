"""Parser for Sherman County 2026 primary "Detail Results By Precinct" PDF.

The PDF has no text layer and the cloud PaddleOCR markdown cache pairs values
with the wrong rows, so this parser works from local PaddleOCR boxes
(`/tmp/sherman_ocr_boxes.json`, produced by the batch OCR worker over rendered
pages, cached to `.paddleocr_cache/Sherman/boxes.json`).

Layout: five precinct blocks (Rufus p1-5, Wasco p6-10, Moro p11-15,
Grass Valley p16-20, Kent p21-25).  Contest order per precinct: Labor
Commissioner, Supreme Court 4, Court of Appeals 1/9/12/13, Circuit 7-3,
District Attorney, County Assessor, Justice of the Peace, State Measure
120, then the DEM and REP partisan contests (U.S. Senate, U.S. House 2,
Governor, State House 57, County Commissioner 2, PCP).  Grass Valley also
votes on local Measure 28-49 (fire protection district) after Measure 120.
Contests flow across page boundaries; every page repeats the block header
and a "Contest / Votes" column header above the rows.

Pairing: inside one contest block the label lines and value numbers are
strictly one-to-one in document order, so labels and values are zipped and
each contest validated by candidates + over + under == Total.  OCR noise the
parser recovers from:

- a contest title at a page bottom (its "(Vote For N)" lands on the next
  page) -- the title/row lookahead runs over one global stream, not per page;
- a contest title dropped entirely -- the block opens "pending" and is
  resolved after the scan by its candidate set (or the expected contest
  order for all-write-in PCP blocks);
- a row label followed by a "(Vote For N)" line (Total/Over/Under/Write-in
  are never treated as titles);
- a value box sorting just before the next block's trigger line (it belongs
  to the next block; validated and carried over) or a Total value dropped
  by OCR (pairs without it).

Usage:
    uv run python src/parsers/2026_primary_sherman_parser.py
"""

import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from precinct_2026_common import (
    COUNTY_FILENAME,
    ELECTION_DATE,
    format_candidate_name,
    make_row,
    normalize_office,
    normalize_party,
    parse_district,
    write_csv,
)

COUNTY = "Sherman"
CACHE = Path(".paddleocr_cache/Sherman/boxes.json")
TMP_BOXES = Path("/tmp/sherman_ocr_boxes.json")
PRECINCTS = ("Rufus", "Wasco", "Moro", "Grass Valley", "Kent")
LINE_TOL = 22  # boxes whose y-centers differ by less merge into one line

# Canonical per-precinct contest order, used to resolve pending blocks whose
# title line the OCR dropped.  Grass Valley adds the local fire district
# measure right after State Measure 120.
BASE_EXPECTED = [
    ("Labor Commissioner", "", ""),
    ("Judge of the Supreme Court", "Position 4", ""),
    ("Judge of the Court of Appeals", "Position 1", ""),
    ("Judge of the Court of Appeals", "Position 9", ""),
    ("Judge of the Court of Appeals", "Position 12", ""),
    ("Judge of the Court of Appeals", "Position 13", ""),
    ("Judge of the Circuit Court", "7th District, Position 3", ""),
    ("District Attorney", "", ""),
    ("County Assessor", "", ""),
    ("Justice of the Peace", "", ""),
    ("Measure 120", "", ""),
    ("U.S. Senate", "", "D"),
    ("U.S. House", "2", "D"),
    ("Governor", "", "D"),
    ("State House", "57", "D"),
    ("County Commissioner", "Position 2", "D"),
    ("Precinct Committee Person", "", "D"),
    ("U.S. Senate", "", "R"),
    ("U.S. House", "2", "R"),
    ("Governor", "", "R"),
    ("State House", "57", "R"),
    ("County Commissioner", "Position 2", "R"),
    ("Precinct Committee Person", "", "R"),
]
EXPECTED = {prec: list(BASE_EXPECTED) for prec in PRECINCTS}
# The South Sherman fire district measure: Grass Valley and Kent vote on it.
EXPECTED["Grass Valley"].insert(11, ("Measure 28-49", "", ""))
EXPECTED["Kent"].insert(11, ("Measure 28-49", "", ""))

STAT_LABELS = {"Over Votes", "Under Votes", "Total", "Write-ins"}
# Labels never used for pending-contest resolution (ambiguous or generic).
UNRESOLVABLE = {"Write-ins", "Yes", "No"}

# OCR dropped the leading letter at 300 DPI; a 600-DPI re-read of the
# strip (page 25, Kent PCP R) recovers the name.
NAME_FIXES = {"_ee von Borstel": "Lee von Borstel"}


def load_boxes():
    if CACHE.exists():
        return {int(k): v for k, v in json.loads(CACHE.read_text()).items()}
    raw = json.loads(TMP_BOXES.read_text())
    boxes = {
        int(k): [
            {"box": b, "text": t, "score": s}
            for b, t, s in zip(v["boxes"], v["texts"], v["scores"])
        ]
        for k, v in raw.items()
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(boxes))
    return boxes


def digitish(text: str) -> bool:
    return re.fullmatch(r"[\d,]+", text) is not None


def page_stream(boxes):
    """One page -> (lines, values) in document order.

    Every standalone number is a value box; label boxes are non-numeric
    text at the left margin (x0 < 1600 keeps out right-margin OCR noise
    like the votes-column header).  Header-zone rows (party ballots,
    ballot styles) are dropped later via the "Contest" column header.
    """
    values = [
        (int((b["box"][1] + b["box"][3]) / 2), int(b["text"].replace(",", "")))
        for b in boxes
        if digitish(b["text"]) and len(b["text"]) <= 7
    ]
    labels = [
        b
        for b in boxes
        if not digitish(b["text"]) and b["box"][0] < 1600
    ]

    labels.sort(key=lambda b: (b["box"][1] + b["box"][3]) / 2)
    lines = []
    band = []
    prev = None
    for b in labels:
        center = (b["box"][1] + b["box"][3]) / 2
        if band and prev is not None and center - prev > LINE_TOL:
            lines.append(band)
            band = []
        band.append(b)
        prev = center
    if band:
        lines.append(band)
    out = []
    for band in lines:
        toks = sorted(band, key=lambda b: b["box"][0])
        center = sum((b["box"][1] + b["box"][3]) / 2 for b in band) / len(band)
        out.append((center, toks[0]["box"][0], " ".join(b["text"] for b in toks)))
    return out, sorted(values)


def match_precinct(text: str):
    norm = re.sub(r"\s+", " ", text).strip()
    for name in PRECINCTS:
        if norm.startswith(name):
            return name
    # OCR mangles the standalone header line ("Nasco" for Wasco); short
    # lines get a fuzzy match.
    if len(norm) <= 16:
        for name in PRECINCTS:
            if difflib.SequenceMatcher(None, norm.lower(), name.lower()).ratio() >= 0.75:
                return name
    return None


# --- office mapping -------------------------------------------------------

PCP_RE = re.compile(r"Precinct Committee Person", re.IGNORECASE)
LOCAL_MEASURE_RE = re.compile(r"^(\d{1,2}-\d{1,3})\b")


def parse_office(title: str):
    """Map a contest title line to (office, district, party) or (None,)*3."""
    t = re.sub(r"\s+", " ", title).strip()
    party = ""
    if re.match(r"^DEM\b", t):
        party = normalize_party("DEM")
        t = t[3:].strip()
    elif re.match(r"^REP\b", t):
        party = normalize_party("REP")
        t = t[3:].strip()

    if PCP_RE.search(t):
        # The party prefix OCR-mangles ("PEM"); the title also carries the
        # spelled-out party ("... Democrat Kent Kent").
        if not party:
            party = normalize_party(t)
        return "Precinct Committee Person", "", party

    m = LOCAL_MEASURE_RE.match(t)
    if m:
        return f"Measure {m.group(1)}", "", ""

    office = normalize_office(t)
    if office is not None:
        return office, parse_district(t), party

    if "County Commissioner" in t:
        return "County Commissioner", parse_district(t), party
    if "County Assessor" in t:
        return "County Assessor", "", party
    if "Justice of the Peace" in t:
        return "Justice of the Peace", "", party
    return None, None, None


def label_candidate(name: str):
    """Classify a row label; None for rows to drop (No Candidate Filed)."""
    n = re.sub(r"\s+", " ", name).strip()
    lowered = n.lower()
    if "no candidate filed" in lowered:
        return None
    if lowered.startswith("write-in") or lowered.startswith("write in"):
        return "Write-ins"
    if difflib.SequenceMatcher(None, lowered, "write-in").ratio() >= 0.7:
        return "Write-ins"
    if re.fullmatch(r"over ?votes?", lowered) or (
        lowered.startswith("o") and difflib.SequenceMatcher(None, lowered, "overvotes").ratio() >= 0.75
    ):
        return "Over Votes"
    if re.fullmatch(r"under ?votes?", lowered) or (
        lowered.startswith("u") and difflib.SequenceMatcher(None, lowered, "undervotes").ratio() >= 0.75
    ):
        return "Under Votes"
    if lowered == "total":
        return "Total"
    if lowered in ("yes", "no"):
        return n.title()
    return NAME_FIXES.get(n, format_candidate_name(n.split()))


def is_stat(text: str) -> bool:
    """True for row labels that can never be contest titles."""
    c = label_candidate(text)
    return c is None or c in STAT_LABELS


def build_aliases(counts, threshold=0.85):
    """Cluster OCR spellings of the same candidate; canonical = most frequent."""
    names = sorted(counts)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if difflib.SequenceMatcher(None, a, b).ratio() >= threshold:
                ra, rb = find(a), find(b)
                if ra != rb:
                    if counts[ra] >= counts[rb]:
                        parent[rb] = ra
                    else:
                        parent[ra] = rb
    return {n: find(n) for n in names if find(n) != n}


def run(boxes, aliases=None, collect=None):
    """One parse pass.  Returns (blocks, notes, mismatches, checked).

    blocks are flush records in document order; pending blocks carry
    okey=None.  When collect is a counter, raw candidate labels are counted
    (pass 1, for alias building) and rows are not emitted.
    """
    entries = []  # (page_no, y, kind, payload) over all pages
    page_precinct = {}
    for page_no in sorted(boxes):
        lines, values = page_stream(boxes[page_no])
        if not lines:
            continue
        for _c, _x, text in lines:
            m = match_precinct(text)
            if m:
                page_precinct[page_no] = m
                break
        stream = [(c, "line", t) for c, _x, t in lines]
        stream += [(c, "value", v) for c, v in values]
        stream.sort(key=lambda s: s[0])
        # Drop the page header zone: everything up to and including the
        # "Contest" column header ("Contest" sometimes OCR-merges with
        # "Votes").
        header_idx = 0
        for i, s in enumerate(stream):
            if s[1] == "line" and s[2].strip().lower().startswith("contest"):
                header_idx = i
                break
        stream = stream[header_idx + 1 :]
        entries.extend((page_no, c, kind, p) for c, kind, p in stream)

    blocks = []
    notes = []
    mismatches = []
    checked = 0
    state = {"cur": None, "precinct": None, "page": None}

    def tally(labels, values):
        cands = defaultdict(int)
        over = under = 0
        total = None
        for lab, val in zip(labels, values):
            if lab is None:
                continue
            if lab == "Total":
                total = val
            elif lab == "Over Votes":
                over = val
            elif lab == "Under Votes":
                under = val
            else:
                if aliases:
                    lab = aliases.get(lab, lab)
                cands[lab] += val
        return cands, over, under, total

    def fix_stats(labels):
        """Repair OCR-mangled Over/Under labels ("vtes", "n te"): they are
        short letter-only strings sitting right before the Total row."""
        if len(labels) < 3 or labels[-1] != "Total":
            return labels
        for pos, stat in ((-2, "Under Votes"), (-3, "Over Votes")):
            lab = labels[pos]
            if lab is None or lab in STAT_LABELS or lab in ("Yes", "No"):
                continue
            if len(re.sub(r"[^a-z]", "", lab.lower())) <= 6:
                labels[pos] = stat
        return labels

    def flush():
        """Close the current block; return values that belong to the next."""
        nonlocal checked
        cur = state["cur"]
        state["cur"] = None
        if cur is None:
            return []
        okey = cur["okey"]
        labels_raw = cur["labels"]
        labels = fix_stats([label_candidate(t) for t in labels_raw])
        if collect is not None:
            for lab in labels:
                if lab is not None and lab not in STAT_LABELS:
                    collect[lab] += 1
        values = cur["values"]
        leftover = []

        if len(labels) == len(values) + 1:
            if labels_raw and parse_office(labels_raw[-1])[0] is not None:
                # A contest title leaked into this block's labels (its
                # "(Vote For N)" line was the trigger lookahead missed).
                labels_raw = labels_raw[:-1]
                labels = labels[:-1]
            elif labels and labels[-1] == "Total":
                # Total's value was dropped by OCR: zip simply pairs one
                # fewer, leaving Total unvalidated.
                notes.append(
                    f"{cur['precinct']} {okey}: Total value dropped by OCR"
                )
            else:
                notes.append(
                    f"{cur['precinct']} {okey}: {len(labels)} labels vs "
                    f"{len(values)} values\n    labels: {labels}\n    values: {values}"
                )
                return []
        elif len(values) == len(labels) + 1:
            cands, over, under, total = tally(labels, values[:-1])
            if "Total" in labels and total is not None and (
                sum(cands.values()) + over + under == total
            ):
                # The next block's first value sorted before its trigger
                # line; carry it over.
                leftover = [values[-1]]
                values = values[:-1]
            elif "Total" not in labels:
                # The Total *label* was dropped by OCR; its value is last.
                labels = labels + ["Total"]
            else:
                notes.append(
                    f"{cur['precinct']} {okey}: {len(labels)} labels vs "
                    f"{len(values)} values\n    labels: {labels}\n    values: {values}"
                )
                return []

        if len(labels) != len(values):
            notes.append(
                f"{cur['precinct']} {okey}: {len(labels)} labels vs "
                f"{len(values)} values\n    labels: {labels}\n    values: {values}"
            )
            return []
        cands, over, under, total = tally(labels, values)
        s = sum(cands.values()) + over + under
        if total is not None:
            checked += 1
            if s != total:
                mismatches.append(
                    f"{cur['precinct']} {okey}: sum={s} total={total}"
                )
                return []
        blocks.append(
            {
                "precinct": cur["precinct"],
                "okey": okey,
                "cands": cands,
                "over": over,
                "under": under,
                "total": total,
            }
        )
        return leftover

    prev_line = ""
    for i, (page_no, _cy, kind, payload) in enumerate(entries):
        if page_no != state["page"]:
            state["page"] = page_no
            if page_precinct.get(page_no):
                state["precinct"] = page_precinct[page_no]
        if kind == "value":
            if state["cur"] is not None:
                state["cur"]["values"].append(payload)
            continue
        text = payload
        # The next line tells us whether this line is a contest title
        # (followed by its "(Vote For N)" line) -- titles must not leak
        # into the previous block's labels.  The lookahead runs over the
        # global stream so it crosses page boundaries.
        nxt_line = next(
            (p2 for _pg, _c2, k2, p2 in entries[i + 1 :] if k2 == "line"),
            "",
        )
        if "Vote For" in text:
            leftover = flush()
            parsed = parse_office(prev_line)
            cur = {
                "okey": None if parsed[0] is None else (parsed[0], parsed[1], parsed[2]),
                "precinct": state["precinct"],
                "labels": [],
                "values": list(leftover),
            }
            if parsed[0] is None:
                notes.append(f"pending block at p{page_no}: title {prev_line!r} unmapped")
            state["cur"] = cur
            prev_line = text
            continue
        if "Vote For" in nxt_line and not is_stat(text):
            prev_line = text  # contest title line, not a row label
            continue
        if state["cur"] is not None:
            state["cur"]["labels"].append(text)
        prev_line = text
    flush()

    return blocks, notes, mismatches, checked


def resolve(blocks):
    """Fill in okey for pending blocks: candidate set first, then the
    expected contest order.  Returns the still-unresolved blocks."""
    cand_index = defaultdict(set)
    for b in blocks:
        if b["okey"] is None:
            continue
        for c in b["cands"]:
            if c not in UNRESOLVABLE:
                cand_index[c].add(b["okey"])
    pointer = defaultdict(int)
    unresolved = []
    for b in blocks:
        exp = EXPECTED[b["precinct"]]
        if b["okey"] is None:
            hits = None
            for c in b["cands"]:
                if c in UNRESOLVABLE:
                    continue
                s = cand_index.get(c, set())
                hits = set(s) if hits is None else (hits & s)
            if hits and len(hits) == 1:
                b["okey"] = next(iter(hits))
            else:
                idx = pointer[b["precinct"]]
                if idx < len(exp):
                    b["okey"] = exp[idx]
                    b["via_sequence"] = True
        if b["okey"] is None:
            unresolved.append(b)
            continue
        if b["okey"] in exp:
            pointer[b["precinct"]] = exp.index(b["okey"]) + 1
    return unresolved


def main():
    boxes = load_boxes()

    # Pass 1 collects candidate label spellings; pass 2 emits with aliases.
    counts = defaultdict(int)
    run(boxes, None, collect=counts)
    aliases = build_aliases(counts)
    if aliases:
        for src in sorted(aliases):
            print(f"alias: {src} -> {aliases[src]}", file=sys.stderr)
    blocks, notes, mismatches, checked = run(boxes, aliases)
    unresolved = resolve(blocks)

    for m in mismatches[:20]:
        print(f"SUM MISMATCH: {m}", file=sys.stderr)
    for n in notes[:30]:
        print(f"NOTE: {n}", file=sys.stderr)
    for b in unresolved:
        print(
            f"UNRESOLVED: {b['precinct']} candidates {sorted(b['cands'])}",
            file=sys.stderr,
        )
    # Per-precinct coverage against the expected contest list.
    seen = defaultdict(set)
    for b in blocks:
        if b["okey"] is not None:
            seen[b["precinct"]].add(b["okey"])
    for prec in PRECINCTS:
        missing = [e for e in EXPECTED[prec] if e not in seen[prec]]
        if missing:
            print(f"MISSING in {prec}: {missing}", file=sys.stderr)
    block_counts = defaultdict(int)
    for b in blocks:
        if b["okey"] is not None:
            block_counts[(b["precinct"], b["okey"])] += 1
    for (prec, okey), n in sorted(block_counts.items(), key=lambda kv: str(kv[0])):
        if n > 1:
            print(f"DUPLICATE contest block: {prec} {okey} x{n}", file=sys.stderr)
    print(
        f"blocks: {len(blocks)} (pending resolved by sequence: "
        f"{sum(1 for b in blocks if b.get('via_sequence'))}); "
        f"checked: {checked}; mismatches: {len(mismatches)}; "
        f"notes: {len(notes)}; unresolved: {len(unresolved)}",
        file=sys.stderr,
    )

    rows_out = []
    for b in sorted(blocks, key=lambda b: (PRECINCTS.index(b["precinct"]), b["okey"] or ())):
        if b["okey"] is None:
            continue
        office, district, party = b["okey"]
        for cand, votes in sorted(b["cands"].items()):
            rows_out.append(
                make_row(
                    county=COUNTY,
                    precinct=b["precinct"],
                    office=office,
                    district=district,
                    party=party,
                    candidate=cand,
                    votes=votes,
                )
            )
        for name, votes in (("Over Votes", b["over"]), ("Under Votes", b["under"])):
            if votes:
                rows_out.append(
                    make_row(
                        county=COUNTY,
                        precinct=b["precinct"],
                        office=office,
                        district=district,
                        party=party,
                        candidate=name,
                        votes=votes,
                    )
                )
    path = (
        Path("2026/counties")
        / f"{ELECTION_DATE}__or__primary__{COUNTY_FILENAME[COUNTY]}__precinct.csv"
    )
    write_csv(rows_out, str(path))
    print(f"Wrote {len(rows_out)} rows to {path}")


if __name__ == "__main__":
    main()