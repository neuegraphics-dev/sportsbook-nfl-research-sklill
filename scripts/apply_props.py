#!/usr/bin/env python3
"""Write a game's `props` block -- the opening player-prop numbers to
watch (receptions / rec yards / rush yards / pass yards / completions /
attempts), each with a one-sentence reason to take the side. The only
write path for this block, same convention as apply_injury_adjustment.py
for injury_adjustments and apply_game_evaluation.py for game_evaluation:
never hand-edit this block.

Why this exists (per Michael, 2026-09-24): props get bet into one
direction within the first ~20 minutes of posting, so the number that
matters is the OPENER, not whatever the number has drifted to by the time
someone looks. This script enforces that: the first `line` and its
timestamp recorded for a given (player, market) become that pick's
`opening_line`/`opening_captured_at` and are NEVER overwritten by a later
call -- only `current_line`/`current_captured_at` update on re-runs, so a
pick's movement since open stays visible without losing the number that
actually mattered.

Not every game has posted props yet when this runs (some books trickle
props out over several days). Pass --status pending with --picks '[]' to
record "checked, nothing posted yet" -- the daily-line-report run behavior
re-checks a pending game every cycle until it flips to captured. Passing
--status captured with a real picks list locks in whichever picks are new
this call as openers; a pick already on file keeps its opener untouched
and only has its current_line/lean/confidence/reasoning refreshed.

Multifactor reasoning (added 2026-09-24 per Michael): `reasoning` stays the
one-sentence headline, but every pick now also carries a `factors` list --
each a separate, stat-backed reason with a direction relative to the lean
("for" / "against" / "neutral"). The more independent factors line up in
our direction, the higher confidence is allowed to go. Rules enforced here:
  - At least MIN_FACTORS factors per pick, each with a real stat in `detail`.
  - Optional `matchup` block: {"opp_rank_vs_pos": 1-32, "rank_basis": "..."},
    rank 1 = TOUGHEST defense vs that position, 32 = softest.
  - Tough-matchup cap: an OVER into a top-10 defense vs the position (or an
    UNDER into a bottom-10 one, rank >= 23) is capped at TOUGH_MATCHUP_CAP.
  - Net alignment (for - against) must be >= 1 for confidence >= 5, and
    >= 3 with at most one "against" for confidence >= 7.
The script stores an `alignment` summary {for, against, neutral, net} on
each pick so the dashboard can show the scorecard without recounting.

Factor checklist to research for every pick (aim for 4-6, each with a number):
  - Matchup: opp D rank vs the position, 1-32 (footballdb.com/fantasy-football/
    points-allowed.html?pos=RB|WR|TE|QB) + yds/g allowed to that position.
    Early season (<4 games), also note WHO they faced -- blend with last season.
  - Volume/usage: carries, targets, target share, snap share, last 2-3 games.
  - Hit rate: games over this exact line so far.
  - Game script: spread/total from this game's own snapshots (fav = rush volume,
    dog = pass volume). Public bet%/handle% is NOT script -- label it neutral.
  - Injuries: the player's own status, his backfield/WR-room competition, and
    the opponent's key defenders / QB.
  - Offense trend: team pass/rush yds per game and rank, QB changes.
Confidence ~ net alignment: 1-2 net -> 4-5, 3 net -> 6, 4+ net with <=1 against -> 7-8.

--merge keeps picks already on file that aren't in this call (default
behavior still drops them, unchanged).

Usage:
  python3 apply_props.py <game_file.json> --status captured \
    --picks '[{"player":"Josh Jacobs","team":"home","position":"RB",
                "market":"rush_yards","line":74.5,"book":"DraftKings",
                "lean":"over","confidence":6,
                "reasoning":"One sentence on why.",
                "matchup":{"opp_rank_vs_pos":27,"rank_basis":"RB fantasy pts allowed, Wks 1-2"},
                "factors":[{"label":"Matchup","dir":"for","detail":"Opp 27th vs RB, 118 RB rush yds/g allowed"},
                           {"label":"Volume","dir":"for","detail":"20+ carries both games"},
                           {"label":"Script","dir":"for","detail":"-6.5 home fav"}]}]' \
    --source-note "DK props page, Tue AM"

  python3 apply_props.py <game_file.json> --status pending --picks '[]'
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from lib import player_key  # Phase 3 alias map
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc, save_game

VALID_MARKETS = ("receptions", "rec_yards", "rush_yards", "pass_yards", "completions", "attempts")
VALID_TEAMS = ("home", "away")
VALID_LEANS = ("over", "under")
MAX_REASON_CHARS = 240  # soft guard for "one sentence" -- not a hard grammar check
VALID_DIRS = ("for", "against", "neutral")
MIN_FACTORS = 3
MAX_FACTOR_DETAIL_CHARS = 180
TOUGH_MATCHUP_TOP_N = 10      # rank 1..10 = top-10 toughest defense vs the position
TOUGH_MATCHUP_CAP = 4         # max confidence when leaning INTO a tough matchup


RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model", "prop_rules.json")


def load_rules():
    """Approved prop rules (model/prop_rules.json, approved by Michael 2026-10-13).
    Strong markets get +1 confidence; weak markets get -1 plus an automatic 'against'
    factor showing that market's graded hit rate. 7+ capped at 6 pending review.
    Stake follows confidence on the 1-5% bankroll scale (0-3 -> $0, 4 -> $6, 5 -> $12, 6+ -> $18 cap)."""
    with open(RULES_PATH) as f:
        r = json.load(f)
    stats = {}
    store = os.path.join(os.path.dirname(RULES_PATH), "..", "results", "props_results.json")
    if os.path.exists(store):
        for row in json.load(open(store)).get("props", []):
            if row.get("result") in ("hit", "miss"):
                h, n = stats.get(row["market"], (0, 0))
                stats[row["market"]] = (h + (row["result"] == "hit"), n + 1)
    return {
        "bump_markets": set(r["confidence_bump"]["qualifying_markets"]),
        "penalty_markets": set((r.get("confidence_penalty") or {}).get("markets", [])),
        "market_stats": stats,
        "default_stake": r["stake"]["default_prop_stake"],
        "stake_by_conf": r["stake"].get("by_confidence", {}),
        "c7_cap": 6,
        "c7_threshold": 7,
    }


def apply_rules(p, rules):
    """Adjust one pick in place per the approved rules, before the gate checks run."""
    base = p["confidence"]
    conf = base
    factors = [f for f in (p.get("factors") or []) if f.get("label") != "Market history"]
    if p["market"] in rules["bump_markets"]:
        conf = base + 1
    elif p["market"] in rules["penalty_markets"]:
        conf = base - 1
        h, n = rules["market_stats"].get(p["market"], (0, 0))
        pct = f"{round(100 * h / n)}%" if n else "no graded picks"
        factors.append({"label": "Market history", "dir": "against",
                        "detail": f"{p['market']} picks hit {h}/{n} ({pct}) in graded Line Watch history"})
    p["factors"] = factors
    if conf >= rules["c7_threshold"]:
        conf = min(conf, rules["c7_cap"])
    conf = max(conf, 0)
    p["confidence_base"] = base
    p["confidence"] = conf
    # Stake follows the skill's Bet sizing by confidence (props capped at the default prop stake).
    # 1-5% bankroll scale (2026-11-01): 4 -> $6, 5 -> $12, 6+ -> $18 (props cap at Tier 3).
    sb = rules["stake_by_conf"]
    if conf <= 3:
        p["stake"] = sb.get("0-3", 0)
    elif conf < 5:
        p["stake"] = sb.get("4", 6)
    elif conf < 6:
        p["stake"] = sb.get("5", 12)
    else:
        p["stake"] = sb.get("6+", rules["default_stake"])
    p["rules_version"] = "2026-10-13-c"
    return p


def alignment_of(factors):
    c = {d: sum(1 for f in factors if f["dir"] == d) for d in VALID_DIRS}
    return {"for": c["for"], "against": c["against"], "neutral": c["neutral"],
            "net": c["for"] - c["against"]}


def validate_factors(i, p):
    factors = p.get("factors")
    if not isinstance(factors, list) or len(factors) < MIN_FACTORS:
        raise ValueError(f"picks[{i}] needs a 'factors' list with >= {MIN_FACTORS} stat-backed reasons "
                         f"(matchup rank, volume/usage, game script, recent trend, injuries...)")
    for j, f in enumerate(factors):
        for field in ("label", "dir", "detail"):
            if not isinstance(f.get(field), str) or not f[field].strip():
                raise ValueError(f"picks[{i}].factors[{j}] missing '{field}'")
        if f["dir"] not in VALID_DIRS:
            raise ValueError(f"picks[{i}].factors[{j}] dir must be one of {VALID_DIRS}")
        if len(f["detail"]) > MAX_FACTOR_DETAIL_CHARS:
            raise ValueError(f"picks[{i}].factors[{j}] detail > {MAX_FACTOR_DETAIL_CHARS} chars -- one stat, one clause")
    m = p.get("matchup")
    if m is not None:
        r = m.get("opp_rank_vs_pos") if isinstance(m, dict) else None
        if not isinstance(r, int) or not (1 <= r <= 32):
            raise ValueError(f"picks[{i}] matchup.opp_rank_vs_pos must be an int 1-32 (1 = toughest)")
        tough = (p["lean"] == "over" and r <= TOUGH_MATCHUP_TOP_N) or \
                (p["lean"] == "under" and r >= 33 - TOUGH_MATCHUP_TOP_N)
        if tough and p["confidence"] > TOUGH_MATCHUP_CAP:
            raise ValueError(f"picks[{i}] leans {p['lean']} into a top-{TOUGH_MATCHUP_TOP_N} tough matchup "
                             f"(rank {r}) -- confidence capped at {TOUGH_MATCHUP_CAP}")
    a = alignment_of(factors)
    if p["confidence"] >= 5 and a["net"] < 1:
        raise ValueError(f"picks[{i}] confidence {p['confidence']} needs net alignment >= 1 (have {a})")
    if p["confidence"] >= 7 and (a["net"] < 3 or a["against"] > 1):
        raise ValueError(f"picks[{i}] confidence {p['confidence']} needs net >= 3 and <= 1 against (have {a})")
    return a


RULES = load_rules()


def validate_picks(picks):
    if not isinstance(picks, list):
        raise ValueError("--picks must be a JSON list (use [] for none/pending)")
    seen = set()
    for i, p in enumerate(picks):
        for field in ("player", "team", "position", "market", "line", "book", "lean", "confidence", "reasoning"):
            if field not in p:
                raise ValueError(f"picks[{i}] missing required field '{field}': {p}")
        if not isinstance(p["player"], str) or not p["player"].strip():
            raise ValueError(f"picks[{i}] player must be a non-empty string")
        if re.match(r"^[A-Z]\.\s", p["player"].strip()):
            raise ValueError(f"picks[{i}] player {p['player']!r} is initials only -- write the full name as on the "
                             f"team roster (confirm player, team, and position against a roster source first)")
        if p["team"] not in VALID_TEAMS:
            raise ValueError(f"picks[{i}] team must be one of {VALID_TEAMS}, got {p['team']!r}")
        if p["market"] not in VALID_MARKETS:
            raise ValueError(f"picks[{i}] market must be one of {VALID_MARKETS}, got {p['market']!r}")
        if not isinstance(p["line"], (int, float)):
            raise ValueError(f"picks[{i}] line must be a number, got {p['line']!r}")
        if p["lean"] not in VALID_LEANS:
            raise ValueError(f"picks[{i}] lean must be one of {VALID_LEANS}, got {p['lean']!r}")
        if not isinstance(p["confidence"], (int, float)) or not (0 <= p["confidence"] <= 10):
            raise ValueError(f"picks[{i}] confidence must be a number 0-10, got {p['confidence']!r}")
        if not isinstance(p["reasoning"], str) or not p["reasoning"].strip():
            raise ValueError(f"picks[{i}] reasoning must be a non-empty string")
        if len(p["reasoning"]) > MAX_REASON_CHARS:
            raise ValueError(
                f"picks[{i}] reasoning is {len(p['reasoning'])} chars -- keep it to one sentence "
                f"(<= {MAX_REASON_CHARS} chars). Put extra detail in game_evaluation instead."
            )
        apply_rules(p, RULES)
        m_pa = re.search(r"pass at\s*([0-9]+(?:\.[0-9]+)?)", p["reasoning"])
        if m_pa:
            pa = float(m_pa.group(1))
            if p["lean"] == "under" and pa >= p["line"]:
                raise ValueError(f"picks[{i}] under {p['line']} has pass-at {pa} -- for an UNDER the pass-at must be LOWER "
                                 f"than the line (a lower line is the worse number)")
            if p["lean"] == "over" and pa <= p["line"]:
                raise ValueError(f"picks[{i}] over {p['line']} has pass-at {pa} -- for an OVER the pass-at must be HIGHER "
                                 f"than the line (a higher line is the worse number)")
        p["alignment"] = validate_factors(i, p)
        key = (player_key(p["player"]), p["market"])
        if key in seen:
            raise ValueError(f"picks[{i}] duplicate (player, market) in this call: {key}")
        seen.add(key)
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_file")
    ap.add_argument("--status", required=True, choices=("captured", "pending"),
                     help="'captured' if --picks has at least one real pick this cycle (or you're "
                          "explicitly recording zero strong plays after a real check); 'pending' if "
                          "props haven't posted for this game yet -- re-check next cycle.")
    ap.add_argument("--picks", required=True, help="JSON list of pick objects, or '[]'.")
    ap.add_argument("--merge", action="store_true",
                    help="Keep picks already on file that aren't in this call (e.g. updating one pick "
                         "on a game that has several).")
    ap.add_argument("--source-note", default=None, help="Where these numbers came from (book/page).")
    args = ap.parse_args()

    game = load_game(args.game_file)
    block = game.get("props") or {"last_checked": None, "status": "pending", "source_note": None, "picks": []}

    try:
        new_picks = validate_picks(json.loads(args.picks))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    now_str = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

    existing_by_key = {(player_key(p["player"]), p["market"]): p for p in block.get("picks", [])}
    merged_picks = []
    opened_count = 0
    for np_ in new_picks:
        key = (player_key(np_["player"]), np_["market"])
        old = existing_by_key.get(key)
        if old is not None:
            # Opener is frozen forever -- only refresh the "as of now" fields.
            merged = dict(old)
            merged["current_line"] = np_["line"]
            merged["current_captured_at"] = now_str
            merged["book"] = np_["book"]
            merged["team"] = np_["team"]
            merged["position"] = np_["position"]
            merged["lean"] = np_["lean"]
            merged["confidence"] = np_["confidence"]
            merged["reasoning"] = np_["reasoning"]
            for k in ("factors", "matchup", "alignment", "confidence_base", "stake", "rules_version"):
                if k in np_:
                    merged[k] = np_[k]
        else:
            merged = dict(np_)
            merged["opening_line"] = np_["line"]
            merged["opening_captured_at"] = now_str
            merged["current_line"] = np_["line"]
            merged["current_captured_at"] = now_str
            del merged["line"]
            opened_count += 1
        merged_picks.append(merged)

    # Picks that existed before but weren't in this call's list are dropped
    # silently -- e.g. a book pulled a prop, or this cycle only re-checked a
    # subset. Re-pass the full current list each call to keep everything.
    if args.merge:
        new_keys = {(player_key(p["player"]), p["market"]) for p in merged_picks}
        merged_picks += [p for k, p in existing_by_key.items() if k not in new_keys]
    block["picks"] = merged_picks
    block["status"] = args.status
    block["last_checked"] = now_str
    if args.source_note is not None:
        block["source_note"] = args.source_note

    game["props"] = block
    save_game(args.game_file, game)

    print(json.dumps({
        "game_id": game["game_id"],
        "status": block["status"],
        "pick_count": len(block["picks"]),
        "newly_opened": opened_count,
    }))


if __name__ == "__main__":
    main()
