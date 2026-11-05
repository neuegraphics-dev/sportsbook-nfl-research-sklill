#!/usr/bin/env python3
"""Write a prop pick's `season_log` -- this player's actual game-by-game
stat line this season (not just games this project happened to grade),
sourced from a real play-by-play/box-score stats site (statmuse.com used
2026-10-01), then compute a `backtest` summary: how many of THOSE games
would have cleared the pick's own current opening_line and lean, had this
exact number been the line every week.

This is a different, complementary stat to `capture_prop_result.py`'s
official per-week grading (results/props_results.json): that one grades
a pick against the REAL line that was actually offered that week (and
only exists once this project has captured and graded a pick on that
player/market before). This one answers "if today's line had applied all
season, how often would it have hit" using the player's real production,
even for a player/market this project has never captured before -- which
is most of them in any given week. Keep both: `backtest` is a volume
proxy from real production, `hist`/official grading is the authoritative
record of what this project actually called and how it did.

Usage:
  python3 apply_prop_backtest.py <game_file.json> --player "Jerry Jeudy" \
    --market rec_yards \
    --games '[{"week":1,"actual":26},{"week":2,"actual":0},{"week":3,"actual":0}]' \
    --source "statmuse.com"

`--games` is this season's real games for that player in that stat, oldest
first, EXCLUDING the upcoming game this pick is actually for (only games
already played). Push (actual == line) counts in total but not hits/misses,
matching report_props.py's convention.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, save_game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--player", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--games", required=True, help='JSON list of {"week":N,"actual":X}, oldest first')
    ap.add_argument("--source", required=True)
    args = ap.parse_args()

    game = load_game(args.game_file)
    props = game.get("props") or {}
    picks = props.get("picks") or []
    target = None
    for p in picks:
        if p.get("player", "").strip().lower() == args.player.strip().lower() and p.get("market") == args.market:
            target = p
            break
    if target is None:
        print(json.dumps({"error": f"no captured pick found for ({args.player!r}, {args.market!r}) in {args.game_file}"}))
        sys.exit(1)

    try:
        games = json.loads(args.games)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"--games is not valid JSON: {e}"}))
        sys.exit(1)
    if not isinstance(games, list) or not games:
        print(json.dumps({"error": "--games must be a non-empty JSON list"}))
        sys.exit(1)

    line = target["opening_line"]
    lean = target["lean"]
    log = []
    hits = misses = pushes = 0
    for g in games:
        actual = g["actual"]
        if actual == line:
            result = "push"
            pushes += 1
        elif (actual > line) == (lean == "over"):
            result = "hit"
            hits += 1
        else:
            result = "miss"
            misses += 1
        log.append({"week": g["week"], "actual": actual, "result": result})

    target["season_log"] = log
    target["season_log_source"] = args.source
    target["backtest"] = {
        "hits": hits, "misses": misses, "pushes": pushes, "total": hits + misses,
        "pct": round(hits / (hits + misses) * 100) if (hits + misses) else None,
        "line": line, "lean": lean,
    }
    props["picks"] = picks
    game["props"] = props
    save_game(args.game_file, game)
    print(json.dumps({"player": args.player, "market": args.market, "backtest": target["backtest"]}))


if __name__ == "__main__":
    main()
