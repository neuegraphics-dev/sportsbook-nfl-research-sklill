#!/usr/bin/env python3
"""Write a game's `game_evaluation` block -- the analyst-style write-up
shown on the dashboard (Lines tab per-game card, Bet Now, Best Bets, and
Bets to consider). The only write path for this block, same convention as
apply_injury_adjustment.py for injury_adjustments and log_result.py for
final scores: never hand-edit this block.

Why this exists: everything else on the page is a number -- a sim
projection, a market line, a cover percentage. None of it says *why* in
plain English, or what a pro handicapper would actually make of the
matchup once the sim, the injury picture, and the research lean are all
sitting in front of them. This script records that written judgment call
as structured data (so the dashboard can render it consistently across all
four locations) instead of leaving it as scratch notes that never leave a
chat.

The two `analysis` paragraphs should read like a pro sports-betting
analyst's game preview: what the simulation and market are saying, why
(key injuries, home/away, recent form/Elo), and what that means for how to
read the game -- written so Michael can form his own take and go do more
research from there, not as a black-box verdict.

Each `leans` entry is a single market call (spread/total/moneyline) with
its own 0-10 confidence and a short reasoning line. At most one lean per
game should be flagged `best_bet: true` -- the single pick this script's
author would actually put money on for that game, if any (a game can
have zero best-bet leans when nothing clears a real bar).

Usage:
  python3 apply_game_evaluation.py <game_file.json> \
    --analysis '["First paragraph...", "Second paragraph..."]' \
    --leans '[{"market":"spread","pick":"SEA -6.5","confidence":7,"reasoning":"...","best_bet":true}]'

Pass --leans '[]' to explicitly record "no lean worth making" for a game
(e.g. nothing separates the two teams) rather than omitting the flag --
same "never omit, always state explicitly" convention used elsewhere in
this project. Omit --analysis or --leans entirely to leave that part of
an existing block untouched (e.g. re-running --leans after a late-week
line move without rewriting the prose).

Confidence scale (0-10), a judgment call like injury pts -- an honest,
documented estimate, not a computed probability:
  0-2  coinflip or worse, mentioned mainly for context
  3-4  a mild lean, low conviction
  5-6  a real lean, would make it a smaller-than-usual play
  7-8  a strong lean, standard-size play
  9-10 as close to a "the model and the research and the injuries all
       point the same way" case as this project produces -- rare

Factors checklist (added 2026-09-25 per Michael, same idea as apply_props.py's
per-pick scorecard): each lean can optionally carry a `factors` list --
each a separate, named reason with a direction relative to the pick ("for"
/ "against" / "neutral") and a one-clause `detail` naming the actual stat
or fact behind it (an Elo/recent-form gap, an injury pts number, a public-
betting split, a DK-vs-consensus book gap, a situational system's ATS%,
etc -- whatever's actually driving the lean, not restated prose). Optional
-- a lean with no factors renders exactly as before (just the reasoning
line). When present, at least MIN_LEAN_FACTORS are required, each with
label/dir/detail; this script computes and stores `alignment`
({for, against, neutral, net}) the same way apply_props.py does, so the
dashboard can render the same for/against chip-and-checklist treatment
used on prop picks. No confidence-vs-alignment cap is enforced here (game
leans don't have a matchup-rank concept the way player props do) -- the
factors are a legibility aid, not a validation gate.

Usage with factors:
  python3 apply_game_evaluation.py <game_file.json> --leans '[
    {"market":"spread","pick":"BUF -7","confidence":6,
     "reasoning":"Elo margin clears the market number.",
     "factors":[
       {"label":"Elo margin","dir":"for","detail":"+10.2 pts, clears the market number (-7)"},
       {"label":"Recent form","dir":"neutral","detail":"+3.6 pts -- softer than Elo, 6.6pt gap worth flagging"},
       {"label":"Sim cover%","dir":"for","detail":"60.1% of sims have BUF covering -7"}
     ]}
  ]'
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc, save_game

VALID_MARKETS = ("spread", "total", "moneyline")
VALID_DIRS = ("for", "against", "neutral")
MIN_LEAN_FACTORS = 2
MAX_FACTOR_DETAIL_CHARS = 180


def alignment_of(factors):
    c = {d: sum(1 for f in factors if f["dir"] == d) for d in VALID_DIRS}
    return {"for": c["for"], "against": c["against"], "neutral": c["neutral"],
            "net": c["for"] - c["against"]}


def validate_lean_factors(i, factors):
    if not isinstance(factors, list) or len(factors) < MIN_LEAN_FACTORS:
        raise ValueError(f"leans[{i}] factors needs a list with >= {MIN_LEAN_FACTORS} entries "
                         f"(or omit 'factors' entirely to leave the lean plain)")
    for j, f in enumerate(factors):
        for field in ("label", "dir", "detail"):
            if not isinstance(f.get(field), str) or not f[field].strip():
                raise ValueError(f"leans[{i}].factors[{j}] missing '{field}'")
        if f["dir"] not in VALID_DIRS:
            raise ValueError(f"leans[{i}].factors[{j}] dir must be one of {VALID_DIRS}")
        if len(f["detail"]) > MAX_FACTOR_DETAIL_CHARS:
            raise ValueError(f"leans[{i}].factors[{j}] detail > {MAX_FACTOR_DETAIL_CHARS} chars -- one stat, one clause")
    return alignment_of(factors)


def validate_analysis(paragraphs):
    if not isinstance(paragraphs, list):
        raise ValueError("--analysis must be a JSON list of paragraph strings")
    if not paragraphs:
        raise ValueError("--analysis must contain at least one paragraph")
    for i, p in enumerate(paragraphs):
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"--analysis[{i}] must be a non-empty string")


def validate_leans(leans):
    if not isinstance(leans, list):
        raise ValueError("--leans must be a JSON list (use [] for none)")
    best_bet_count = 0
    for i, l in enumerate(leans):
        for field in ("market", "pick", "confidence", "reasoning"):
            if field not in l:
                raise ValueError(f"leans[{i}] missing required field '{field}': {l}")
        if l["market"] not in VALID_MARKETS:
            raise ValueError(f"leans[{i}] has invalid market '{l['market']}' -- must be one of {VALID_MARKETS}")
        if not isinstance(l["pick"], str) or not l["pick"].strip():
            raise ValueError(f"leans[{i}] pick must be a non-empty string")
        if not isinstance(l["confidence"], (int, float)) or not (0 <= l["confidence"] <= 10):
            raise ValueError(f"leans[{i}] confidence must be a number 0-10, got {l['confidence']!r}")
        if not isinstance(l["reasoning"], str) or not l["reasoning"].strip():
            raise ValueError(f"leans[{i}] reasoning must be a non-empty string")
        l.setdefault("best_bet", False)
        if not isinstance(l["best_bet"], bool):
            raise ValueError(f"leans[{i}] best_bet must be a boolean")
        if l["best_bet"]:
            best_bet_count += 1
        if "factors" in l and l["factors"] is not None:
            l["alignment"] = validate_lean_factors(i, l["factors"])
    if best_bet_count > 1:
        raise ValueError(f"at most one lean may be flagged best_bet: true, got {best_bet_count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--analysis", default=None, help="JSON list of paragraph strings. Omit to leave existing analysis untouched.")
    ap.add_argument("--leans", default=None, help="JSON list, or '[]' to clear. Omit to leave existing leans untouched.")
    args = ap.parse_args()

    if args.analysis is None and args.leans is None:
        print(json.dumps({"error": "pass --analysis and/or --leans (use '[]' to explicitly clear leans)"}))
        sys.exit(1)

    game = load_game(args.game_file)
    block = game.get("game_evaluation") or {"last_updated": None, "analysis": [], "leans": []}

    try:
        if args.analysis is not None:
            analysis = json.loads(args.analysis)
            validate_analysis(analysis)
            block["analysis"] = analysis
        if args.leans is not None:
            leans = json.loads(args.leans)
            validate_leans(leans)
            block["leans"] = leans
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    block["last_updated"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    game["game_evaluation"] = block
    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"],
        "paragraphs": len(block["analysis"]),
        "leans": [{"market": l["market"], "pick": l["pick"], "confidence": l["confidence"], "best_bet": l["best_bet"]} for l in block["leans"]],
    }))


if __name__ == "__main__":
    main()
