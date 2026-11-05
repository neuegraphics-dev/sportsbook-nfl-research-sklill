#!/usr/bin/env python3
"""Create or re-seed data/team_ratings.json -- the Elo + scoring-history
file scripts/simulate_game.py reads and scripts/log_result.py updates after
every logged final score.

Usage:
  python3 seed_ratings.py <ratings_out.json>
      Flat seed: every one of the 32 teams starts at Elo 1500 with no
      scoring history. Honest default when there's no trustworthy external
      seed yet -- the model will say almost nothing useful about a game
      between two flat-seeded teams (see simulate_game.py's "gamesSeen"
      note), and it self-corrects as real results get logged.

  python3 seed_ratings.py <ratings_out.json> --win-totals wt.json --source "label"
      Seed from a season win-total line per team ({"Team Name": 9.5, ...}
      in wt.json) via the standard win-total-to-Elo conversion in
      ratings.seed_from_win_totals. Only as good as the win-total numbers
      you hand it -- cross-check whatever source you pull them from; two
      sites checked live on 2026-09-06 for the same 2026 season disagreed
      by 2-3 wins on several teams (Cardinals 3.5 vs 8.5, Bills 10.5 vs
      12.5, Rams 11.5 vs 9.5, among others), which is a big enough spread
      to bias the model for weeks if you pick the wrong one blind.

  python3 seed_ratings.py <ratings_out.json> --prior-season ps.json --source "label" [--regression 0.333]
      Seed from last season's final record + point differential per team
      ({"Team Name": {"wins": 14, "losses": 3, "ties": 0, "point_diff": 170},
      ...} in ps.json) via ratings.seed_from_prior_season -- point
      differential converted to Elo, then regressed toward 1500 by
      `--regression` (default 1/3, matching 538's own NFL offseason
      carryover). Recommended over --win-totals when a real completed
      season is available: it's a single unambiguous number (final score
      differential), not a futures market that different books/sites can
      quote differently.

      Each team's record in ps.json can optionally also carry
      "points_for"/"points_against" (seeds the total-projection scoring
      history from prior-season PPG) and, on top of that,
      "off_plays"/"def_plays_faced" (upgrades the scoring seed from raw
      points-per-game to points-per-play efficiency, pace-neutralized to
      the league-average snap count -- see seed_from_prior_season's
      docstring for the full reasoning). All four extra fields are
      optional and independent: missing PF/PA falls back to an empty
      scoring history same as a bare win/loss/point-diff file; having
      PF/PA but not the play counts falls back to the plain PPG method.

Refuses to overwrite a ratings file that already has logged games
(games_played > 0 for any team) unless --force is passed -- reseeding
after real results have been logged would silently throw away the
season's actual Elo history.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from ratings import seed_flat, seed_from_win_totals, seed_from_prior_season, save_ratings, load_ratings
from build_dashboard import ABBR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ratings_out")
    ap.add_argument("--win-totals", help="path to a JSON file of {team_name: win_total}")
    ap.add_argument("--prior-season", help="path to a JSON file of {team_name: {wins, losses, ties, point_diff}}")
    ap.add_argument("--regression", type=float, default=1.0 / 3.0, help="--prior-season only: fraction to regress toward 1500 (default 1/3)")
    ap.add_argument("--source", default="unspecified", help="short label for where --win-totals/--prior-season came from")
    ap.add_argument("--force", action="store_true", help="overwrite even if games have already been logged")
    args = ap.parse_args()

    if args.win_totals and args.prior_season:
        print(json.dumps({"error": "pass only one of --win-totals or --prior-season"}))
        sys.exit(1)

    if os.path.exists(args.ratings_out) and not args.force:
        existing = load_ratings(args.ratings_out)
        played = sum(t.get("games_played", 0) for t in existing.get("teams", {}).values())
        if played > 0:
            print(json.dumps({
                "error": f"{args.ratings_out} already has {played} logged game-team results -- pass --force to reseed and discard them",
            }))
            sys.exit(1)

    team_names = list(ABBR.keys())

    if args.win_totals:
        with open(args.win_totals) as f:
            win_totals = json.load(f)
        missing = [t for t in team_names if t not in win_totals]
        if missing:
            print(json.dumps({"error": f"--win-totals is missing {len(missing)} team(s)", "missing": missing}))
            sys.exit(1)
        ratings = seed_from_win_totals(win_totals, args.source)
    elif args.prior_season:
        with open(args.prior_season) as f:
            prior = json.load(f)
        missing = [t for t in team_names if t not in prior]
        if missing:
            print(json.dumps({"error": f"--prior-season is missing {len(missing)} team(s)", "missing": missing}))
            sys.exit(1)
        ratings = seed_from_prior_season(prior, args.source, args.regression)
    else:
        ratings = seed_flat(team_names)

    save_ratings(args.ratings_out, ratings)
    print(json.dumps({
        "ratings_out": args.ratings_out,
        "teams": len(ratings["teams"]),
        "seed_source": ratings["meta"]["seed_source"],
    }))


if __name__ == "__main__":
    main()
