#!/usr/bin/env python3
"""Write a game's `wagertalk_angle` field -- the historical situational
ATS-trend angle(s) that match this game's real, current situation. The
only write path for this field: formalizes what earlier weeks did with
hand-typed one-off scripts (_add_wagertalk_angles.py, _add_wagertalk_angles_wk3.py)
into a reusable script, same "one write path per kind of derived data"
convention as apply_injury_adjustment.py / apply_props.py / apply_game_evaluation.py.

Never guess a match: check situational_systems.py for the system's
definition and required facts (needs_lookahead / needs_lastweek /
needs_bye), confirm those facts for THIS game via real research (this
week's schedule, last week's result, next week's opponent), then call
this script. Only write a system whose historical pct clears ~50% --
below that there's no edge to report.

Checkbox model (added 2026-09-25 per Michael): the dashboard renders each
contributing system as its OWN checked line with its OWN historical hit
rate, not just one merged paragraph -- so a reader can see exactly which
independent systems are agreeing on this side, and how much each one is
worth on its own. Pass --checks as a JSON list of individually-confirmed
systems; this script derives the combined summary (pick/pct/system/why/
stack_count) from them so the two never drift out of sync.

Usage (checkbox model -- preferred):
  python3 apply_wagertalk_angle.py <game_file.json> \
    --pick "Falcons +6" --pick-team "Atlanta Falcons" \
    --checks '[
      {"key": "winless_road_vs_home_favorite", "pct": 79,
       "why": "Atlanta is 0-2, on the road, and off a loss facing a home favorite."},
      {"key": "road_dog_window_02", "pct": 66,
       "why": "Also fits the broader 0-2-road-dog window on its own."}
    ]'

Each check's `key` should match an entry in situational_systems.py (its
`mechanism`/`source` are looked up automatically for the dashboard tooltip)
-- pass `label` instead of `key` for a one-off system not in that library.
`pct`/`why` in each check always come from you (the library's pct is the
long-run baseline; a per-game `why` documents the actual match).

Legacy single-angle usage (still supported, no checks list):
  python3 apply_wagertalk_angle.py <game_file.json> \
    --pick "Falcons +6" --pick-team "Atlanta Falcons" --pct 79 \
    --system "winless-road-off-a-loss vs. home favorite" \
    --why "Atlanta is 0-2, on the road, and off a loss -- this shape covers ~79% ATS." \
    --stack-count 1

Pass --clear to remove an existing wagertalk_angle (e.g. the situation
that produced it no longer applies).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc, save_game

try:
    from situational_systems import SYSTEMS
except ImportError:
    SYSTEMS = {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("game_file")
    p.add_argument("--pick")
    p.add_argument("--pick-team")
    p.add_argument("--pct", type=float)
    p.add_argument("--system")
    p.add_argument("--why")
    p.add_argument("--stack-count", type=int)
    p.add_argument("--checks")
    p.add_argument("--clear", action="store_true")
    args = p.parse_args()

    game = load_game(args.game_file)

    if args.clear:
        game.pop("wagertalk_angle", None)
        save_game(args.game_file, game)
        print(json.dumps({"game_id": game.get("game_id"), "cleared": True}))
        return

    if not args.pick:
        print(json.dumps({"error": "--pick is required"}))
        sys.exit(1)

    checks = None
    if args.checks:
        try:
            checks = json.loads(args.checks)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"--checks is not valid JSON: {e}"}))
            sys.exit(1)
        if not isinstance(checks, list) or not checks:
            print(json.dumps({"error": "--checks must be a non-empty JSON list"}))
            sys.exit(1)
        resolved = []
        for c in checks:
            key = c.get("key")
            label = c.get("label") or (SYSTEMS.get(key, {}).get("category") and key.replace("_", " ")) or key
            pct = c.get("pct")
            why = c.get("why")
            if pct is None or why is None:
                print(json.dumps({"error": f"each check needs pct and why (got {c!r})"}))
                sys.exit(1)
            entry = {
                "key": key,
                "label": label,
                "pct": pct,
                "why": why,
                "applies": True,
            }
            lib_entry = SYSTEMS.get(key)
            if lib_entry:
                entry["mechanism"] = lib_entry.get("mechanism")
                entry["source"] = lib_entry.get("source")
                entry["baseline_pct"] = lib_entry.get("pct")
            resolved.append(entry)
        checks = resolved

        # Derive the combined summary from the checks so it can never drift
        # out of sync with what's actually listed.
        pct = max(c["pct"] for c in checks)
        stack_count = len(checks)
        system = " + ".join((c["label"] or c["key"] or "system") for c in checks)
        why = " ".join(c["why"] for c in checks)
    else:
        pct = args.pct
        stack_count = args.stack_count or 1
        system = args.system
        why = args.why
        missing = [name for name, val in (("pct", pct), ("system", system), ("why", why)) if val is None]
        if missing:
            print(json.dumps({"error": f"missing required flags (or pass --checks instead): {missing}"}))
            sys.exit(1)

    if pct <= 50:
        print(json.dumps({"error": f"pct {pct} does not clear 50% -- no edge to report, omit this angle"}))
        sys.exit(1)

    angle = {
        "pick": args.pick,
        "pick_team": args.pick_team,
        "pct": pct,
        "system": system,
        "why": why,
        "stack_count": stack_count,
        "applied_at": now_utc().isoformat(),
    }
    if checks:
        angle["checks"] = checks

    game["wagertalk_angle"] = angle
    save_game(args.game_file, game)
    print(json.dumps({"game_id": game.get("game_id"), "wagertalk_angle": angle}))


if __name__ == "__main__":
    main()
