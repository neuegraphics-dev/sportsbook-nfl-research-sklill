#!/usr/bin/env python3
"""Build the single consolidated report across the whole tracked slate.

Usage:
  python3 generate_report.py <data_dir> [--reference ISO_TS] [--out report.md]

<data_dir> should normally be the parent data/ directory (not one week
folder) so games from adjacent weeks all show up in the right tier bucket
together -- see compute_tier.py's docstring for why.

Prints the report to stdout (and writes it to --out if given).
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from lib import SHARP_BOOK, compute_tier, list_game_files, load_game, now_utc


def render(data_dir, reference=None):
    now = reference or now_utc()
    rows = []
    for path in list_game_files(data_dir):
        game = load_game(path)
        if game.get("status") != "active":
            continue
        tier, hours, label = compute_tier(game["kickoff"], now)
        rows.append((tier, hours, label, game))

    rows.sort(key=lambda r: r[1])

    tier1 = [r for r in rows if r[0] == 1]
    tier23 = [r for r in rows if r[0] in (2, 3)]

    def confidence_of(game):
        return (game.get("research") or {}).get("confidence")

    tier1_qualified = [r for r in tier1 if confidence_of(r[3]) in ("Medium", "High")]
    # "Watching" = no research run yet (confidence is null -- still pending
    # something like Friday's injury report). "No edge" = research WAS run
    # and it explicitly landed below Medium -- a completed, negative result,
    # not a pending one. Conflating these two used to make the "no edge"
    # count always read as zero.
    tier1_pending = [r for r in tier1 if confidence_of(r[3]) is None]
    tier1_no_edge = [r for r in tier1 if confidence_of(r[3]) is not None and confidence_of(r[3]) not in ("Medium", "High")]

    lines = []
    lines.append(f"NFL LINE REPORT — {now.strftime('%Y-%m-%d')} — {now.strftime('%H:%M UTC')}")
    lines.append("")
    lines.append("This is research, not a guaranteed outcome.")
    lines.append("")
    lines.append(f"TIER 1 — IMMINENT (<=48 hrs to kickoff)")
    lines.append("FILTERED LEANS (Medium+ confidence only)")
    lines.append("")

    if not tier1_qualified:
        lines.append("  (none clear Medium+ confidence right now)")
    for _, hours, label, game in tier1_qualified:
        r = game["research"]
        sharp = game["snapshots"][-1].get(SHARP_BOOK) if game["snapshots"] else None
        lines.append(f"[{game['matchup']}] ({game.get('week', '')}) — kickoff in {hours:.0f}h ({game['kickoff']})")
        lines.append(f"  SPREAD: {r.get('spread_lean', 'n/a')} | BookMaker.eu {sharp.get('spread') if sharp else 'n/a'}")
        lines.append(f"  TOTAL: {r.get('total_lean', 'n/a')} | BookMaker.eu {sharp.get('total') if sharp else 'n/a'}")
        lines.append(f"  MONEYLINE: {r.get('moneyline_lean', 'no edge')}")
        lines.append(f"  CONFIDENCE: {r.get('confidence')}")
        signals = r.get("top_signals") or []
        lines.append(f"  WHY: {' / '.join(signals[:2]) if signals else 'n/a'}")
        sim = game.get("simulation") or {}
        home_inj = sim.get("homeInjuryPts") or 0.0
        away_inj = sim.get("awayInjuryPts") or 0.0
        if abs(home_inj) >= 0.01 or abs(away_inj) >= 0.01:
            adj_margin = sim.get("injuryAdjustedMarginHome")
            away_name, home_name = [s.strip() for s in game["matchup"].split(" @ ")]
            model_str = "PK" if adj_margin is None or abs(adj_margin) < 0.05 else (
                f"{home_name} -{abs(adj_margin):.1f}" if adj_margin > 0 else f"{away_name} -{abs(adj_margin):.1f}"
            )
            lines.append(f"  MODEL (injury-adjusted): projects {model_str}, total {sim.get('projTotalAdjusted', 'n/a')} "
                         f"(home {home_inj:+.1f} / away {away_inj:+.1f} pts from injuries)")
        lines.append(f"  LINE MOVEMENT: {game['game_id']}-graph.html")
        lines.append("")

    lines.append("WATCHING, NOT YET ACTIONABLE")
    if not tier1_pending:
        lines.append("  (nothing in this window)")
    for _, hours, label, game in tier1_pending:
        lines.append(f"  {game['matchup']} — kickoff in {hours:.0f}h — research not yet run this cycle")
    lines.append("")

    lines.append("NO EDGE")
    lines.append(f"  {len(tier1_no_edge)} game(s) researched with no Medium+ lean — full slate available in data/ if you want to review manually.")
    lines.append("")

    lines.append("TIER 2/3 — TRACKING (not yet imminent)")
    if not tier23:
        lines.append("  (nothing tracked yet)")
    for _, hours, label, game in tier23:
        days = hours / 24
        conf = confidence_of(game)
        status_txt = f"early confidence: {conf}" if conf in ("Medium", "High") else "opening line logged"
        sim = game.get("simulation") or {}
        home_inj = sim.get("homeInjuryPts") or 0.0
        away_inj = sim.get("awayInjuryPts") or 0.0
        if abs(home_inj) >= 0.01 or abs(away_inj) >= 0.01:
            status_txt += f" — injury-adjusted (home {home_inj:+.1f} / away {away_inj:+.1f} pts)"
        lines.append(f"  {game['matchup']} ({game.get('week', '')}) — {days:.1f} days out — {status_txt}")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from lib import parse_iso
    reference = parse_iso(args.reference) if args.reference else None
    report = render(args.data_dir, reference)
    print(report)
    if args.out:
        with open(args.out, "w") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
