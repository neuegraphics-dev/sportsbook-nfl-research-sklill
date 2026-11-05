#!/usr/bin/env python3
"""Build the Line Watch dashboard HTML from the real tracked game files.

This is the dashboard's only write path, mirroring how update_snapshot.py is
the only write path for a game's JSON -- never hand-edit the dashboard HTML
or hand-retype numbers into it. Everything the dashboard shows (spreads,
totals, moneylines, the key-number explainer text, Wong-teaser
qualification) is derived here from data/<game_id>.json, the same files
update_snapshot.py writes -- there is no separate hand-maintained dataset
backing the dashboard.

Usage:
  python3 build_dashboard.py <data_dir> <template_html> <output_html> \
    [--results results/results.json]

<data_dir> should be the parent data/ directory (see compute_tier.py's
docstring for why -- same reasoning applies here: a game's dashboard
treatment doesn't depend on which week folder it's filed under).
<template_html> is scripts/dashboard_template.html, bundled alongside this
script -- it's the full page (styles, tab logic, the arbitrage/+EV/teaser/
results math already live in its JavaScript) with two placeholders,
`__GAMES_JSON__` and `__RESULTS_JSON__`, where this script injects the real
data. --results points at capture_result.py's own store (see that script)
-- omit it and the Results tab just renders empty rather than erroring, so
this stays backward compatible with a folder that hasn't logged any
completed games yet.

Prints one JSON line summarizing what was built (game count, Wong
qualifiers, completed-games count; +EV/arb hits aren't recomputed here --
that math runs client-side in the page itself off the same fields this
script supplies).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from lib import compute_tier, compute_team_streaks, list_game_files, load_game, most_recent_book, normalize_team_name, now_utc, spread_crossed_key_number, TEAM_NAMES
from update_power_rankings import seed_order_from_elo, save_rankings

ABBR = {
    "New England Patriots": "NE", "Seattle Seahawks": "SEA",
    "San Francisco 49ers": "SF", "Los Angeles Rams": "LAR",
    "Chicago Bears": "CHI", "Carolina Panthers": "CAR",
    "Buffalo Bills": "BUF", "Houston Texans": "HOU",
    "Denver Broncos": "DEN", "Kansas City Chiefs": "KC",
    "Cleveland Browns": "CLE", "Jacksonville Jaguars": "JAX",
    "Tampa Bay Buccaneers": "TB", "Cincinnati Bengals": "CIN",
    "Arizona Cardinals": "ARI", "Los Angeles Chargers": "LAC",
    "Washington Commanders": "WAS", "Philadelphia Eagles": "PHI",
    "Dallas Cowboys": "DAL", "New York Giants": "NYG",
    "Miami Dolphins": "MIA", "Las Vegas Raiders": "LV",
    "Atlanta Falcons": "ATL", "Pittsburgh Steelers": "PIT",
    "New York Jets": "NYJ", "Tennessee Titans": "TEN",
    "Green Bay Packers": "GB", "Minnesota Vikings": "MIN",
    "Baltimore Ravens": "BAL", "Indianapolis Colts": "IND",
    "New Orleans Saints": "NO", "Detroit Lions": "DET",
}

WONG_TOTAL_MAX = 47.0     # strict -- teaser points are worth less as scoring goes up, so a
                          # total over 47 is a straight reject now, not a softer "ok but not
                          # ideal" tier. Was 49/47 two-tier; Michael's updated Wong-teaser
                          # algorithm (2026-09-08) collapses that to one hard cutoff.
WONG_FAVORITE_MARGIN = 7.5  # exact match only -- see the -8.5 fallacy note below


def abbr_of(team_name):
    # Strip a trailing parenthetical (e.g. "Los Angeles Rams (Melbourne)" for
    # a neutral-site game) before looking the name up -- otherwise it both
    # misses the ABBR dict entry for the plain team name and pollutes the
    # last-two-words fallback (caught live 2026-09-04: "R(" instead of "LAR").
    clean_name = re.sub(r"\s*\([^)]*\)\s*$", "", team_name).strip()
    return ABBR.get(clean_name, "".join(w[0] for w in clean_name.split()[-2:]).upper())


def spread_side_str(home_relative_spread, away_abbr, home_abbr):
    """Home-relative signed float -> display string like 'SEA -4' or 'PK'."""
    if home_relative_spread is None:
        return None
    if home_relative_spread < 0:
        return f"{home_abbr} -{abs(home_relative_spread):g}"
    if home_relative_spread > 0:
        return f"{away_abbr} -{abs(home_relative_spread):g}"
    return "PK"


# Book priority for Wong-teaser spread qualification. Caesars first, since
# that's where Michael actually places these -- but NOT Caesars-only
# (2026-09-08 change, reverting part of an earlier same-day change): most
# games don't have a captured Caesars spread at all, which was excluding
# them from consideration entirely rather than just from an exact-price
# guarantee. Falling back through the other captured books means more games
# get flagged as candidates; per Michael, a flag from any book is enough to
# go check the exact number at Caesars before betting, so the qualification
# doesn't need to be gated on Caesars data existing -- it needs to point at
# the right games.
WONG_SPREAD_BOOK_PRIORITY = [
    ("caesars", "Caesars"),
    ("draftkings", "DraftKings"),
    ("bookmaker_eu", "BookMaker.eu"),
    ("fanduel", "FanDuel"),
]


def spread_margin_qualifies(spread_side):
    """True if a 'TEAM -N' string's margin sits in a Wong-qualifying window
    (exactly 7.5 favorite, or 1.5-2.5 underdog) -- ignoring total, which
    wong_check checks separately. Mirrors wong_check's own margin logic
    (including rejecting -8.5, which is neither 7.5 nor in 1.5-2.5) so this
    probe and the real qualification check never disagree."""
    if not spread_side:
        return False
    fav_abbr, fav_margin_str = spread_side.split(" ")
    if fav_abbr == "PK":
        return False
    margin = abs(float(fav_margin_str.replace("-", "")))
    return margin == WONG_FAVORITE_MARGIN or 1.5 <= margin <= 2.5


def best_available_spread(cur, away_abbr, home_abbr):
    """Returns (spread_side_str, source_key, source_label) for Wong-teaser
    qualification. NOT simply the first book in WONG_SPREAD_BOOK_PRIORITY
    that has any data -- that was the bug caught live 2026-09-08 (Denver@KC:
    DraftKings had a captured-but-non-qualifying -3, so the old version
    stopped there and never looked further, even though BookMaker.eu's own
    current line, -2.5, sits right in the underdog window). Instead: check
    every captured current book in priority order and return the first one
    whose margin actually qualifies, so a non-qualifying number from a
    higher-priority book can't hide a qualifying number from a lower-priority
    one. Only if NO captured book qualifies does this fall back to the
    first-available book, purely so non-qualifying games still have
    something to display (the trap example, key-number-style copy) --
    wong_check() is still the sole authority on the yes/no qualification
    call either way, this function only picks which number gets checked."""
    candidates = []
    for key, label in WONG_SPREAD_BOOK_PRIORITY:
        book = cur.get(key)
        if book and book.get("spread") is not None:
            candidates.append((spread_side_str(book["spread"], away_abbr, home_abbr), key, label))
    if not candidates:
        return None, None, None
    for spread_side, key, label in candidates:
        if spread_margin_qualifies(spread_side):
            return spread_side, key, label
    return candidates[0]


def crossed_numbers(open_mag, cur_mag):
    """Which of 3/7 were crossed between two spread magnitudes, using the
    exact same boundary rule as lib.spread_crossed_key_number (kept in sync
    with that function's per-number logic, not reimplemented differently)."""
    crossed = []
    for k in (3, 7):
        p, c = open_mag, cur_mag
        if (p < k <= c) or (c < k <= p) or (p == k and c != k) or (c == k and p != k):
            crossed.append(k)
    return crossed


def key_number_note(away_abbr, home_abbr, open_hr, cur_hr):
    """Plain-language explanation of a key-number-relevant spread move, or
    None if nothing crossed. open_hr/cur_hr are home-relative signed spreads.

    Deliberately conservative about the word "crossed": a number only counts
    as crossed if the magnitude actually passed through or landed on it
    (crossed_numbers, mirroring the trigger's own boundary rule) -- a
    favorite-flip alone (sign change) does NOT imply both 3 and 7 were
    crossed unless the magnitude math says so. (An earlier hand-written
    version of this note for a real flip overstated this -- said "crossed
    both 3 and 7" when the magnitude only moved from 3.0 to 3.5, touching
    just the 3 boundary. This function fixes that class of mistake by
    deriving the claim from the numbers instead of the sign change alone.)
    """
    if open_hr is None or cur_hr is None or open_hr == cur_hr:
        return None

    open_mag, cur_mag = abs(open_hr), abs(cur_hr)
    crossed = crossed_numbers(open_mag, cur_mag)
    if not crossed:
        return None

    flipped = (open_hr < 0) != (cur_hr < 0) and open_hr != 0 and cur_hr != 0
    cur_fav = home_abbr if cur_hr < 0 else away_abbr
    open_fav = home_abbr if open_hr < 0 else away_abbr

    def fmt(mag):
        return f"{mag:g}"

    STAT = {3: "About 15% of NFL games are decided by exactly 3 points",
             7: "Roughly 8-10% of games land on a 7-point margin"}

    if flipped:
        crossed_txt = " and ".join(str(k) for k in crossed) if crossed else "the pick'em line"
        return (
            f"This is the big one: {open_fav} opened favored by {fmt(open_mag)}; "
            f"{cur_fav} is now favored by {fmt(cur_mag)} -- a full flip that crossed "
            f"{crossed_txt} on the way. Anyone who bet {open_fav} at the open is now on "
            f"a very different line than today's, while today's number treats {cur_fav} "
            f"as the play instead. A full favorite flip like this usually means real "
            f"information (an injury, a depth-chart change) hit the board, not just "
            f"routine drift."
        )

    parts = []
    for k in crossed:
        if cur_mag == k and open_mag < k:
            parts.append(
                f"opened {open_fav} -{fmt(open_mag)}, now {cur_fav} -{fmt(cur_mag)} -- "
                f"moved onto {k} rather than past it. A {cur_fav} win by exactly {k} was "
                f"a clean win for the early number; at -{k} today, that same margin is a "
                f"push instead. The market didn't fully cross {k}, but it gave up the "
                f"cushion around it."
            )
        elif cur_mag > k and open_mag <= k:
            parts.append(
                f"opened {open_fav} -{fmt(open_mag)}, now {cur_fav} -{fmt(cur_mag)} -- "
                f"crossed {'the single most common margin in football' if k == 3 else 'the second key number'}. "
                f"A {cur_fav} win by exactly {k} now loses for anyone betting -{fmt(cur_mag)} today"
                f"{', but still wins outright for anyone who got the early number' if open_mag < k else ''}. "
                f"{STAT[k]}, so this move is worth more than it looks."
            )
        else:
            parts.append(
                f"moved off {k} (from -{fmt(open_mag)} to -{fmt(cur_mag)}), giving back "
                f"some of the cushion {'a bettor' if cur_mag < open_mag else ''} picked up at the open."
            )
    return " ".join(parts)


def wong_check(cur_spread_side, away_abbr, home_abbr, total):
    if not cur_spread_side or total is None:
        return None
    fav_abbr, fav_margin_str = cur_spread_side.split(" ")
    if fav_abbr == "PK":
        return None
    margin = abs(float(fav_margin_str.replace("-", "")))

    qualifies_spread = False
    teased_side = teased_from = teased_to = reason = None
    rejected_reason = None

    if fav_abbr != "PK" and margin == 8.5:
        # The -8.5 fallacy: teasing 6 points off an 8.5-point favorite lands
        # at -2.5, not -1.5. -2.5 still requires a 3+ point win to cash, so
        # it does NOT actually clear the 3 the way -7.5 -> -1.5 does --
        # -8.5 looks like it's in the old "7.5-8.5" window but it isn't
        # mathematically equivalent to -7.5, and treating it as qualifying
        # was the bug in the old window-based check. Explicitly rejected
        # here (not just falling through to "no window matched") so the
        # reason shows up distinctly rather than looking like any other
        # non-qualifying spread.
        rejected_reason = f"{fav_abbr} -8.5 teases to -2.5, not -1.5 -- still needs a 3+ point win to cash, so it doesn't actually clear the 3. Rejected (the -8.5 fallacy), not treated as equivalent to -7.5."
    elif margin == WONG_FAVORITE_MARGIN:
        qualifies_spread = True
        teased_side = f"{fav_abbr} -{margin:g}"
        teased_from = -margin
        teased_to = -(margin - 6)
        reason = f"Favorite at -{margin:g} teases down to -{margin - 6:g}, clearing both 7 and 3 with room to spare."
    elif 1.5 <= margin <= 2.5:
        dog_abbr = away_abbr if fav_abbr == home_abbr else home_abbr
        qualifies_spread = True
        teased_side = f"{dog_abbr} +{margin:g}"
        teased_from = margin
        teased_to = margin + 6
        reason = f"Underdog at +{margin:g} teases up to +{margin + 6:g}, crossing both 3 and 7."

    total_ok = total <= WONG_TOTAL_MAX

    return {
        "qualifies": bool(qualifies_spread and total_ok),
        "spreadInWindow": qualifies_spread,
        "totalOk": total_ok,
        "totalIdeal": total_ok,  # single strict tier now -- kept as an alias so
                                  # any template code still reading totalIdeal
                                  # doesn't silently regress to the old meaning.
        "teasedSide": teased_side,
        "teasedFrom": teased_from,
        "teasedTo": teased_to,
        "reason": reason,
        "rejectedReason": rejected_reason,
        "margin": margin,
        "total": total,
    }


def build_game_record(game, reference):
    # normalize_team_name strips a parenthetical venue qualifier (e.g. "Los
    # Angeles Rams (Melbourne)") before the abbreviation lookup -- without
    # it, abbr_of() misses the ABBR dict entry entirely and falls back to
    # word-initials, so an international-series game would show a wrong
    # abbreviation on every card, chart link, and cross-reference on the
    # page. The raw matchup string (with the venue qualifier intact) is
    # still what's stored/displayed elsewhere -- this only affects the
    # abbreviation used for lookups and short display.
    away_raw, home_raw = [s.strip() for s in game["matchup"].split(" @ ")]
    away_name, home_name = normalize_team_name(away_raw), normalize_team_name(home_raw)
    away_abbr, home_abbr = abbr_of(away_name), abbr_of(home_name)

    opening = game.get("opening_line") or {}
    op_dk = opening.get("draftkings")

    snapshots = game.get("snapshots") or []
    latest_date = snapshots[-1]["captured_at"][:10] if snapshots else None

    # Carry the last KNOWN value forward per book AND per field (spread,
    # total, ml_fav, ml_dog independently), rather than reading only the
    # latest snapshot -- see most_recent_book()'s own docstring in lib.py
    # for the two gaps this closes (a book posting nothing that day, and
    # the subtler case of a book posting SOME fields but not others). This
    # never touches the stored snapshot data, only what gets displayed as
    # "current." *_as_of collapses each book's per-field as-of dates down
    # to one row-level date for the template's "(as of M/D)" tag: the
    # OLDEST of spread/total's source dates when either is carried forward
    # from before today, so a row that's mixing a fresh spread with a
    # 3-day-old total gets tagged with the more conservative (older) date
    # rather than silently reading as more current than it is. moneyline
    # staleness isn't a separate tag -- it's covered by the same row-level
    # note since ml_fav/ml_dog print right alongside spread/total in that
    # row.
    def row_as_of(as_of_dict):
        dates = [as_of_dict.get(f) for f in ("spread", "total") if as_of_dict.get(f) and as_of_dict.get(f) != latest_date]
        return min(dates) if dates else None

    cur_dk, dk_as_of_fields = most_recent_book(snapshots, "draftkings")
    cur_cz, cz_as_of_fields = most_recent_book(snapshots, "caesars")
    cur_bm, bm_as_of_fields = most_recent_book(snapshots, "bookmaker_eu")
    cur_fd, fd_as_of_fields = most_recent_book(snapshots, "fanduel")
    dk_as_of = row_as_of(dk_as_of_fields)
    cz_as_of = row_as_of(cz_as_of_fields)
    bm_as_of = row_as_of(bm_as_of_fields)
    fd_as_of = row_as_of(fd_as_of_fields)
    # Synthetic "current" bundle, for the two helpers below that expect a
    # single snapshot-shaped dict (best_available_spread's book-priority
    # fallback, wong_check's caller) -- built from the carried-forward
    # values above so a Wong-teaser check or the spread-side display can't
    # be quietly starved by one book's today-only gap either.
    cur = {"draftkings": cur_dk, "caesars": cur_cz, "bookmaker_eu": cur_bm, "fanduel": cur_fd}

    # FanDuel opening reference: prefer a real opening-day FD capture, but
    # early-season backfills often only had DK confirmed that far back (see
    # setup_week.py-seeded history) -- FD gets checked for the first time
    # later, so fall back to the earliest snapshot that actually has it.
    # IMPORTANT: that fallback snapshot is NOT a real opening line -- it's
    # just the first day this tracker happened to see FD data, which can be
    # days (not months) old, e.g. the Odds API integration adopted 2026-09-07
    # gave FD real data for the first time that day, and the dashboard was
    # labeling that Sept 7 snapshot "open" as if it were the true mid-May
    # opening (caught 2026-09-08 -- Michael flagged the open/current FD rows
    # for Ravens/Colts as looking backwards: the "open" row (-178, actually
    # from Sept 7) read as more current than the "current" row (-120, a bad
    # same-day fetch), which is exactly what a mislabeled fallback produces).
    # fd_opening_is_true_open distinguishes the two cases so the template can
    # be honest about which one it's showing instead of calling both "open."
    op_fd = opening.get("fanduel")
    fd_opening_is_true_open = op_fd is not None
    fd_opening_captured_at = opening.get("captured_at") if fd_opening_is_true_open else None
    if op_fd is None:
        for snap in snapshots:
            if snap.get("fanduel"):
                op_fd = snap["fanduel"]
                fd_opening_captured_at = snap.get("captured_at")
                break
    # (cur_dk/cur_cz/cur_bm/cur_fd already carry-forwarded above -- not
    # re-read from `cur` here, since `cur` is now the same carried-forward
    # bundle, not a fresh snapshot slice.)

    tier, hours, _label = compute_tier(game["kickoff"], reference)

    open_hr = op_dk.get("spread") if op_dk else None
    cur_hr = cur_dk.get("spread") if cur_dk else None

    spread_delta = round(cur_hr - open_hr, 2) if open_hr is not None and cur_hr is not None else None
    total_delta = (
        round(cur_dk["total"] - op_dk["total"], 2)
        if op_dk and cur_dk and op_dk.get("total") is not None and cur_dk.get("total") is not None
        else None
    )
    key_crossed = (
        spread_crossed_key_number(open_hr, cur_hr) if open_hr is not None and cur_hr is not None else False
    )
    flipped = (
        (open_hr < 0) != (cur_hr < 0)
        if open_hr is not None and cur_hr is not None and open_hr != 0 and cur_hr != 0
        else False
    )
    # "Moved" tracks spread movement specifically -- it's grouped on the
    # dashboard with "Key number crossed" and "Favorite flip" (all
    # spread-derived), not with total movement, which is common/noisy and
    # shown separately in the total cell's own delta chip.
    moved = bool(spread_delta)

    cur_spread_side = spread_side_str(cur_hr, away_abbr, home_abbr)
    cur_cz_hr = cur_cz.get("spread") if cur_cz else None
    cur_spread_side_caesars = spread_side_str(cur_cz_hr, away_abbr, home_abbr) if cur_cz_hr is not None else None
    wong_spread_side, wong_spread_source, wong_spread_source_label = best_available_spread(cur, away_abbr, home_abbr)

    return {
        "id": game["game_id"],
        "away": away_name, "home": home_name,
        "awayAbbr": away_abbr, "homeAbbr": home_abbr,
        "kickoff": game["kickoff"],
        "daysOut": round(hours / 24, 1),
        "tier": tier,
        "venueNote": game.get("venue"),
        "openSpreadSide": spread_side_str(open_hr, away_abbr, home_abbr),
        "openTotal": op_dk.get("total") if op_dk else None,
        "curSpreadSide": cur_spread_side,
        "curSpreadHomeRel": cur_hr,
        "curTotalDK": cur_dk.get("total") if cur_dk else None,
        "curSpreadCaesars": spread_side_str(cur_cz.get("spread"), away_abbr, home_abbr) if cur_cz else None,
        # Raw home-relative Caesars spread (Caesars' own pre-formatted
        # display string is curSpreadCaesars above) -- added so the
        # template can compare magnitudes across books numerically for
        # the "Best" row (see bestSpreadCell() there) instead of parsing
        # a display string.
        "curSpreadCaesarsHomeRel": cur_cz_hr,
        "dkMlFav": cur_dk.get("ml_fav") if cur_dk else None,
        "dkMlDog": cur_dk.get("ml_dog") if cur_dk else None,
        # Set only when that book's "current" value was carried forward from
        # an earlier day than this build's latest snapshot (see
        # most_recent_book() in lib.py) -- null means either the book has
        # today's data, or it has never posted at all (curDk/curCz/etc is
        # itself null in that second case, so the template can tell the two
        # "no tag" situations apart already without this field).
        "curDkAsOf": dk_as_of,
        "curCzAsOf": cz_as_of,
        "curBmAsOf": bm_as_of,
        "curFdAsOf": fd_as_of,
        "fdOpeningSpreadSide": spread_side_str(op_fd.get("spread"), away_abbr, home_abbr) if op_fd else None,
        "fdOpeningTotal": op_fd.get("total") if op_fd else None,
        "fdOpeningMlFav": op_fd.get("ml_fav") if op_fd else None,
        "fdOpeningMlDog": op_fd.get("ml_dog") if op_fd else None,
        # True = op_fd came from opening_line.fanduel itself (a real capture from
        # the game's actual opening date). False = it's a fallback -- the earliest
        # snapshot that happened to have FD data, which can be recent, not a real
        # opening line. The template must not label a False case "open" the same
        # way it labels a True case -- see the comment above op_fd's assignment.
        "fdOpeningIsTrueOpen": fd_opening_is_true_open,
        "fdOpeningCapturedAt": fd_opening_captured_at,
        "bmSpreadSide": spread_side_str(cur_bm.get("spread"), away_abbr, home_abbr) if cur_bm else None,
        "bmTotal": cur_bm.get("total") if cur_bm else None,
        "bmMlFav": cur_bm.get("ml_fav") if cur_bm else None,
        "bmMlDog": cur_bm.get("ml_dog") if cur_bm else None,
        # Real current FanDuel moneyline, sourced separately from vegasinsider.com
        # (distinct from fdOpening* above, which is the frozen fanduel.com editorial
        # page and never current) -- added 2026-09-08 specifically so Arbitrage has a
        # second real NY-licensed book to pair against DraftKings, since BookMaker.eu
        # is no longer used as an arbitrage leg (see checkGameArb() in the template).
        "fdMlFav": cur_fd.get("ml_fav") if cur_fd else None,
        "fdMlDog": cur_fd.get("ml_dog") if cur_fd else None,
        "moved": moved,
        "flipped": flipped,
        "keyNumberCrossed": key_crossed,
        "spreadDelta": spread_delta,
        "totalDelta": total_delta,
        "confidence": (game.get("research") or {}).get("confidence"),
        "keyNumberNote": key_number_note(away_abbr, home_abbr, open_hr, cur_hr) if key_crossed else None,
        # Wong-teaser qualification runs off the best available current
        # spread -- Caesars first (Michael places these there), falling back
        # to DraftKings, BookMaker.eu, then FanDuel if Caesars hasn't been
        # captured for this game (see best_available_spread() above). Changed
        # 2026-09-08 from Caesars-only, same day it was added: Caesars-only
        # was excluding most of the slate from consideration just because
        # Caesars data wasn't captured, not because the spread didn't
        # qualify. Per Michael, a flag from any book is enough signal to go
        # check the exact number at Caesars before betting -- it doesn't need
        # to be gated on Caesars data existing. wongSpreadSource below records
        # which book actually supplied the number for each game, so the
        # template can label it honestly per-card rather than assuming
        # Caesars. Total is unchanged: Caesars doesn't reliably expose a
        # total through any source tested so far, so the total-ceiling check
        # still borrows DraftKings' total; wongTotalSource records that.
        "wong": wong_check(wong_spread_side, away_abbr, home_abbr, cur_dk.get("total") if cur_dk else None),
        "wongSpreadSource": wong_spread_source,
        "wongSpreadSourceLabel": wong_spread_source_label,
        # The actual spread string wong_check ran against, independent of
        # whether it qualified -- lets the template show "what number did
        # this get checked against" for any game, not just qualifying ones
        # (e.g. the Wong-trap illustration, which deliberately isn't one of
        # the qualifying legs).
        "wongSpreadSideUsed": wong_spread_side,
        "wongTotalSource": "draftkings",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("template_html")
    ap.add_argument("output_html")
    ap.add_argument("--reference", default=None, help="ISO8601 timestamp, defaults to now (testing only)")
    ap.add_argument("--results", default=None,
                     help="Path to capture_result.py's results store (e.g. results/results.json). "
                          "Omit if nothing has been logged yet -- the Results tab renders empty rather than erroring.")
    ap.add_argument("--rankings", default="model/power_rankings.json",
                     help="Path to update_power_rankings.py's store. Auto-seeded from current Elo "
                          "if it doesn't exist yet -- see that script's docstring. Never overwritten "
                          "here once it exists; the manual order is untouched by every later rebuild.")
    ap.add_argument("--ratings-file", default="model/team_ratings.json",
                     help="Only used to seed --rankings the very first time it's built.")
    args = ap.parse_args()

    from lib import parse_iso
    reference = parse_iso(args.reference) if args.reference else now_utc()

    # Chart links are relative paths from output_html's own directory to each
    # game's <game_id>-graph.html (which generate_chart.py always writes
    # alongside that game's JSON) -- this only resolves when the built page
    # is opened as a local file next to the rest of the tracked folder (see
    # "Two builds, one file" in the dashboard docs); a hosted copy can't
    # reach a path on the user's disk, so the link simply won't resolve
    # there, which is why the page also prints a note about this near the
    # top instead of pretending the links always work.
    out_dir = os.path.dirname(args.output_html) or "."

    records = []
    for path in list_game_files(args.data_dir):
        game = load_game(path)
        if game.get("status") != "active":
            continue
        record = build_game_record(game, reference)
        graph_path = os.path.join(os.path.dirname(path), f"{game['game_id']}-graph.html")
        record["graphLink"] = os.path.relpath(graph_path, out_dir)
        # Passed through verbatim from scripts/simulate_game.py's output --
        # this script doesn't recompute or reshape any of it, same rule as
        # everything else here: one write path per kind of derived data.
        record["sim"] = game.get("simulation")
        # Same passthrough rule as `sim` above -- one write path
        # (apply_game_evaluation.py), this script only collects it.
        record["evaluation"] = game.get("game_evaluation")
        # Same passthrough rule -- one write path each (apply_props.py,
        # apply_market_signal.py), this script only collects and reshapes
        # for display, never recomputes the underlying numbers.
        record["props"] = game.get("props")
        record["marketSignal"] = game.get("market_signal")
        records.append(record)
    records.sort(key=lambda r: r["daysOut"])

    # Props tab -- a flat, cross-game feed of every captured pick (not
    # nested under each game card, since "what should I look at across the
    # whole slate" is the question this answers, same reasoning as the
    # wagertalk_angles feed below). Each entry carries enough game context
    # (matchup, abbreviations, chart link, kickoff) to stand alone on the
    # Props tab without the reader needing to cross-reference the Lines
    # tab. Pending games (checked, nothing posted yet) are summarized
    # separately by count only -- see props_pending_games below -- so the
    # tab can say "N games still waiting on props" without a picks list.
    props_feed = []
    props_pending_games = []
    for rec in records:
        props_block = rec.get("props")
        if not props_block:
            continue
        if props_block.get("status") == "pending" or not props_block.get("picks"):
            props_pending_games.append({"gameId": rec["id"], "awayAbbr": rec["awayAbbr"], "homeAbbr": rec["homeAbbr"]})
            continue
        for pick in props_block["picks"]:
            props_feed.append({
                **pick,
                "gameId": rec["id"],
                "awayAbbr": rec["awayAbbr"],
                "homeAbbr": rec["homeAbbr"],
                "kickoff": rec["kickoff"],
                "graphLink": rec["graphLink"],
            })
    # Highest-conviction picks first; ties broken by soonest kickoff so a
    # closer game's opener (the whole point of this tab, per Michael) sorts
    # above a pick with more time left before it needs action.
    props_feed.sort(key=lambda p: (-p["confidence"], p["kickoff"]))

    # "WagerTalk system" -- historical situational ATS-trend angles
    # (off-a-loss/off-a-win pairings, repeat-home-loser fades, division
    # double-revenge spots, winless-team-small-number-on-the-road, an
    # away-favorite after a home-favorite win, etc.), matched against this
    # week's REAL matchups and REAL prior-season results by the research
    # step -- never against whatever team names a given system happened to
    # be sourced with, since those go stale the moment the schedule turns
    # over. One write path, same rule as `sim`/`research` elsewhere: the
    # research step writes a game's own `wagertalk_angle` field (with `pick`,
    # `pct`, `system`, `why`, `stackCount`), this script only collects,
    # sorts, and passes it through untouched. Only angles clearing >50%
    # are ever written there in the first place, so no filtering needed
    # here -- but sort descending by pct so the strongest historical
    # hit-rate always renders first.
    wagertalk_angles = []
    records_by_id = {r["id"]: r for r in records}
    for path_ in list_game_files(args.data_dir):
        game = load_game(path_)
        if game.get("status") != "active":
            continue
        angle = game.get("wagertalk_angle")
        if not angle:
            continue
        rec = records_by_id.get(game["game_id"])
        away_abbr = rec["awayAbbr"] if rec else abbr_of(normalize_team_name(game["matchup"].split(" @ ")[0]))
        home_abbr = rec["homeAbbr"] if rec else abbr_of(normalize_team_name(game["matchup"].split(" @ ")[1]))
        graph_path = os.path.join(os.path.dirname(path_), f"{game['game_id']}-graph.html")
        pick_team = angle.get("pick_team")
        wagertalk_angles.append({
            "gameId": game["game_id"],
            "awayAbbr": away_abbr,
            "homeAbbr": home_abbr,
            "pick": angle.get("pick"),
            # Which side the pick is actually on, as a team abbreviation --
            # separate from the free-text `pick` display string so the
            # dashboard's Best Bets cross-check (does the simulation and/or
            # a Wong leg land on the SAME team) can compare abbreviations
            # instead of parsing team names back out of display text.
            "pickTeamAbbr": abbr_of(pick_team) if pick_team else None,
            "pct": angle.get("pct"),
            "system": angle.get("system"),
            "why": angle.get("why"),
            "stackCount": angle.get("stack_count", 1),
            # Per-system checklist (added 2026-09-25) -- each entry the
            # template renders as its own checked line with its own hit
            # rate; absent on any angle still on the pre-checkbox shape,
            # in which case the template falls back to system/why above.
            "checks": angle.get("checks"),
            "graphLink": os.path.relpath(graph_path, out_dir),
        })
    # Tie-break on stack count (more independently-agreeing systems first)
    # when two angles share the same headline pct, so e.g. a 4-system stack
    # outranks a 2-system stack even though both cite an 80% number.
    wagertalk_angles.sort(key=lambda a: (a["pct"] if a["pct"] is not None else 0, a.get("stackCount") or 1), reverse=True)

    # capture_result.py is the only write path for this file -- passed
    # through verbatim here, same "one write path per kind of derived data"
    # rule as `sim` above. A missing or unreadable file is NOT an error:
    # most folders won't have any completed+logged games yet, and the
    # Results tab is written to handle an empty list gracefully.
    results_records = []
    if args.results and os.path.exists(args.results):
        with open(args.results) as f:
            results_store = json.load(f)
        results_records = results_store.get("games", [])
    # Team abbreviations aren't stored in the results file itself (that file
    # is capture_result.py's raw facts -- final score, closing line, grading
    # -- not a display layer); derived here the same way GAMES records get
    # theirs, so the Results tab can show "SEA 13, NE 10" instead of full
    # team names without duplicating the ABBR table into a second file.
    for r in results_records:
        r["awayAbbr"] = abbr_of(normalize_team_name(r["awayTeam"]))
        r["homeAbbr"] = abbr_of(normalize_team_name(r["homeTeam"]))
    results_records.sort(key=lambda r: (r.get("week") or "", r.get("kickoff") or ""))

    # Power Rankings tab -- a personal, manually-ordered team list (dragged
    # in the browser, persisted to that browser's localStorage) alongside
    # an auto-computed win/loss streak per team, refreshed every build so a
    # neglected ranking still shows what's happened recently. The ORDER
    # itself is never touched here -- see update_power_rankings.py's
    # docstring for why -- this only bootstraps a first-ever file so a
    # brand-new browser has *something* to seed from.
    rankings_seeded_this_run = not os.path.exists(args.rankings)
    if rankings_seeded_this_run:
        seed_order = seed_order_from_elo(args.ratings_file)
        save_rankings(args.rankings, seed_order, "elo_seed")
    with open(args.rankings) as f:
        rankings_doc = json.load(f)
    streaks = compute_team_streaks(args.data_dir)

    # "Market" comparison -- how the current Elo model (team_ratings.json,
    # which moves with every real result logged via log_result.py) would
    # rank each team RIGHT NOW, next to Michael's own manual order, purely
    # as a reference point. His manual order is still the one and only
    # thing driving the actual page (game cards, "Your rank" lines,
    # everything) -- this never touches or overwrites `rankings_doc`
    # itself, it's a read-only side-by-side delta computed fresh every
    # build so it's never stale. "Market" here means specifically THIS
    # project's own Elo model, not Vegas or any third-party consensus
    # ranking -- the dashboard copy says so explicitly so it's never
    # mistaken for an outside source.
    elo_order = seed_order_from_elo(args.ratings_file)
    elo_rank_of = {team: i + 1 for i, team in enumerate(elo_order)}
    user_order_for_delta = rankings_doc.get("order") or list(TEAM_NAMES)
    market_delta = {
        team: (i + 1) - elo_rank_of.get(team, i + 1)
        for i, team in enumerate(user_order_for_delta)
    }

    rankings_payload = {
        "order": rankings_doc.get("order") or list(TEAM_NAMES),
        "seededFrom": rankings_doc.get("seeded_from"),
        "lastUpdated": rankings_doc.get("last_updated"),
        "streaks": {team: (s if s.get("result") else None) for team, s in streaks.items()},
        "marketRank": elo_rank_of,
        "marketDelta": market_delta,
    }

    with open(args.template_html) as f:
        template = f.read()

    games_json = json.dumps(records, separators=(",", ":"))
    placeholder = "__GAMES_JSON__"
    if template.count(placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {placeholder} in template, found {template.count(placeholder)}"}))
        sys.exit(1)
    output = template.replace(placeholder, games_json)

    results_json = json.dumps(results_records, separators=(",", ":"))
    results_placeholder = "__RESULTS_JSON__"
    if output.count(results_placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {results_placeholder} in template, found {output.count(results_placeholder)}"}))
        sys.exit(1)
    output = output.replace(results_placeholder, results_json)

    rankings_json = json.dumps(rankings_payload, separators=(",", ":"))
    rankings_placeholder = "__RANKINGS_JSON__"
    if output.count(rankings_placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {rankings_placeholder} in template, found {output.count(rankings_placeholder)}"}))
        sys.exit(1)
    output = output.replace(rankings_placeholder, rankings_json)

    wagertalk_angles_json = json.dumps(wagertalk_angles, separators=(",", ":"))
    wagertalk_angles_placeholder = "__WAGERTALK_ANGLES_JSON__"
    if output.count(wagertalk_angles_placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {wagertalk_angles_placeholder} in template, found {output.count(wagertalk_angles_placeholder)}"}))
        sys.exit(1)
    output = output.replace(wagertalk_angles_placeholder, wagertalk_angles_json)

    props_payload = {"picks": props_feed, "pendingGames": props_pending_games}
    props_json = json.dumps(props_payload, separators=(",", ":"))
    props_placeholder = "__PROPS_JSON__"
    if output.count(props_placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {props_placeholder} in template, found {output.count(props_placeholder)}"}))
        sys.exit(1)
    output = output.replace(props_placeholder, props_json)

    # Every build stamps the actual build time into the page (rendered
    # client-side as "refreshed <date/time>" next to the game count) so a
    # reader can tell a same-day re-run (mid-day, ad hoc, for last-minute
    # research) apart from a page that's just showing yesterday's data --
    # this was previously nowhere on the page, so two builds hours apart
    # were visually indistinguishable. Same assert-exactly-one pattern as
    # __GAMES_JSON__ above, so a template edit that drops or duplicates the
    # placeholder fails loudly at build time instead of shipping quietly
    # wrong.
    built_at_placeholder = "__BUILT_AT__"
    if output.count(built_at_placeholder) != 1:
        print(json.dumps({"error": f"expected 1 occurrence of {built_at_placeholder} in template, found {output.count(built_at_placeholder)}"}))
        sys.exit(1)
    output = output.replace(built_at_placeholder, reference.strftime("%Y-%m-%dT%H:%M:%SZ"))

    with open(args.output_html, "w") as f:
        f.write(output)

    # Also refresh dashboard/line-watch.html (same directory as output_html)
    # -- the page's own copy tells the reader to bookmark this exact local
    # file for one-click access to tomorrow's data, but nothing was ever
    # writing to it: every run only produced the scratch build (this
    # function's output_html, conventionally *-build.html) that then gets
    # staged and published to claude.ai, leaving the bookmarked local copy
    # frozen at whatever it was last hand-copied to. Caught live on
    # 2026-09-08: line-watch.html was still the 2026-09-07 build (missing
    # that day's snapshots and the entire "Bets to consider" section) while
    # line-watch-build.html and the published Artifact were both current.
    # Writing it here, every build, makes the local bookmark and the
    # published Artifact two views of the same always-current output rather
    # than two files that can silently drift apart.
    canonical = os.path.join(os.path.dirname(args.output_html) or ".", "line-watch.html")
    if os.path.abspath(canonical) != os.path.abspath(args.output_html):
        with open(canonical, "w") as f:
            f.write(output)

    qualifying = [r["id"] for r in records if r["wong"] and r["wong"]["qualifies"]]
    carried = [r["id"] for r in records if r["curDkAsOf"] or r["curCzAsOf"] or r["curBmAsOf"] or r["curFdAsOf"]]
    print(json.dumps({
        "output_html": args.output_html,
        "games": len(records),
        "wong_qualifiers": qualifying,
        "key_number_notes": [r["id"] for r in records if r["keyNumberNote"]],
        "carried_forward_lines": carried,
        "results_logged": len(results_records),
        "power_rankings_seeded_this_run": rankings_seeded_this_run,
        "power_rankings_teams_with_streak": len([1 for s in streaks.values() if s.get("result")]),
        "wagertalk_angles": [a["gameId"] for a in wagertalk_angles],
        "props_picks": len(props_feed),
        "props_pending_games": [g["gameId"] for g in props_pending_games],
        "market_signal_games": [r["id"] for r in records if r.get("marketSignal")],
    }))


if __name__ == "__main__":
    main()
