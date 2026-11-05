"""Elo power ratings + team scoring-average tracking for the game simulator.

Dependency-free (stdlib only, matching lib.py) since this runs on the
user's local device shell.

Methodology, and why these specific constants:
- The Elo update (expected-score formula + a margin-of-victory multiplier)
  is FiveThirtyEight's published NFL Elo methodology -- the most publicly
  documented, reproducible version of "Elo for football" available, so it's
  the sensible default rather than inventing a new one. K_FACTOR=20 and
  HFA_ELO=48 are 538's long-published defaults; NFL home-field advantage has
  trended down league-wide in recent seasons, so HFA_ELO is the single most
  likely constant to want tuning down over time -- it's isolated here as one
  named constant for exactly that reason.
- ELO_SPREAD_DIVISOR=25 is 538's conversion from an Elo-point gap to a
  point-spread estimate (25 Elo points ~= 1 point of spread).
- Every constant below is a documented assumption, not a measured fact about
  this user's league -- flag it plainly rather than presenting the model's
  output as more precise than a first-pass simulation actually is.
"""
import json
import math
import os

K_FACTOR = 20.0
HFA_ELO = 48.0              # home-field advantage, in Elo points
ELO_SPREAD_DIVISOR = 25.0   # Elo-point gap -> point-spread estimate
BASE_ELO = 1500.0
LEAGUE_AVG_PPG = 22.5        # placeholder league-average points/team/game, used
                              # for any team with no logged games yet
SCORE_HISTORY_WINDOW = 8     # rolling window for each team's ppg averages

# 2025 season actuals, used only to pace-neutralize a points-per-play seed
# (see seed_from_prior_season) -- not placeholders like LEAGUE_AVG_PPG above,
# these are measured: footballdb.com's offensive-plays table and pro-
# football-reference.com/opp.htm's defensive-plays-faced table were fetched
# independently and cross-validated against each other (both summed to
# exactly 33,327 plays league-wide) and against this project's own stored
# PF/PA figures (both summed to exactly 12,519 points league-wide).
LEAGUE_AVG_PLAYS_PER_GAME = 61.3   # 33,327 total offensive plays / (32 teams x 17 games)
LEAGUE_AVG_PPP = 0.376              # 12,519 total points / 33,327 total plays


def blank_team():
    return {
        "elo": BASE_ELO,
        "games_played": 0,
        "scored_history": [],
        "allowed_history": [],
        # Parallel to scored_history/allowed_history (same order, same
        # rolling window, evicted together) -- whether THIS team itself had
        # a significant injury_adjustments hit (see
        # apply_injury_adjustment.py) active for that particular game.
        # Added 2026-09-23 per Michael: a stretch of games played without a
        # key starter shouldn't quietly drag down that team's rolling
        # scoring average forever once the player is back -- ppg() below
        # can exclude these once enough clean games exist, which is what
        # actually performs the "correct once resolved" Michael asked for
        # (no manual step -- it self-corrects as clean games accumulate).
        "injury_flags": [],
        # Same length/order as injury_flags -- a short human-readable
        # reason for each True entry (None for a clean game), so a later
        # review of a team's history shows WHY a game was excluded, not
        # just a bare boolean.
        "injury_flag_notes": [],
    }


def load_ratings(path):
    if not os.path.exists(path):
        return {"teams": {}, "meta": {"seed_source": "none (unseeded)", "hfa_elo": HFA_ELO, "k_factor": K_FACTOR}}
    with open(path) as f:
        return json.load(f)


def save_ratings(path, ratings):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ratings, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def get_team(ratings, team_name):
    return ratings["teams"].get(team_name, blank_team())


MIN_CLEAN_GAMES_FOR_EXCLUSION = 3  # need at least this many non-injury-flagged
                                    # games in the window before excluding the
                                    # flagged ones -- otherwise there's too little
                                    # left to average, so fall back to using
                                    # everything (documented, same honest-fallback
                                    # pattern as everywhere else in this project)


def ppg(team, key, exclude_injury_flagged=False):
    history = team.get(key) or []
    if not history:
        return LEAGUE_AVG_PPG
    if exclude_injury_flagged:
        flags = team.get("injury_flags") or []
        if len(flags) == len(history):
            clean = [v for v, flagged in zip(history, flags) if not flagged]
            if len(clean) >= MIN_CLEAN_GAMES_FOR_EXCLUSION:
                return sum(clean) / len(clean)
    return sum(history) / len(history)


def had_injury_exclusion_effect(team, key):
    """True if excluding injury-flagged games from `key`'s history would
    actually change the average returned by ppg() -- i.e. there's real
    flagged data AND enough clean games to exclude it. Used by
    simulate_game.py to decide whether to surface a note explaining why the
    projection differs from a naive full-history average; returns False
    (nothing to explain) whenever ppg()'s own fallback would kick in."""
    history = team.get(key) or []
    flags = team.get("injury_flags") or []
    if not history or len(flags) != len(history):
        return False
    flagged_count = sum(1 for f in flags if f)
    clean_count = len(flags) - flagged_count
    return flagged_count > 0 and clean_count >= MIN_CLEAN_GAMES_FOR_EXCLUSION


def elo_expected(elo_a, elo_b):
    """Probability A beats B, A's rating already including any home bump."""
    return 1.0 / (1.0 + 10 ** (-(elo_a - elo_b) / 400.0))


def elo_margin_points(home_elo, away_elo):
    """Points home is favored by (positive = home favored), per 538's
    Elo-to-spread conversion. Negate for this project's home-relative sign
    convention (negative = home favored) when writing a spread value."""
    return (home_elo + HFA_ELO - away_elo) / ELO_SPREAD_DIVISOR


def win_prob_home(home_elo, away_elo):
    return elo_expected(home_elo + HFA_ELO, away_elo)


def mov_multiplier(margin, winner_elo_diff):
    """FiveThirtyEight's margin-of-victory dampener: a blowout moves the
    rating more than a 1-point win, but the effect shrinks the further the
    favorite already was ahead in Elo (an expected blowout teaches the model
    less than a surprising one)."""
    if margin <= 0:
        return 0.0
    return math.log(margin + 1) * (2.2 / (winner_elo_diff * 0.001 + 2.2))


def _backfill_injury_arrays(team):
    """Pad injury_flags/injury_flag_notes with clean (False/None) entries
    up to scored_history's current length -- handles a team record written
    before this field existed, or a prior-season phantom seed (which has
    no injury context of its own to report), without erroring or losing
    sync between the three parallel arrays."""
    n = len(team.get("scored_history") or [])
    flags = team.setdefault("injury_flags", [])
    notes = team.setdefault("injury_flag_notes", [])
    if len(flags) < n:
        team["injury_flags"] = ([False] * (n - len(flags))) + flags
    if len(notes) < n:
        team["injury_flag_notes"] = ([None] * (n - len(notes))) + notes


def apply_result(ratings, home_team, away_team, home_score, away_score,
                  home_injury_flag=False, home_injury_note=None,
                  away_injury_flag=False, away_injury_note=None):
    """Update both teams' Elo and rolling scoring history in place. Returns
    a dict summarizing the Elo change, for the caller to print/log.

    home_injury_flag/away_injury_flag: whether THAT team itself had a
    significant injury_adjustments hit active for this specific game (see
    log_result.py, which derives this from the game's own
    injury_adjustments block before calling here) -- recorded into
    injury_flags/injury_flag_notes alongside the score itself, purely for
    ppg()'s later use. Never affects the Elo update in this call; Elo
    stays exactly what it's always been, a pure result of who won by how
    much."""
    home = ratings["teams"].setdefault(home_team, blank_team())
    away = ratings["teams"].setdefault(away_team, blank_team())
    _backfill_injury_arrays(home)
    _backfill_injury_arrays(away)

    expected_home = elo_expected(home["elo"] + HFA_ELO, away["elo"])
    if home_score > away_score:
        actual_home = 1.0
    elif away_score > home_score:
        actual_home = 0.0
    else:
        actual_home = 0.5

    margin = abs(home_score - away_score)
    winner_elo = (home["elo"] + HFA_ELO) if home_score >= away_score else away["elo"]
    loser_elo = away["elo"] if home_score >= away_score else (home["elo"] + HFA_ELO)
    mult = mov_multiplier(margin, winner_elo - loser_elo)

    delta = K_FACTOR * mult * (actual_home - expected_home)
    old_home_elo, old_away_elo = home["elo"], away["elo"]
    home["elo"] = old_home_elo + delta
    away["elo"] = old_away_elo - delta
    home["games_played"] += 1
    away["games_played"] += 1

    home["scored_history"] = (home["scored_history"] + [home_score])[-SCORE_HISTORY_WINDOW:]
    home["allowed_history"] = (home["allowed_history"] + [away_score])[-SCORE_HISTORY_WINDOW:]
    away["scored_history"] = (away["scored_history"] + [away_score])[-SCORE_HISTORY_WINDOW:]
    away["allowed_history"] = (away["allowed_history"] + [home_score])[-SCORE_HISTORY_WINDOW:]
    home["injury_flags"] = (home["injury_flags"] + [home_injury_flag])[-SCORE_HISTORY_WINDOW:]
    home["injury_flag_notes"] = (home["injury_flag_notes"] + [home_injury_note])[-SCORE_HISTORY_WINDOW:]
    away["injury_flags"] = (away["injury_flags"] + [away_injury_flag])[-SCORE_HISTORY_WINDOW:]
    away["injury_flag_notes"] = (away["injury_flag_notes"] + [away_injury_note])[-SCORE_HISTORY_WINDOW:]

    return {
        "home_team": home_team, "away_team": away_team,
        "home_elo_before": round(old_home_elo, 1), "home_elo_after": round(home["elo"], 1),
        "away_elo_before": round(old_away_elo, 1), "away_elo_after": round(away["elo"], 1),
        "expected_home_win_prob": round(expected_home, 3),
        "actual_home_result": actual_home,
    }


def seed_flat(team_names):
    """Every team at BASE_ELO, zero history. The honest default when there's
    no trustworthy external seed yet -- see seed_ratings.py."""
    return {
        "teams": {name: blank_team() for name in team_names},
        "meta": {"seed_source": "flat (all teams at 1500, unseeded)", "hfa_elo": HFA_ELO, "k_factor": K_FACTOR},
    }


def seed_from_prior_season(team_records, source_label, regression_fraction=1.0 / 3.0):
    """team_records: {team_name: {"wins": int, "losses": int, "ties": int
    (optional), "point_diff": int, "points_for": int (optional),
    "points_against": int (optional)}} for the season just completed.

    Point differential per game is converted to an Elo rating using the
    same ELO_SPREAD_DIVISOR this project already uses to go the other
    direction (elo_margin_points) -- a team that outscored its opponents by
    X points/game on average would be about X points better than an
    average (1500) opponent on a neutral field, and 25 Elo points is this
    project's standard "worth 1 point of spread" conversion throughout, so
    seeding this way keeps the whole system internally consistent rather
    than importing a second, unrelated conversion just for the seed step.

    Then regressed toward 1500 by `regression_fraction` (default 1/3) --
    the same offseason carryover FiveThirtyEight's own NFL Elo model uses:
    a team's final rating overstates how good they'll be next season, since
    some of it was luck (turnovers, injuries, close-game record) that
    doesn't reliably repeat, on top of the real turnover of coaching staff
    and rosters between seasons. Regressing 1/3 of the way back to the
    league average is a deliberately simple way to acknowledge that
    uncertainty for every team at once, rather than hand-judging how much
    each team's own offseason moves should discount their prior year.

    When `points_for`/`points_against` are present, this ALSO seeds each
    team's `scored_history`/`allowed_history` with SCORE_HISTORY_WINDOW
    "phantom" entries at that team's prior-season scoring rate, regressed by
    the same `regression_fraction` (the same "last year's rate doesn't fully
    repeat" reasoning applies to scoring as much as to win/loss record).
    This is what gives simulate_game.py's total projection a matchup-
    specific shape from Week 1 on, instead of falling back to a flat
    league-average total for every game simply because no 2026 results
    exist yet (the bug this fixes, caught live 2026-09-07: every tracked
    game's projTotal was printing an identical 45.0). Because
    `apply_result()` appends real scores onto these same lists and caps
    them at SCORE_HISTORY_WINDOW via the existing rolling window, no
    separate blending logic is needed anywhere else -- each of a team's
    first 8 REAL logged games organically evicts one phantom prior-season
    entry, so the projection fades from "pure 2025" to "pure 2026" over
    that team's first 8 games, the same way Elo itself fades from its
    prior-season seed toward whatever the current season's results
    actually show.

    When `off_plays` (that team's own offensive plays run) and
    `def_plays_faced` (offensive plays their defense faced) are ALSO
    present, the scoring seed is computed from points-PER-PLAY efficiency
    instead of raw points-per-game, then converted back to a points-per-
    game number at LEAGUE_AVG_PLAYS_PER_GAME rather than that team's own
    2025 pace (added 2026-09-07, per Michael's ask to fold in play-level
    data "until we have 2026 data"). The reasoning: raw PF/game conflates
    two different things -- how efficient an offense is per snap, and how
    many snaps it ran -- and a team's own pace is exactly the kind of thing
    that can change year to year with a new coordinator or personnel group,
    the same way win/loss record can. We have zero 2026 signal on any
    team's actual pace yet, so rather than carry forward a team's own 2025
    pace as if it were still true, this neutralizes it: it credits each
    team with its regressed 2025 per-play efficiency at a *league-average*
    number of plays, which is the more defensible assumption with no other
    information. This isn't a stacked/blended average with the plain PF/PA
    method above -- when play counts are present they REPLACE the raw-
    points-per-game computation for that team, since points-per-play is
    strictly the more precise version of the same underlying idea. Once
    real 2026 games get logged, `apply_result()` appends actual final
    scores (a team's true 2026 pace already baked in) exactly as before, so
    this pace-neutral assumption only ever matters for the phantom entries
    still sitting in the window -- it fades out on the same 8-game clock as
    everything else here, no separate logic needed.

    A team missing `points_for`/`points_against` gets an empty history
    exactly like `blank_team` (so `ppg()` falls back to LEAGUE_AVG_PPG for
    it, same as before this was added) rather than erroring -- this stays
    backward compatible with a `--prior-season` file that only has win/
    loss/point-diff. Missing just `off_plays`/`def_plays_faced` falls back
    to the plain points-per-game method above, same reasoning."""
    teams = {}
    for name, rec in team_records.items():
        games = rec["wins"] + rec["losses"] + rec.get("ties", 0)
        pd_per_game = (rec["point_diff"] / games) if games else 0.0
        raw_elo = BASE_ELO + pd_per_game * ELO_SPREAD_DIVISOR
        team = blank_team()
        team["elo"] = BASE_ELO + (raw_elo - BASE_ELO) * (1.0 - regression_fraction)

        pf, pa = rec.get("points_for"), rec.get("points_against")
        off_plays, def_plays = rec.get("off_plays"), rec.get("def_plays_faced")
        if pf is not None and pa is not None and games:
            if off_plays and def_plays:
                off_ppp_raw = pf / off_plays
                def_ppp_raw = pa / def_plays
                off_ppp_seed = LEAGUE_AVG_PPP + (off_ppp_raw - LEAGUE_AVG_PPP) * (1.0 - regression_fraction)
                def_ppp_seed = LEAGUE_AVG_PPP + (def_ppp_raw - LEAGUE_AVG_PPP) * (1.0 - regression_fraction)
                scored_seed = off_ppp_seed * LEAGUE_AVG_PLAYS_PER_GAME
                allowed_seed = def_ppp_seed * LEAGUE_AVG_PLAYS_PER_GAME
            else:
                scored_raw = pf / games
                allowed_raw = pa / games
                scored_seed = LEAGUE_AVG_PPG + (scored_raw - LEAGUE_AVG_PPG) * (1.0 - regression_fraction)
                allowed_seed = LEAGUE_AVG_PPG + (allowed_raw - LEAGUE_AVG_PPG) * (1.0 - regression_fraction)
            team["scored_history"] = [round(scored_seed, 1)] * SCORE_HISTORY_WINDOW
            team["allowed_history"] = [round(allowed_seed, 1)] * SCORE_HISTORY_WINDOW

        teams[name] = team
    return {
        "teams": teams,
        "meta": {
            "seed_source": source_label,
            "hfa_elo": HFA_ELO,
            "k_factor": K_FACTOR,
            "regression_fraction": regression_fraction,
        },
    }


def seed_from_win_totals(win_totals, source_label):
    """win_totals: {team_name: season_win_total_line (float, e.g. 9.5)}.
    Standard preseason Elo seed: each win above/below a .500 (8-win) season
    is worth ~25 Elo points, centered on 1505 -- a commonly used, simple
    conversion (this is NOT this project's own measurement; treat it as a
    rough prior that self-corrects once real results start feeding
    apply_result, not as a precise rating)."""
    teams = {}
    for name, win_total in win_totals.items():
        team = blank_team()
        team["elo"] = 1505.0 + (win_total - 8.0) * 25.0
        teams[name] = team
    return {
        "teams": teams,
        "meta": {"seed_source": source_label, "hfa_elo": HFA_ELO, "k_factor": K_FACTOR},
    }
