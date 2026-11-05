#!/usr/bin/env python3
"""Build (or refresh) one game's entry in the results-comparison store, once
that game has been finalized via log_result.py. This is the only write path
for results/results.json -- never hand-edit it, same rule as every other
store this skill maintains.

Run AFTER log_result.py, not instead of it: log_result.py is what actually
updates team Elo/scoring history and flips the game's status to "final";
this script only reads that already-finalized game file and derives a
comparison record from it -- the closing line, whatever simulate_game.py's
last pregame run projected, the actual final score, and how far off the
projection and the market both were. Splitting this into its own script
(rather than folding it into log_result.py) keeps "one write path per kind
of derived data" -- log_result.py owns Elo/status, this owns the comparison
record, exactly like update_snapshot.py vs. generate_chart.py.

Usage:
  python3 capture_result.py <game_file.json> <results_store.json> \
    [--home-yards N --away-yards N --home-plays N --away-plays N \
     --home-top MM:SS --away-top MM:SS --home-turnovers N --away-turnovers N \
     --source "cbssports.com gametracker"]

Box-score extras are optional and purely for display on the Results tab --
none of the grading math below depends on them. Pass whatever the cloud
session could actually confirm; omit anything it couldn't rather than
guessing (same honesty rule as everywhere else in this skill).

Idempotent / always safe to re-run: upserts by game_id, so re-running after
a score correction (or to attach box-score extras fetched in a later pass)
just replaces that one entry rather than duplicating it.

Refuses to run against a game that isn't finalized yet (status != "final"
or no final_score) -- run log_result.py first.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc


def spread_home_rel(book_line):
    return book_line.get("spread") if book_line else None


def total_of(book_line):
    return book_line.get("total") if book_line else None


def closing_line(game):
    """The last snapshot captured AT OR BEFORE kickoff -- not simply
    snapshots[-1], since a finalized game is excluded from the daily active
    slate going forward (build_dashboard.py's `status != "active"` filter)
    but nothing stops a stray manual snapshot from landing after kickoff in
    principle. Falls back to the plain last snapshot if none exist before
    kickoff (should only happen for a game whose very first snapshot was
    somehow captured post-kickoff), and flags that fallback so the template
    doesn't present it as a real closing line without comment."""
    from lib import parse_iso

    kickoff = parse_iso(game["kickoff"])
    snapshots = game.get("snapshots") or []
    pre_kickoff = [s for s in snapshots if parse_iso(s["captured_at"]) <= kickoff]
    if pre_kickoff:
        return pre_kickoff[-1], False
    if snapshots:
        return snapshots[-1], True
    return None, True


def grade_result(game, snap):
    home_score = game["final_score"]["home"]
    away_score = game["final_score"]["away"]
    actual_margin_home = home_score - away_score
    actual_total = home_score + away_score
    winner = "home" if home_score > away_score else ("away" if away_score > home_score else "tie")

    dk = (snap or {}).get("draftkings") or {}
    bm = (snap or {}).get("bookmaker_eu") or {}
    closing_spread = spread_home_rel(dk) if spread_home_rel(dk) is not None else spread_home_rel(bm)
    closing_total = total_of(dk) if total_of(dk) is not None else total_of(bm)
    closing_source = "draftkings" if spread_home_rel(dk) is not None else ("bookmaker_eu" if closing_spread is not None else None)

    ats_result = None
    market_margin_error = None
    if closing_spread is not None:
        adj = actual_margin_home + closing_spread  # same convention as simulate_game.py's simulate()
        ats_result = "home_covered" if adj > 0 else ("away_covered" if adj < 0 else "push")
        market_margin_error = round(actual_margin_home - (-closing_spread), 1)

    total_result = None
    market_total_error = None
    if closing_total is not None:
        if actual_total > closing_total:
            total_result = "over"
        elif actual_total < closing_total:
            total_result = "under"
        else:
            total_result = "push"
        market_total_error = round(actual_total - closing_total, 1)

    sim = game.get("simulation") or {}
    sim_present = bool(sim)
    sim_margin_error = None
    sim_total_error = None
    sim_straight_up_correct = None
    sim_ats_pick_correct = None
    sim_total_pick_correct = None
    win_prob_of_actual_winner = None

    if sim_present:
        elo_margin = sim.get("eloMarginHome")  # positive = home favored, i.e. projected home margin
        if elo_margin is not None:
            sim_margin_error = round(actual_margin_home - elo_margin, 1)
            if winner != "tie":
                sim_favored_home = elo_margin > 0
                sim_straight_up_correct = (sim_favored_home and winner == "home") or (
                    (not sim_favored_home) and winner == "away"
                )
        proj_total = sim.get("projTotal")
        if proj_total is not None:
            sim_total_error = round(actual_total - proj_total, 1)

        home_win_prob = sim.get("homeWinProbElo")
        if home_win_prob is not None and winner != "tie":
            win_prob_of_actual_winner = home_win_prob if winner == "home" else round(100 - home_win_prob, 1)

        # Grade the model's DIRECTIONAL read, not just its formal "lean".
        # simulate_game.py only sets spreadLean/totalLean once the model's
        # edge clears its own confidence threshold, so on early-season
        # (low-sample-size) games those are usually null -- but the model
        # still has a raw central estimate (eloMarginHome, projTotal) that
        # picked an implicit side of the market even without clearing that
        # bar. Gating grading on the formal lean silently threw those picks
        # away instead of scoring them, which is how a game the model
        # correctly called (e.g. projTotal well under the closing total,
        # and the game actually went under) still showed up ungraded on
        # the Results tab. Prefer the explicit lean when the model issued
        # one; otherwise fall back to the raw estimate vs. the closing
        # number, matching how the Results tab describes itself: "the
        # accuracy record for the model," not "the record of its
        # confident picks only."
        if closing_spread is not None and elo_margin is not None and ats_result and ats_result != "push":
            if sim.get("spreadLean"):
                picked_home = sim["spreadLean"] == "home"
            else:
                predicted_adj = elo_margin + closing_spread
                picked_home = (predicted_adj > 0) if predicted_adj != 0 else None
            if picked_home is not None:
                sim_ats_pick_correct = (picked_home and ats_result == "home_covered") or (
                    (not picked_home) and ats_result == "away_covered"
                )
        if closing_total is not None and proj_total is not None and total_result and total_result != "push":
            if sim.get("totalLean"):
                implied_total_lean = sim["totalLean"]
            elif proj_total > closing_total:
                implied_total_lean = "over"
            elif proj_total < closing_total:
                implied_total_lean = "under"
            else:
                implied_total_lean = None
            if implied_total_lean is not None:
                sim_total_pick_correct = implied_total_lean == total_result

    return {
        "actual": {"homeScore": home_score, "awayScore": away_score, "marginHome": actual_margin_home,
                    "total": actual_total, "winner": winner},
        "closingLine": {"spreadHomeRel": closing_spread, "total": closing_total, "source": closing_source},
        "grading": {
            "atsResult": ats_result,
            "totalResult": total_result,
            "marketMarginErrorPts": market_margin_error,
            "marketTotalErrorPts": market_total_error,
            "simMarginErrorPts": sim_margin_error,
            "simTotalErrorPts": sim_total_error,
            "simStraightUpCorrect": sim_straight_up_correct,
            "simAtsPickCorrect": sim_ats_pick_correct,
            "simTotalPickCorrect": sim_total_pick_correct,
            "winProbOfActualWinner": win_prob_of_actual_winner,
        },
        "pregameSim": sim if sim_present else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("results_store")
    ap.add_argument("--home-yards", type=int, default=None)
    ap.add_argument("--away-yards", type=int, default=None)
    ap.add_argument("--home-plays", type=int, default=None)
    ap.add_argument("--away-plays", type=int, default=None)
    ap.add_argument("--home-top", default=None, help="Time of possession, MM:SS")
    ap.add_argument("--away-top", default=None)
    ap.add_argument("--home-turnovers", type=int, default=None)
    ap.add_argument("--away-turnovers", type=int, default=None)
    ap.add_argument("--source", default=None, help="Where the box score came from, e.g. 'cbssports.com gametracker'")
    args = ap.parse_args()

    game = load_game(args.game_file)
    if game.get("status") != "final" or not game.get("final_score"):
        print(json.dumps({
            "game_id": game.get("game_id"), "error": "game is not finalized -- run log_result.py first",
        }))
        sys.exit(1)

    snap, closing_is_fallback = closing_line(game)
    graded = grade_result(game, snap)

    away_name, home_name = [s.strip() for s in game["matchup"].split(" @ ")]
    entry = {
        "gameId": game["game_id"],
        "week": game.get("week"),
        "matchup": game["matchup"],
        "awayTeam": away_name,
        "homeTeam": home_name,
        "kickoff": game["kickoff"],
        "openingLine": {
            "spreadHomeRel": spread_home_rel((game.get("opening_line") or {}).get("draftkings")),
            "total": total_of((game.get("opening_line") or {}).get("draftkings")),
        },
        "closingLineIsFallback": closing_is_fallback,
        "boxscore": {
            "homeYards": args.home_yards, "awayYards": args.away_yards,
            "homePlays": args.home_plays, "awayPlays": args.away_plays,
            "homeTimeOfPossession": args.home_top, "awayTimeOfPossession": args.away_top,
            "homeTurnovers": args.home_turnovers, "awayTurnovers": args.away_turnovers,
            "source": args.source,
        },
        "capturedAt": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        **graded,
    }

    store = {"games": []}
    if os.path.exists(args.results_store):
        with open(args.results_store) as f:
            store = json.load(f)
        store.setdefault("games", [])

    store["games"] = [g for g in store["games"] if g.get("gameId") != entry["gameId"]] + [entry]

    tmp = args.results_store + ".tmp"
    os.makedirs(os.path.dirname(args.results_store) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(store, f, indent=2)
        f.write("\n")
    os.replace(tmp, args.results_store)

    print(json.dumps({"game_id": entry["gameId"], "entry": entry}))


if __name__ == "__main__":
    main()
