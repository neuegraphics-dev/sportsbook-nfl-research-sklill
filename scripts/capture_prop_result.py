#!/usr/bin/env python3
"""Build (or refresh) one player-prop's entry in the props-results-comparison
store. This is the only write path for results/props_results.json -- same
rule as every other store this skill maintains (see capture_result.py).

Why this exists: player props from game_evaluation/props.picks were being
graded by hand every week -- re-reading game files, re-deriving which prop
belonged to which game, pasting a table into chat. That's how a phantom
prop (Matthew Golden, Bijan Robinson -- never existed in any game file) and
a wrong-game attribution (Rashee Rice graded against the wrong final stat
line entirely) both slipped into a report on 2026-09-27. This script reads
the prop straight out of the game file's own props.picks -- the one place
it's actually defined -- so a mis-remembered player/market/game combo can't
be entered at all: --player/--market must match an entry that exists, or
the script refuses.

Usage:
  python3 capture_prop_result.py <game_file.json> <props_store.json> \
    --player "Ja'Marr Chase" --market rec_yards --actual 98 \
    [--source "cbssports.com box score"]

Grading: reads the pick's own `lean` (over/under) and `opening_line` off
the game file -- never pass the line by hand, so the store can't drift
from what was actually recommended. over: actual > line -> hit, actual <
line -> miss, actual == line -> push. under is the mirror image.
receptions counts as a plain numeric market, same rule.

Refuses to run against a game that isn't finalized yet (status != "final")
-- run log_result.py first, same as capture_result.py. This also protects
against grading a prop from a game that's still live (e.g. grading Kyren
Williams mid-Rams-Broncos), which is exactly the kind of premature-grade
mistake this script exists to rule out structurally.

Idempotent / always safe to re-run: upserts by (gameId, player, market), so
correcting a source number just replaces that one entry.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from lib import player_key  # Phase 3 alias map
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc


def find_pick(game, player, market):
    picks = (game.get("props") or {}).get("picks") or []
    for p in picks:
        if player_key(p.get("player")) == player_key(player) and p.get("market") == market:
            return p
    return None


def grade(pick, actual):
    line = pick.get("opening_line")
    lean = (pick.get("lean") or "").lower()
    if line is None or lean not in ("over", "under"):
        return None
    if actual == line:
        return "push"
    if lean == "over":
        return "hit" if actual > line else "miss"
    return "hit" if actual < line else "miss"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("props_store")
    ap.add_argument("--player", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--actual", type=float, required=True)
    ap.add_argument("--source", default=None, help="Where the stat was confirmed, e.g. a box-score URL or article")
    ap.add_argument("--force", action="store_true", help="grade even if the game isn't marked final yet")
    ap.add_argument("--void", action="store_true", help="void the pick (withdrawn/cancelled): excluded from hit rate, never counted as hit or miss")
    args = ap.parse_args()

    game = load_game(args.game_file)
    if game.get("status") != "final" and not args.force:
        print(json.dumps({
            "error": "game is not finalized yet -- run log_result.py first, or pass --force if you've "
                     "independently confirmed this player's stat line is final (e.g. he left the game early)",
            "game_id": game.get("game_id"),
        }))
        sys.exit(1)

    pick = find_pick(game, args.player, args.market)
    if pick is None:
        available = [(p.get("player"), p.get("market")) for p in (game.get("props") or {}).get("picks") or []]
        print(json.dumps({
            "error": f"no prop pick for player={args.player!r} market={args.market!r} in {args.game_file}",
            "available_picks_in_this_game": available,
        }))
        sys.exit(1)

    result = "void" if args.void else grade(pick, args.actual)
    if result is None:
        print(json.dumps({"error": "pick is missing opening_line or a valid over/under lean", "pick": pick}))
        sys.exit(1)

    away_name, home_name = [s.strip() for s in game["matchup"].split(" @ ")]
    entry = {
        "gameId": game["game_id"],
        "week": game.get("week"),
        "matchup": game["matchup"],
        "awayTeam": away_name,
        "homeTeam": home_name,
        "kickoff": game["kickoff"],
        "player": pick.get("player"),
        "position": pick.get("position"),
        "team": pick.get("team"),
        "market": pick.get("market"),
        "lean": pick.get("lean"),
        "line": pick.get("opening_line"),
        "confidence": pick.get("confidence"),
        "reasoning": pick.get("reasoning"),
        "actual": args.actual,
        "result": result,
        "source": args.source,
        "capturedAt": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    store = {"props": []}
    if os.path.exists(args.props_store):
        with open(args.props_store) as f:
            store = json.load(f)
        store.setdefault("props", [])

    store["props"] = [
        p for p in store["props"]
        if not (p.get("gameId") == entry["gameId"] and player_key(p.get("player")) == player_key(entry["player"]) and p.get("market") == entry["market"])
    ] + [entry]

    tmp = args.props_store + ".tmp"
    os.makedirs(os.path.dirname(args.props_store) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(store, f, indent=2)
        f.write("\n")
    os.replace(tmp, args.props_store)

    print(json.dumps({"result": result, "entry": entry}))


if __name__ == "__main__":
    main()
