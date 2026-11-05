#!/usr/bin/env python3
"""PERMANENT build script (promoted from _redesign_transform.py 2026-09-28).
Usage: python3 scripts/build_dashboard_v2.py data scripts/dashboard_template_v2.html dashboard/line-watch-build.html
Fills the template's `const D = __D_JSON__;`, incl. D.propsRecord (player-prop hit rate
from results/props_results.json), per-pick results on D.props, and (2026-10-01)
each pick's own `hist` -- that same player/market's past graded weeks, oldest
first, for the Props section's week-by-week hit/miss chips.

Original note: transform: read the tracked game JSONs and emit the exact `D`
object shape the "Line Watch Redesign" wireframe artifact's JS expects, with
TODAY's real numbers instead of the stale Sept 25 snapshot baked into that
artifact. Not part of the regular pipeline -- scripts/build_dashboard.py
remains the only write path for the production "Line Watch" dashboard.

Sign convention note: this project stores every spread home-relative with
negative = home favored. The Redesign artifact's own D.open/cur/bm/cz use
the OPPOSITE sign (positive = home favored) -- confirmed by reading its own
JS (`homeSpread = g => -g.cur`, and sideStr() treats m>0 as "home favored").
So every spread number written into D here is the NEGATION of this
project's own stored value. model/modelTot/elo/form/cover/over/win% are NOT
flipped -- those already use "positive = home better" in both places.

2026-09-27 additions (Michael asked for these after the redesign UI went
live and Props/Receipts/Teasers came up empty -- the redesign template's JS
already expects D.props, D.pending, D.results and (new) D.games[].wong, this
script just wasn't emitting them yet):
  - D.props / D.pending: pulled from each active game's own "props.picks"
    (matches scripts/apply_props.py's write shape).
  - D.results: pulled from results/results.json (matches
    scripts/capture_result.py's write shape), independent of "active" week.
  - D.games[].wong: Wong-teaser qualification per game, computed off this
    script's OWN already-flipped D-shaped spread/total fields (cur/bm/cz/tot,
    positive = home favored) rather than re-deriving from the project's
    internal negative-home-favored convention -- so this mirrors
    build_dashboard.py's wong_check()/best_available_spread() rule (Caesars
    > DraftKings > BookMaker.eu, exact -7.5 favorite or 1.5-2.5 underdog,
    total <= 47), just against numbers already in this file's own sign
    convention. FanDuel isn't tracked in the D shape, so it's left out of
    the book-priority fallback here (build_dashboard.py's own priority list
    checks it last anyway).
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from lib import player_key  # Phase 3 alias map
import datetime
import glob
import json
import os
import sys
from collections import defaultdict

DATA_DIR = "data"
RESULTS_PATH = "results/results.json"

ABBR = {
    "New England Patriots": "NE", "Seattle Seahawks": "SEA", "San Francisco 49ers": "SF",
    "Los Angeles Rams": "LAR", "Chicago Bears": "CHI", "Carolina Panthers": "CAR",
    "Buffalo Bills": "BUF", "Houston Texans": "HOU", "Denver Broncos": "DEN",
    "Kansas City Chiefs": "KC", "Cleveland Browns": "CLE", "Jacksonville Jaguars": "JAX",
    "Tampa Bay Buccaneers": "TB", "Cincinnati Bengals": "CIN", "Arizona Cardinals": "ARI",
    "Los Angeles Chargers": "LAC", "Washington Commanders": "WAS", "Philadelphia Eagles": "PHI",
    "Dallas Cowboys": "DAL", "New York Giants": "NYG", "Miami Dolphins": "MIA",
    "Las Vegas Raiders": "LV", "Atlanta Falcons": "ATL", "Pittsburgh Steelers": "PIT",
    "New York Jets": "NYJ", "Tennessee Titans": "TEN", "Green Bay Packers": "GB",
    "Minnesota Vikings": "MIN", "Baltimore Ravens": "BAL", "Indianapolis Colts": "IND",
    "New Orleans Saints": "NO", "Detroit Lions": "DET", "Houston Texans": "HOU",
    "Los Angeles Rams (Melbourne)": "LAR",
}

PROP_MARKET_MAP = {
    "receptions": "receptions", "rec_yards": "rec_yards", "rush_yards": "rush_yards",
    "pass_yards": "pass_yards", "completions": "completions", "attempts": "attempts",
    "pass_tds": "pass_tds", "rush_attempts": "rush_attempts",
}

WONG_TOTAL_MAX = 47.0
WONG_FAVORITE_MARGIN = 7.5


def wx_note(game):
    """Phase 4: one plain line per outdoor-game forecast (apply_weather.py)."""
    w = game.get("weather")
    if not w:
        return []
    bits = [f"wind {w['wind_mph']:g} mph"] + ([f"gusts {w['gust_mph']:g}"] if w.get("gust_mph") else []) \
        + ([f"{w['temp_f']:g}F"] if w.get("temp_f") is not None else []) + ([f"{w['precip_pct']:g}% precip"] if w.get("precip_pct") is not None else [])
    tag = {"strong": "STRONG weather factor for totals/props", "mild": "mild wind, mention only", "none": "no weather factor"}[w.get("impact", "none")]
    return [f"Weather ({w.get('stadium')}, logged {w['captured_at'][:10]}): {', '.join(bits)} -> {tag}"]


def cluster_note(game):
    """Phase 5: defensive injury clusters (apply_injury_adjustment.py --def-clusters)."""
    out = []
    for c in (game.get("injury_adjustments") or {}).get("def_clusters", []):
        who = f"Injury cluster: {c['team_side']} {c['unit']} missing {c['count']} starters ({', '.join(c['players'])})"
        if c.get("defensive"):
            out.append(f"{who} -> opponent +{c['opp_pts']:g} pts in sim; check opponent {c['watch']} props and the total")
        else:
            out.append(f"{who} -> {c.get('own_pts', 0):g} pts off own offense; check {c['watch']}")
    return out


def abbr_of(name):
    import re
    clean = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return ABBR.get(clean, "".join(w[0] for w in clean.split()[-2:]).upper())


def latest_book(snapshots, book, field):
    for snap in reversed(snapshots):
        b = snap.get(book)
        if b and b.get(field) is not None:
            return b.get(field)
    return None


def spread_side_note(away_abbr, home_abbr, open_hr, cur_hr):
    """Simplified key-number note -- only returned when a cross actually
    happened between the two home-relative numbers."""
    if open_hr is None or cur_hr is None:
        return None
    lo, hi = min(open_hr, cur_hr), max(open_hr, cur_hr)
    crossed = [k for k in (3, -3, 7, -7) if lo < k < hi]
    if not crossed:
        return None

    def fmt(v):
        team = home_abbr if v < 0 else away_abbr
        return f"{team} -{abs(v):g}"

    nums = ", ".join(str(abs(k)) for k in sorted(set(abs(k) for k in crossed)))
    return (f"opened {fmt(open_hr)}, now {fmt(cur_hr)} -- crossed {nums}, "
            f"one of the NFL's key numbers (games decided by exactly 3 or 7 points "
            f"are unusually common, so a cross like this is worth more than the raw "
            f"point move suggests).")


def build_wong(a, h, cur, cz, bm, tot):
    """Wong-teaser qualification, operating on this script's own D-shaped
    (positive = home favored) spread values. Book priority Caesars > DK >
    BookMaker.eu, first one whose margin actually qualifies wins -- mirrors
    build_dashboard.py's best_available_spread()/wong_check() rule set."""
    total_ok = tot is not None and tot <= WONG_TOTAL_MAX

    def qualify(val):
        if val is None or val == 0:
            return None
        if val > 0:
            fav, dog, margin = h, a, val
        else:
            fav, dog, margin = a, h, -val
        if margin == 8.5:
            return {"qualifies": False, "rejectedReason": f"{fav} -8.5 teases to -2.5, not -1.5 -- doesn't actually clear the 3 (the -8.5 fallacy)."}
        if margin == WONG_FAVORITE_MARGIN:
            return {"qualifies": True, "side": "favorite", "team": fav, "from": -margin, "to": -(margin - 6),
                    "reason": f"{fav} -{margin:g} teases down to -{margin - 6:g}, clearing both 7 and 3 with room to spare."}
        if 1.5 <= margin <= 2.5:
            return {"qualifies": True, "side": "dog", "team": dog, "from": margin, "to": margin + 6,
                    "reason": f"{dog} +{margin:g} teases up to +{margin + 6:g}, crossing both 3 and 7."}
        return None

    for key, val in (("cz", cz), ("cur", cur), ("bm", bm)):
        q = qualify(val)
        if q and q.get("qualifies"):
            q["book"] = {"cz": "Caesars", "cur": "DraftKings", "bm": "BookMaker.eu"}[key]
            q["totalOk"] = total_ok
            q["total"] = tot
            q["qualifies"] = bool(q["qualifies"] and total_ok)
            return q
    # nothing qualified on spread (or total failed) -- still surface the best
    # candidate's rejection reason when there is one, so a near-miss can be
    # explained rather than just silently absent.
    for key, val in (("cz", cz), ("cur", cur), ("bm", bm)):
        q = qualify(val)
        if q:
            q["book"] = {"cz": "Caesars", "cur": "DraftKings", "bm": "BookMaker.eu"}[key]
            q["totalOk"] = total_ok
            q["total"] = tot
            q["qualifies"] = False
            return q
    return {"qualifies": False, "totalOk": total_ok, "total": tot}


def _ts(v):
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def build_line_history(game):
    """2026-10-02: per-run market history for the Slate's stock-style chart.
    One point per logged snapshot (D sign: positive = home favored), oldest
    first; the first point is the opening line. Same-cycle research re-checks
    that repeat identical numbers within 30 minutes collapse into one point."""
    pts = []
    for s in game.get("snapshots", []):
        def g(book, field):
            b = s.get(book) or {}
            v = b.get(field)
            return None if v is None else (-v if field == "spread" else v)
        p = {"t": s.get("captured_at"), "dk": g("draftkings", "spread"),
             "cz": g("caesars", "spread"), "bm": g("bookmaker_eu", "spread"),
             "tot": g("draftkings", "total")}
        if pts:
            last = pts[-1]
            t0, t1 = _ts(last["t"]), _ts(p["t"])
            same = all(last[k] == p[k] for k in ("dk", "cz", "bm", "tot"))
            if same and t0 and t1 and (t1 - t0).total_seconds() < 1800:
                continue
        pts.append(p)
    return pts


def build_model_history(game):
    """Every logged simulate_game.py run (simulation_history, added
    2026-10-02); falls back to the current simulation block alone for a game
    simulated before that log existed."""
    out = [{"t": h.get("run_at"), "m": h.get("marginHome"), "tot": h.get("total")}
           for h in (game.get("simulation_history") or [])]
    sim = game.get("simulation") or {}
    if not out and sim.get("run_at"):
        out.append({"t": sim["run_at"], "m": sim.get("injuryAdjustedMarginHome"),
                    "tot": sim.get("projTotalAdjusted")})
    return out


def build_game_entry(game):
    matchup = game["matchup"]
    away_name, home_name = matchup.split(" @ ")
    a = abbr_of(away_name)
    h = abbr_of(home_name)

    snaps = game.get("snapshots", [])
    opening = game.get("opening_line") or {}
    op_dk = opening.get("draftkings") or {}
    open_hr = op_dk.get("spread")
    open_tot = op_dk.get("total")

    cur_dk = latest_book(snaps, "draftkings", "spread")
    cur_dk_tot = latest_book(snaps, "draftkings", "total")
    cur_bm = latest_book(snaps, "bookmaker_eu", "spread")
    cur_bm_tot = latest_book(snaps, "bookmaker_eu", "total")
    cur_cz = latest_book(snaps, "caesars", "spread")

    ml_fav = latest_book(snaps, "bookmaker_eu", "ml_fav")
    ml_dog = latest_book(snaps, "bookmaker_eu", "ml_dog")
    if ml_fav is None:
        ml_fav = latest_book(snaps, "draftkings", "ml_fav")
        ml_dog = latest_book(snaps, "draftkings", "ml_dog")

    sim = game.get("simulation") or {}
    ge = game.get("game_evaluation") or {}
    ms = game.get("market_signal") or {}
    wt = game.get("wagertalk_angle")

    def flip(v):
        return None if v is None else -v

    moved = bool(open_hr is not None and cur_dk is not None and abs(open_hr - cur_dk) > 0.1)
    flipped = bool(
        open_hr is not None and cur_dk is not None and open_hr != 0 and cur_dk != 0
        and (open_hr < 0) != (cur_dk < 0)
    )
    kn = spread_side_note(a, h, open_hr, cur_dk)

    leans = []
    for l in (ge.get("leans") or []):
        leans.append({
            "market": l.get("market"),
            "pick": l.get("pick"),
            "conf": l.get("confidence"),
            "why": l.get("reasoning"),
            "best": bool(l.get("best_bet")),
            "factors": l.get("factors") or [],
            "al": l.get("alignment") or {},
        })

    ms_out = None
    if ms:
        spread_ms = ms.get("spread") or {}
        total_ms = ms.get("total") or {}
        ml_ms = ms.get("moneyline") or {}
        ms_out = {
            "sb": spread_ms.get("bets_pct_home"),
            "sh": spread_ms.get("handle_pct_home"),
            "tb": total_ms.get("bets_pct_over"),
            "th": total_ms.get("handle_pct_over"),
            "mb": ml_ms.get("bets_pct_home"),
            "mh": ml_ms.get("handle_pct_home"),
            "rlm": bool(ms.get("reverse_line_move")),
            "note": ms.get("rlm_note"),
        }

    sys_out = None
    if wt:
        sys_out = {
            "pick": wt.get("pick"),
            "team": wt.get("pick_team"),
            "pct": wt.get("pct"),
            "checks": [
                {"key": c.get("key"), "pct": c.get("pct"), "why": c.get("why")}
                for c in (wt.get("checks") or [])
            ],
        }

    entry_cur, entry_bm, entry_cz = flip(cur_dk), flip(cur_bm), flip(cur_cz)
    wong = build_wong(a, h, entry_cur, entry_cz, entry_bm, cur_dk_tot)

    return {
        "id": game["game_id"],
        "away": away_name, "home": home_name, "a": a, "h": h,
        "ko": game["kickoff"],
        "open": flip(open_hr), "cur": entry_cur,
        "bm": entry_bm, "cz": entry_cz,
        "openTot": open_tot, "tot": cur_dk_tot, "bmTot": cur_bm_tot,
        "model": sim.get("injuryAdjustedMarginHome"),
        "modelTot": sim.get("projTotalAdjusted"),
        "cover": sim.get("coverPctHome"), "over": sim.get("overPct"),
        "winH": sim.get("homeWinPct"), "winA": sim.get("awayWinPct"),
        "medH": sim.get("medianHomeScore"), "medA": sim.get("medianAwayScore"),
        "n": sim.get("n"), "seen": sim.get("gamesSeen"),
        "elo": sim.get("eloMarginHome"), "form": sim.get("statsMarginHome"),
        "injH": sim.get("homeInjuryPts"), "injA": sim.get("awayInjuryPts"),
        "notes": (sim.get("notes") or []) + wx_note(game) + cluster_note(game),
        "wx": game.get("weather"),
        "kn": kn, "flipped": flipped, "moved": moved,
        "mlFav": ml_fav, "mlDog": ml_dog,
        "analysis": ge.get("analysis") or [],
        "leans": leans,
        "ms": ms_out,
        "sys": sys_out,
        "wong": wong,
        "hist": build_line_history(game),
        "mh": build_model_history(game),
    }


def build_props(game, entry):
    """Extract this game's prop picks into the flat shape D.props expects.
    game['props']['picks'] entries have team:'away'/'home' -- resolved
    against this game's own a/h abbrs (entry['a']/entry['h'])."""
    props = game.get("props") or {}
    out = []
    for p in (props.get("picks") or []):
        team_abbr = entry["a"] if p.get("team") == "away" else entry["h"] if p.get("team") == "home" else p.get("team")
        out.append({
            "player": p.get("player"),
            "pos": p.get("position"),
            "team": team_abbr,
            "gid": entry["id"],
            "a": entry["a"], "h": entry["h"], "ko": entry["ko"],
            "market": p.get("market"),
            "lean": p.get("lean"),
            "open": p.get("opening_line"),
            "cur": p.get("current_line"),
            "conf": p.get("confidence"),
            "why": p.get("reasoning"),
            "factors": p.get("factors") or [],
            "al": p.get("alignment") or {},
            "book": p.get("book"),
            "rank": (p.get("matchup") or {}).get("opp_rank_vs_pos"),
            "seasonLog": p.get("season_log") or [],
            "seasonLogSource": p.get("season_log_source"),
            "backtest": p.get("backtest"),
        })
    return out


def week_tag(week_str):
    # "Week 3" -> "WK3"
    if not week_str:
        return None
    digits = "".join(c for c in week_str if c.isdigit())
    return f"WK{digits}" if digits else week_str


def build_edges():
    """Early-edge / CLV tracker (results/early_edges.json, written only by early_edges.py)."""
    path = os.path.join("results", "early_edges.json")
    if not os.path.exists(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("early_edges", os.path.join(os.path.dirname(__file__), "early_edges.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    store = json.load(open(path))
    rows = [{"wk": week_tag(e.get("week")), "day": e.get("recDay"), "pick": e.get("pick"), "conf": e.get("conf"),
             "stake": e.get("stake"), "rec": e.get("recLine"), "now": e.get("currentLine"), "close": e.get("closeLine"),
             "clv": e.get("clvPts"), "taken": e.get("taken"), "res": e.get("result"), "pnl": e.get("paperPnl"),
             "a": abbr_of(e.get("matchup", "").split(" @ ")[0]), "h": abbr_of(e.get("matchup", " @ ").split(" @ ")[1]),
             "ko": e.get("kickoff")} for e in store.get("edges", [])]
    rows.sort(key=lambda r: (r["ko"] or "", -(r["clv"] or 0)))
    return {"summary": mod.summary(store), "rows": rows}


def build_results():
    if not os.path.exists(RESULTS_PATH):
        return []
    with open(RESULTS_PATH) as f:
        data = json.load(f)
    out = []
    for g in data.get("games") or []:
        actual = g.get("actual") or {}
        grading = g.get("grading") or {}
        if not actual or grading.get("winProbOfActualWinner") is None:
            continue
        out.append({
            "wk": week_tag(g.get("week")),
            "ko": g.get("kickoff"),
            "a": abbr_of(g.get("awayTeam", "")),
            "h": abbr_of(g.get("homeTeam", "")),
            "as_": actual.get("awayScore"),
            "hs": actual.get("homeScore"),
            "su": grading.get("simStraightUpCorrect"),
            "ats": grading.get("simAtsPickCorrect"),
            "tot": grading.get("simTotalPickCorrect"),
            "wp": grading.get("winProbOfActualWinner"),
        })
    out.sort(key=lambda r: r["ko"] or "")
    return out


PROPS_RESULTS_PATH = "results/props_results.json"


def load_props_store():
    if not os.path.exists(PROPS_RESULTS_PATH):
        return []
    with open(PROPS_RESULTS_PATH) as f:
        return (json.load(f).get("props")) or []


def build_props_record(store):
    graded = [p for p in store if p.get("result") in ("hit", "miss")]
    pushes = [p for p in store if p.get("result") == "push"]
    if not graded:
        return None
    hits = sum(1 for p in graded if p["result"] == "hit")
    weeks, tiers = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for p in graded:
        wk = week_tag(p.get("week")) or "?"
        c = p.get("confidence") or 0
        tier = "7-8 strong" if c >= 7 else "5-6 real" if c >= 5 else "1-4 marginal"
        for bucket, key in ((weeks, wk), (tiers, tier)):
            bucket[key][1] += 1
            bucket[key][0] += p["result"] == "hit"
    order = ["7-8 strong", "5-6 real", "1-4 marginal"]
    return {
        "hits": hits, "total": len(graded), "pushes": len(pushes),
        "weeks": [{"wk": k, "hits": v[0], "total": v[1]} for k, v in sorted(weeks.items())],
        "tiers": [{"label": k, "hits": tiers[k][0], "total": tiers[k][1]} for k in order if k in tiers],
        "misses": [{"player": p["player"], "market": p["market"], "lean": p["lean"],
                    "line": p["line"], "actual": p.get("actual")} for p in graded if p["result"] == "miss"],
    }


def build_prop_history(store):
    """Per (player, market) history of PAST graded weeks, oldest first --
    keyed case-insensitively on player name so a current-week pick (not
    itself in this map until graded) can show what that same player/market
    combo has actually run the last few weeks it was tracked. Each entry
    is this project'''s own graded result (not a league-wide stat), so a
    player new to being tracked on this market will just have an empty
    history -- that'''s an honest gap, not a bug."""
    hist = defaultdict(list)
    for p in store:
        if p.get("result") not in ("hit", "miss", "push"):
            continue
        key = (player_key(p.get("player", "")), p.get("market"))
        hist[key].append({
            "wk": week_tag(p.get("week")),
            "actual": p.get("actual"),
            "result": p.get("result"),
            "ko": p.get("kickoff") or "",
        })
    for key in hist:
        hist[key].sort(key=lambda h: h["ko"])
        for h in hist[key]:
            del h["ko"]
    return hist


def main():
    global DATA_DIR
    if len(sys.argv) != 4:
        print("usage: build_dashboard_v2.py <data_dir> <template_html> <output_html>", file=sys.stderr)
        sys.exit(2)
    DATA_DIR, template_path, out_path = sys.argv[1:4]
    store = load_props_store()
    lookup = {(p.get("gameId"), p.get("player"), p.get("market")): p for p in store}
    prop_history = build_prop_history(store)
    games, props = [], []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*", "*.json"))):
        with open(path) as f:
            game = json.load(f)
        if game.get("status") != "active":
            continue
        entry = build_game_entry(game)
        games.append(entry)
        props.extend(build_props(game, entry))
    for p in props:
        r = lookup.get((p["gid"], p["player"], p["market"]))
        if r and r.get("result") in ("hit", "miss", "push"):
            p["res"], p["actual"] = r["result"], r.get("actual")
        p["hist"] = prop_history.get((player_key(p["player"]), p["market"]), [])
        _h, _m = sum(1 for x in p["hist"] if x["result"] == "hit"), sum(1 for x in p["hist"] if x["result"] == "miss")
        p["histRate"] = {"hits": _h, "misses": _m, "total": _h + _m,
                          "pct": round(_h / (_h + _m) * 100) if (_h + _m) else None}
    games.sort(key=lambda g: g["ko"])
    props.sort(key=lambda p: (-(p["conf"] or 0), p["ko"] or ""))
    out = {
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week": games[0]["id"].split("-")[-1].upper() if games else None,
        "season": 2026,
        "games": games, "props": props, "pending": [],
        "propsRecord": build_props_record(store),
        "propRows": [{"wk": week_tag(p.get("week")), "conf": p.get("confidence"),
                      "res": p.get("result"), "player": p.get("player"),
                      "market": p.get("market"), "lean": p.get("lean"),
                      "line": p.get("line"), "actual": p.get("actual"),
                      "a": abbr_of(p.get("awayTeam", "")), "h": abbr_of(p.get("homeTeam", "")),
                      "ko": p.get("kickoff")}
                     for p in store if p.get("result") in ("hit", "miss", "push")],
        "results": build_results(),
        "edges": build_edges(),
    }
    with open(template_path) as f:
        tpl = f.read()
    if tpl.count("__D_JSON__") != 1:
        print(json.dumps({"error": "expected exactly 1 __D_JSON__ in template"}))
        sys.exit(1)
    with open(out_path, "w") as f:
        f.write(tpl.replace("__D_JSON__", json.dumps(out, separators=(",", ":"))))
    # Always keep dashboard/index.html as the single, current copy of the latest UI.
    import shutil
    idx = os.path.join(os.path.dirname(out_path) or ".", "index.html")
    if os.path.abspath(idx) != os.path.abspath(out_path):
        shutil.copyfile(out_path, idx)
    rec = out["propsRecord"] or {}
    print(json.dumps({"games": len(games), "props": len(props),
                      "wong": [g["id"] for g in games if g["wong"].get("qualifies")],
                      "propsHits": rec.get("hits", 0), "propsGraded": rec.get("total", 0)}))


if __name__ == "__main__":
    main()
