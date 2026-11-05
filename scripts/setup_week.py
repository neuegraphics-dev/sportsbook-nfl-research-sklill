#!/usr/bin/env python3
"""Create the per-game JSON skeletons for a week's slate.

The schedule itself (matchups + kickoff times) comes from the cloud session
via web search -- this script just materializes the files so update_snapshot.py
has something to write into. Safe to re-run: it never overwrites a game file
that already exists (so re-running mid-week to pick up a flexed/added game
doesn't clobber accumulated history).

Usage:
  python3 setup_week.py <week_dir> <games.json>

games.json shape:
[
  {"game_id": "bills-jets-2026-wk3", "matchup": "Buffalo Bills @ New York Jets",
   "week": "Week 3", "kickoff": "2026-09-21T17:00:00Z"},
  ...
]
"""
import argparse
import json
import os

from lib import save_game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("week_dir")
    ap.add_argument("games_json", help="Path to a JSON file, or '-' to read JSON from stdin")
    args = ap.parse_args()

    os.makedirs(args.week_dir, exist_ok=True)

    if args.games_json == "-":
        import sys
        games = json.load(sys.stdin)
    else:
        with open(args.games_json) as f:
            games = json.load(f)

    created, skipped = [], []
    for g in games:
        path = os.path.join(args.week_dir, f"{g['game_id']}.json")
        if os.path.exists(path):
            skipped.append(g["game_id"])
            continue
        record = {
            "game_id": g["game_id"],
            "matchup": g["matchup"],
            "week": g["week"],
            "kickoff": g["kickoff"],
            "opening_line": None,
            "snapshots": [],
            "research": {
                "last_run": None,
                "spread_lean": None,
                "total_lean": None,
                "moneyline_lean": None,
                "confidence": None,
                "top_signals": [],
            },
            "triggers": {
                "book_vs_sharp_gap": False,
                "key_number_crossed": False,
                "steam_no_news": False,
            },
            "status": "active",
        }
        save_game(path, record)
        created.append(g["game_id"])

    print(json.dumps({"created": created, "skipped_existing": skipped}))


if __name__ == "__main__":
    main()
