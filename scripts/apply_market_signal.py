#!/usr/bin/env python3
"""Write a game's `market_signal` block -- public bet% vs handle%(money%)
splits for spread/total/moneyline, plus a derived reverse-line-move (RLM)
read against this game's own logged line movement. The only write path
for this block, same convention as the other apply_*.py scripts.

Why this exists (per Michael, 2026-09-24): a lopsided ticket count means
little on its own -- the useful signal is handle (money) skewed one way
while the LINE hasn't moved that way (or moved the other way), which
means the side taking fewer, bigger bets is the side the book is more
worried about. This script does that comparison itself so it doesn't have
to be re-derived by eye on the dashboard: it reads the game's own
opening_line and most recent snapshot (preferring the sharp book,
BookMaker.eu, same as everywhere else in this project) to see which way
the spread has actually moved, then flags rlm=true when a >=60/40 handle
split sits on the side the line did NOT move toward.

Percentages are all home-relative for spread/moneyline (bets_pct_home /
handle_pct_home = the SHARE on the home team, not on "the favorite") and
over-relative for total (bets_pct_over / handle_pct_over), matching this
project's home-relative spread-sign convention elsewhere -- never pass a
favorite-relative number here without converting it first.

Usage:
  python3 apply_market_signal.py <game_file.json> \
    --spread '{"bets_pct_home":47,"handle_pct_home":83}' \
    --total '{"bets_pct_over":33,"handle_pct_over":8}' \
    --moneyline '{"bets_pct_home":51,"handle_pct_home":35}' \
    --source cleatz.com

Omit any of --spread/--total/--moneyline to leave that market's split
untouched on a re-run (e.g. only the total posted this time). Pass 'null'
for a market with no data yet.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import SHARP_BOOK, load_game, most_recent_field, now_utc, save_game

RLM_THRESHOLD = 60.0  # >=60% handle on one side counts as "lopsided" for this check


def validate_split(name, d, pct_fields):
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError(f"--{name} must be a JSON object or 'null'")
    for f in pct_fields:
        if f not in d:
            raise ValueError(f"--{name} missing required field '{f}'")
        v = d[f]
        if not isinstance(v, (int, float)) or not (0 <= v <= 100):
            raise ValueError(f"--{name}.{f} must be a number 0-100, got {v!r}")
    return d


BOOK_ORDER = (SHARP_BOOK, "draftkings", "caesars", "fanduel")


def freshest_book(game):
    """The book with the most recently captured spread (ties broken by
    BOOK_ORDER). Added 2026-10-08: comparing DraftKings' open to a stale
    DraftKings 'current' (the rotowire DK page stopped refreshing for 3 days)
    produced false RLM reads while Caesars/FanDuel had clearly moved.
    Opening and current must come from the SAME book, and that book must be
    one that is still being refreshed."""
    snaps = game.get("snapshots") or []
    best = None
    for i, book in enumerate(BOOK_ORDER):
        val, ts = most_recent_field(snaps, book, "spread")
        if val is None:
            continue
        key = (ts or "", -i)
        if best is None or key > best[0]:
            best = (key, book)
    return best[1] if best else None


def current_home_spread(game, book=None):
    snaps = game.get("snapshots") or []
    book = book or freshest_book(game)
    if not book:
        return None
    val, _ = most_recent_field(snaps, book, "spread")
    return val


def opening_home_spread(game, book=None):
    book = book or freshest_book(game)
    if not book:
        return None
    opening = game.get("opening_line") or {}
    line = opening.get(book)
    if line and line.get("spread") is not None:
        return line["spread"]
    for snap in game.get("snapshots") or []:   # earliest snapshot this book posted
        l = snap.get(book)
        if l and l.get("spread") is not None:
            return l["spread"]
    return None


def compute_rlm(game, spread_split):
    """Returns (rlm: bool|None, note: str|None). None means 'not enough
    data to say' -- never guess when opening or current spread is missing."""
    if not spread_split:
        return None, None
    handle = spread_split["handle_pct_home"]
    if handle >= RLM_THRESHOLD:
        majority_side, other_side = "home", "away"
    elif handle <= (100 - RLM_THRESHOLD):
        majority_side, other_side = "away", "home"
    else:
        return False, "Handle split isn't lopsided enough (40-60%) to call a majority side."

    book = freshest_book(game)
    open_spread = opening_home_spread(game, book)
    cur_spread = current_home_spread(game, book)
    if open_spread is None or cur_spread is None:
        return None, "Can't compute RLM -- missing opening or current spread for this game."

    delta = cur_spread - open_spread  # negative = line moved toward home being MORE favored
    moved_toward_home = delta < -0.05
    moved_toward_away = delta > 0.05
    # Three-way, not two-way: a lopsided handle with NO movement yet is the
    # normal early-week state (most lines sit flat until closer to kickoff)
    # and isn't evidence of anything -- only an ACTUAL move against the
    # majority-handle side counts as reverse line move. Collapsing
    # "hasn't moved" and "moved against" into one True was flagging most of
    # a fresh week's slate as RLM on day one, which isn't a real signal.
    if majority_side == "home":
        if moved_toward_away:
            return True, (f"{handle:.0f}% of handle is on the home side but the spread moved AGAINST them "
                           f"({open_spread:+g} -> {cur_spread:+g}) -- real reverse line move toward away.")
        if moved_toward_home:
            return False, (f"{handle:.0f}% of handle is on the home side and the spread has moved toward "
                            f"them ({open_spread:+g} -> {cur_spread:+g}) -- money and line agree, no RLM.")
        return False, (f"{handle:.0f}% of handle is on the home side but the spread hasn't moved yet "
                        f"({open_spread:+g}, unchanged) -- too early to call RLM, not evidence of one.")
    if majority_side == "away":
        if moved_toward_home:
            return True, (f"{100 - handle:.0f}% of handle is on the away side but the spread moved AGAINST them "
                           f"({open_spread:+g} -> {cur_spread:+g}) -- real reverse line move toward home.")
        if moved_toward_away:
            return False, (f"{100 - handle:.0f}% of handle is on the away side and the spread has moved toward "
                            f"them ({open_spread:+g} -> {cur_spread:+g}) -- money and line agree, no RLM.")
        return False, (f"{100 - handle:.0f}% of handle is on the away side but the spread hasn't moved yet "
                        f"({open_spread:+g}, unchanged) -- too early to call RLM, not evidence of one.")
    return False, f"Handle-majority side ({majority_side}) matches the direction the line has actually moved -- no RLM."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--spread", default=None, help='JSON {"bets_pct_home":N,"handle_pct_home":N} or "null"')
    ap.add_argument("--total", default=None, help='JSON {"bets_pct_over":N,"handle_pct_over":N} or "null"')
    ap.add_argument("--moneyline", default=None, help='JSON {"bets_pct_home":N,"handle_pct_home":N} or "null"')
    ap.add_argument("--source", required=True, help="e.g. cleatz.com")
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    if args.spread is None and args.total is None and args.moneyline is None:
        print(json.dumps({"error": "pass at least one of --spread/--total/--moneyline"}))
        sys.exit(1)

    game = load_game(args.game_file)
    block = game.get("market_signal") or {"captured_at": None, "source": None, "spread": None, "total": None, "moneyline": None, "reverse_line_move": None, "rlm_note": None, "note": None}

    try:
        if args.spread is not None:
            block["spread"] = validate_split("spread", json.loads(args.spread), ("bets_pct_home", "handle_pct_home"))
        if args.total is not None:
            block["total"] = validate_split("total", json.loads(args.total), ("bets_pct_over", "handle_pct_over"))
        if args.moneyline is not None:
            block["moneyline"] = validate_split("moneyline", json.loads(args.moneyline), ("bets_pct_home", "handle_pct_home"))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    rlm, rlm_note = compute_rlm(game, block.get("spread"))
    block["reverse_line_move"] = rlm
    block["rlm_note"] = rlm_note
    block["source"] = args.source
    block["captured_at"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.note is not None:
        block["note"] = args.note

    game["market_signal"] = block
    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"],
        "spread": block["spread"],
        "total": block["total"],
        "moneyline": block["moneyline"],
        "reverse_line_move": block["reverse_line_move"],
        "rlm_note": block["rlm_note"],
    }))


if __name__ == "__main__":
    main()
