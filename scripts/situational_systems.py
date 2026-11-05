#!/usr/bin/env python3
"""Canonical library of situational betting systems/angles for this
project. Two sources feed this: systems already in live use on the
dashboard's "WagerTalk system" section (originally hand-typed per week in
one-off `_add_wagertalk_angles*.py` scripts), and the broader research
Michael asked for on 2026-09-25 (sandwich spot, letdown, look-ahead, trap
game, bye-week/rest systems, revenge systems, divisional-dog systems --
see the "NFL Situational Betting Systems" doc for full writeups and
sources).

This is a REFERENCE, not an auto-matcher: matching a system to a specific
game still requires real facts about that game (last week's result and
location, this week's rest situation, next week's opponent) that have to
be looked up, same as any other research signal. Use apply_wagertalk_angle.py
to write a match once you've confirmed the facts -- never guess a match
from memory.

Percentages are historical ATS hit rates for the side the system favors,
as published by the sources below -- not independently re-verified game
by game. Treat as directional evidence, not fixed probabilities (see the
"Using these without getting burned" section of the reference doc): favor
systems with an obvious mechanism (fatigue, motivation, divided focus)
over ones that are just a coincidental data slice, and never call a lean
above Medium confidence on a situational system alone -- combine with a
real injury/line-movement/form signal per the project's usual rule.
"""

SYSTEMS = {
    # --- Already in live use (hand-typed per week before this file existed) ---
    "winless_road_vs_home_favorite": {
        "category": "record/situation",
        "pct": 79,
        "mechanism": "A winless team on the road, off a loss, facing a home favorite -- desperation/no-respect shape.",
        "source": "WagerTalk-style trend video, cross-checked against real records each week (see _add_wagertalk_angles*.py history)",
    },
    "road_dog_window_02": {
        "category": "record/situation",
        "pct": 66,
        "mechanism": "Broader window: any 0-2 team as a road underdog.",
        "source": "same as above",
    },
    "off_loss_vs_off_win": {
        "category": "momentum",
        "pct": 57.5,
        "mechanism": "Team off a loss (chip on shoulder / correction) facing a team off a win (letdown risk) -- fade the off-win side.",
        "source": "same as above",
    },
    "repeat_home_loser_fade": {
        "category": "momentum",
        "pct": 69,
        "mechanism": "A team that lost at home in Week 1 and is home again in Week 2 covers only ~31% ATS -- fade the home team, take the visitor.",
        "source": "same as above",
    },
    "double_revenge_home_favorite_fade": {
        "category": "revenge",
        "pct": 67,
        "mechanism": "A team swept 2-0 by an opponent last season hosts them as a favorite this season -- the favorite covers only ~33% ATS.",
        "source": "same as above",
    },
    "swept_away_dog_first_meeting": {
        "category": "revenge",
        "pct": 58,
        "mechanism": "A team swept 2-0 last season is the away underdog in the first meeting of the new season.",
        "source": "same as above",
    },
    "travel_under_backtoback_road_off_win": {
        "category": "travel/rest",
        "pct": 74,
        "mechanism": "Away team won on the road last week and is on the road again; home team is also coming off a road game -- fatigue on both sides suppresses scoring.",
        "source": "same as above",
    },
    "desperation_over_02_singledigit": {
        "category": "motivation",
        "pct": 70,
        "mechanism": "Both teams 0-2 with a single-digit spread -- desperation/aggressive play-calling shape.",
        "source": "same as above",
    },

    # --- Added 2026-09-25, from the "NFL Situational Betting Systems" doc ---
    "sandwich_spot": {
        "category": "schedule/motivation",
        "pct": None,
        "mechanism": "Team plays a lesser, non-divisional/non-playoff-caliber opponent this week with a marquee game (rivalry, playoff contender, conference showdown) next week -- fade the sandwiched team, especially as a favorite.",
        "source": "Marco D'Angelo (WagerTalk), definition cross-checked against Boyd's Bets' independent 'sandwich spot' entry",
        "needs_lookahead": True,
    },
    "look_ahead_spot": {
        "category": "schedule/motivation",
        "pct": None,
        "mechanism": "Subset of sandwich spot -- focus splits toward a notable upcoming opponent even without a big win behind them.",
        "source": "Boyd's Bets",
        "needs_lookahead": True,
    },
    "letdown_spot": {
        "category": "schedule/motivation",
        "pct": None,
        "mechanism": "Team coming off an emotionally charged result (statement win, rivalry win, OT win) plays flat the following week.",
        "source": "Boyd's Bets",
        "needs_lastweek": True,
    },
    "trap_game": {
        "category": "market",
        "pct": None,
        "mechanism": "Heavily favored team draws lopsided public money while a hidden factor (rest, look-ahead, revenge dog, letdown) makes them more vulnerable than the number suggests.",
        "source": "VegasOdds",
    },
    "road_favorites_post_bye": {
        "category": "rest",
        "pct": 59.1,
        "record": "94-63-4 ATS since 1999",
        "roi": "+15.7%",
        "mechanism": "Road favorite coming off its bye week -- extra prep time compounds with an already-good team.",
        "source": "NFLBettingSystems.com",
        "needs_bye": True,
    },
    "favorites_off_bye": {
        "category": "rest",
        "pct": 63,
        "record": "105-61-3 ATS (75% SU, 126-42)",
        "mechanism": "Favorites off a bye overall; underdogs off a bye are weak (45.7% ATS) so this is really 'good team gets extra prep', not rest alone.",
        "source": "Lineups.com",
        "needs_bye": True,
    },
    "divisional_underdogs": {
        "category": "divisional",
        "pct": 53.8,
        "record": "314-270 ATS since 2019",
        "roi": "+3.4%",
        "mechanism": "Divisional dogs outperform non-divisional dogs (50.5% over the same span) -- familiarity/rivalry keeps games closer.",
        "source": "NFLBettingSystems.com",
    },
    "low_total_underdogs": {
        "category": "totals-linked",
        "pct": 57.7,
        "record": "205-150-10 ATS since 2018",
        "mechanism": "Underdogs in games with a total <=42 -- low-scoring/defensive environments favor the dog.",
        "source": "NFLBettingSystems.com",
    },
    "home_dog_off_win_vs_500plus_after_wk3": {
        "category": "record/situation",
        "pct": 66.7,
        "record": "118-59-4 ATS",
        "mechanism": "Home underdog coming off a win, facing a .500-or-better team, after Week 3.",
        "source": "ProComputerGambler",
    },
    "road_dog_revenge_close_loss": {
        "category": "revenge",
        "pct": 73.8,
        "record": "62-22 ATS",
        "mechanism": "Road underdog seeking revenge off a close road loss to this same opponent.",
        "source": "ProComputerGambler",
    },
    "road_dog_same_season_revenge": {
        "category": "revenge",
        "pct": 73.7,
        "record": "42-15-1 ATS",
        "mechanism": "Road underdog, lost by <14 to this opponent earlier the same season.",
        "source": "ProComputerGambler",
    },
    "road_dog_off_road_loss": {
        "category": "record/situation",
        "pct": 61.2,
        "record": "180-114-3 ATS",
        "mechanism": "Road underdog coming off a road loss (any opponent).",
        "source": "ProComputerGambler",
    },
    "road_dog_plus3_off_7plus_losing_streak": {
        "category": "record/situation",
        "pct": 71.2,
        "record": "47-19-2 ATS",
        "mechanism": "Road dog getting +3 or more, off a 7+ game losing streak. Small sample -- streak itself is doing a lot of the filtering.",
        "source": "ProComputerGambler",
    },
    "subpar_road_off_road_loss": {
        "category": "record/situation",
        "pct": 63.1,
        "record": "82-48-4 ATS (78% since 2007 sub-sample)",
        "mechanism": "Sub-.500 road team, spread within +/-3, off a road loss.",
        "source": "ProComputerGambler",
    },
    "home_favorite_thanksgiving": {
        "category": "calendar",
        "pct": 91.7,
        "record": "11-1 ATS / 12-0 SU since 2004",
        "mechanism": "Home favorites on Thanksgiving. Very small sample (12 games) -- treat as a footnote, not a real system.",
        "source": "ProComputerGambler",
    },
}
