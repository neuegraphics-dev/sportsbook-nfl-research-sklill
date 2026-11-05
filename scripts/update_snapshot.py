#!/usr/bin/env python3
"""Append a snapshot for one game, recompute triggers, and handle
opening-line capture. This is the single write-path for game JSON files --
always go through this script rather than hand-editing the JSON, so
opening_line and triggers stay consistent.

Snapshots are timestamped (not just dated) on purpose: Tier 1 games get
checked every 3-4 hours in the last 48 hours before kickoff, and that
intraday movement is the whole point of the chart in that window. Tier 2/3
games only get checked once a day, so pass --skip-if-same-day for those --
it no-ops (prints skipped:true, doesn't touch the file) if a snapshot from
today already exists, so a Tier 2 game accidentally re-run twice in one day
doesn't pile up duplicate points.

Usage:
  python3 update_snapshot.py <game_file.json> \
    --bookmaker '{"spread":-3.0,"total":44.5,"ml_fav":-160,"ml_dog":138}' \
    --draftkings '{"spread":-3.5,"total":45.0,"ml_fav":-168,"ml_dog":142}' \
    --fanduel '{"spread":-3.0,"total":44.5,"ml_fav":-160,"ml_dog":138}' \
    --caesars '{"spread":-3.0,"total":44.5,"ml_fav":-162,"ml_dog":140}' \
    [--skip-if-same-day] \
    [--note "DK moved off BookMaker.eu without matching news"] \
    [--research '{"spread_lean": "...", ...}'] \
    [--captured-at 2026-09-17T18:00:00Z]

Pass the literal string null for a book that hasn't posted a line yet --
never omit the flag and never guess a number.

--bookmaker is BookMaker.eu (lines.bookmaker.eu), the sharp/no-vig
reference book -- replaces Pinnacle, which was confirmed unavailable from
every tested source (see SKILL.md "Line fetching"). It's a public odds
board with no login wall, so it's fetchable directly every run.

Prints one JSON line describing what happened (opening_capture, skipped,
triggers, gap sizes) so the caller can decide what goes in the report.
"""
import argparse
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from lib import SHARP_BOOK, gap, load_game, now_utc, save_game, spread_crossed_key_number, today_str

SPREAD_GAP_THRESHOLD = 1.0   # points, US book vs the sharp book -- a 0.5 gap is routine cross-book noise
TOTAL_GAP_THRESHOLD = 1.5    # points, US book vs the sharp book
STEAM_SPREAD_MOVE = 1.0      # points, vs the immediately prior snapshot, any single US book
STEAM_TOTAL_MOVE = 1.5


def parse_book_arg(raw):
    if raw is None or raw.strip().lower() == "null":
        return None
    return json.loads(raw)


# (CLI flag name, JSON schema key) -- the flag stays short/typeable, the schema key
# stays explicit about which book it is.
BOOK_FLAGS = (("bookmaker", "bookmaker_eu"), ("draftkings", "draftkings"),
              ("fanduel", "fanduel"), ("caesars", "caesars"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--captured-at", default=None, help="ISO8601 timestamp, defaults to now (UTC)")
    for flag, _key in BOOK_FLAGS:
        ap.add_argument(f"--{flag}", required=True, help="JSON line object or 'null'")
    ap.add_argument("--skip-if-same-day", action="store_true",
                     help="No-op if the most recent snapshot is already from today (use for Tier 2/3)")
    ap.add_argument("--note", default=None)
    ap.add_argument("--research", default=None, help="JSON object to replace the research block")
    args = ap.parse_args()

    game = load_game(args.game_file)
    captured_at = args.captured_at or now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    date = captured_at[:10]

    prev = game["snapshots"][-1] if game["snapshots"] else None

    if args.skip_if_same_day and prev is not None and prev["captured_at"][:10] == date:
        # Still a no-op on price data (today's snapshot stands as-is, no
        # duplicate point) -- but a research pass run later the same day
        # shouldn't be blocked from attaching, or every research-only
        # follow-up would have to fabricate a redundant snapshot just to
        # get --research applied.
        research_attached = False
        if args.research:
            research = json.loads(args.research)
            research.setdefault("last_run", date)
            game["research"] = research
            save_game(args.game_file, game)
            research_attached = True
        print(json.dumps({
            "game_id": game["game_id"],
            "skipped": True,
            "reason": "already have a snapshot from today",
            "last_captured_at": prev["captured_at"],
            "research_attached": research_attached,
        }))
        return

    lines = {key: parse_book_arg(getattr(args, flag)) for flag, key in BOOK_FLAGS}

    opening_capture = game.get("opening_line") is None
    if opening_capture:
        game["opening_line"] = {"captured_at": date, **lines}
        if args.note:
            game["opening_line"]["note"] = args.note

    snapshot = {"captured_at": captured_at, **lines}
    if args.note:
        snapshot["note"] = args.note
    game["snapshots"].append(snapshot)
    game["snapshots"].sort(key=lambda s: s["captured_at"])

    # --- Triggers -----------------------------------------------------
    sharp = lines[SHARP_BOOK]
    gaps = {}
    gap_trigger = False
    if sharp is not None:
        for book in ("draftkings", "fanduel", "caesars"):
            bl = lines[book]
            if bl is None:
                continue
            spread_gap = gap(bl.get("spread"), sharp.get("spread"))
            total_gap = gap(bl.get("total"), sharp.get("total"))
            gaps[book] = {"spread_gap": spread_gap, "total_gap": total_gap}
            if (spread_gap is not None and abs(spread_gap) >= SPREAD_GAP_THRESHOLD) or (
                total_gap is not None and abs(total_gap) >= TOTAL_GAP_THRESHOLD
            ):
                gap_trigger = True

    key_number_trigger = False
    steam_trigger = False
    if prev is not None:
        for book in ("draftkings", "fanduel", "caesars", SHARP_BOOK):
            prev_line = prev.get(book)
            cur_line = lines[book]
            if prev_line is None or cur_line is None:
                continue
            if spread_crossed_key_number(prev_line.get("spread"), cur_line.get("spread")):
                key_number_trigger = True
            spread_move = gap(cur_line.get("spread"), prev_line.get("spread"))
            total_move = gap(cur_line.get("total"), prev_line.get("total"))
            if (spread_move is not None and abs(spread_move) >= STEAM_SPREAD_MOVE) or (
                total_move is not None and abs(total_move) >= STEAM_TOTAL_MOVE
            ):
                steam_trigger = True

    game["triggers"] = {
        "book_vs_sharp_gap": gap_trigger,
        "key_number_crossed": key_number_trigger,
        # Raw-movement heuristic, not a verified "no news" judgment -- the
        # script can't check beat-reporter news. Treat this as "worth
        # checking for news"; only call it steam in the report once you've
        # confirmed no matching news explains the move.
        "steam_no_news": steam_trigger,
    }

    if args.research:
        research = json.loads(args.research)
        research.setdefault("last_run", date)
        game["research"] = research

    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"],
        "captured_at": captured_at,
        "skipped": False,
        "opening_capture": opening_capture,
        "triggers": game["triggers"],
        "sharp_gaps": gaps,
        "sharp_missing": sharp is None,
    }))


if __name__ == "__main__":
    main()
