#!/usr/bin/env python3
"""export_snapshot.py <dashboard_build.html> <out.json> [--max-kb 80]

Turns the Line Watch dashboard's embedded `const D = {...}` read-model into a
slim, minified, phone-friendly JSON file for Claude chat to read from Google
Drive. Reads the dashboard HTML (not data/*.json), so the snapshot is always
exactly what the dashboard shows. Prints one JSON summary line.
"""
import datetime as dt
import json
import re
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SCHEMA = "line-watch-snapshot/1"


def load_D(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r"const D\s*=\s*", html)
    if not m:
        sys.exit("ERROR: no 'const D =' found in " + path)
    D, _ = json.JSONDecoder().raw_decode(html, m.end())
    return D


def wk_num(label):
    m = re.search(r"(\d+)", str(label or ""))
    return int(m.group(1)) if m else -1


def latest_weeks(rows, keep=2):
    weeks = sorted({r.get("wk") for r in rows if r.get("wk")}, key=wk_num)
    return set(weeks[-keep:])


def fmt_et(iso):
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
        return t.strftime("%a %m/%d %-I:%M%p ET").replace("AM", "am").replace("PM", "pm")
    except Exception:
        return None


def sgn(x):
    return f"{x:+g}" if isinstance(x, (int, float)) else None


def stake_pass(text):
    s = re.search(r"Stake \$([\d.]+)", text or "")
    p = re.search(r"pass at ([+-]?[\d.]+|n/a)", text or "")
    return (float(s.group(1)) if s else None, p.group(1) if p else None)


def slim_game(g):
    g = dict(g)
    g.pop("mh", None)  # model-run history, rarely asked about
    g["notes"] = [n for n in g.get("notes", []) if "scoring average excludes" not in n]
    a, h, cur, tot, mdl = g.get("a"), g.get("h"), g.get("cur"), g.get("tot"), g.get("model")
    # D stores spreads from the AWAY team's side (negative = away favored).
    if isinstance(cur, (int, float)):
        fav = a if cur < 0 else h if cur > 0 else None
        g["marketNow"] = (
            f"{a} {sgn(cur)} / {h} {sgn(-cur)} (DraftKings), total {tot}"
            + (f" -> favorite {fav} by {abs(cur):g}" if fav else " -> pick'em")
        )
    if isinstance(mdl, (int, float)):
        mf = a if mdl < 0 else h if mdl > 0 else None
        g["modelNow"] = (
            f"model margin {mf} by {abs(mdl):g}, projected total {g.get('modelTot')}"
            if mf else f"model pick'em, projected total {g.get('modelTot')}"
        )
    g["koET"] = fmt_et(g.get("ko"))
    for lean in g.get("leans", []):
        st, pa = stake_pass(lean.get("why"))
        if st is not None:
            lean["stake"] = st
        if pa is not None:
            lean["passAt"] = pa
    return g


def slim_prop(p):
    p = dict(p)
    st, pa = stake_pass(p.get("why"))
    if st is not None:
        p["stake"] = st
    if pa is not None:
        p["passAt"] = pa
    p["koET"] = fmt_et(p.get("ko"))
    return p


def picksIndex(games, props):
    rows = []
    for g in games:
        for l in g.get("leans", []):
            # 2026-11-04: any staked lean, incl. $6 minimum-action picks (conf 4), so the
            # game-day check sees every bet on the card.
            if (l.get("stake") or 0) > 0 or ((l.get("conf") or 0) >= 5 and l.get("stake") is None):
                rows.append({"kind": "game", "game": f"{g['a']}@{g['h']}", "market": l["market"],
                             "pick": l["pick"], "conf": l["conf"], "stake": l.get("stake"),
                             "passAt": l.get("passAt"), "best": bool(l.get("best")),
                             "minAction": "minimum-action" in (l.get("pick", "") + l.get("why", "")).lower(), "koET": g.get("koET")})
    for p in props:
        if (p.get("conf") or 0) >= 5 and p.get("stake") != 0:
            rows.append({"kind": "prop", "game": f"{p['a']}@{p['h']}", "market": p["market"],
                         "pick": f"{p['player']} {p['lean']} {p.get('cur', p.get('open'))} {p['market']}",
                         "conf": p["conf"], "stake": p.get("stake"), "passAt": p.get("passAt"),
                         "best": False})
    rows.sort(key=lambda r: (-r["conf"], r["kind"]))
    return rows


def week_rollup(results):
    by = {}
    for r in results:
        w = by.setdefault(r["wk"], {"wk": r["wk"], "games": 0, "su": 0, "ats": 0, "tot": 0})
        w["games"] += 1
        w["su"] += bool(r.get("su"))
        w["ats"] += bool(r.get("ats"))
        w["tot"] += bool(r.get("tot"))
    return sorted(by.values(), key=lambda w: wk_num(w["wk"]))


def build(D, max_kb):
    games = [slim_game(g) for g in D.get("games", [])]
    props = [slim_prop(p) for p in D.get("props", [])]

    res_all = D.get("results", [])
    keep_res = latest_weeks(res_all, 1)
    edges = D.get("edges", {"summary": {}, "rows": []})
    e_rows = edges.get("rows", [])
    e_slim = [r for r in e_rows if r.get("res") is None]
    pr_all = D.get("propRows", [])
    keep_pr = latest_weeks(pr_all, 1)

    out = {
        "meta": {
            "schema": SCHEMA,
            "exportedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dashboardBuilt": D.get("built"),
            "season": D.get("season"),
            "week": D.get("week"),
            "gameCount": len(games),
            "propCount": len(props),
            "howToAnswer": [
                "Always say how fresh this is: compare meta.exportedAt / dashboardBuilt to now and flag it if older than ~12 hours or if a game has already kicked off.",
                "These are research leans with a confidence score, never guaranteed outcomes, and never call a bet a lock.",
                "A bet is only live while the current line is still better than its passAt number; past passAt the stake is $0.",
                "Stakes follow a $600 bankroll at 1-5% per bet ($6/$12/$18/$24/$30); props cap at $18.",
                "Spread fields in games[] (open, cur, bm, cz, model) are from the AWAY team's side: negative = away favored, positive = away underdog. marketNow / modelNow spell it out in words - prefer those.",
                "If a Drive reader shows backslashes before [ ] _ ~ or > (markdown escaping) or trailing double spaces on lines, they are not part of the data - ignore them.",
                "Answer from this file only. For anything it lacks (full snapshot history, raw game files) say it needs the computer.",
            ],
            "fieldGuide": {
                "games": "a/h = away/home abbreviation; ko = kickoff UTC (koET = Eastern); open/cur = DraftKings opening/current spread; bm = BookMaker.eu (sharp book); cz = Caesars; openTot/tot/bmTot = totals; model/modelTot = simulation margin and total; cover = % of sims the home side covers; over = % of sims going over; winH/winA = sim win %; mlFav/mlDog = DK moneylines; injH/injA = injury adjustment in points (negative hurts that side); leans[] = analyst picks with conf 0-10, stake, passAt; ms = public splits (sb/sh = spread bets%/handle% on HOME, tb/th = total bets%/handle% on OVER, mb/mh = moneyline bets%/handle% on HOME; rlm = reverse line move flag); wong = Wong teaser qualification; hist = recent line points (t, dk, cz, bm, tot).",
                "picksIndex": "Every staked pick (conf 5+, plus $6 minimum-action picks flagged minAction) (stake null = no stake was written on the pick; size it by conf using line-watch-slate-bets: 4 = $6, 5 = $12, 6 = $18, 7 = $24, 8+ = $30). It is an index, not the dashboard Bet card - check passAt against the current line before calling a bet live.",
                "props": "lean = over/under; open = line when first posted, cur = line now; conf 0-10; factors[] = reasons (dir for/against/neutral); rank = opponent rank vs the position (1 = toughest); seasonLog/backtest = how the player's games this season compare with the line.",
                "results": "resultsByWeek counts games where the model's pregame call was right (su = straight up, ats = against the spread, tot = total); resultsRecent has per-game rows for the latest logged week (as_/hs = away/home score, wp = model win % for the home side).",
                "edges": "Early-edge / closing-line-value tracker (summary covers every graded edge; rows[] lists only edges still open): rec = line when recommended, close = closing line, clv = points gained vs the close, res = win/loss/push, pnl = paper P/L at the recommended stake unless taken.",
            },
        },
        "picksIndex": picksIndex(games, props),
        "games": games,
        "props": props,
        "propsPendingGames": D.get("pending", []),
        "propsRecord": D.get("propsRecord", {}),
        "propsGradedRecent": [r for r in pr_all if r.get("wk") in keep_pr],
        "resultsByWeek": week_rollup(res_all),
        "resultsRecent": [r for r in res_all if r.get("wk") in keep_res],
        "edges": {"summary": edges.get("summary", {}), "rows": e_slim,
                  "rowsOmittedAlreadyGraded": len(e_rows) - len(e_slim)},
    }
    out["meta"]["trimmed"] = []

    def size():
        return len(json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    # Size ladder: drop the heaviest optional text first, never silently.
    ladder = [
        ("games[].analysis", lambda: [g.pop("analysis", None) for g in out["games"]]),
        ("propsGradedRecent", lambda: out.__setitem__("propsGradedRecent", [])),
        ("props[].factors", lambda: [p.pop("factors", None) for p in out["props"]]),
    ]
    for name, fn in ladder:
        if size() <= max_kb * 1024:
            break
        fn()
        out["meta"]["trimmed"].append(name)
    return out, size()


def dump_lines(out):
    """Valid JSON, but one game/prop/row per line so Read can page it."""
    j = lambda x: json.dumps(x, separators=(",", ":"), ensure_ascii=False)
    keys = list(out)
    lines = ["{"]
    for i, k in enumerate(keys):
        v, tail = out[k], ("," if i < len(keys) - 1 else "")
        if isinstance(v, list) and v and isinstance(v[0], dict):
            lines.append(f'{j(k)}:[')
            lines.append(",\n".join(j(x) for x in v))
            lines.append("]" + tail)
        elif isinstance(v, dict) and k == "edges":
            lines.append(f'{j(k)}:{{"summary":{j(v.get("summary", {}))},"rowsOmittedAlreadyGraded":{j(v.get("rowsOmittedAlreadyGraded", 0))},"rows":[')
            lines.append(",\n".join(j(x) for x in v.get("rows", [])))
            lines.append("]}" + tail)
        else:
            lines.append(f"{j(k)}:{j(v)}{tail}")
    lines.append("}")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    max_kb = int(sys.argv[sys.argv.index("--max-kb") + 1]) if "--max-kb" in sys.argv else 80
    D = load_D(src)
    out, nbytes = build(D, max_kb)
    text = dump_lines(out)
    json.loads(text)  # round-trip check: must still be valid JSON
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    print(json.dumps({
        "ok": True, "bytes": nbytes, "kb": round(nbytes / 1024, 1),
        "games": len(out["games"]), "props": len(out["props"]),
        "picksIndex": len(out["picksIndex"]), "trimmed": out["meta"]["trimmed"],
        "dashboardBuilt": out["meta"]["dashboardBuilt"], "out": dst,
        "over_budget": nbytes > max_kb * 1024,
    }))


if __name__ == "__main__":
    main()
