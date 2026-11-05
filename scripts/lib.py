"""Shared helpers for the nfl-daily-line-report skill scripts.

Keep this dependency-free (stdlib only) since it runs on the user's local
device shell, which may not have pip access.
"""
import json
import os
from datetime import datetime, timezone

BOOKS = ("bookmaker_eu", "draftkings", "fanduel", "caesars")
SHARP_BOOK = "bookmaker_eu"  # no-vig reference book; was pinnacle (confirmed unavailable
                             # from every tested source -- see SKILL.md "Line fetching")
KEY_NUMBERS = (3, 7)

# A book "line" is one of these shapes:
#   {"spread": -3.0, "total": 44.5, "ml_fav": -160, "ml_dog": 138}
#   None   (book hasn't posted / unavailable that day)


_ALIASES = None


def _aliases():
    """model/aliases.json (Phase 3, 2026-11-04): one canonical name per player/team/book."""
    global _ALIASES
    if _ALIASES is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "model", "aliases.json")
        raw = json.load(open(path)) if os.path.exists(path) else {}
        _ALIASES = {}
        for kind in ("players", "teams", "books"):
            m = {}
            for canon, others in (raw.get(kind) or {}).items():
                for n in [canon] + list(others):
                    m[_norm(n)] = canon
            _ALIASES[kind] = m
    return _ALIASES


def _norm(name):
    import re
    n = re.sub(r"[.'’]", "", str(name or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def player_key(name):
    """Match key for a player: same person -> same key, however a source spells him
    ("Harold Fannin" / "Harold Fannin Jr." / "D'Andre Swift" / "DAndre Swift")."""
    n = _norm(name)
    canon = _aliases()["players"].get(n)
    return _norm(canon) if canon else n


def team_name(name):
    """Full franchise name from any abbreviation or nickname (JAC/JAX/Jaguars -> Jacksonville Jaguars)."""
    n = _norm(name)
    return _aliases()["teams"].get(n, str(name).strip())


def book_key(name):
    """One key per sportsbook/source (dk/DraftKings -> draftkings)."""
    n = _norm(name)
    return _aliases()["books"].get(n, n or None)


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(ts):
    # Accept trailing 'Z' as UTC.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def hours_to_kickoff(kickoff_iso, reference=None):
    ref = reference or now_utc()
    kickoff = parse_iso(kickoff_iso)
    delta = kickoff - ref
    return delta.total_seconds() / 3600.0


def compute_tier(kickoff_iso, reference=None):
    """Returns (tier:int, hours:float, label:str). Tier 0 = kicked off / past."""
    hours = hours_to_kickoff(kickoff_iso, reference)
    if hours <= 0:
        return 0, hours, "Final/In Progress"
    if hours <= 48:
        return 1, hours, "Imminent (<=48 hrs)"
    if hours <= 144:  # 6 days
        return 2, hours, "Active (3-6 days)"
    return 3, hours, "Fresh (7+ days)"


def load_game(path):
    with open(path) as f:
        return json.load(f)


def save_game(path, game):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(game, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def today_str(reference=None):
    ref = reference or now_utc()
    return ref.strftime("%Y-%m-%d")


def spread_crossed_key_number(prev_spread, cur_spread):
    """True if the absolute spread crossed 3 or 7 between two snapshots."""
    if prev_spread is None or cur_spread is None:
        return False
    p, c = abs(prev_spread), abs(cur_spread)
    for k in KEY_NUMBERS:
        if (p < k <= c) or (c < k <= p) or (p == k and c != k) or (c == k and p != k):
            return True
    return False


def gap(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 3)


def most_recent_field(snapshots, book_key, field):
    """Scan backward from most-recent and return (value, captured_at) for
    the first snapshot where `book_key`'s line actually has a non-null
    `field` -- or (None, None) if that field has never once been posted for
    this book on this game."""
    for snap in reversed(snapshots or []):
        line = snap.get(book_key)
        if line is not None and line.get(field) is not None:
            return line.get(field), snap.get("captured_at")
    return None, None


def most_recent_book(snapshots, book_key):
    """Carry a book's line forward for DISPLAY purposes, field by field --
    spread, total, ml_fav, ml_dog each independently take the most recent
    snapshot where THAT field was actually posted, not just the most recent
    snapshot where the book posted anything at all. Returns
    (merged_line_dict_or_None, as_of_dict): merged_line_dict is None only
    when the book has never posted a single field, ever; as_of_dict maps
    each field name to the date it actually came from (None if that field
    itself has never been posted).

    Two gaps this fixes, both caught live 2026-09-10 on the real dashboard:
    (1) A book not posting on a GIVEN day is normal (a slow book, a fetch
        source that didn't carry it that day), and update_snapshot.py is
        right to record that day's snapshot as null for it -- that's the
        honest per-day truth. But build_dashboard.py was reading only
        `snapshots[-1]` for "current," so one day's null wiped out a
        perfectly good number from the day before and the dashboard showed
        "not captured" even though the site had a real, only-slightly-stale
        line to show.
    (2) The subtler version of the same bug: a book CAN post on a given day
        but only for some fields (e.g. DraftKings via a spread-only source
        that day) -- that day's snapshot entry for the book is a real,
        non-null dict, so a book-level "did this book post at all today"
        check doesn't catch it, and total/ml_fav/ml_dog still went blank
        even though spread was fine. Carrying forward per FIELD, not just
        per book, is what actually closes this one.

    Neither case touches the stored snapshot data itself -- only what
    build_dashboard.py hands to the template as "current."
    """
    fields = ("spread", "total", "ml_fav", "ml_dog")
    merged, as_of = {}, {}
    for field in fields:
        value, captured_at = most_recent_field(snapshots, book_key, field)
        merged[field] = value
        as_of[field] = captured_at[:10] if captured_at else None
    if all(v is None for v in merged.values()):
        return None, as_of
    return merged, as_of


def list_game_files(root):
    """Find game JSON files under `root`. Works whether `root` is a single
    week directory (data/2026-nfl-wk3/) or the parent data/ directory
    containing several week directories -- a game's tier depends on its
    kickoff time, not which week folder it happens to live in, so most
    scripts should be pointed at the parent data/ dir to see the whole
    tracked slate (this week's stragglers and next week's early opens
    alike) rather than one week at a time."""
    if not os.path.isdir(root):
        return []
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            if f.endswith(".json") and not f.endswith("-graph.json"):
                found.append(os.path.join(dirpath, f))
    return sorted(found)


# The fixed 32-team franchise list, used by the Power Rankings tab (both to
# seed a first-ever ranking and to compute each team's streak). Kept here
# rather than in build_dashboard.py so update_power_rankings.py can import
# it too without a circular import.
TEAM_NAMES = [
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
    "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
    "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
    "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
    "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
    "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants",
    "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
    "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders",
]


def normalize_team_name(name):
    """Strip a parenthetical venue/event qualifier some game files use for
    an international-series "home" team (e.g. "Los Angeles Rams
    (Melbourne)") so streak/ranking lookups fold back onto the real
    32-team franchise list instead of silently spawning a 33rd phantom
    team -- the same issue already visible in team_ratings.json, which
    this doesn't fix (that's apply_result's call), just doesn't repeat for
    display purposes here."""
    if not name:
        return name
    idx = name.find(" (")
    return name[:idx] if idx != -1 else name


def compute_team_streaks(data_dir):
    """Scan every game file under data_dir for finalized games and return
    {team_name: {"result": "W"/"L"/"T"/None, "label": "W2"/"L1"/None,
    "text": "beat Jaguars, beat Titans"/None, "games": int}} for each of
    TEAM_NAMES. Display-only -- this never touches Elo or scoring history
    (that's ratings.py/apply_result's job exclusively via log_result.py),
    it just re-derives a human-readable streak from the same finalized
    game files log_result.py already wrote status="final" onto."""
    games_by_team = {name: [] for name in TEAM_NAMES}
    for path in list_game_files(data_dir):
        game = load_game(path)
        if game.get("status") != "final" or not game.get("final_score"):
            continue
        try:
            away_raw, home_raw = [s.strip() for s in game["matchup"].split(" @ ")]
        except Exception:
            continue
        away, home = normalize_team_name(away_raw), normalize_team_name(home_raw)
        home_score = game["final_score"]["home"]
        away_score = game["final_score"]["away"]
        kickoff = game.get("kickoff", "")
        if home_score > away_score:
            home_result, away_result = "W", "L"
        elif away_score > home_score:
            home_result, away_result = "L", "W"
        else:
            home_result = away_result = "T"
        if home in games_by_team:
            games_by_team[home].append({"kickoff": kickoff, "result": home_result, "opponent": away})
        if away in games_by_team:
            games_by_team[away].append({"kickoff": kickoff, "result": away_result, "opponent": home})

    out = {}
    for team, games in games_by_team.items():
        games.sort(key=lambda g: g["kickoff"])
        if not games:
            out[team] = {"result": None, "label": None, "text": None, "games": 0}
            continue
        # Current streak: consecutive identical results counting back from
        # the most recent game.
        streak_result = games[-1]["result"]
        streak_games = []
        for g in reversed(games):
            if g["result"] != streak_result:
                break
            streak_games.append(g)
        verb = {"W": "beat", "L": "lost to", "T": "tied"}[streak_result]
        text = ", ".join(f"{verb} {g['opponent']}" for g in streak_games)
        out[team] = {
            "result": streak_result,
            "label": f"{streak_result}{len(streak_games)}",
            "text": text,
            "games": len(games),
        }
    return out
