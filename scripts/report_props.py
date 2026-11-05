#!/usr/bin/env python3
"""Print the props hit-rate record from results/props_results.json --
overall, by week, and by confidence tier (so we can see whether the
highest-conviction picks (7-8) actually outperform the marginal ones (4-5),
which is the real question for whether to tighten the selection bar).

Usage: python3 report_props.py <props_store.json>
"""
import json
import sys
from collections import defaultdict


def pct(hits, total):
    return f"{100 * hits / total:.1f}%" if total else "n/a"


def main():
    with open(sys.argv[1]) as f:
        store = json.load(f)
    props = store.get("props") or []
    graded = [p for p in props if p.get("result") in ("hit", "miss")]  # pushes excluded from the rate, shown separately
    pushes = [p for p in props if p.get("result") == "push"]

    hits = sum(1 for p in graded if p["result"] == "hit")
    total = len(graded)
    print(f"OVERALL: {hits}/{total} = {pct(hits, total)}" + (f"  ({len(pushes)} push, excluded)" if pushes else ""))

    by_week = defaultdict(lambda: [0, 0])
    by_tier = defaultdict(lambda: [0, 0])
    for p in graded:
        wk = p.get("week") or "?"
        by_week[wk][1] += 1
        by_week[wk][0] += p["result"] == "hit"
        conf = p.get("confidence")
        tier = "7-8 (strong)" if conf and conf >= 7 else "5-6 (real)" if conf and conf >= 5 else "1-4 (marginal)"
        by_tier[tier][1] += 1
        by_tier[tier][0] += p["result"] == "hit"

    print("\nBY WEEK:")
    for wk in sorted(by_week):
        h, t = by_week[wk]
        print(f"  {wk}: {h}/{t} = {pct(h, t)}")

    print("\nBY CONFIDENCE TIER:")
    for tier in ("7-8 (strong)", "5-6 (real)", "1-4 (marginal)"):
        if tier in by_tier:
            h, t = by_tier[tier]
            print(f"  {tier}: {h}/{t} = {pct(h, t)}")

    misses = [p for p in graded if p["result"] == "miss"]
    if misses:
        print("\nMISSES:")
        for p in misses:
            print(f"  {p['player']} ({p['market']}) {p['lean']} {p['line']} -> {p['actual']}  [{p.get('matchup')}]")


if __name__ == "__main__":
    main()
