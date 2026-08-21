#!/usr/bin/env python3
"""Cross-check the Benton precinct CSV against the county's official
"Precinct Results by Contest" PDF (2026/sources/2026May19-benton-precinct-results.pdf).

The PDF is parsed with pdfplumber word coordinates. Pages carry one or two
contest sections side by side; the precinct label at the far left serves
both. Section columns are evenly spaced with no visual gutter, so dual
sections are split by *count*: a full row holds the left section's
C1+stats columns followed by the right section's. The learned split x
position then resolves partial rows where one section has no row.

A section's numeric row is its candidate columns followed by a prefix or
suffix of the 6-column stats block (Write-in Totals, Write-in: Not Assigned,
Total Votes Cast, Overvotes, Undervotes, Contest Total). Measures carry only
the last 4 (no write-ins); some contests split the stats block onto
continuation pages. No-filed-candidate (carousel) contests list itemized
write-in columns followed by stats; their Write-ins total equals TVC.
"""

import csv
import re
import sys
from collections import defaultdict

import pdfplumber

PDF = "2026/sources/2026May19-benton-precinct-results.pdf"
CSV = "2026/counties/20260519__or__primary__benton__precinct.csv"

NUM_RE = re.compile(r"^\d{1,3}(,\d{3})*$")
LABEL_RE = re.compile(r"^\d{1,3}$")
STAT_KEYS = ["Write-ins", None, "TVC", "Over Votes", "Under Votes", "CT"]

SECTION_SPECS = [
    (re.compile(r"(DEM|REP) US Senator"), lambda m: ("U.S. Senate", "", party(m))),
    (re.compile(r"(DEM|REP) US Representative, (\d+)"),
     lambda m: ("U.S. House", m.group(2), party(m))),
    (re.compile(r"(DEM|REP) Governor"), lambda m: ("Governor", "", party(m))),
    (re.compile(r"(DEM|REP) State Senator, (\d+)"), lambda m: ("State Senate", m.group(2), party(m))),
    (re.compile(r"(DEM|REP) State Representative, (\d+)"), lambda m: ("State House", m.group(2), party(m))),
    (re.compile(r"(DEM|REP) County Commissioner, Position (\d+)"),
     lambda m: ("County Commissioner", "Position " + m.group(2), party(m))),
    (re.compile(r"Judge of the Supreme Court, Position (\d+)"),
     lambda m: ("Judge of the Supreme Court", "Position " + m.group(1), "")),
    (re.compile(r"Judge of the Court of Ap\w+, Position (\d+)"),
     lambda m: ("Judge of the Court of Appeals", "Position " + m.group(1), "")),
    (re.compile(r"Judge of the Circuit Court, (?:21st|21) District, Position (\d+)"),
     lambda m: ("Judge of the Circuit Court", "21st District, Position " + m.group(1), "")),
    (re.compile(r"Commissioner of the Bureau of Labor"), lambda m: ("Labor Commissioner", "", "")),
    (re.compile(r"State Measure (\d+)"), lambda m: ("Measure " + m.group(1), "", "")),
    (re.compile(r"(2-\d+)\s+\D"), lambda m: ("Measure " + m.group(1), "", "")),
]


def party(m):
    return "D" if m.group(1) == "DEM" else "R"


def parse_int(word):
    return int(word.replace(",", ""))


def load_csv():
    """contest key -> {'cands': [names in order], 'rows': precinct -> dict}"""
    contests = {}
    with open(CSV) as f:
        for r in csv.DictReader(f):
            key = (r["office"], r["district"], r["party"])
            c = contests.setdefault(key, {"cands": [], "rows": defaultdict(dict)})
            row = c["rows"][r["precinct"]]
            cand, v = r["candidate"], int(r["votes"])
            if cand in ("Write-ins", "Over Votes", "Under Votes"):
                row[cand] = row.get(cand, 0) + v
            else:
                if cand not in c["cands"]:
                    c["cands"].append(cand)
                row[cand] = v
    return contests


def group_lines(words, tol=3.0):
    """Group words into text lines by vertical position (robust clustering)."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and w["top"] - lines[-1][0] <= tol:
            lines[-1][1].append(w)
            # keep the running anchor near the group's median row position
            lines[-1][0] = (lines[-1][0] + w["top"]) / 2
        else:
            lines.append([w["top"], [w]])
    for _, ws in lines:
        yield sorted(ws, key=lambda w: w["x0"])


def find_titles(words):
    for tops in group_lines(words):
    found = []
    for tops in by_top.values():
        tops.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in tops)
        spans = []
        pos = 0
        for w in tops:
            spans.append((pos, w))
            pos += len(w["text"]) + 1
        for rx, keyfn in SECTION_SPECS:
            for m in rx.finditer(text):
                for cs, w in spans:
                    if cs <= m.start():
                        first = w
                found.append((first["x0"], keyfn(m)))
    return found


def classify(nums, ccount):
    """Return (candidate values or None, {stat_offset: value}) or (None, None)."""
    if ccount == 0:
        # carousel contests: itemized write-in columns then [WNA?,] TVC,
        # Over, Under, CT; every vote is a write-in so Write-ins == TVC
        if len(nums) >= 4 and nums[-4] + nums[-3] + nums[-2] == nums[-1] and nums[-1] > 0:
            return None, {2: nums[-4], 3: nums[-3], 4: nums[-2], 5: nums[-1]}
        return None, None
    if len(nums) == ccount:
        return nums, {}
    if len(nums) > ccount:
        tail = nums[ccount:]
        if len(tail) == 6 and tail[2] + tail[3] + tail[4] == tail[5]:
            return nums[:ccount], dict(enumerate(tail))
        if len(tail) == 4:
            if tail[0] + tail[1] + tail[2] == tail[3]:
                return nums[:ccount], {2 + i: v for i, v in enumerate(tail)}  # TVC..CT
            return nums[:ccount], dict(enumerate(tail))  # WIT,WNA,TVC,Over
        if 0 < len(tail) < 4:
            return nums[:ccount], dict(enumerate(tail))
    # stats-only rows (no candidate columns on this page)
    if len(nums) == 6 and nums[2] + nums[3] + nums[4] == nums[5]:
        return None, dict(enumerate(nums))
    if len(nums) == 7 and nums[3] + nums[4] + nums[5] == nums[6]:
        return None, dict(enumerate(nums[1:]))  # leading "No Candidate Filed" col
    if len(nums) == 4 and nums[0] + nums[1] + nums[2] == nums[3]:
        return None, {2 + i: v for i, v in enumerate(nums)}
    if len(nums) == 2 and nums[0] != nums[1]:
        return None, {4: nums[0], 5: nums[1]}  # Under, CT continuation
    return None, None


def ok(nums, ccount):
    return classify(nums, ccount) != (None, None)


def page_bands(pdf, contests):
    """Yield (page_no, key, label, nums)."""
    for pno, page in enumerate(pdf.pages, start=1):
        words = page.extract_words()
        titles = sorted(set(find_titles(words)))
        if not titles:
            continue
        lines = []
        by_top = defaultdict(list)
        for w in words:
            by_top[round(w["top"] / 3)].append(w)
        for tops in by_top.values():
            tops.sort(key=lambda w: w["x0"])
            label = tops[0]["text"] if tops else ""
            if not (LABEL_RE.match(label) or label == "Totals"):
                continue
            rest = tops[1:]
            if rest and all(NUM_RE.match(w["text"]) for w in rest):
                lines.append((label, rest))
        if len(titles) == 1:
            for label, ws in lines:
                yield pno, titles[0][1], label, [parse_int(w["text"]) for w in ws]
            continue
        (_, key1), (_, key2) = titles
        c1 = len(contests[key1]["cands"]) if key1 in contests else None
        c2 = len(contests[key2]["cands"]) if key2 in contests else None
        # Pass 1: learn the x boundary between sections from any line where a
        # count-based split is unambiguous (usually a full row or the Totals).
        boundary = None
        for label, ws in lines:
            nums = [parse_int(w["text"]) for w in ws]
            sides = [(label, ws, nums)]
            if c1 is not None and c2 is not None:
                both = [k for k in range(len(nums) + 1)
                        if ok(nums[:k], c1) and ok(nums[k:], c2)]
                if len(both) == 1 and 0 < both[0] < len(ws):
                    boundary = (ws[both[0] - 1]["x1"] + ws[both[0]]["x0"]) / 2
                    break
        # Pass 2: split every line, position-first via the learned boundary.
        for label, ws in lines:
            nums = [parse_int(w["text"]) for w in ws]
            if boundary is not None:
                split = len([w for w in ws if (w["x0"] + w["x1"]) / 2 < boundary])
            elif c1 is not None and c2 is not None:
                both = [k for k in range(len(nums) + 1)
                        if ok(nums[:k], c1) and ok(nums[k:], c2)]
                split = both[0] if len(both) == 1 else None
                if split is None:  # largest-gap last resort
                    gaps = [(ws[i]["x0"] - ws[i - 1]["x1"], i)
                            for i in range(1, len(ws))]
                    split = max(gaps)[1] if gaps else len(ws)
            else:
                split = len(ws)
            if split and (c1 is None or ok(nums[:split], c1)):
                yield pno, key1, label, nums[:split]
            if split < len(nums) and (c2 is None or ok(nums[split:], c2)):
                yield pno, key2, label, nums[split:]


def main():
    contests = load_csv()
    official = defaultdict(lambda: {
        "rows": defaultdict(lambda: {"cands": None, "stats": {}}),
        "totals": {"cands": None, "stats": {}},
    })

    def store(entry, cands, stats, ctx):
        if cands:
            if entry["cands"] and entry["cands"] != cands:
                print(f"NOTE {ctx}: duplicate cands {entry['cands']} vs {cands}")
            else:
                entry["cands"] = cands
        stats = dict(stats or {})
        # carousel itemization rows can satisfy the CT identity spuriously;
        # the genuine summary row has the largest Contest Total for a
        # precinct, so on a CT conflict keep only that whole row
        if 5 in stats:
            prev5 = entry["stats"].get(5)
            if prev5 is not None and prev5 != stats[5]:
                if stats[5] > prev5:
                    entry["stats"] = dict(stats)
                    return
                stats = {o: v for o, v in stats.items() if o not in entry["stats"]}
        for off, v in stats.items():
            prev = entry["stats"].get(off)
            if prev is not None and prev != v:
                print(f"NOTE {ctx}: conflicting stat {STAT_KEYS[off]} {prev} vs {v}")
            else:
                entry["stats"][off] = v

    pdf = pdfplumber.open(PDF)
    for pno, key, label, nums in page_bands(pdf, contests):
        if key not in contests:
            print(f"NOTE: no CSV contest for official section {key} (p{pno})")
            continue
        cands, stats = classify(nums, len(contests[key]["cands"]))
        if cands is None and stats is None:
            print(f"NOTE: unclassified row {key} {label} p{pno}: {nums}")
            continue
        sec = official[key]
        entry = sec["totals"] if label == "Totals" else sec["rows"][label]
        store(entry, cands, stats, f"{key} {label} (p{pno})")

    n_bad = 0
    for key, c in sorted(contests.items()):
        if not key[0].startswith("Measure") and key[0] not in {
            "U.S. Senate", "U.S. House", "Governor", "State Senate", "State House",
            "County Commissioner", "Judge of the Supreme Court",
            "Judge of the Court of Appeals", "Judge of the Circuit Court",
            "Labor Commissioner",
        }:
            continue
        sec = official.get(key)
        if sec is None:
            print(f"MISSING official section for {key}")
            n_bad += 1
            continue
        cands = c["cands"]
        totals = defaultdict(int)
        for precinct, row in sorted(c["rows"].items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            got = sec["rows"].get(precinct)
            csv_vals = [row.get(cand, 0) for cand in cands]
            csv_wit = row.get("Write-ins", 0)
            csv_over = row.get("Over Votes", 0)
            csv_under = row.get("Under Votes", 0)
            if got is None:
                print(f"NO-OFFICIAL-ROW {key} p{precinct}: "
                      f"csv={csv_vals} wit={csv_wit} over={csv_over} under={csv_under}")
                n_bad += 1
                continue
            for cand in cands:
                totals[cand] += row.get(cand, 0)
            totals["Write-ins"] += csv_wit
            totals["Over Votes"] += csv_over
            totals["Under Votes"] += csv_under
            probs = []
            ocands = got["cands"]
            if ocands and cands and ocands != csv_vals:
                probs.append(f"cands {csv_vals} != official {ocands}")
            ost = got["stats"]
            if 2 in ost and sum(csv_vals) + csv_wit != ost[2]:
                probs.append(f"sum+wit {sum(csv_vals)+csv_wit} != TVC {ost[2]}")
            if 0 in ost and csv_wit != ost[0]:
                probs.append(f"Write-ins {csv_wit} != {ost[0]}")
            if 3 in ost and csv_over != ost[3]:
                probs.append(f"Over {csv_over} != {ost[3]}")
            if 4 in ost and csv_under != ost[4]:
                probs.append(f"Under {csv_under} != {ost[4]}")
            if probs:
                n_bad += 1
                print(f"MISMATCH {key} p{precinct}: " + "; ".join(probs))
        # official precincts with no CSV row at all
        csv_precincts = set(c["rows"])
        missing = [p for p, e in sec["rows"].items()
                   if p not in csv_precincts and (e["cands"] or e["stats"])]
        if missing:
            print(f"CSV-MISSING-PRECINCTS {key}: {sorted(missing, key=int)}")
            n_bad += len(missing)
        tot = sec["totals"]
        if tot["cands"]:
            for i, cand in enumerate(cands):
                if i < len(tot["cands"]) and totals[cand] != tot["cands"][i]:
                    print(f"TOTAL-MISMATCH {key} {cand}: "
                          f"csv {totals[cand]} != official {tot['cands'][i]}")
                    n_bad += 1
        for off, name in [(0, "Write-ins"), (3, "Over Votes"), (4, "Under Votes")]:
            if off in tot["stats"] and totals[name] != tot["stats"][off]:
                print(f"TOTAL-MISMATCH {key} {name}: "
                      f"csv {totals[name]} != {tot['stats'][off]}")
                n_bad += 1
    print(f"\n{n_bad} discrepancies" if n_bad else "\nALL CHECKS MATCH OFFICIAL")


if __name__ == "__main__":
    main()
