#!/usr/bin/env python3
"""Log a game's final score. The only write path for a game's final_score
and "final" status, and for the Elo/scoring-history update that final score
feeds into data/team_ratings.json -- mirrors update_snapshot.py's role for
price snapshots. Never hand-edit a game's final_score or the ratings file.

Usage:
  python3 log_result.py <game_file.json> <ratings_file.json> --home-score N --away-score N

Refuses to double-log: if the game's status is already "final", this is a
no-op (prints "skipped": true) unless --force is passed -- re-applying the
Elo update would double-count that game.

Also reads the game's own `injury_adjustments` block (if apply_injury_adjustment.py
ever ran against this game) and passes each side's flag/reason into
apply_result() -- this is what lets ratings.py's ppg() later exclude
this specific game from a team's rolling scoring average while the
injury was live, then automatically fold it back in once enough clean
games replace it. FLAG_THRESHOLD_PTS is the bar for "significant" here:
below it, a team's own injury_adjustments total is treated as too minor
to flag (a single questionable role player, say), not because it isn't
real, just because it's not worth diluting the sample over.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, save_game, normalize_team_name
from ratings import load_ratings, save_ratings, apply_result

FLAG_THRESHOLD_PTS = 2.0  # a side's total injury_adjustments pts at or beyond
                          # this magnitude is "significant" enough to flag that
                          # game in the team's rolling history (see module docstring)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("ratings_file")
    ap.add_argument("--home-score", type=int, required=True)
    ap.add_argument("--away-score", type=int, required=True)
    ap.add_argument("--force", action="store_true", help="re-log and re-apply the Elo update even if already final")
    args = ap.parse_args()

    game = load_game(args.game_file)
    if game.get("status") == "final" and not args.force:
        print(json.dumps({"game_id": game["game_id"], "skipped": True, "reason": "already logged final"}))
        return

    # normalize_team_name strips a parenthetical venue qualifier (e.g. "Los
    # Angeles Rams (Melbourne)") so an international "home team" game folds
    # back onto the real 32-team franchise instead of silently creating a
    # 33rd phantom team in the ratings file with its own blank Elo seed --
    # caught live 2026-09-14 on exactly that game, after it had already
    # happened once with no ratings-file update ever applying to the real
    # team. The game file itself and the away/home split used for anything
    # else keep the raw matchup string; only the ratings lookup is affected.
    away_raw, home_raw = [s.strip() for s in game["matchup"].split(" @ ")]
    away_name, home_name = normalize_team_name(away_raw), normalize_team_name(home_raw)

    inj = game.get("injury_adjustments") or {}
    home_inj = inj.get("home") or {}
    away_inj = inj.get("away") or {}
    home_inj_pts = home_inj.get("total_pts", 0.0) or 0.0
    away_inj_pts = away_inj.get("total_pts", 0.0) or 0.0
    home_injury_flag = abs(home_inj_pts) >= FLAG_THRESHOLD_PTS
    away_injury_flag = abs(away_inj_pts) >= FLAG_THRESHOLD_PTS
    # Short reason string for injury_flag_notes -- named players + pts, not
    # just the bare total, so a later look at a team's history explains
    # itself instead of showing an unexplained True.
    home_injury_note = None
    if home_injury_flag:
        players = ", ".join(f"{it['player']} ({it['pts']:+.1f})" for it in (home_inj.get("items") or []))
        home_injury_note = f"{players or 'flagged'} [{home_inj_pts:+.1f} pts total]"
    away_injury_note = None
    if away_injury_flag:
        players = ", ".join(f"{it['player']} ({it['pts']:+.1f})" for it in (away_inj.get("items") or []))
        away_injury_note = f"{players or 'flagged'} [{away_inj_pts:+.1f} pts total]"

    ratings = load_ratings(args.ratings_file)
    summary = apply_result(
        ratings, home_name, away_name, args.home_score, args.away_score,
        home_injury_flag=home_injury_flag, home_injury_note=home_injury_note,
        away_injury_flag=away_injury_flag, away_injury_note=away_injury_note,
    )
    save_ratings(args.ratings_file, ratings)

    game["final_score"] = {"home": args.home_score, "away": args.away_score}
    game["status"] = "final"
    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"], "skipped": False, "elo_update": summary,
        "home_injury_flag": home_injury_flag, "away_injury_flag": away_injury_flag,
    }))


if __name__ == "__main__":
    main()
