#!/usr/bin/env python3
"""Early-edge / closing-line-value (CLV) tracker. The only write path for
results/early_edges.json -- never hand-edit it.

Why (per Michael, 2026-10-06): the point of researching at line open
(Monday for Sunday games) is to grab a number before the market moves.
That skill is measurable separately from whether the bet wins:
  * CLV  -- did the line move our way between our recommendation and
            kickoff? (beat-the-close rate, avg points gained)
  * Result -- did the recommended side actually win at OUR number?
  * Taken -- did Michael actually place it (paper vs real P/L)?
Beating the close consistently is the best long-run evidence of an edge,
even across weeks where results are noisy.

Conventions
  spread: side = home|away; line is SIDE-relative (JAX home -2.5 -> -2.5;
          LV away +3.5 -> +3.5). Higher side-line is better.
  total:  side = over|under on the game total. Over: lower is better.
  prop:   market = rec_yards/rush_yards/... ; side = over|under; player set.
CLV pts = how many points the close moved in our favor (positive = we beat it).

Commands
  add     --game FILE --market M --side S --line X --odds -110 --book DK
          --conf N --stake 15 --pick "JAX -2.5" [--player P] [--recorded-at ISO] [--taken]
  taken   --id ID [--line X]          mark that Michael placed it (optionally at X)
  prop-line --id ID --line X [--odds N] [--at ISO]
                                       record a prop's latest pre-kickoff line (its close)
  refresh                              update current/closing lines + grade finals
  report                               print summary (also used by the dashboard)

Phase 2 (2026-11-04): every edge also gets
  window   -- betting window from kickoff (Thu night / Sun 1pm / Sun 4pm / Sun night / Mon night)
  pickType -- "min-action" (forced action pick) or "edge" (real lean), so the two are graded apart
  sharpClose / sharpClvPts -- the close at BookMaker.eu (SHARP_BOOK), the market's best estimate
  priceClv -- moneylines only: no-vig close probability minus our price's implied probability
Props: captured props are never re-checked in the game file, so their CLV froze at capture.
Each run now records the posted line for upcoming props with `prop-line`; the last one
recorded before kickoff becomes the prop's close.
"""
import argparse, json, os, sys, datetime
sys.path.insert(0, os.path.dirname(__file__))
from lib import load_game, now_utc, parse_iso, SHARP_BOOK, player_key, book_key
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

STORE = "results/early_edges.json"
BOOK_KEYS = {"dk": "draftkings", "draftkings": "draftkings", "fd": "fanduel", "fanduel": "fanduel",
             "czr": "caesars", "caesars": "caesars", "bookmaker": "bookmaker_eu", "bookmaker_eu": "bookmaker_eu"}
FALLBACK = ("draftkings", "fanduel", "caesars", "bookmaker_eu")


def load():
    if not os.path.exists(STORE):
        return {"edges": []}
    with open(STORE) as f:
        return json.load(f)


def save(s):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(s, f, indent=2)


def et_weekday(iso):
    return parse_iso(iso).astimezone(ET).strftime("%a")


def window_of(kickoff_iso):
    """Betting window from kickoff in Eastern time (matches line-watch-slate-bets)."""
    t = parse_iso(kickoff_iso).astimezone(ET)
    d, h = t.strftime("%a"), t.hour + t.minute / 60
    if d == "Sun":
        return "Sun AM" if h < 12 else "Sun 1pm" if h < 15 else "Sun 4pm" if h < 19 else "Sun night"
    if d == "Thu":
        return "Thu night"
    if d == "Mon":
        return "Mon night"
    return d


def pick_type(e):
    return "min-action" if "minimum-action" in (e.get("pick") or "").lower() or e.get("minAction") else "edge"


def implied(odds):
    odds = float(odds)
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def no_vig(p_side, p_other):
    return p_side / (p_side + p_other)


def home_ml(line, side):
    """(our_odds, other_odds) from a book line, mapping fav/dog roles to home/away
    with the home-relative spread sign (negative = home favored)."""
    if not line or line.get("ml_fav") is None or line.get("ml_dog") is None or line.get("spread") is None:
        return None, None
    home_fav = line["spread"] < 0
    home, away = (line["ml_fav"], line["ml_dog"]) if home_fav else (line["ml_dog"], line["ml_fav"])
    return (home, away) if side == "home" else (away, home)


def ml_close(game, book, side, before):
    """Latest ML pair before `before`, preferring the sharp book (no-vig reference)."""
    for b in [SHARP_BOOK, book] + [x for x in FALLBACK if x not in (SHARP_BOOK, book)]:
        for s in reversed(game.get("snapshots") or []):
            if before and parse_iso(s["captured_at"]) > before:
                continue
            ours, other = home_ml(s.get(b), side)
            if ours is not None:
                return ours, other, b
    return None, None, None


def market_value(game, book, field, before=None):
    """Most recent value of `field`, preferring the recommended book -- unless
    that book's latest value is more than 12h older than another book's
    (2026-10-08: a stale DK source froze CLV for 3 days while CZR/FD moved)."""
    snaps = game.get("snapshots") or []
    order = [book] + [b for b in FALLBACK if b != book] if book else list(FALLBACK)
    found = []
    for b in order:
        for s in reversed(snaps):
            if before and parse_iso(s["captured_at"]) > before:
                continue
            v = (s.get(b) or {}).get(field)
            if v is not None:
                found.append((v, b, s["captured_at"]))
                break
    if not found:
        return None, None, None
    newest = max(parse_iso(f[2]) for f in found)
    for v, b, ts in found:   # keeps preference order
        if (newest - parse_iso(ts)).total_seconds() <= 12 * 3600:
            return v, b, ts
    return found[0]


def side_line_from_home_spread(home_spread, side):
    return home_spread if side == "home" else -home_spread


def clv(e, close):
    if close is None:
        return None
    if e["market"] == "spread":
        return round(e["recLine"] - close, 1) if False else round(close_adv(e, close), 1)
    return round(close_adv(e, close), 1)


def close_adv(e, close):
    # positive = market moved in our favor after we recommended
    if e["market"] == "spread":
        return e["recLine"] - close          # e.g. rec -2.5, close -6 -> +3.5
    if e["side"] == "over":
        return close - e["recLine"]          # rec over 47.5, close 49 -> +1.5
    return e["recLine"] - close              # rec under 46.5, close 42.5 -> +4.0


def payout(stake, odds):
    odds = odds or -110
    return round(stake * (100 / abs(odds)) if odds < 0 else stake * odds / 100, 2)


def grade(e, game, props_store):
    fs = game.get("final_score")
    if game.get("status") != "final" or not fs:
        return None
    if e["market"] == "spread":
        my, opp = (fs["home"], fs["away"]) if e["side"] == "home" else (fs["away"], fs["home"])
        m = my - opp + e["recLine"]
    elif e["market"] == "total":
        tot = fs["home"] + fs["away"]
        m = (tot - e["recLine"]) if e["side"] == "over" else (e["recLine"] - tot)
    elif e["market"] == "moneyline":
        my, opp = (fs["home"], fs["away"]) if e["side"] == "home" else (fs["away"], fs["home"])
        return "win" if my > opp else "push" if my == opp else "loss"
    else:
        act = None
        for p in props_store:
            if p.get("gameId") == game["game_id"] and player_key(p.get("player", "")) == player_key(e.get("player")) and p.get("market") == e["market"]:
                act = p.get("actual")
        if act is None:
            return None
        e["actual"] = act
        m = (act - e["recLine"]) if e["side"] == "over" else (e["recLine"] - act)
    return "win" if m > 0 else "push" if m == 0 else "loss"


def cmd_add(a):
    s = load()
    g = load_game(a.game)
    mkt = a.market
    eid = f"{g['game_id']}|{mkt}|{a.side}|{(a.player or '').lower()}"
    # A voided entry (pick pulled before kickoff) may be replaced by a fresh recommendation.
    s["edges"] = [x for x in s["edges"] if not (x["id"] == eid and x.get("void") and not x.get("taken"))]
    if any(x["id"] == eid for x in s["edges"]):
        print(json.dumps({"skipped": True, "reason": "edge already logged (first recommendation is the one that counts)", "id": eid}))
        return
    rec_at = a.recorded_at or now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    e = {"id": eid, "week": g.get("week"), "gameId": g["game_id"], "matchup": g["matchup"], "kickoff": g["kickoff"],
         "market": mkt, "side": a.side, "player": a.player, "pick": a.pick, "recLine": a.line, "odds": a.odds,
         "book": BOOK_KEYS.get((a.book or "").lower()) or book_key(a.book), "conf": a.conf, "stake": a.stake,
         "recAt": rec_at, "recDay": et_weekday(rec_at), "taken": bool(a.taken), "takenLine": a.line if a.taken else None,
         "currentLine": None, "closeLine": None, "closeBook": None, "clvPts": None, "beatClose": None,
         "result": None, "paperPnl": None, "realPnl": None,
         "window": window_of(g["kickoff"])}
    e["pickType"] = "min-action" if a.min_action else pick_type(e)
    s["edges"].append(e)
    save(s)
    print(json.dumps({"added": eid}))


def cmd_taken(a):
    s = load()
    for e in s["edges"]:
        if e["id"] == a.id:
            e["taken"] = True
            e["takenLine"] = a.line if a.line is not None else e["recLine"]
            save(s); print(json.dumps({"taken": a.id})); return
    print(json.dumps({"error": "no such id"}))


def cmd_void(a):
    s = load()
    for e in s["edges"]:
        if e["id"] == a.id:
            if e.get("taken"):
                print(json.dumps({"error": "edge was taken (a real bet) -- not voiding", "id": a.id})); return
            e["void"] = True; e["voidReason"] = a.reason
            save(s); print(json.dumps({"voided": a.id})); return
    print(json.dumps({"error": "no such id"}))


def cmd_prop_line(a):
    s = load()
    at = a.at or now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    for e in s["edges"]:
        if e["id"] == a.id:
            if not e.get("player"):
                print(json.dumps({"error": "prop-line is for props only", "id": a.id})); return
            if parse_iso(at) >= parse_iso(e["kickoff"]):
                print(json.dumps({"skipped": True, "reason": "at/after kickoff, not a pre-game line", "id": a.id})); return
            e["propLine"], e["propLineAt"] = a.line, at
            if a.odds is not None:
                e["propLineOdds"] = a.odds
            save(s); print(json.dumps({"propLine": a.line, "id": a.id})); return
    print(json.dumps({"error": "no such id"}))


def cmd_refresh(a):
    s = load()
    props_store = []
    if os.path.exists("results/props_results.json"):
        props_store = json.load(open("results/props_results.json")).get("props", [])
    cache = {}
    for e in s["edges"]:
        if e.get("void"):
            continue
        path = None
        wk = e["gameId"].split("-")[-1]
        for d in os.listdir("data"):
            if d.endswith(wk) and os.path.exists(f"data/{d}/{e['gameId']}.json"):
                path = f"data/{d}/{e['gameId']}.json"
        if not path:
            continue
        g = cache.setdefault(path, load_game(path))
        ko = parse_iso(g["kickoff"])
        started = now_utc() >= ko
        cur = None
        if e["market"] == "spread":
            v, b, _ = market_value(g, e["book"], "spread", before=ko if started else None)
            cur = side_line_from_home_spread(v, e["side"]) if v is not None else None
        elif e["market"] == "total":
            v, b, _ = market_value(g, e["book"], "total", before=ko if started else None)
            cur = v
        elif e["market"] == "moneyline":
            ours, other, b = ml_close(g, e["book"], e["side"], ko if started else None)
            if ours is not None:
                e["mlNow"], e["mlNowBook"] = ours, b
                fair = no_vig(implied(ours), implied(other))
                e["priceClv"] = round((fair - implied(e["recLine"])) * 100, 1)   # pct points
                e["clvPts"] = e["priceClv"]
                e["beatClose"] = (e["priceClv"] > 0) if e["priceClv"] != 0 else None
                e["currentLine"] = ours
                if started:
                    e["closeLine"], e["closeBook"] = ours, b
        else:
            b = e["book"]
            if e.get("propLine") is not None:
                cur = e["propLine"]
            else:
                for p in (g.get("props") or {}).get("picks", []):
                    if player_key(p["player"]) == player_key(e.get("player")) and p["market"] == e["market"]:
                        cur = p.get("current_line")
        e.setdefault("window", window_of(g["kickoff"]))
        e["pickType"] = e.get("pickType") or pick_type(e)
        if e["market"] in ("spread", "total"):
            sv, sb, _ = market_value(g, SHARP_BOOK, e["market"], before=ko if started else None)
            if sb == SHARP_BOOK and sv is not None:
                sc = side_line_from_home_spread(sv, e["side"]) if e["market"] == "spread" else sv
                e["sharpNow"] = sc
                e["sharpClvPts"] = round(close_adv(e, sc), 1)
                if started:
                    e["sharpClose"] = sc
        if cur is not None and e["market"] != "moneyline":
            e["currentLine"] = cur
            e["clvPts"] = round(close_adv(e, cur), 1)
            e["beatClose"] = (e["clvPts"] > 0) if e["clvPts"] != 0 else None
            if started:
                e["closeLine"], e["closeBook"] = cur, b
        r = grade(e, g, props_store)
        if r:
            e["result"] = r
            win = payout(e["stake"], e["recLine"] if e["market"] == "moneyline" else e["odds"])
            e["paperPnl"] = win if r == "win" else 0.0 if r == "push" else -e["stake"]
            if e["taken"]:
                e["realPnl"] = e["paperPnl"]
    save(s)
    print(json.dumps(summary(s)))


def summary(s):
    E = [x for x in s["edges"] if not x.get("void")]
    def rec(rows):
        w = sum(1 for x in rows if x["result"] == "win"); l = sum(1 for x in rows if x["result"] == "loss")
        p = sum(1 for x in rows if x["result"] == "push")
        return {"w": w, "l": l, "p": p, "pct": round(w / (w + l) * 100) if w + l else None}
    clv_rows = [x for x in E if x["clvPts"] is not None]
    beat = sum(1 for x in clv_rows if x["clvPts"] > 0); flat = sum(1 for x in clv_rows if x["clvPts"] == 0)
    by_day = {}
    for x in clv_rows:
        d = by_day.setdefault(x["recDay"], {"n": 0, "beat": 0, "pts": 0.0})
        d["n"] += 1; d["beat"] += x["clvPts"] > 0; d["pts"] += x["clvPts"]
    for d in by_day.values():
        d["avgPts"] = round(d.pop("pts") / d["n"], 2)
    def group(key):
        out = {}
        for x in E:
            k = x.get(key) or (window_of(x["kickoff"]) if key == "window" else pick_type(x))
            g = out.setdefault(k, {"n": 0, "withLine": 0, "beat": 0, "pts": 0.0, "rows": []})
            g["n"] += 1; g["rows"].append(x)
            if x["clvPts"] is not None and x["market"] != "moneyline":
                g["withLine"] += 1; g["beat"] += x["clvPts"] > 0; g["pts"] += x["clvPts"]
        for k, g in out.items():
            g["avgPts"] = round(g.pop("pts") / g["withLine"], 2) if g["withLine"] else None
            g["beatPct"] = round(g["beat"] / g["withLine"] * 100) if g["withLine"] else None
            g["record"] = rec(g.pop("rows"))
        return out
    sharp = [x for x in E if x.get("sharpClvPts") is not None]
    ml = [x for x in E if x.get("priceClv") is not None]
    taken = [x for x in E if x["taken"]]
    return {"byWindow": group("window"), "byPickType": group("pickType"),
            "sharp": {"n": len(sharp), "beat": sum(1 for x in sharp if x["sharpClvPts"] > 0),
                      "avgPts": round(sum(x["sharpClvPts"] for x in sharp) / len(sharp), 2) if sharp else None},
            "mlPriceClv": {"n": len(ml), "avgPct": round(sum(x["priceClv"] for x in ml) / len(ml), 1) if ml else None},
            "propsWithClose": sum(1 for x in E if x.get("propLine") is not None),"logged": len(E), "withLine": len(clv_rows), "beatClose": beat, "flat": flat,
            "beatPct": round(beat / len(clv_rows) * 100) if clv_rows else None,
            "avgClvPts": round(sum(x["clvPts"] for x in clv_rows) / len(clv_rows), 2) if clv_rows else None,
            "closedFinal": sum(1 for x in E if x["closeLine"] is not None),
            "record": rec(E), "paperPnl": round(sum(x["paperPnl"] or 0 for x in E), 2),
            "taken": len(taken), "takenRecord": rec(taken), "realPnl": round(sum(x["realPnl"] or 0 for x in taken), 2),
            "byRecDay": by_day}


def cmd_report(a):
    s = load()
    print(json.dumps(summary(s), indent=2))
    for x in sorted(s["edges"], key=lambda x: x["kickoff"]):
        print(f"{x['week']:7} {x['recDay']} {x['pick'][:34]:34} rec {x['recLine']:>6} now/close {str(x['closeLine'] if x['closeLine'] is not None else x['currentLine']):>6} CLV {str(x['clvPts']):>5} {'TAKEN' if x['taken'] else '     '} {x['result'] or '-'}")


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("--game", required=True); p.add_argument("--market", required=True)
    p.add_argument("--side", required=True, choices=["home", "away", "over", "under"]); p.add_argument("--player")
    p.add_argument("--line", type=float, required=True); p.add_argument("--odds", type=int, default=-110)
    p.add_argument("--book", default="draftkings"); p.add_argument("--conf", type=float, required=True)
    p.add_argument("--stake", type=float, required=True); p.add_argument("--pick", required=True)
    p.add_argument("--recorded-at"); p.add_argument("--taken", action="store_true")
    p.add_argument("--min-action", action="store_true", help="forced minimum-action pick (graded apart)")
    pl = sub.add_parser("prop-line"); pl.add_argument("--id", required=True); pl.add_argument("--line", type=float, required=True)
    pl.add_argument("--odds", type=int); pl.add_argument("--at")
    t = sub.add_parser("taken"); t.add_argument("--id", required=True); t.add_argument("--line", type=float)
    v = sub.add_parser("void"); v.add_argument("--id", required=True); v.add_argument("--reason", required=True)
    sub.add_parser("refresh"); sub.add_parser("report")
    a = ap.parse_args()
    {"add": cmd_add, "taken": cmd_taken, "void": cmd_void, "prop-line": cmd_prop_line, "refresh": cmd_refresh, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
