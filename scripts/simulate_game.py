#!/usr/bin/env python3
"""Monte Carlo game simulator -- the "run it 10,000 times" style model
(Steve Merril / SportsLine popularized the public-facing version of this;
the exact proprietary algorithm isn't published, so this is an independent,
simpler build of the same general technique, not a reproduction of theirs).
The only write path for a game's `simulation` block; never hand-edit it.

Usage:
  python3 simulate_game.py <game_file.json> <ratings_file.json> [--n 10000]

Design (see ratings.py's module docstring for the Elo constants this leans
on -- HFA, K-factor, the Elo-to-spread conversion):
- Elo is the base for the projected SPREAD: home Elo + home-field bump vs.
  away Elo, converted to points via 538's standard 25-Elo-points-per-point
  formula.
- Each team's own rolling scoring average (points scored/allowed, last 8
  games) is the sanity check: it drives the projected TOTAL independently
  of Elo, and this script flags when the two signals disagree by more than
  DIVERGENCE_FLAG_POINTS, since that's worth a second look rather than
  silently trusting either number. That rolling window isn't only real
  2026 results -- seed_from_prior_season() (see ratings.py) pre-fills it
  with 8 "phantom" entries at the team's regressed 2025 scoring rate, so
  the total has a matchup-specific shape from Week 1 on instead of
  collapsing to a flat league-average total for every game (the bug this
  fixed, caught live 2026-09-07). Real results from log_result.py evict
  those phantom entries one at a time as they're logged, so this fades
  from "pure 2025 rate" to "pure 2026 rate" over each team's first 8 real
  games, with no separate blending logic needed here.
- Each trial draws both teams' scores from independent Gaussians centered
  on their projected mean with SCORE_STD standard deviation. A real NFL
  score isn't perfectly Gaussian (it clusters near multiples of 3 and 7,
  and the two teams' scores aren't fully independent of each other), but
  this is a defensible first-pass approximation -- not a claim of more
  precision than a simple model can actually deliver.
- Early in a season (few or no logged games for either team), every
  rating is close to its seed value, so the model has little to say yet.
  `gamesSeen` and `notes` say this plainly in the output rather than
  dressing up noise as a signal.
- Elo and the rolling scoring average are both pure TEAM-STRENGTH signals
  -- neither knows who's actually active for this specific game. Injury
  adjustments (see apply_injury_adjustment.py) are applied as a separate,
  explicit step AFTER the raw Elo/stats projection is computed: each
  side's `injury_adjustments.<side>.total_pts` (0.0 if that block doesn't
  exist yet, or is empty) shifts that side's projected scoring mean before
  the Monte Carlo draw. This is deliberately a shift to the MEAN, not to
  Elo itself -- Elo stays a clean, injury-blind team-strength number (so
  marginDivergence / eloMarginHome keep meaning "how good is this team on
  a neutral roster," not a mix of team quality and this week's health
  report), while the actual simulated final score, spread lean, and total
  lean all do reflect who's actually expected to play. Both the raw and
  injury-adjusted means are stored in the output so a reader can see
  exactly how much the injury report is doing to the projection, never
  just the after number with no way to check it.
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, save_game, most_recent_book, now_utc, normalize_team_name
from ratings import load_ratings, get_team, ppg, elo_margin_points, win_prob_home, had_injury_exclusion_effect

SCORE_STD = 10.0            # approx per-team NFL score std deviation -- a documented
                             # assumption, not something measured for this league
VIG_BREAKEVEN = 0.5238       # break-even win rate needed at standard -110 juice
EDGE_THRESHOLD = 0.055       # how far past breakeven before calling it a lean, not noise
DIVERGENCE_FLAG_POINTS = 4.0  # elo-margin vs. stats-margin gap worth flagging
MIN_GAMES_SEEN_FOR_LEAN = 4  # combined games logged for both teams before a "lean" is allowed at all
MIN_PROJECTED_MEAN = 3.0     # floor for a side's projected mean after injury adjustment --
                              # stops an extreme pts value from producing a degenerate
                              # near-zero-score distribution; a documented safety clamp,
                              # not a claim that 3.0 is a real minimum NFL score

# Verified live on 2026-09-06 against flat-seeded (unplayed) teams: with
# both teams at a neutral Elo, the model has no opinion on who's actually
# better, so a market spread of any real size reads as "the market is
# wrong" and produces a confident-looking lean on the underdog of nearly
# every heavily-favored game -- not a real signal, just the model not
# knowing anything yet. MIN_GAMES_SEEN_FOR_LEAN suppresses spreadLean/
# totalLean entirely below that threshold so a cold-start run can't be
# mistaken for an actionable one; the raw numbers (coverPctHome, overPct)
# still print for reference, just without being promoted to a "lean."


def simulate(home_mean, away_mean, market_home_spread, market_total, n, rng):
    home_wins = away_wins = ties = 0
    covers = pushes = 0
    overs = unders = total_pushes = 0
    home_scores, away_scores = [], []

    for _ in range(n):
        h = max(0, round(rng.gauss(home_mean, SCORE_STD)))
        a = max(0, round(rng.gauss(away_mean, SCORE_STD)))
        home_scores.append(h)
        away_scores.append(a)

        if h > a:
            home_wins += 1
        elif a > h:
            away_wins += 1
        else:
            ties += 1

        if market_home_spread is not None:
            adj = h + market_home_spread - a
            if adj > 0:
                covers += 1
            elif adj == 0:
                pushes += 1

        if market_total is not None:
            t = h + a
            if t > market_total:
                overs += 1
            elif t == market_total:
                total_pushes += 1

    home_scores.sort()
    away_scores.sort()
    mid = n // 2
    ats_denom = n - pushes
    total_denom = n - total_pushes

    return {
        "homeWinPct": round(home_wins / n * 100, 1),
        "awayWinPct": round(away_wins / n * 100, 1),
        "tiePct": round(ties / n * 100, 1),
        "medianHomeScore": home_scores[mid],
        "medianAwayScore": away_scores[mid],
        "coverPctHome": round(covers / ats_denom * 100, 1) if market_home_spread is not None and ats_denom else None,
        "overPct": round(overs / total_denom * 100, 1) if market_total is not None and total_denom else None,
    }


# Same book-priority order build_dashboard.py's best_available_spread()
# uses for Wong-teaser qualification (Caesars/DraftKings/BookMaker.eu/
# FanDuel) -- reused here so the simulation's own cover%/total% read is
# never blind to a real posted line just because DraftKings and
# BookMaker.eu specifically didn't post today. Caught live 2026-09-14:
# on a night DraftKings' source failed to render and BookMaker.eu hadn't
# posted several Week 2 games yet, the OLD version of this function (DK
# then BM only, straight of the latest snapshot with no carry-forward)
# returned (None, None) for six games that had a perfectly good, freshly-
# fetched FanDuel line sitting right there -- silently blanking their
# coverPctHome/overPct (and so Bets-to-consider/Best-Bets couldn't ever
# surface them) even though the dashboard's own gap-based read still had
# a number to compare against.
CURRENT_MARKET_BOOK_PRIORITY = ("caesars", "draftkings", "bookmaker_eu", "fanduel")


def current_market(game):
    """Best currently-known spread/total across all four tracked books, in
    CURRENT_MARKET_BOOK_PRIORITY order -- checked field-by-field (a book
    can have a spread but not a total, or vice versa) via lib.py's
    most_recent_book(), which also carries a field forward from an older
    snapshot when today's didn't repost it, same as the dashboard does for
    display. Only genuinely returns None for a field when NO tracked book
    has ever posted it for this game."""
    snapshots = game.get("snapshots") or []
    home_spread = total = None
    for book_key in CURRENT_MARKET_BOOK_PRIORITY:
        book, _as_of = most_recent_book(snapshots, book_key)
        if not book:
            continue
        if home_spread is None and book.get("spread") is not None:
            home_spread = book["spread"]
        if total is None and book.get("total") is not None:
            total = book["total"]
        if home_spread is not None and total is not None:
            break
    return home_spread, total


def history_exclusion_note(label, team):
    """If ppg() actually excluded any of `team`'s past games from its
    rolling average this run (see ratings.py's had_injury_exclusion_effect),
    say so plainly and name which games -- via each excluded game's own
    injury_flag_notes entry -- rather than letting the average quietly
    shift with no explanation."""
    if not had_injury_exclusion_effect(team, "scored_history"):
        return None
    flags = team.get("injury_flags") or []
    reasons = [n for f, n in zip(flags, team.get("injury_flag_notes") or []) if f and n]
    excluded = sum(1 for f in flags if f)
    reason_str = f" ({'; '.join(reasons)})" if reasons else ""
    return (
        f"{label}'s scoring average excludes {excluded} recent game(s) logged with a "
        f"significant injury at the time{reason_str} -- averaging over the "
        f"remaining clean games instead of diluting the read with injury-affected "
        f"performances. Will fold those games back in automatically once they age "
        f"out of the rolling window."
    )


def effective_injury_pts(game, side):
    """Phase 5: points added to `side`'s projected mean. Its own offensive injuries
    lower it; the OPPONENT's defensive injuries raise it. Legacy blocks without
    offense_pts/defense_pts fall back to the old own-side total."""
    adj = game.get("injury_adjustments") or {}
    own = adj.get(side) or {}
    opp = adj.get("away" if side == "home" else "home") or {}
    if "offense_pts" not in own and "defense_pts" not in opp:
        return injury_pts(game, side)
    own_off = own.get("offense_pts", own.get("total_pts", 0.0)) or 0.0
    opp_def = opp.get("defense_pts", 0.0) or 0.0
    return own_off - opp_def


def injury_pts(game, side):
    """Total pts adjustment for one side ("home"/"away") from
    apply_injury_adjustment.py's block -- 0.0 if the block, or that side's
    entry, doesn't exist yet (no injuries logged is the common case for
    most games most weeks, not an error)."""
    block = (game.get("injury_adjustments") or {}).get(side) or {}
    return block.get("total_pts", 0.0) or 0.0


def injury_summary_notes(game):
    """Human-readable lines describing what injury_adjustments actually
    applied, for both sides combined -- surfaced in the simulation's own
    `notes` (same place the Elo/stats-divergence note already prints) so a
    reader sees WHY the projection moved, not just the moved number."""
    lines = []
    for side, label in (("home", "Home"), ("away", "Away")):
        block = (game.get("injury_adjustments") or {}).get(side) or {}
        items = block.get("items") or []
        total_pts = block.get("total_pts", 0.0) or 0.0
        if not items or abs(total_pts) < 0.01:
            continue
        item_strs = [f"{it['player']} ({it['position']}, {it['status']}, {it['pts']:+.1f})" for it in items]
        line = f"{label} injury adjustment: {total_pts:+.1f} pts -- {', '.join(item_strs)}."
        stacking = block.get("stacking_notes") or []
        if stacking:
            line += " " + " ".join(stacking)
        lines.append(line)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("ratings_file")
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=None, help="RNG seed, testing only")
    args = ap.parse_args()

    game = load_game(args.game_file)
    ratings = load_ratings(args.ratings_file)
    # Same fix as log_result.py: strip a parenthetical venue qualifier
    # (e.g. "Los Angeles Rams (Melbourne)") before looking the team up in
    # ratings, so an international-game simulation reads the real team's
    # actual current Elo instead of silently falling back to get_team's
    # blank_team() default for a name that was never a real ratings key.
    away_raw, home_raw = [s.strip() for s in game["matchup"].split(" @ ")]
    away_name, home_name = normalize_team_name(away_raw), normalize_team_name(home_raw)
    home = get_team(ratings, home_name)
    away = get_team(ratings, away_name)

    elo_margin = elo_margin_points(home["elo"], away["elo"])  # positive = home favored
    home_prob = win_prob_home(home["elo"], away["elo"])

    # exclude_injury_flagged=True: a team's rolling scored/allowed average
    # automatically drops any game where IT ITSELF had a significant
    # injury_adjustments hit at the time (see log_result.py), once enough
    # clean games exist to still have a real sample -- this is what makes
    # "correct once the player is back healthy" happen on its own, with no
    # manual step, rather than letting a few backup-QB games quietly drag
    # the average down forever. See ratings.py's ppg()/had_injury_exclusion_effect().
    home_off = ppg(home, "scored_history", exclude_injury_flagged=True)
    home_def = ppg(home, "allowed_history", exclude_injury_flagged=True)
    away_off = ppg(away, "scored_history", exclude_injury_flagged=True)
    away_def = ppg(away, "allowed_history", exclude_injury_flagged=True)
    naive_home_mean = (home_off + away_def) / 2
    naive_away_mean = (away_off + home_def) / 2
    stats_margin = naive_home_mean - naive_away_mean
    naive_total = naive_home_mean + naive_away_mean

    games_seen = home["games_played"] + away["games_played"]
    # No separate weighting needed here anymore -- ppg() above already
    # blends prior-season-seeded phantom history with any real 2026 games
    # via the same rolling SCORE_HISTORY_WINDOW apply_result() writes to,
    # so naive_total is already the right matchup-specific number whether
    # it's coming from a pure prior-season seed, a mix, or pure 2026 data.
    proj_total = naive_total

    # Raw (injury-blind) projection -- Elo margin split evenly around the
    # projected total. This is what eloMarginHome/statsMarginHome/
    # marginDivergence below are computed from, and stays that way even
    # when an injury adjustment applies (see module docstring).
    proj_home_mean_raw = proj_total / 2 + elo_margin / 2
    proj_away_mean_raw = proj_total / 2 - elo_margin / 2

    home_inj_pts = effective_injury_pts(game, "home")
    away_inj_pts = effective_injury_pts(game, "away")
    proj_home_mean = max(MIN_PROJECTED_MEAN, proj_home_mean_raw + home_inj_pts)
    proj_away_mean = max(MIN_PROJECTED_MEAN, proj_away_mean_raw + away_inj_pts)

    market_home_spread, market_total = current_market(game)

    rng = random.Random(args.seed)
    sim = simulate(proj_home_mean, proj_away_mean, market_home_spread, market_total, args.n, rng)

    divergence = elo_margin - stats_margin
    notes = []
    if games_seen == 0:
        if home.get("scored_history") or away.get("scored_history"):
            notes.append(
                "Neither team has a 2026 result logged yet -- the total lean on "
                "each team's regressed 2025 scoring rate, not anything that's "
                "actually happened this season. The spread side (Elo) is "
                "seeded the same way. Both will shift as real 2026 results get "
                "logged."
            )
        else:
            notes.append(
                "Neither team has a logged result yet, and no prior-season "
                "scoring seed was available for at least one team -- this "
                "run's total falls back to a flat league-average estimate "
                "rather than anything matchup-specific."
            )
    elif abs(divergence) >= DIVERGENCE_FLAG_POINTS:
        notes.append(
            f"Elo margin ({elo_margin:+.1f}) and recent-scoring margin "
            f"({stats_margin:+.1f}) disagree by {abs(divergence):.1f} points "
            "-- worth a second look before trusting either alone."
        )
    notes.extend(injury_summary_notes(game))
    for label, team in ((f"{home_name} (home)", home), (f"{away_name} (away)", away)):
        note = history_exclusion_note(label, team)
        if note:
            notes.append(note)

    lean = None
    total_lean = None
    if games_seen >= MIN_GAMES_SEEN_FOR_LEAN:
        if sim["coverPctHome"] is not None:
            p = sim["coverPctHome"] / 100
            if p >= VIG_BREAKEVEN + EDGE_THRESHOLD:
                lean = "home"
            elif (1 - p) >= VIG_BREAKEVEN + EDGE_THRESHOLD:
                lean = "away"

        if sim["overPct"] is not None:
            p = sim["overPct"] / 100
            if p >= VIG_BREAKEVEN + EDGE_THRESHOLD:
                total_lean = "over"
            elif (1 - p) >= VIG_BREAKEVEN + EDGE_THRESHOLD:
                total_lean = "under"
    elif games_seen > 0 and (sim["coverPctHome"] is not None or sim["overPct"] is not None):
        notes.append(
            f"Only {games_seen} logged game(s) across both teams -- leans are "
            f"suppressed below {MIN_GAMES_SEEN_FOR_LEAN} to avoid mistaking a "
            "cold-start rating gap for a real signal. Raw cover%/over% still "
            "shown below for reference."
        )

    game["simulation"] = {
        "run_at": now_utc().isoformat(),
        "n": args.n,
        "homeElo": round(home["elo"], 1),
        "awayElo": round(away["elo"], 1),
        "gamesSeen": games_seen,
        "eloMarginHome": round(elo_margin, 1),
        "statsMarginHome": round(stats_margin, 1),
        "marginDivergence": round(divergence, 1),
        "projTotal": round(proj_total, 1),
        "homeWinProbElo": round(home_prob * 100, 1),
        "marketHomeSpread": market_home_spread,
        "marketTotal": market_total,
        "homeInjuryPts": round(home_inj_pts, 2),
        "awayInjuryPts": round(away_inj_pts, 2),
        "projHomeMeanRaw": round(proj_home_mean_raw, 1),
        "projAwayMeanRaw": round(proj_away_mean_raw, 1),
        "projHomeMeanAdjusted": round(proj_home_mean, 1),
        "projAwayMeanAdjusted": round(proj_away_mean, 1),
        # Elo margin folded together with both sides' injury pts --
        # the number the dashboard should actually DISPLAY as "what the
        # model projects for this game" (modelSpreadStr()), since
        # eloMarginHome alone is deliberately injury-blind (see module
        # docstring) and showing it next to an injury-adjusted win%/
        # cover% would silently disagree with those numbers.
        "injuryAdjustedMarginHome": round(elo_margin + home_inj_pts - away_inj_pts, 1),
        "projTotalAdjusted": round(proj_home_mean + proj_away_mean, 1),
        "spreadLean": lean,
        "totalLean": total_lean,
        "notes": notes,
        **sim,
    }
    # 2026-10-02: keep a compact per-run log so the dashboard's line-history
    # chart can plot the model's own number next to every market snapshot.
    # One point per run; a re-run within 10 minutes replaces the last point
    # instead of stacking near-duplicates.
    hist = game.setdefault("simulation_history", [])
    point = {
        "run_at": game["simulation"]["run_at"],
        "marginHome": game["simulation"]["injuryAdjustedMarginHome"],
        "total": game["simulation"]["projTotalAdjusted"],
    }
    if hist:
        try:
            from datetime import datetime as _dt
            last = _dt.fromisoformat(hist[-1]["run_at"].replace("Z", "+00:00"))
            cur = _dt.fromisoformat(point["run_at"].replace("Z", "+00:00"))
            if (cur - last).total_seconds() < 600:
                hist.pop()
        except Exception:
            pass
    hist.append(point)
    save_game(args.game_file, game)

    print(json.dumps({"game_id": game["game_id"], "simulation": game["simulation"]}))


if __name__ == "__main__":
    main()
