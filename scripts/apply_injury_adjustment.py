#!/usr/bin/env python3
"""Injury-adjust a game's projected scoring means before simulate_game.py
runs. The only write path for a game's `injury_adjustments` block --
mirrors update_snapshot.py's role for price snapshots and log_result.py's
for final scores: never hand-edit this block.

Why this exists: simulate_game.py's projection is built entirely from a
team's Elo rating and its rolling scored/allowed average (see that
script's docstring) -- it has no idea a starting QB is out, and nothing
else in the pipeline feeds it one. The `research` block's `top_signals`
already narrates injury news in prose for the report/dashboard reader, but
prose never touches the simulation's actual numbers. This script is the
structured, point-valued counterpart: it records which players are out
(or meaningfully limited) for each side, at what point cost, and lets
simulate_game.py shift that side's projected scoring mean accordingly --
so the model's own spread/total lean and the human-written research lean
can be compared honestly instead of silently disagreeing (see the
Eagles-Bears case, 2026-09-22: research flagged Caleb Williams as likely
out, but the sim still had the Bears within half a point of a coinflip,
because Elo has no way to know that).

Per-item point values are a judgment call made during research (informed
by how big the talent/scheme drop-off actually looks -- a starting QB to a
clearly worse backup is usually a bigger hit than a WR2, which is usually
bigger than a rotational lineman), not something this script computes.
Treat them the same way a spread_lean or confidence level is treated
elsewhere in this project: an honest, documented estimate, never dressed
up as more precise than it is. A conservative starting point, worth
keeping in mind while assigning pts (not a hard rule):
  - Starting QB out, clearly worse backup: -4 to -7 pts
  - Starting QB out, backup playing well / minimal drop-off: -1.5 to -3 pts
  - Alpha skill player (true WR1/RB1, no real complementary backup): -1 to -2.5 pts
  - Starting O-line anchor (LT, C) or top pass rusher: -1 to -2 pts
  - Secondary starter / role player: -0.5 to -1 pt
  - A player who is questionable/limited but expected to play: 0, or a small
    fraction of the above if genuinely playing hurt -- don't price out a
    player who is probably going to suit up.

Depth-stacking: when 2+ players at the SAME position on the SAME side are
logged with status "out", this script automatically adds an extra penalty
on top of each player's own individual pts -- losing two starters at one
position exhausts real depth (down to a 3rd string or practice-squad
level player), which tends to cost more than the sum of two individually
"solid backup" swaps. STACK_PENALTY_PER_EXTRA per additional "out" beyond
the first at that position, capped at STACK_PENALTY_MAX per position
group. "Questionable"/"doubtful" players don't count toward this --
stacking only fires on confirmed-out depth, not maybes.

Usage:
  python3 apply_injury_adjustment.py <game_file.json> \
    --home-items '[{"position":"QB","player":"Caleb Williams","status":"out","pts":-6.0,"note":"Hamstring, HC called him week-to-week and likely to miss; Bagent/Keenum to start"}]' \
    --away-items '[]'

Pass '[]' (not the flag omitted) to explicitly clear a side's injuries once
they're no longer relevant (player activated, or a fresh research pass
finds nothing notable) -- same "never omit, always state explicitly"
convention as update_snapshot.py's book flags. Omit a side's flag entirely
to leave that side's existing block untouched (e.g. updating only the side
that changed since the last pass).

Every item requires position, player, status (one of out/doubtful/
questionable), pts (a number -- negative hurts that side, 0 is allowed for
a purely informational entry), and note (why -- required, since these are
judgment calls that need to be checkable later, not black-box numbers).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc, save_game

VALID_STATUSES = ("out", "doubtful", "questionable")
STACK_PENALTY_PER_EXTRA = 0.75  # extra pts per additional confirmed-"out" player
                                 # at the same position, beyond the first
STACK_PENALTY_MAX = 2.5         # cap on the stacking penalty for one position group


# Phase 5 (2026-11-04): defensive injuries no longer lower the injured team's OWN
# scoring (the old, wrong-direction behavior the skill documented). Items at a
# defensive position are stored as defense_pts and simulate_game.py adds them to
# the OPPONENT's projected points instead. pts are still entered the same way
# (negative = hurts the injured team). total_pts keeps the old all-in number so
# older readers don't break.
UNITS = {
    "secondary": {"CB", "S", "FS", "SS", "DB", "NB", "NICKEL"},
    "front": {"DT", "DE", "EDGE", "NT", "DL", "IDL"},
    "linebackers": {"LB", "OLB", "ILB", "MLB"},
    "offensive line": {"LT", "LG", "C", "RG", "RT", "OL", "T", "G", "OT", "OG"},
}
DEFENSE = UNITS["secondary"] | UNITS["front"] | UNITS["linebackers"]
WATCH = {"secondary": "passing/receiving OVER", "front": "rushing OVER", "linebackers": "rushing and TE receiving OVER",
         "offensive line": "own QB/RB UNDER (pressure, fewer clean runs)"}


def unit_of(pos):
    p = (pos or "").upper().strip()
    for u, members in UNITS.items():
        if p in members:
            return u
    return None


def is_def(pos):
    return (pos or "").upper().strip() in DEFENSE


def clusters(items, side):
    """2+ confirmed-out starters in one unit -> a cluster flag."""
    out = []
    by_unit = {}
    for it in items:
        u = unit_of(it["position"])
        if u and it["status"] == "out":
            by_unit.setdefault(u, []).append(it)
    for u, its in by_unit.items():
        if len(its) < 2:
            continue
        same_pos = max(sum(1 for j in its if j["position"].upper() == i["position"].upper()) for i in its)
        pts = abs(sum(i["pts"] for i in its)) + (min(STACK_PENALTY_PER_EXTRA * (same_pos - 1), STACK_PENALTY_MAX) if same_pos > 1 else 0)
        out.append({"team_side": side, "unit": u, "count": len(its), "players": [i["player"] for i in its],
                    "opp_pts" if u != "offensive line" else "own_pts": round(pts, 2), "watch": WATCH[u],
                    "defensive": u != "offensive line"})
    for c in out:
        c.setdefault("opp_pts", 0.0)
    return out


def validate_items(items):
    if not isinstance(items, list):
        raise ValueError("items must be a JSON list (use [] for none)")
    for i, item in enumerate(items):
        for field in ("position", "player", "status", "pts", "note"):
            if field not in item:
                raise ValueError(f"item {i} missing required field '{field}': {item}")
        if item["status"] not in VALID_STATUSES:
            raise ValueError(f"item {i} has invalid status '{item['status']}' -- must be one of {VALID_STATUSES}")
        if not isinstance(item["pts"], (int, float)):
            raise ValueError(f"item {i} pts must be a number, got {item['pts']!r}")
        if not item["note"].strip():
            raise ValueError(f"item {i} note must be non-empty -- document the reasoning behind the pts value")
        if not item["player"].strip() or not item["position"].strip():
            raise ValueError(f"item {i} player/position must be non-empty")


def compute_side(items):
    """Returns (total_pts, stacking_notes) for one side's items list."""
    items_total = round(sum(item["pts"] for item in items), 2)

    by_position = {}
    for item in items:
        if item["status"] == "out":
            by_position.setdefault(item["position"], []).append(item["player"])

    stacking_pts = 0.0
    stacking_notes = []
    for position, players in by_position.items():
        if len(players) < 2:
            continue
        penalty = min(STACK_PENALTY_PER_EXTRA * (len(players) - 1), STACK_PENALTY_MAX)
        stacking_pts -= penalty
        stacking_notes.append(
            f"{position} depth: {len(players)} confirmed out ({', '.join(players)}) "
            f"-- extra -{penalty:.2f} pts for exhausted depth at that position, "
            f"on top of each player's own individual pts"
        )

    total_pts = round(items_total + stacking_pts, 2)
    return total_pts, stacking_notes


def build_side_block(items):
    validate_items(items)
    total_pts, stacking_notes = compute_side(items)
    off_items = [i for i in items if not is_def(i["position"])]
    def_items = [i for i in items if is_def(i["position"])]
    off_pts, _ = compute_side(off_items)
    def_pts, _ = compute_side(def_items)
    return {
        "offense_pts": off_pts,
        "defense_pts": def_pts,
        "items": items,
        "items_total_pts": round(sum(item["pts"] for item in items), 2),
        "stacking_notes": stacking_notes,
        "total_pts": total_pts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--home-items", default=None, help="JSON list, or '[]' to clear. Omit to leave home side untouched.")
    ap.add_argument("--away-items", default=None, help="JSON list, or '[]' to clear. Omit to leave away side untouched.")
    args = ap.parse_args()

    if args.home_items is None and args.away_items is None:
        print(json.dumps({"error": "pass --home-items and/or --away-items (use '[]' to explicitly clear a side)"}))
        sys.exit(1)

    game = load_game(args.game_file)
    block = game.get("injury_adjustments") or {
        "last_updated": None,
        "home": {"items": [], "items_total_pts": 0.0, "stacking_notes": [], "total_pts": 0.0},
        "away": {"items": [], "items_total_pts": 0.0, "stacking_notes": [], "total_pts": 0.0},
    }

    try:
        if args.home_items is not None:
            block["home"] = build_side_block(json.loads(args.home_items))
        if args.away_items is not None:
            block["away"] = build_side_block(json.loads(args.away_items))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    block["def_clusters"] = clusters(block["home"].get("items", []), "home") + clusters(block["away"].get("items", []), "away")
    block["last_updated"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    game["injury_adjustments"] = block
    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"],
        "home_total_pts": block["home"]["total_pts"],
        "away_total_pts": block["away"]["total_pts"],
        "home_stacking_notes": block["home"]["stacking_notes"],
        "away_stacking_notes": block["away"]["stacking_notes"],
        "clusters": block["def_clusters"],
    }))


if __name__ == "__main__":
    main()
