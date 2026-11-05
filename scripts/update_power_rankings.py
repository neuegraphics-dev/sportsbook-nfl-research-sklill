#!/usr/bin/env python3
"""Power Rankings -- the Line Watch dashboard's Power Rankings tab shows a
personal, manually-ordered team list (dragged/dropped in the browser, saved
instantly to that browser's localStorage) alongside an auto-computed
win/loss streak per team (see lib.compute_team_streaks). The ORDER itself
is intentionally never touched by anything automated -- it's Michael's own
ranking, meant to sit exactly where he leaves it until he deliberately
drags a team -- so this script is the only write path for
model/power_rankings.json, mirroring update_snapshot.py's role for a
game's JSON, and it only ever writes when explicitly asked to:

  --seed              Create the file if (and only if) it doesn't exist
                       yet, ordered by current Elo (model/team_ratings.json,
                       descending) as a reasonable starting point. A no-op
                       if the file already exists -- never re-seeds over a
                       manual order. build_dashboard.py calls this same
                       logic automatically the very first time it runs
                       against a folder with no rankings file yet, so this
                       flag is mostly for re-seeding deliberately (below)
                       or for seeding ahead of time without a full
                       dashboard build.
  --force-reseed       Same as --seed but overwrites an existing file with
                       a fresh Elo-ordered seed. Use deliberately (e.g. a
                       full offseason reset) -- this discards whatever
                       manual order was there.
  --set-order "Team A, Team B, ..."
                       Explicit full reorder. This is the write path for
                       the dashboard's "Copy order to save permanently"
                       button: that button copies a ready-to-paste chat
                       message: paste it here, or to Claude directly, to
                       make a browser-only localStorage order permanent so
                       it survives a cleared cache and seeds correctly the
                       next time build_dashboard.py runs against a fresh
                       browser. Must name exactly the 32 tracked teams,
                       once each (a pure permutation) -- refuses rather
                       than silently dropping or duplicating a team on a
                       typo.

Usage:
  python3 update_power_rankings.py model/power_rankings.json --seed
  python3 update_power_rankings.py model/power_rankings.json --set-order "Buffalo Bills, Baltimore Ravens, ..."
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import now_utc, TEAM_NAMES
from ratings import load_ratings


def seed_order_from_elo(ratings_path):
    ratings = load_ratings(ratings_path)
    teams = ratings.get("teams", {})

    def elo_of(name):
        return teams.get(name, {}).get("elo", 1500.0)

    return sorted(TEAM_NAMES, key=elo_of, reverse=True)


def save_rankings(path, order, seeded_from):
    doc = {
        "order": order,
        "seeded_from": seeded_from,
        "last_updated": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rankings_file")
    ap.add_argument("--ratings-file", default="model/team_ratings.json")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--force-reseed", action="store_true")
    ap.add_argument("--set-order", default=None)
    args = ap.parse_args()

    if args.set_order:
        names = [t.strip() for t in args.set_order.split(",") if t.strip()]
        given = set(names)
        expected = set(TEAM_NAMES)
        if len(names) != len(TEAM_NAMES) or given != expected:
            missing = expected - given
            extra = given - expected
            dupes = sorted({n for n in names if names.count(n) > 1})
            print(json.dumps({
                "error": "order must name exactly the 32 tracked teams, once each",
                "missing": sorted(missing),
                "unexpected": sorted(extra),
                "duplicated": dupes,
            }))
            sys.exit(1)
        doc = save_rankings(args.rankings_file, names, "manual")
        print(json.dumps({"skipped": False, "action": "set-order", **doc}))
        return

    exists = os.path.exists(args.rankings_file)
    if exists and not args.force_reseed:
        print(json.dumps({"skipped": True, "reason": "already exists, not re-seeding (pass --force-reseed to override)"}))
        return

    order = seed_order_from_elo(args.ratings_file)
    doc = save_rankings(args.rankings_file, order, "elo_seed")
    print(json.dumps({"skipped": False, "action": "reseed" if exists else "seed", **doc}))


if __name__ == "__main__":
    main()
