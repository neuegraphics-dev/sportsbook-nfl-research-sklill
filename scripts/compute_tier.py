#!/usr/bin/env python3
"""Compute tiers for every active game in a week directory.

Usage:
  python3 compute_tier.py <data_dir> [--reference 2026-09-19T12:00:00Z]

<data_dir> can be a single week folder (data/2026-nfl-wk3/) or the parent
data/ directory -- pass the parent so games from adjacent weeks (this
week's stragglers, next week's early opens) all get tiered together in one
pass, since tier depends on kickoff time, not which week folder a game
lives in.

Prints one JSON object per line (JSONL) to stdout, one per game:
  {"game_id": "...", "matchup": "...", "kickoff": "...", "tier": 1,
   "hours_to_kickoff": 36.2, "tier_label": "Imminent (<=48 hrs)",
   "status": "active"}

This is the entry point the skill should call FIRST each run: it tells you
which games are Tier 1 (fetch + research every run), which are Tier 2/3
(fetch once/day, light research), and which have already kicked off
(tier 0 -- skip, or mark final).
"""
import argparse
import json
import sys

from lib import compute_tier, list_game_files, load_game, parse_iso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--reference", default=None, help="ISO timestamp to treat as 'now' (defaults to actual now, UTC)")
    args = ap.parse_args()

    reference = parse_iso(args.reference) if args.reference else None

    rows = []
    for path in list_game_files(args.data_dir):
        game = load_game(path)
        tier, hours, label = compute_tier(game["kickoff"], reference)
        rows.append({
            "game_id": game["game_id"],
            "matchup": game["matchup"],
            "kickoff": game["kickoff"],
            "tier": tier,
            "hours_to_kickoff": round(hours, 2),
            "tier_label": label,
            "status": game.get("status", "active"),
            "path": path,
        })

    # Sort soonest-kickoff first -- that's the natural priority order.
    rows.sort(key=lambda r: r["hours_to_kickoff"])
    for r in rows:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
