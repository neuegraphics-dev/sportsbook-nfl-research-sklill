#!/usr/bin/env python3
"""Regenerate the self-contained line-movement chart for one game.

Overwrites <game_id>-graph.html in place every time -- history lives in the
JSON, not in old copies of this file. Uses Chart.js from a CDN (this file is
opened locally in a browser, not published through any sandboxed artifact
pipeline, so a normal CDN URL is fine).

Renders four interactive charts (one per market -- spread, total, moneyline
favorite, moneyline underdog), each with one line per book (BookMaker.eu,
DraftKings, FanDuel, Caesars), plus a compact recent-moneyline table for
exact values. All four markets/all four books/every logged day, per game --
this is the per-game companion to the dashboard, which aggregates across
games instead of showing one game's full history.

Usage:
  python3 generate_chart.py <game_file.json> [<output_dir>]

If <output_dir> is omitted, writes "<game_id>-graph.html" next to the JSON.
"""
import html
import json
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from lib import BOOKS, load_game

BOOK_STYLE = {
    "bookmaker_eu": {"color": "#e8590c", "dash": [6, 4], "width": 3, "label": "BookMaker.eu (no-vig ref)"},
    "draftkings":   {"color": "#1971c2", "dash": [],     "width": 2, "label": "DraftKings"},
    "fanduel":      {"color": "#2f9e44", "dash": [],     "width": 2, "label": "FanDuel"},
    "caesars":      {"color": "#9c36b5", "dash": [],     "width": 2, "label": "Caesars"},
}


def fmt_label(captured_at):
    # "2026-09-17T18:00:00Z" -> "09/17 18:00"
    d = captured_at[5:16].replace("T", " ")
    return d


def series_for(snapshots, book, field):
    return [s.get(book, {}).get(field) if s.get(book) else None for s in snapshots]


def build_html(game):
    snapshots = game["snapshots"]
    labels = [fmt_label(s["captured_at"]) for s in snapshots]

    datasets_spread = []
    datasets_total = []
    datasets_ml_fav = []
    datasets_ml_dog = []
    for book in BOOKS:
        style = BOOK_STYLE[book]
        base = {
            "label": style["label"],
            "borderColor": style["color"],
            "backgroundColor": style["color"],
            "borderWidth": style["width"],
            "borderDash": style["dash"],
            "spanGaps": True,
            "pointRadius": 3,
            "tension": 0.15,
        }
        datasets_spread.append({**base, "data": series_for(snapshots, book, "spread")})
        datasets_total.append({**base, "data": series_for(snapshots, book, "total")})
        datasets_ml_fav.append({**base, "data": series_for(snapshots, book, "ml_fav")})
        datasets_ml_dog.append({**base, "data": series_for(snapshots, book, "ml_dog")})

    # Recent moneyline table (last 6 snapshots, newest first) -- kept alongside
    # the moneyline charts below for exact values at a glance.
    recent = list(reversed(snapshots))[:6]
    ml_rows = []
    for s in recent:
        row = [fmt_label(s["captured_at"])]
        for book in BOOKS:
            line = s.get(book)
            if line:
                row.append(f"{line.get('ml_fav', '-')}/{line.get('ml_dog', '-')}")
            else:
                row.append("&mdash;")
        ml_rows.append(row)

    matchup = html.escape(game["matchup"])
    week = html.escape(game.get("week", ""))
    kickoff = html.escape(game.get("kickoff", ""))
    game_id = html.escape(game["game_id"])
    n = len(snapshots)
    opening = game.get("opening_line")
    opening_txt = "not yet captured"
    if opening:
        p = opening.get("bookmaker_eu") or {}
        opening_txt = f"BookMaker.eu {p.get('spread', '?')} / {p.get('total', '?')} (captured {opening.get('captured_at', '?')})"

    ml_table_rows = "\n".join(
        "<tr><td>" + "</td><td>".join(r) + "</td></tr>" for r in ml_rows
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{matchup} — line movement</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 900px; margin: 32px auto; padding: 0 16px; }}
  h1 {{ font-size: 1.3rem; margin-bottom: 0; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 24px; }}
  .chart-wrap {{ margin-bottom: 40px; }}
  h2 {{ font-size: 1rem; margin-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  td, th {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right; }}
  td:first-child, th:first-child {{ text-align: left; }}
  .empty {{ color: #888; font-style: italic; }}
  .note {{ color: #666; font-size: 0.8rem; margin-top: 4px; }}
</style>
</head>
<body>
<h1>{matchup}</h1>
<div class="meta">{week} &middot; kickoff {kickoff} &middot; {n} snapshot{'s' if n != 1 else ''} logged &middot; opening line: {opening_txt}</div>

{'<p class="empty">No snapshots logged yet.</p>' if n == 0 else ''}

<div class="chart-wrap"><h2>Spread</h2><canvas id="spreadChart" height="110"></canvas></div>
<div class="chart-wrap"><h2>Total</h2><canvas id="totalChart" height="110"></canvas></div>
<div class="chart-wrap">
  <h2>Moneyline &mdash; favorite</h2>
  <canvas id="mlFavChart" height="110"></canvas>
  <p class="note">Tracks whichever side each book currently favors, not one fixed team &mdash; if the favorite flips sides, this line keeps following the favorite role, not the team. Cross-check against the Spread chart above (home-team-relative, so it stays continuous through a flip) to see whether a jump here is a role flip or a real price move.</p>
</div>
<div class="chart-wrap">
  <h2>Moneyline &mdash; underdog</h2>
  <canvas id="mlDogChart" height="110"></canvas>
</div>

<div class="chart-wrap">
  <h2>Moneyline (favorite / underdog), most recent first</h2>
  <table>
    <thead><tr><th>Captured</th><th>BookMaker.eu</th><th>DraftKings</th><th>FanDuel</th><th>Caesars</th></tr></thead>
    <tbody>{ml_table_rows if ml_rows else '<tr><td colspan="5" class="empty">No snapshots yet</td></tr>'}</tbody>
  </table>
</div>

<script>
const labels = {json.dumps(labels)};
const commonScales = {{
  x: {{ ticks: {{ maxRotation: 45, minRotation: 45 }} }},
  y: {{ ticks: {{ precision: 1 }} }}
}};
const mlScales = {{
  x: {{ ticks: {{ maxRotation: 45, minRotation: 45 }} }},
  y: {{ ticks: {{ precision: 0 }} }}
}};
new Chart(document.getElementById('spreadChart'), {{
  type: 'line',
  data: {{ labels, datasets: {json.dumps(datasets_spread)} }},
  options: {{ responsive: true, scales: commonScales, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
new Chart(document.getElementById('totalChart'), {{
  type: 'line',
  data: {{ labels, datasets: {json.dumps(datasets_total)} }},
  options: {{ responsive: true, scales: commonScales, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
new Chart(document.getElementById('mlFavChart'), {{
  type: 'line',
  data: {{ labels, datasets: {json.dumps(datasets_ml_fav)} }},
  options: {{ responsive: true, scales: mlScales, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
new Chart(document.getElementById('mlDogChart'), {{
  type: 'line',
  data: {{ labels, datasets: {json.dumps(datasets_ml_dog)} }},
  options: {{ responsive: true, scales: mlScales, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});
</script>
</body>
</html>
"""


def main():
    game_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(game_file)
    game = load_game(game_file)
    out_path = os.path.join(out_dir, f"{game['game_id']}-graph.html")
    with open(out_path, "w") as f:
        f.write(build_html(game))
    print(json.dumps({"chart_path": out_path, "snapshots": len(game["snapshots"])}))


if __name__ == "__main__":
    main()
