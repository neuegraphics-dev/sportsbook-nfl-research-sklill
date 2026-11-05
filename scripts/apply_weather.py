#!/usr/bin/env python3
"""Weather for outdoor games (Phase 4, 2026-11-04). The only write path for a
game's `weather` block.

Why two steps: api.weather.gov and open-meteo are blocked from both the Mac
shell and the cloud shell, but the National Weather Service forecast page
(forecast.weather.gov/MapClick.php) is readable with WebFetch. So:

  1. list   -- prints every active outdoor game with its stadium and the NWS
               forecast URL to read. Domes and retractable roofs are skipped.
  2. WebFetch that URL and ask for the forecast period covering kickoff
     (wind mph, gusts, temp F, precip %).
  3. apply  -- records it on the game file and computes the impact flag.

    python3 scripts/apply_weather.py list data
    python3 scripts/apply_weather.py apply data/2026-nfl-wk10/x.json --wind 14 --gust 24 \
        --temp 38 --precip 30 --source "forecast.weather.gov MapClick, Sun afternoon period"

Impact (a judgment call, documented; markets price big wind quickly, so the
value is logging it early in the week):
  strong  -- sustained wind >= 15 mph, or gusts >= 25, or precip >= 70% with wind >= 10
             -> Environment factor `for` totals UNDER / passing & kicking UNDERs,
                `against` the matching OVERs
  mild    -- sustained wind 10-14 mph -> one `neutral`-to-light factor, mention only
  none    -- under 10 mph: no factor (do not count calm weather as an edge)
Weather is one Environment factor for totals and props only, never for a spread,
and never counts as an independent signal category on its own.
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import load_game, save_game, now_utc, parse_iso

HERE = os.path.dirname(os.path.abspath(__file__))
STADIUMS = json.load(open(os.path.join(HERE, "..", "model", "stadiums.json")))["stadiums"]


def home_team(g):
    return g["matchup"].split(" @ ")[1].strip()


def impact(wind, gust, precip):
    if (wind or 0) >= 15 or (gust or 0) >= 25 or ((precip or 0) >= 70 and (wind or 0) >= 10):
        return "strong"
    if (wind or 0) >= 10:
        return "mild"
    return "none"


def cmd_list(a):
    out = []
    for f in sorted(glob.glob(os.path.join(a.data_dir, "*", "*.json"))):
        g = load_game(f)
        if g.get("status") == "final" or parse_iso(g["kickoff"]) < now_utc():
            continue
        h = home_team(g)
        st = STADIUMS.get(h)
        if "(" in h or st is None:
            out.append({"file": f, "skip": "neutral site or unknown stadium -- check by hand", "home": h}); continue
        if st["roof"] != "open":
            continue
        out.append({"file": f, "kickoff": g["kickoff"], "stadium": st["name"],
                    "url": f"https://forecast.weather.gov/MapClick.php?lat={st['lat']}&lon={st['lon']}",
                    "logged": (g.get("weather") or {}).get("captured_at")})
    for o in out:
        print(json.dumps(o))


def cmd_apply(a):
    g = load_game(a.game_file)
    st = STADIUMS.get(home_team(g), {})
    w = {"captured_at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "stadium": st.get("name"), "roof": st.get("roof"),
         "wind_mph": a.wind, "gust_mph": a.gust, "temp_f": a.temp, "precip_pct": a.precip,
         "impact": impact(a.wind, a.gust, a.precip), "source": a.source}
    hist = (g.get("weather") or {}).get("history", [])
    if g.get("weather"):
        prev = dict(g["weather"]); prev.pop("history", None); hist.append(prev)
    w["history"] = hist[-6:]
    g["weather"] = w
    save_game(a.game_file, g)
    print(json.dumps({k: w[k] for k in ("stadium", "wind_mph", "gust_mph", "temp_f", "precip_pct", "impact")}))


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list"); l.add_argument("data_dir")
    p = sub.add_parser("apply"); p.add_argument("game_file")
    p.add_argument("--wind", type=float, required=True); p.add_argument("--gust", type=float)
    p.add_argument("--temp", type=float); p.add_argument("--precip", type=float)
    p.add_argument("--source", required=True)
    a = ap.parse_args(); {"list": cmd_list, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    main()
