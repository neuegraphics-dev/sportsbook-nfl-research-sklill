# Line Watch reference (moved out of the nfl-daily-line-report skill, 2026-11-04)

Read the section a step points to; never read this whole file in a run. Section headings are unchanged from the old skill.


# NFL Daily Line + Research Report

Personal research tooling only. Every output is a lean with a confidence
level, never a guaranteed outcome -- say so in every report.

## Where everything lives

All data is stored in a folder on the user's computer, reached through the
device bridge (`mcp__remote-devices__*` tools), **not** in this skill's own
bundled files and **not** in the cloud session's own filesystem. This
matters more than it sounds: a scheduled run starts a brand-new cloud
session with an empty filesystem every time it fires, so anything written
only to the cloud workspace is gone by the next firing. The local folder is
the only thing that persists across runs. The exceptions are two read-only
*copies* of that data made for when the computer is off: the published
dashboard, which lives on claude.ai via the Artifact tool (see "The
dashboard" below), and a slim JSON snapshot in the Google Drive folder
"Line Watch" (see "Drive JSON snapshot" below) that Claude chat reads from
Michael's phone. Neither is a source of truth -- `data/` is -- and nothing
is ever read back from them to build the next run.

Current folder (confirm this hasn't changed if the skill seems to be
starting fresh each run -- that's the symptom of the folder becoming
disconnected):
```
/Users/mchamberlin/Local Sites/@claude_cowork/Sportsbook Research Sklill/
├── scripts/            (bundled, see below -- copy from this skill's own
│                        scripts/ dir onto the device once; don't hand-edit
│                        the on-device copy, update the skill and re-sync)
├── model/
│   └── team_ratings.json
├── data/
│   └── <season>-nfl-wk<N>/
│       ├── <game_id>.json
│       └── <game_id>-graph.html
├── results/
│   ├── results.json          (game final scores + grading, log_result.py)
│   ├── props_results.json    (graded player props, capture_prop_result.py)
│   └── early_edges.json      (early-edge / closing-line-value tracker, early_edges.py)
├── dashboard/
│   └── line-watch-build.html (scratch build output, staged + published each run)
└── reports/
    └── <date>-<time>.md
```

If `mcp__remote-devices__*` tools aren't available or the folder can't be
reached, say so plainly and stop -- don't fall back to writing into the
cloud workspace, since that data will vanish on the next scheduled firing
and silently break the whole point of tracking history over a week.

## Why the scripts exist, and why you should use them

The fetching and reasoning (web search, judging confidence, weighing
signals) has to happen in the cloud session -- the local device shell has
no web search and often no network at all. But the JSON read-modify-write,
trigger computation, chart/report rendering, and now the dashboard build
are pure mechanical transforms with an exact schema to get right across
dozens of files and growing histories -- the kind of thing that's easy to
get subtly wrong by hand (duplicate snapshots, clobbered opening lines,
off-by-one key-number detection, a hand-typed "moved" flag that
disagrees with its own spread delta) and cheap to get right once with a
tested script. So: **do the research in the cloud, then always call the
bundled scripts via `mcp__remote-devices__device_bash` to do the actual
read/write** -- never hand-construct or hand-edit the JSON files or the
dashboard HTML yourself, even for something that looks simple like adding
one snapshot or fixing one number on the dashboard.

Run scripts from inside the connected folder, e.g.:
```
cd "$HOME/mnt/Sportsbook Research Sklill" && python3 scripts/compute_tier.py data
```
(`python3` is confirmed available on the device; if a run reports it
missing, the device changed and you'll need to check again.)

### scripts/compute_tier.py `<data_dir>` [`--reference ISO_TS`]
Run this FIRST every time. Prints one JSON line per active game with its
tier (1/2/3), hours-to-kickoff, and file path. Pass the parent `data/` dir
(not one week folder) so games from adjacent weeks tier together correctly
-- a game's tier depends purely on hours-to-kickoff, not which week folder
it happens to be filed under, which is exactly what lets this self-adjust
for flexed games and bye weeks without any special-casing. `--reference`
is for testing only; omit it in real runs so it uses the actual current
time.

### scripts/setup_week.py `<week_dir> <games.json|->`
Creates the empty JSON skeleton for each game in a week's slate. Safe to
re-run -- never overwrites a game file that already exists, so re-running
mid-week to pick up a newly-flexed game doesn't touch existing history.
You supply `games.json` (matchup, kickoff, game_id, week) from a web
search of that week's schedule.

### scripts/update_snapshot.py `<game_file.json>` `--bookmaker ... --draftkings ... --fanduel ... --caesars ...` [`--skip-if-same-day`] [`--note ...`] [`--research '{...}'`]
The only write path for a game's JSON. Pass each book's line as a JSON
object string, or the literal string `null` if that book hasn't posted --
never omit a book or guess a number. (The `--bookmaker` flag writes into
the JSON's `bookmaker_eu` key -- see "Line fetching" for why BookMaker.eu
is the book here instead of Pinnacle.)
- **Tier 1 games**: call it plain, no `--skip-if-same-day`. Every call
  appends a new timestamped snapshot, which is the point -- the 3-4 hour
  cadence only produces a useful movement chart if each check is its own
  point in the history.
- **Tier 2/3 games**: always pass `--skip-if-same-day`. It's a no-op on
  price data (prints `"skipped": true`, doesn't touch the snapshot list)
  if today already has a snapshot, so calling it more than once a day for
  a slow-moving game never creates duplicate points. "Same day" means the
  Eastern-time calendar day (fixed 2026-11-07; it used the UTC date, so an
  11pm ET snapshot blocked the whole next ET day).
- **Research-only follow-up on an already-skipped day**: `--skip-if-same-day`
  only blocks a duplicate *price* snapshot -- it does not block `--research`.
  If a Tier 2/3 game's triggers flip true later the same day it was already
  logged (or you're doing a second research pass for any other reason), call
  `update_snapshot.py` again with the same `--skip-if-same-day` flag plus
  `--research '{...}'`: the script still prints `"skipped": true` but sets
  `"research_attached": true` in its JSON output and writes the research
  block onto the existing game file, no fabricated snapshot required.
  Verified live on 2026-09-02: 11 games hit this exact path (a same-day
  research re-run after the morning snapshot), all 11 returned
  `research_attached: true` with no duplicate snapshot points created.
  If you don't pass `--research`, the flag has no effect beyond the normal
  skip.
- First-ever snapshot for a game automatically becomes its `opening_line`
  and is never overwritten afterward.
- It computes and writes the `triggers` block for you (sharp-book-vs-book
  gap, key-number crossing, a raw movement heuristic). Read the printed
  JSON to decide whether this game needs a research re-run.
- Pass `--research` only when you're attaching a freshly-reasoned research
  block this call (see schema below) -- omit it to just log a line without
  touching the existing research.

### scripts/simulate_game.py `<game_file.json> <ratings_file.json>` [`--n 10000`] [`--seed N`]
Monte Carlo "run it 10,000 times" simulator. The only write path for a
game's `simulation` block -- builds a projected score for each team from
Elo (`model/team_ratings.json`, the spread signal) and each team's rolling
scored/allowed average (the total signal), folds in whatever
`injury_adjustments` the game file already has (see the next section --
0 pts if none), then draws `--n` independent trials to produce win%,
median score, cover%, over%, and (once enough real games are logged) a
promoted spread/total lean. Run it for every active game every cycle --
cheap, and it's what keeps the dashboard's win%/cover%/lean numbers,
"Bets to consider," and Best Bets current. Re-run it again any time
`apply_injury_adjustment.py` changes a game's injury pts, so the
simulation actually reflects the update rather than sitting stale until
the next scheduled cycle happens to touch that game.

### scripts/apply_injury_adjustment.py `<game_file.json>` `--home-items '[...]' --away-items '[...]'`
The only write path for a game's `injury_adjustments` block -- this is
what makes `simulate_game.py` injury-aware instead of purely a team-
strength (Elo + scoring-average) read that has no idea who's actually
active this week. Added 2026-09-22 after Eagles-Bears research flagged
Caleb Williams as a likely-out starter while the simulation still had the
Bears within half a point of a coinflip, because Elo genuinely can't know
that on its own.

Each `--home-items`/`--away-items` value is a JSON list of `{"position",
"player", "status" (one of `out`/`doubtful`/`questionable`), "pts"
(negative hurts that side; a number, not a string), "note" (required --
document the reasoning, since these are judgment calls made during
research, not something the script computes)}`. Pass `'[]'` to explicitly
clear a side once its injuries are no longer relevant; omit the flag
entirely to leave that side untouched. The script auto-detects **depth
stacking**: 2+ players logged `status: "out"` at the SAME position on the
SAME side adds an extra penalty on top of each player's own pts (losing
two starting corners costs more than the sum of two "solid backup"
swaps) -- capped, and only fires on confirmed-out players, never on
doubtful/questionable maybes. Prints the computed total pts per side plus
any stacking notes; always run `simulate_game.py` again afterward so the
change actually reaches the model.

Point-value starting points (a documented judgment call, not a formula --
see the script's own docstring for the full reasoning):
- Starting QB out, clearly worse backup: -4 to -7 pts
- Starting QB out, backup playing well / minimal drop-off: -1.5 to -3 pts
- Alpha skill player (true WR1/RB1, no real complementary backup): -1 to -2.5 pts
- Starting O-line anchor (LT, C) or top pass rusher: -1 to -2 pts
- Secondary starter / role player: -0.5 to -1 pt
- Questionable/limited but expected to play: 0, or a small fraction of the
  above if genuinely playing hurt -- don't price out a player who's
  probably suiting up. A genuinely unresolved situation (real doubt, no
  confirmed status yet) is fine to price at a smaller, uncertainty-
  weighted number rather than either 0 or the full out-penalty -- say so
  in the note, and revisit once the status firms up.

Example (Bears QB Caleb Williams likely out, home side):
```
python3 apply_injury_adjustment.py data/2026-nfl-wk3/eagles-bears-2026-wk3.json \
  --home-items '[{"position":"QB","player":"Caleb Williams","status":"out","pts":-6.0,"note":"Hamstring, HC called week-to-week and likely to miss; Bagent (UDFA)/Keenum to start"}]' \
  --away-items '[]'
python3 simulate_game.py data/2026-nfl-wk3/eagles-bears-2026-wk3.json model/team_ratings.json
```

**A team's injury-affected games also get flagged in its rolling history,
and the model self-corrects once the player is back healthy.** Added
2026-09-23 per Michael: a stretch of games played without a key starter
shouldn't quietly drag down that team's scored/allowed rolling average
forever once the player returns. This isn't a separate script -- it's
automatic, driven entirely by whatever `injury_adjustments` a game already
had at the time `log_result.py` logs its final score:
- `log_result.py` reads that game's own `injury_adjustments.home`/`.away`
  block and flags a side "injury-affected" if its total pts was <=-2.0
  (`FLAG_THRESHOLD_PTS` in that script) -- below that is treated as too
  minor to dilute the sample over. The flag and a short reason (named
  players + pts) get written into `model/team_ratings.json`'s
  `injury_flags`/`injury_flag_notes` arrays, parallel to and evicted in
  lockstep with `scored_history`/`allowed_history`.
- `simulate_game.py`'s scoring-average calls now pass
  `exclude_injury_flagged=True` -- `ratings.py`'s `ppg()` averages only the
  non-flagged ("clean") games in the window once there are at least 3 of
  them (`MIN_CLEAN_GAMES_FOR_EXCLUSION`), falling back to the full average
  when there aren't (an honest gap, not a guess). This is the entire
  "correct once resolved" mechanism -- no manual step, it self-corrects as
  clean games accumulate and flagged ones age out of the rolling window.
- When exclusion actually changes the number, `simulate_game.py` says so
  plainly in the simulation's own `notes` (which the dashboard already
  renders) -- naming how many games were excluded and why, never a silent
  shift.
- A team record written before this field existed (i.e. every team in
  `model/team_ratings.json` as of 2026-09-22) upgrades automatically and
  safely the next time any game is logged for it -- `log_result.py`
  backfills clean (False/None) history for any game gap rather than
  erroring or losing sync between the arrays. Verified against a
  synthetic 5-game sequence (2 flagged, 3 clean) and against a real
  legacy team record with no `injury_flags` key at all before this
  session's changes -- both produced the expected clean-only average.

### scripts/apply_game_evaluation.py `<game_file.json>` `--analysis '["p1","p2"]' --leans '[...]'`
The only write path for a game's `game_evaluation` block -- the analyst-
style write-up shown as "The read" in that game's drawer on the dashboard's
Slate section (see "The dashboard" below), and folded into the Board
section's pooled leans. Added 2026-09-23 per Michael: everything
else on the page is a number (a sim projection, a market line, a cover
percentage) with no plain-English "why" -- this records the judgment call
a pro handicapper would make once the sim, the injury picture, and the
research lean are all sitting in front of them, as structured data instead
of scratch notes that never leave a chat.

`--analysis` is a JSON list of prose paragraphs (aim for two, short --
what the sim and market are saying, why (injuries, home/away, Elo/recent-
scoring form), and what that means for reading the game, written like a
pro sports-betting analyst's preview, not a black-box verdict). `--leans`
is a JSON list of `{"market" (one of `spread`/`total`/`moneyline`),
"pick" (the literal line, e.g. "SEA -6.5"), "confidence" (0-10 number),
"reasoning" (required, a sentence explaining the number), "best_bet"
(optional bool, default false)}`. At most one lean per game may be
`best_bet: true` -- the single pick worth actually betting on that game,
if any (a flat game can have zero best-bet leans, or an empty `leans`
list entirely). Pass `'[]'` for `--leans` to explicitly record "no real
lean this game" rather than omitting the flag. Omit `--analysis` or
`--leans` entirely to leave that part of an existing block untouched
(e.g. updating leans after a late-week line move without rewriting the
prose). End every lean's `reasoning` with ` | Stake $X · pass at Y` per
"Bet sizing" below, so the dashboard shows the stake next to the reason.

Confidence scale (0-10), a judgment call like injury pts -- an honest,
documented estimate, not a computed probability. Each tier maps to a fixed
stake -- see "Bet sizing" below:
- 0-2 coinflip or worse, mentioned mainly for context -- no bet
- 3-4 a mild lean, low conviction -- no bet at 3; half unit or skip at 4
- 5-7 a real-to-strong lean -- the standard unit ($15)
- 8 model, injuries, and market agree -- 1.5 units
- 9-10 model, injuries, and research all point the same way -- rare, 2 units max

Write this AFTER `simulate_game.py` has the current picture for that game
(post injury-adjustment) so the prose and lean numbers actually describe
what the model is showing, not a stale read. Re-run it whenever the
injury picture or the sim's own read on a game changes meaningfully during
a cycle -- not just once per game per week.

Example:
```
python3 apply_game_evaluation.py data/2026-nfl-wk3/seahawks-commanders-2026-wk3.json \
  --analysis '["First paragraph -- the sim/market picture and why.", "Second paragraph -- what that means for the game."]' \
  --leans '[{"market":"spread","pick":"SEA -6.5 to -7","confidence":8,"reasoning":"Daniels confirmed out, every book has converged on the same number. | Stake $22.50 · pass at -7.5","best_bet":true}]'
```

### scripts/generate_chart.py `<game_file.json>` [`<output_dir>`]
Regenerates that game's `<game_id>-graph.html` in place from its full
snapshot history. Run this after every `update_snapshot.py` call that
wasn't skipped. Self-contained HTML (Chart.js from a CDN, opens fine
directly in a browser), and covers **all three markets, all four books,
every logged day, for that one game**: an interactive line chart each for
Spread, Total, Moneyline-favorite, and Moneyline-underdog, each with one
series per book (BookMaker.eu drawn as a thicker dashed line so it reads
as the sharp reference at a glance), plus a compact recent-moneyline table
underneath for exact numbers. This is the per-game companion to the
dashboard -- the dashboard aggregates the actionable stuff *across* games,
this page is the full history *within* one game. Handles zero-snapshot
games without erroring, so it's safe to run right after `setup_week.py`
too.

One caveat on the two moneyline charts: `ml_fav`/`ml_dog` track the
favorite/underdog *role*, not a fixed team -- if a game's favorite flips
sides (see the spread sign-convention note below), the "favorite" line
keeps following whichever team is currently favored, not one team
throughout. The chart page says this explicitly under the favorite chart
and points back at the Spread chart (home-team-relative, so it stays
continuous through a flip) as the way to tell a role flip apart from a
real price move.

### scripts/capture_prop_result.py `<game_file.json> <props_store.json> --player "Name" --market market_name --actual VALUE [--source "source_url"]`
The only write path for player prop results tracking. Grades individual
prop picks against actual game outcomes and maintains a persistent
`results/props_results.json` store. **Prevents phantom entries structurally**
by requiring the player/market pair to exist in the game file's own
`props.picks` array -- if it doesn't match, the script refuses rather than
allowing hand-entered data to drift from source.

Grading logic: reads the prop's own `opening_line` and `lean` (over/under)
from the game file, never takes the line by hand (so the store can't drift
from what was actually recommended). Compares actual outcome against line:
- **Over lean**: hit if actual > line, miss if actual < line, push if actual == line
- **Under lean**: hit if actual < line, miss if actual > line, push if actual == line
- **Receptions/plain numeric markets**: same comparison as over/under

Refuses to grade games not marked `status: "final"` unless `--force` is
passed (protects against grading mid-game when a player's final stat line
isn't known yet). Idempotent -- upserts by (gameId, player, market), so
correcting a source number just replaces that entry.

Output schema per prop (written to `results/props_results.json`):
```
{
  "gameId": "chiefs-dolphins-2026-wk3",
  "week": "Week 3",
  "matchup": "Kansas City Chiefs @ Miami Dolphins",
  "awayTeam": "Kansas City Chiefs",
  "homeTeam": "Miami Dolphins",
  "kickoff": "2026-09-21T17:00:00Z",
  "player": "Ja'Marr Chase",
  "position": "WR",
  "team": "Cincinnati Bengals",
  "market": "rec_yards",
  "lean": "over",
  "line": 98.5,
  "confidence": 8,
  "reasoning": "League-leading pace WR, perfect matchup",
  "actual": 102,
  "result": "hit",
  "source": "cbssports.com box score",
  "capturedAt": "2026-09-24T23:15:00Z"
}
```

Example usage:
```
python3 capture_prop_result.py data/2026-nfl-wk3/chiefs-dolphins-2026-wk3.json \
  results/props_results.json \
  --player "Kenneth Walker III" --market rec_yards --actual 22 \
  --source "espn.com box score"
```

### scripts/apply_props.py `<game_file.json> --status captured|pending --picks '[...]'` [`--merge`] [`--source-note ...`]
The only write path for a game's `props` block -- the opening player-prop
numbers to watch (receptions / rec_yards / rush_yards / pass_yards /
completions / attempts), each with a one-sentence reason plus structured
factors. Never hand-edit this block.

Props get bet into one direction within the first ~20 minutes of posting,
so the number that matters is the OPENER, not whatever it's drifted to by
the time anyone looks. The script enforces that: the first `line` and its
timestamp recorded for a given (player, market) become that pick's
`opening_line`/`opening_captured_at` and are NEVER overwritten by a later
call -- only `current_line`/`current_captured_at` update on re-runs, so a
pick's movement since open stays visible without losing the number that
actually mattered.

Pass `--status pending --picks '[]'` to record "checked this game, nothing
posted or nothing clean enough yet" -- `Run behavior` below re-checks a
pending game every cycle until it flips to captured. Pass
`--status captured` with a real `--picks` list once there's at least one
pick worth recording; a pick already on file keeps its opener untouched and
only has its current_line/lean/confidence/reasoning/factors refreshed.
**Once a game's props are `captured`, do not touch that game's props again**
-- re-checking an already-captured game risks silently replacing a good
pick with a worse one found on a later pass; a brand-new pick for a
different player/market on that same game is fine via `--merge`.

**This is the filter the user is relying on to avoid sifting through 100+
raw lines a week -- it is deliberately strict, and the strictness is
working: the hit rate has run around 68% since this went live. Do not
loosen it to manufacture more picks.** Every pick must carry a `factors`
list -- each a separate, stat-backed reason with a direction relative to
the lean (`for` / `against` / `neutral`), never just a one-line vibe:
- At least `MIN_FACTORS` (3) factors per pick, each with a real stat in
  `detail` (opponent rank + rate stat, volume/usage number, model
  projection gap, game-script number, injury note -- see the factor
  checklist below).
- Optional `matchup` block: `{"opp_rank_vs_pos": 1-32, "rank_basis": "..."}`,
  rank 1 = toughest defense vs that position, 32 = softest. Use
  `footballdb.com/fantasy-football/points-allowed.html?pos=RB|WR|TE|QB`
  for this (fetch once per position per cycle, not per player -- it covers
  every team in one page).
- **Tough-matchup cap**: an OVER leaning into a top-10 toughest defense vs
  the position (or an UNDER into a bottom-10/softest one, rank >= 23) is
  capped at confidence 4, whatever the other factors say. A great model
  gap still isn't a strong play if it's fighting the league's best defense
  at that position.
- **Alignment gate, enforced by the script, not just a guideline**:
  confidence >= 5 needs net alignment (`for` - `against`) >= 1; confidence
  >= 7 needs net >= 3 *and* at most one `against`. A pick that doesn't
  clear these raises a validation error rather than silently saving at a
  lower number -- fix the factors or lower the confidence, don't retry
  with the same confidence and a padded factor.
- The script stores an `alignment` summary (`{for, against, neutral, net}`)
  on each pick automatically from the factors you pass -- never set this
  by hand.

The Props section of the dashboard (see `build_dashboard_v2.py` / "The
dashboard" below) renders this `factors` list as an actual checklist next
to each pick -- write each `detail` as something a person would want to
read there, not an internal shorthand.

Factor checklist to research for every pick (aim for 4-6 real ones, not
padding to hit the minimum):
- **Matchup**: opponent's defense-vs-position rank (footballdb, above) +
  a rate stat (pts/g or yds/g allowed to the position) + the opponent's
  team-level per-game numbers allowed for this market from lineups.com
  (see "Team defense per-game allowed -- lineups.com" below). Early season
  (<4 games), also note *who* they faced so a plus matchup against a weak
  offense doesn't get overweighted.
- **Volume/usage**: carries, targets, target share, snap share, last 2-3
  games -- a name alone ("he's good") is not a factor.
- **Model**: when a props source posts its own projection next to the
  line (covers.com does -- see "Player props -- validated sources"
  below), the gap between its projection and the actual line, with the
  %-edge it quotes, is a legitimate single factor -- but it's one factor,
  not corroboration on its own.
- **Game script**: this game's own current spread/total from its
  `snapshots` (favorite -> more rush volume likely, dog -> more pass
  volume likely; a big favorite protecting a lead can mean *more*
  garbage-time rushing late, which can cut against an RB-rushing OVER on
  a big home favorite -- don't assume "favorite = more rushing" blindly).
  Public bet%/handle% (see `apply_market_signal.py` below) is NOT game
  script -- label any reference to it `neutral` here.
- **Injuries**: the player's own status, backfield/WR-room competition
  for his touches, and the opponent's key defenders or starting QB.
- **Offense trend**: team pass/rush yds per game and league rank, any
  recent QB change.
- **Implied efficiency** (rushing/passing props): divide the yards line by
  the attempts line to get the book's implied per-carry/per-attempt rate,
  and compare it to the player's own rate and the matchup. When the
  attempts line already prices the workload, efficiency is the only edge
  left.

**A third-party grade is never a substitute for the Matchup factor, even
when it's the only thing a source hands you.** Caught live 2026-10-01:
several picks sourced from RotoWire's board used "RotoWire grades this an
A" as their `Model` factor and had no real `Matchup` factor at all --
RotoWire's own grade is opaque (no visible number behind it), so a pick
built that way was really resting on 1-2 real factors, not 3+. Always
still fetch the real opponent-defense-vs-position rank from footballdb.com
(above) for every pick, RotoWire-sourced or not, even if the source
itself didn't hand you one. Two real examples from that same catch: a
Jerry Jeudy WR-yardage over looked fine on RotoWire's grade alone, but
PIT turned out to rank 8th of 32 (genuinely tough) vs WR -- the tough-
matchup cap applied and the confidence had to drop from 6 to 4; a Daniel
Jones passing-yards under looked fine too, but WAS ranked 31st of 32 vs
QB (one of the most generous pass defenses in the league) -- also capped.
In the same pass, a Marcus Mariota passing-yards over that had looked
like a solid 6 turned out to have IND ranked dead last (32nd) vs QB, a
real, strong `for` factor that hadn't been checked -- raising it to a 7
was the honest call once the number was actually in hand. The lesson
cuts both ways: getting the real number sometimes lowers confidence and
sometimes raises it, but skipping the lookup and leaning on a vague
grade is never allowed to stand in its place.

Confidence ~ net alignment, as a starting point (the alignment gate above
is the hard rule; this is the judgment call within it): 1-2 net -> 4-5,
3 net -> 6, 4+ net with <= 1 against -> 7-8.

`--merge` keeps picks already on file that weren't included in this call's
`--picks` list (e.g. adding one new player/market to a game that already
has picks, without re-passing every existing one). Default behavior
without `--merge` replaces the whole picks list with what's passed --
don't use that to "update" a game that already has other captured picks
unless you mean to drop them.

Usage:
```
python3 apply_props.py data/2026-nfl-wk4/cowboys-texans-2026-wk4.json --status captured \
  --picks '[{"player":"C.J. Stroud","team":"home","position":"QB","market":"pass_yards",
             "line":245.5,"book":"BetRivers","lean":"over","confidence":6,
             "reasoning":"Model and matchup both point the same direction as Houston favored at home. | Stake $15 · pass at 252.5",
             "factors":[{"label":"Matchup","dir":"for","detail":"Dallas allows 53.0 fantasy pts to WRs thru 3 games, middle of the pack"},
                        {"label":"Model","dir":"for","detail":"Covers.com projection 282.9 vs 245.5 line, +37.4 diff, 26.4% edge"},
                        {"label":"Script","dir":"for","detail":"Sim favors Houston by 5.2 net pts at home"}]}]' \
  --source-note "covers.com player-props board, rendered via browser"

python3 apply_props.py data/2026-nfl-wk4/jets-bears-2026-wk4.json --status pending --picks '[]' \
  --source-note "Only line found (Burden rec over 42.5) leans into the Jets' #1 WR defense -- fails the tough-matchup cap. Re-check next cycle."
```

### scripts/apply_market_signal.py `<game_file.json>` [`--spread '{...}'`] [`--total '{...}'`] [`--moneyline '{...}'`] `--source SOURCE` [`--note ...`]
The only write path for a game's `market_signal` block -- public bet% vs
handle%(money%) splits for spread/total/moneyline, plus a derived
reverse-line-move (RLM) read against this game's own logged line
movement. Added 2026-09-24 per Michael: a lopsided *ticket* count means
little on its own -- the useful signal is handle (money) skewed one way
while the LINE hasn't moved that way (or moved the other way), which
means the side taking fewer, bigger bets is the side the book is more
worried about. The script does this comparison itself (reading this
game's own `opening_line` vs its most recent snapshot, preferring the
sharp book then falling back to draftkings/caesars/fanduel) so it never
has to be eyeballed off the dashboard.

Percentages are all **home-relative** for spread/moneyline
(`bets_pct_home`/`handle_pct_home` = the share ON the home team, not on
"the favorite") and **over-relative** for total
(`bets_pct_over`/`handle_pct_over`) -- matching this project's
home-team-relative spread-sign convention everywhere else. A source will
usually label its split by team name or over/under directly; convert to
home/over before passing it in, never pass a favorite-relative number
unconverted.

**RLM note -- read this before calling a split a reverse line move in a
report**: the script flags `reverse_line_move: true` only when handle is
genuinely lopsided (>=60/40) on one side *and* the line has moved (or
already stands) against that side. A lopsided handle split with the line
still unchanged is **not** a reverse line move yet -- it's a thing to
watch, not a finding. The script's own `rlm_note` says which case applies
in plain language each time; use that wording rather than re-deriving it
by eye, and don't report "RLM" in a message to the user unless the
printed `reverse_line_move` is actually `true`.

Omit any of `--spread`/`--total`/`--moneyline` to leave that market's
split untouched on a re-run (e.g. only the total posted this cycle) --
pass `--note` on its own only together with at least one of them, not
as a bare update (the script requires at least one market per call).

Usage:
```
python3 apply_market_signal.py data/2026-nfl-wk4/broncos-49ers-2026-wk4.json \
  --spread '{"bets_pct_home":70,"handle_pct_home":92}' \
  --total '{"bets_pct_over":70,"handle_pct_over":94}' \
  --source cleatz.com --note "49ers(home)-relative, converted from cleatz's Broncos(away) +3 row (30/8 bets/handle)."
```
See "Public betting splits -- validated sources" below for where to pull
`bets_pct`/`handle_pct` numbers from and how often to refresh them.

### scripts/apply_prop_backtest.py `<game_file.json> --player "Name" --market market_name --games '[...]' --source SOURCE` (added 2026-10-01)
The only write path for a captured pick's `season_log`/`backtest` -- a
**different, complementary** stat to `capture_prop_result.py`'s official
grading. That one grades a pick against the real line that was actually
offered the week it happened, and only exists once this project has
captured and graded a pick on that exact player/market before -- which is
most weeks for most picks, since a given player/market combo rarely
repeats. This script instead pulls that player's **real game-by-game
production this season** (see "Player prop season game logs -- validated
source" below) and checks how many of those already-played games would
have cleared *today's* line and lean, had this exact number been the line
every week. It's a volume/form proxy grounded in real production, not a
guess -- use it exactly because most picks have zero official grading
history yet; never substitute it for `capture_prop_result.py`'s official
record once that exists.

`--games` is a JSON list of `{"week": N, "actual": X}` for games the
player has **already played** this season, oldest first -- never the
upcoming game this pick is actually for. Pass as many weeks as the source
actually has; a rookie or an early-return-from-injury player legitimately
may only have 1-2 games, which is an honest small sample, not an error --
say so in `--source` (e.g. `"statmuse.com (only 1 game found, missed
time)"`) rather than padding with a guess. The script looks up the pick
by (player, market) -- it must already exist via `apply_props.py` first.
Pushes (`actual == line`) count toward `total` display context but not
into the hit/miss percentage, matching `report_props.py`'s convention.

Usage:
```
python3 apply_prop_backtest.py data/2026-nfl-wk4/steelers-browns-2026-wk4.json \
  --player "Jerry Jeudy" --market rec_yards \
  --games '[{"week":1,"actual":26},{"week":2,"actual":0},{"week":3,"actual":0}]' \
  --source "statmuse.com"
```

This is a per-week-cycle research task (one query per player, roughly
30 for a full slate), not part of the automated hands-off pipeline --
running it every 2-hour scheduled cycle would be wasted cost for data
that only changes once a week when new games are played. Run it once,
interactively, after that week's props are captured (or when asked for
prop hit rates specifically), and again the following week once more
games have been played.

### scripts/early_edges.py `add|taken|refresh|report` (added 2026-10-06 per Michael)
The only write path for `results/early_edges.json` -- the early-edge /
**closing line value (CLV)** tracker. Never hand-edit that file. The
whole point of researching at line open (Monday for Sunday games) is to
grab a number before the market moves, and that skill is measured
separately from whether the bet wins:
- **CLV**: did the line move in our favor between our recommendation and
  kickoff? (beat-the-close rate, average points gained). Beating the close
  consistently is the best long-run evidence of a real edge, even in weeks
  where results are noisy. Benchmark: beating it on 55%+ of picks with a
  positive average.
- **Result**: did the recommended side win *at our number*? Tracked with
  paper P/L at the recommended stake.
- **Taken**: did Michael actually place it? Real P/L is tracked only for
  taken bets. As of Week 5 2026 he is in a refining stage and not placing
  the bets, so most entries are paper.

Conventions: `spread` side = `home`|`away` and the line is side-relative
(JAX home -2.5 -> `-2.5`; LV away +3.5 -> `3.5`); `total` side =
`over`|`under`; props use the prop market name (`rush_yards`,
`rec_yards`, ...) with `--player` and side `over`|`under`. Only the FIRST
recommendation of a given (game, market, side, player) is kept -- a later
re-recommendation at a different number is not a new edge.

```
# log a recommendation (do this the moment a lean/prop is recommended at stake >= $7.50)
python3 scripts/early_edges.py add --game data/2026-nfl-wk5/eagles-jaguars-2026-wk5.json \
  --market spread --side home --line -2.5 --book dk --conf 6 --stake 15 --pick "Jaguars -2.5"
python3 scripts/early_edges.py add --game data/2026-nfl-wk5/buccaneers-cowboys-2026-wk5.json \
  --market rush_yards --side over --player "Jalon Daniels" --line 43.5 --odds -114 --book FanDuel \
  --conf 6 --stake 15 --pick "Jalon Daniels o43.5 rush yds"
# mark one Michael actually placed (optionally at the number he got)
python3 scripts/early_edges.py taken --id "eagles-jaguars-2026-wk5|spread|home|" --line -2.5
# every run: update current lines, lock the close at kickoff, grade finals
python3 scripts/early_edges.py refresh
python3 scripts/early_edges.py report
```
`refresh` reads each game's own snapshots (the recommended book first,
then DraftKings/FanDuel/Caesars), locks `closeLine` from the last
snapshot before kickoff, and grades spreads/totals from `final_score` and
props from `results/props_results.json` (so grade props first). Its
summary breaks CLV down **by day recommended** (Mon vs Tue vs ...), which
is the direct answer to "does researching on Monday pay off."
`build_dashboard_v2.py` reads the store and the template renders it as an
"Early edges & closing line value" table under Hit rates. Note: this
script and the dashboard hook were added directly on the device on
2026-10-06 (backups `*.bak_pre_edges` beside the edited builder and
template) -- if this skill's bundled scripts are ever re-synced onto the
device, carry `early_edges.py` and those two edits along.

### scripts/report_props.py `<props_store.json>`
Aggregates hit rates from `results/props_results.json` across multiple
dimensions: overall record, by week, and by confidence tier (7-8 strong,
5-6 real, 1-4 marginal). Excludes pushes from hit rate calculations but
reports them separately. Also lists all misses with full details (player,
market, lean, line, actual result, game context) for review.

Output format:
```
OVERALL: 16/21 = 76.2%  (2 pushes, excluded)

BY WEEK:
  3: 16/21 = 76.2%

BY CONFIDENCE TIER:
  7-8 (strong): 1/2 = 50.0%
  5-6 (real): 8/11 = 72.7%
  1-4 (marginal): 7/8 = 87.5%

MISSES:
  [player] ([market]) [lean] [line] -> [actual]  [[matchup]]
```

Use this to spot confidence-tier performance patterns (e.g., whether
highest-conviction picks actually outperform marginal ones, which is the
real question for tightening the selection bar). Push games are excluded
from the hit rate so the denominator represents "definitive wins vs losses"
only, not "how many times was I right in the broadest sense."

Example:
```
python3 report_props.py results/props_results.json
```

### scripts/generate_report.py `<data_dir>` [`--out reports/<name>.md`]
Builds the single consolidated report across every active game, in every
tracked week, in the exact section order in "Output format" below. Point
it at the parent `data/` dir, same reasoning as `compute_tier.py`.

### scripts/build_dashboard_v2.py `<data_dir> <template_html> <output_html>` [`--reference ISO_TS`]
**Use this one, not `build_dashboard.py`.** `build_dashboard.py` /
`scripts/dashboard_template.html` are the retired old UI, kept on disk
only for reference -- they do not render `props`/`market_signal` data at
all, so building with them would silently drop the props roster, the
Receipts prop hit-rate, and the Follow-the-money RLM section off the
published dashboard. Always call `build_dashboard_v2.py` with
`scripts/dashboard_template_v2.html` as the template.

Builds the Line Watch dashboard's HTML from the exact same `data/*.json`
files everything else above reads and writes, plus `results/results.json`,
`results/props_results.json` and `results/early_edges.json` -- there is no
separate hand-maintained dataset behind the dashboard, and there should
never be again (an earlier ad hoc, hand-typed version of this dashboard
shipped a factual bug -- a key-number note claiming a spread move crossed
both 3 and 7 when the math only supported 3 -- that this script's
mechanical derivation exists specifically to prevent). Point `data_dir` at
the parent `data/` dir, `template_html` at the bundled
`scripts/dashboard_template_v2.html`, and `output_html` at a scratch path
in the connected folder, e.g. `dashboard/line-watch-build.html`. Prints
one JSON line summarizing what it built -- `games`, `props` (pick count
across the whole slate), `wong` (which games qualify for a Wong teaser),
`propsHits`/`propsGraded` (from `results/props_results.json`). **Read that
line before publishing**: confirm `propsGraded` is what you expect (0 only
if genuinely nothing has been graded yet) and that there's no error --
it's the sanity check that the run produced something sensible before it
goes live. `--reference` is for testing only.

**This is a single scrolling page, not a tabbed dashboard** -- the nav bar
links are anchor jumps to seven sections: **Board, Teasers, Slate, Money,
Systems, Props, Receipts**. (An earlier version of this file described a
tabbed Bet Now/Lines/+EV/Arbitrage/Teasers layout -- that was `build_dashboard.py`'s
retired UI; v2 is a full redesign, not a relabeling, so don't assume the old
tab names or per-tab behavior carry over.)

- **Board** (top of page, right under the masthead): pools every game's
  spread/total leans together with every player-prop pick, filters to
  confidence >= 5, and caps the combined list to the **top 10 by
  confidence**. Any Wong-teaser-qualifying leg is the one exception -- shown
  in its own uncapped tier above that top-10 list, so a qualifying teaser
  is never silently dropped by the cap. **This is "what to look at first,"
  not the full props list** -- a prop that doesn't crack the combined
  top 10 still exists and is fully visible in the Props section below; if
  that's ever confusing (it has been), say "the Board's capped top-10"
  rather than just "props" to keep the two straight.
- **Teasers**: every Wong-teaser-qualifying leg, in full, never capped.
- **Slate**: every tracked game, sorted by how far the model and the
  market disagree (biggest gap first). Opening a game's row reveals its
  full drawer: the model-vs-market read ("The read," from
  `game_evaluation.analysis`), that game's own leans with their factors
  checklist, any matching situational systems, the market-signal/RLM
  read (see `apply_market_signal.py` above), and a 4-row book table (Open,
  DraftKings, BookMaker.eu, Caesars, plus moneyline).
- **Money**: the Follow-the-money section -- cleatz.com bet%/handle%
  splits and the derived reverse-line-move read.
- **Systems**: situational historical-ATS systems that match this week's
  actual situations for each game (home/away, divisional, short week,
  etc.) -- third-party historical records, not this model's own output.
- **Props**: every captured player-prop pick, uncapped -- unlike the
  Board, nothing here is ever hidden by a top-N cut. Each pick shows its
  full **factors checklist** (✓ for / ✕ against / • neutral, each with the
  real stat behind it -- added 2026-10-01; before that the roster only
  showed a colored-dot tally with no text, which is how a 31-pick week
  could look like it was missing data when it wasn't). As of the same
  date, each pick also shows a row of **colored history chips**: that
  same (player, market) combo's own past graded weeks from
  `results/props_results.json` (hit=green, miss=red, push=gold, labeled
  with the week and the actual number), so recent trend for that exact
  prop is visible at a glance -- empty for a player/market this project
  hasn't graded before, which is an honest gap, not a bug. See
  `build_dashboard_v2.py`'s `build_prop_history()` for how this is
  derived (case-insensitive player-name match + market, sorted oldest
  first, past weeks only -- never includes the current, ungraded pick).
  The chip row's label also carries that same (player, market)'s hit
  rate as `hits-misses (pct%)` (pushes counted in neither, same convention
  as `report_props.py`) -- shown only once there's at least one graded
  week. **A player/market with no history still gets a visible row** --
  "No graded weeks yet for `<player>` · `<market>`" -- rather than
  nothing at all; an empty `pr-hist` div with no fallback text looked
  indistinguishable from the feature being missing entirely (caught live
  2026-10-01, since most of a given week's picks are on a player/market
  this project hasn't tracked before and so have no chips to show).
  Directly below that (when present), a second row -- visually set off by
  a dashed top border -- shows `apply_prop_backtest.py`'s **season
  backtest**: that player's real game log this season, each game colored
  by whether it would have cleared *today's* line, with the same
  `hits-misses (pct%)` summary and a small source credit. This is the one
  that actually covers most of a week's picks (official per-week grading
  above it usually doesn't, since most player/market combos haven't been
  captured and graded before) -- see that script's section above for how
  it's computed and why it's kept as a separate, clearly-labeled row
  rather than merged into the official hist chips.
- **Receipts / Hit rates**: every logged game result graded against the
  model's pregame call, plus the player-prop hit-rate breakdown (see
  `report_props.py` above), and below them the **Early edges & closing
  line value** table from `early_edges.py` -- each recommendation's line,
  the current/closing line, CLV points, paper vs taken, and result.

One scope note on `key_number_crossed`/the dashboard's key-number copy:
it's derived from **DraftKings' own opening line vs. DraftKings' current
line**, not from any US book vs. the sharp book. That's by design (DK is
the most consistently available "current" source across the validated
fetch sources below), but it means a key-number note on the dashboard is
answering "has DK's own number crossed 3 or 7 since it opened," not
"has the market broadly crossed a key number" -- worth remembering before
reading a missing key-number note as "nothing crossed anywhere." For the
full multi-book picture on a specific game, that's what the per-game
`generate_chart.py` page is for.

## Folder connection preflight (added 2026-10-25 per Michael)

A scheduled firing on 2026-10-25 12:37Z found `connectedFolders: []` and
had to stop even though the scheduled task lists the folder. The folder is
only usable once this session's device link has it mounted, so every run
begins with this check BEFORE `compute_tier.py` or anything else:

1. Run `mcp__remote-devices__device_bash` with
   `cd "$HOME/mnt/Sportsbook Research Sklill" && ls scripts data results model`.
2. If it fails with "No folders are connected" (or the path is missing), call
   `mcp__remote-devices__get_device_info`. If `connectedFolders` is empty, call
   `mcp__remote-devices__device_request_folder_access` ONCE with
   `["/Users/mchamberlin/Local Sites/@claude_cowork/Sportsbook Research Sklill"]`
   (reason: scheduled Line Watch run needs the data folder). On the 2026-10-25
   interactive session this call was granted immediately and the mount worked
   at once. Then repeat step 1.
3. If the grant is declined, times out, or the device is offline, STOP: send
   the user a plain message (and a push notification) saying the folder is not
   reachable. Never write to the cloud workspace instead, and never retry the
   request more than once.
4. Keep the scheduled task's `folders` list set to this exact path (check with
   `list_triggers`: `derived_state.folders_state` should be
   `FOLDERS_STATE_PRESENT`). The folder also needs to stay attached in the
   Claude desktop app, and the app must be open on the Mac, for unattended
   runs to see it.

## Run behavior, in order

0. Folder connection preflight (see above) -- stop here if it fails.
1. `compute_tier.py data` (the parent dir, covering every tracked week).
2. If there's no active week directory yet, or the furthest-out game is
   more than ~10 days away with nothing closer, this is a setup run: ask
   the user "track the full slate, or specific games?" (default: full
   slate -- the point of this skill is the filter, not manual curation),
   web-search that week's schedule, and call `setup_week.py`. Then stop --
   nothing to fetch yet. As soon as the next week's openers post (Sunday
   night / Monday morning), set that week up and capture openers right
   away -- the early number is the edge.
3. For each **Tier 1** game: fetch fresh lines (see "Line fetching" below)
   and re-run the research signal check every time, since this is the
   window where timing execution actually matters.
4. For each **Tier 2/3** game: fetch lines once a day. Only re-run the
   full research check if `update_snapshot.py`'s printed triggers flipped
   true, or research has never been run for that game -- don't burn a
   research pass re-analyzing a game that opened 6 days out and hasn't
   moved.
   **Before writing up any book-vs-sharp divergence as a per-game story,
   check whether it's actually systemic.** If two or more US books show
   numbers identical to the prior snapshot across nearly the entire
   tracked slate while BookMaker.eu (or one book in particular) moved
   broadly, that's much more likely one book-wide event -- a stale
   fetch source, a source page that didn't refresh, or a single sharp-book
   reprice -- than N independent per-game news stories. Caught live on
   2026-09-02: DraftKings and Caesars returned numbers identical to
   2026-09-01 across nearly the whole slate while BookMaker.eu moved on
   most games. Writing that up as 11 separate "steam" narratives would
   have been overconfident and wrong; the honest call was one shared
   caveat ("DK/Caesars sources didn't refresh today -- treat today's
   BookMaker.eu movement as informational, not yet confirmed as a genuine
   market move until a book actually reprices") attached to every affected
   game's research note, at Low confidence, rather than a pile of
   independently-argued Medium/High leans the evidence didn't support.
5. After each successful (non-skipped) `update_snapshot.py` call, run
   `generate_chart.py` for that game.
6. **Injury check + re-simulate.** For every active game researched this
   cycle (Tier 1 every time; Tier 2/3 whenever research was actually run,
   per step 4 above), check whether anything found should change that
   game's `injury_adjustments` -- a new out/doubtful/questionable
   designation on a player significant enough to matter, or a previously-
   out player getting cleared to play. Call `apply_injury_adjustment.py`
   to record it (or `'[]'` to clear a resolved side), then run
   `simulate_game.py` for that game so its win%/cover%/over%/lean actually
   reflect the update rather than sitting on a stale, injury-blind read.
   See "scripts/apply_injury_adjustment.py" above for the schema and
   point-value guidance. Even on a cycle with nothing new to log, it's
   cheap and fine to re-run `simulate_game.py` across the whole active
   slate anyway, same reasoning as step 9 below for the dashboard.
7. **Analyst evaluation.** For every active game (not just ones touched
   this cycle -- a game with no `game_evaluation` yet should get one the
   first time this step runs for it), once `simulate_game.py` has that
   game's current picture, write or refresh its two-paragraph analyst
   write-up and confidence-scored leans with `apply_game_evaluation.py`.
   Synthesize the sim output, `injury_adjustments`, and the research
   block into an honest, analyst-voiced read -- ground every claim in an
   actual number already on the game file (Elo margin, cover%, injury
   pts, the market gap) rather than inventing color. Re-run this for a
   game whenever its injury picture or the sim's own read changed
   meaningfully this cycle, not just once per game per week. See
   "scripts/apply_game_evaluation.py" above for the schema and confidence
   scale.
8. **Result + prop grading check.** Before touching props or market
   signal, check whether any game newly finished since the last cycle
   (`status` flips to `final`): log its result (`log_result.py` then
   `capture_result.py`, cross-checking the final score against at least
   two sources), and for every newly-final game that had captured prop
   picks, grade each one with `capture_prop_result.py` against the real
   box score (verify the player's team/stat line against at least one
   more source than the first box score you find -- see the
   misattribution caution under "Line fetching" above, which applies just
   as much to box scores as to injury articles).
9. **Props.** For every active game whose `props` block is missing or
   still `pending`, look for opening player-prop numbers (see "Player
   props -- validated sources" below) and call `apply_props.py` with
   `--status captured` (real picks, factors, and reasoning -- see that
   script's section above for the filter) or `--status pending --picks
   '[]'` (checked, nothing clean enough yet). **Skip a game entirely once
   its props are already `captured` -- never re-check it.** Use two
   sources where both have something for a game (currently covers.com and
   RotoWire's props board -- see below) so one source's gap doesn't leave
   a game pending when the other already has a clean pick. Once per cycle
   (not per game), also fetch the lineups.com team-defense table and
   compute per-game allowed for every opponent on the slate -- see "Team
   defense per-game allowed -- lineups.com" below. A pick whose Matchup
   factor has no team-level per-game number from that table is incomplete.
10. **Market signal / public betting.** For every active game, pull the
    public bet%/handle% split (see "Public betting splits -- validated
    sources" below) and call `apply_market_signal.py` -- Tier 1 every
    cycle, Tier 2/3 once a day. Read that script's RLM note above before
    writing anything up as a reverse line move in the report: a lopsided
    split with the line not yet moved is a thing to watch, not a finding.
11. **Early edges / CLV.** For every lean or prop newly recommended this
    cycle at a stake of $7.50 or more, call `early_edges.py add` with the
    exact number and stake (the first recommendation is the one that
    counts). Then, every run, call `early_edges.py refresh` (after step 8's
    grading, so props can be graded) and include the beat-the-close rate,
    average CLV points, and the by-day-recommended breakdown in the report
    message.
12. **Props Record** (if results/props_results.json exists): Run
   `report_props.py` to generate weekly hit rates and total record across
   all graded props. This should be displayed on the Receipts tab of the
   dashboard (when prop results are present) showing: (1) overall record
   and hit rate, (2) weekly breakdown, (3) performance by confidence tier,
   and (4) a list of all misses for review. The weekly breakdown is crucial
   for tracking whether accuracy is sustainable across multiple weeks --
   a single 75%+ week is encouraging, but two weeks at 75%+ means the
   system has something real. Include this in the dashboard's results
   display section so the user can monitor prop performance alongside game
   results. Also report the game-lean record by confidence tier for the
   week (e.g. 5+ vs 4 vs 3), since that calibration is what justifies the
   sizing table.

13. Run `generate_report.py`, save it under `reports/`, and present it to
   the user (message them directly if this is a scheduled/unattended
   firing; see "Reporting on a scheduled run" below). Whenever you present
   leans or props to Michael -- in the report message or in chat -- attach
   a dollar stake to every recommended bet using "Bet sizing" below.

14. Rebuild and republish the dashboard -- see "The dashboard" below. Do
    this every run regardless of whether anything moved; a no-op-looking
    run (nothing crossed a trigger) still means the dashboard's "days out"
    countdowns and bet-now timing windows are now stale if you skip it.

15. **Drive JSON snapshot** (added 2026-10-23 per Michael) -- see "Drive JSON
    snapshot" below. Runs after the dashboard build is staged, on that same
    staged `line-watch-build.html`: export a slim JSON, then replace the one
    `line-watch-snapshot.json` in the Drive folder "Line Watch" so Claude chat
    can answer from it on Michael's phone. Do it every run, and do it even if
    the Artifact publish in step 14 failed or was refused (it only needs the
    staged build file). If the Drive step fails, finish the run, leave the
    old Drive file alone, and say so in the report -- never block or redo the
    rest of the run for it.

## The dashboard

The Line Watch dashboard is a published Artifact (not a file left in the
connected folder) so the user can reopen it any time from claude.ai without
digging through report files, and so "the data will populate the
dashboard" (the whole point of automating this) actually holds run over
run. Rebuilding and republishing it is a cloud-session step, since the
Artifact tool only runs there -- the sequence:

1. On the device, run `build_dashboard_v2.py data scripts/dashboard_template_v2.html dashboard/line-watch-build.html` (paths relative to the connected folder; it reads `results/results.json`, `results/props_results.json` and `results/early_edges.json` itself). **Never `build_dashboard.py`/`dashboard_template.html` -- that's the retired old UI and silently drops props/market-signal data.** Confirm the printed `propsGraded`/`propsHits` line looks sane and has no error before moving to step 2.
2. Stage that one output file into the cloud workspace with
   `mcp__remote-devices__device_stage_files`. Run the build and the stage
   as two separate, sequential calls -- staging in the same parallel batch
   as the build can pick up the previous file.
3. **Find the existing dashboard's URL by lookup, not from memory** --
   call `Artifact({action: "list"})` and find the entry titled "Line
   Watch". A scheduled firing starts a brand-new cloud session every time,
   so there is no stored URL to reuse from a prior run; publishing without
   passing that `url` creates a duplicate artifact instead of updating the
   one the user already has open/bookmarked. (Verified working across two
   live runs, 2026-09-01 and 2026-09-02: a fresh `list` call found it
   correctly both times.) If more than one "Line Watch" shows up, read the
   most recently updated one and publish to it.
4. Publish the staged file with `Artifact`, passing that `url` so it
   updates in place. If the publish is refused because a newer version
   exists, it was built by another run from the same `data/` files --
   republish from a fresh build rather than resending the refused file.
   Describe what changed since the last build using the
   **`label`** parameter (short, roughly under 60 characters -- put any
   longer explanation in `description` instead). **There is no `note`
   parameter** -- passing one fails the whole publish with
   `InputValidationError: ... An unexpected parameter 'note' was
   provided` (hit live on 2026-09-02; fixed by switching to `label`).
   Make the label specific (new games crossing a key number, a new Wong
   qualifier, a new dashboard feature) rather than generic -- it's what
   shows up as the version history.
5. Mention in the run's report/message that the dashboard was refreshed;
   don't paste the URL unless asked.

The data-collection cadence (how often lines get fetched -- see "The
scheduler" below) is independent of this: whatever cadence the user has
set via `update_trigger`, every firing that reaches the dashboard step
rebuilds it from whatever's in `data/` at that moment. Changing the
cadence never requires touching `build_dashboard_v2.py` or the template.

## Drive JSON snapshot (added 2026-10-23 per Michael)

> **Superseded 2026-11-07.** The snapshot is now published as a file of the
> "Line Watch" artifact (https://claude.ai/artifact/PSAGLffSH8buxbrqDFPgfo,
> path `line-watch-snapshot.json`) in the same publish as the dashboard (SKILL
> step 16). The Drive upload below needed the whole file retyped through the
> connector (~50k tokens) and ran out of room on 2026-11-05. It is kept only as
> a reference and fallback, and runs no longer write Drive.

Michael wants to ask Claude chat questions from his phone while away from
his computer, with chat reading the latest Line Watch numbers from Google
Drive. So every run ends by exporting a slim JSON and **replacing** the one
copy of it in Drive. This is a read-only copy for chat -- `data/` stays the
source of truth and nothing is ever read back from Drive to build a run.

Fixed facts:
- Drive folder: "Line Watch", folder id `12iXLUAVb39miB05PcEAyaI1qqMIiY0qv`
  (owner neuegraphics@gmail.com).
- File title: `line-watch-snapshot.json`. Exactly one live copy at a time.
- The exporter reads the staged dashboard HTML's embedded `const D = {...}`
  (not `data/*.json`), so the snapshot is always exactly what the dashboard
  shows, and it needs no extra device calls beyond the step-14 staging.
- The Drive connector's `update_file` only changes metadata, never content.
  So "replace" means create the new file -> verify it -> trash the old one.
  Consequence: **the file id and link change every run**, which is why chat
  searches by title (see the next section) and why you never reuse an id.

Procedure (sequential calls, never in parallel -- a trash racing a create is
how you end up with zero copies):
1. If the Drive tools are deferred, load them in one call: ToolSearch
   `select:mcp__Google_Drive__create_file,mcp__Google_Drive__search_files,mcp__Google_Drive__trash_file`.
2. Write the exporter script below to a file in the cloud workspace (not into
   the Mac folder) and run it on the staged build file from step 14 (the
   path `device_stage_files` returned for `line-watch-build.html`):
   `python3 -I export_snapshot.py <staged line-watch-build.html> line-watch-snapshot.json`
   It prints one JSON summary line. Continue only if `ok` is true, `games` is
   above 0 and `dashboardBuilt` is this run's build; otherwise stop here, leave
   Drive untouched, and report the gap. Then run `wc -c < line-watch-snapshot.json`
   -- that byte count is EXPECTED (it is not the summary's `bytes`, which is the
   minified size before newlines).
3. Read the output file in chunks of about 30 lines (`wc -l` for the total; the
   file is one record per line on purpose). Read caps each call at roughly 25k
   tokens, so if a chunk comes back truncated, halve it. Read every line.
4. Find the existing copies: `search_files` with
   `parentId = '12iXLUAVb39miB05PcEAyaI1qqMIiY0qv' and title = 'line-watch-snapshot.json'`
   and `excludeContentSnippets: true`. A page can come back empty yet carry a
   `nextPageToken` -- keep paging until a call returns no token. Collect every
   id as OLD_IDS (none is fine on a first run).
5. `create_file` with `title: "line-watch-snapshot.json"`,
   `parentId: "12iXLUAVb39miB05PcEAyaI1qqMIiY0qv"`,
   `contentMimeType: "application/json"`, `disableConversionToGoogleType: true`
   and `textContent` = the file's complete text exactly as read: do not
   summarize, reorder, re-indent, drop rows or "fix" anything.
6. Verify: the returned `fileSize` must equal EXPECTED. If it doesn't, trash
   that new id (only that one), redo step 5 once, and if it is still wrong stop
   with the old copies untouched and report the gap.
7. Only after the new file verifies, `trash_file` each id in OLD_IDS one at a
   time. Trash nothing except ids that step 4's exact-title search returned
   from that folder -- never anything else in the folder or elsewhere in
   Drive. (Trash is recoverable from Drive's Trash.)
8. Put one line in the run's report message: "Drive snapshot refreshed
   (exportedAt <time>, <N> KB, <n> old copies trashed)", or what went wrong.

What the file contains (it carries its own `meta.howToAnswer` and
`meta.fieldGuide`, so chat needs nothing from this skill): `meta`,
`picksIndex` (every conf 5+ pick with a non-zero stake), `games` (lines, model
read, leans, public splits, injuries, Wong, line history), `props`,
`propsPendingGames`, `propsRecord`, `propsGradedRecent` (latest week),
`resultsByWeek`, `resultsRecent` (latest week), and `edges` (summary plus open
rows only). Spread fields in `games[]` are AWAY-relative (negative = away
favored) -- the dashboard's own convention, opposite of the home-relative
game-file convention in "Spread sign convention" below -- so the exporter adds
plain-words `marketNow` / `modelNow` strings; leave those as generated.

Size/cost knob: the default budget is `--max-kb 80` and a normal export is
about 70 KB. Reading it and re-emitting it into `create_file` costs very roughly
50k tokens per run. To cut that, pass `--max-kb 55`: the exporter then drops
`games[].analysis` first, then `propsGradedRecent`, then `props[].factors`, and
lists whatever it dropped in `meta.trimmed` so chat knows. Never trim by
hand.

The exporter lives at `scripts/export_snapshot.py` (moved out of this file 2026-11-04). Run it on the Mac against `dashboard/line-watch-build.html`, then stage the JSON it writes.


## Answering a quick question with no device access (rewritten 2026-10-23)

Michael can ask a quick question like "what are my best prop bets" or
"any good bets right now" from a session with no connection to his
computer at all -- his phone, most obviously. **That's fine, and it
doesn't mean falling back to "I can't check, I need the device."** Every
`data/*.json` file this skill otherwise reads needs the device bridge to be
live, but two copies of the data do not: the Drive JSON snapshot (primary,
written by step 15) and the published dashboard artifact (fallback). For a
quick question like this, skip the whole Run behavior pipeline (it needs the
device and is for refreshing data, not reading it) and:

1. Find the snapshot. Search Drive for `title = 'line-watch-snapshot.json'`
   (adding `parentId = '12iXLUAVb39miB05PcEAyaI1qqMIiY0qv'` is fine) and keep
   paging while a `nextPageToken` comes back, even after an empty page. If more
   than one file shows up, the newest `modifiedTime` wins. **Never reuse a file
   id from earlier in the conversation or from memory -- every run replaces the
   file, so its id and link change.**
2. Read it with `read_file_content` (or `download_file_content`, which returns
   base64). The reader returns the whole file as text and adds markdown
   escapes -- backslashes before `[ ] _ ~ >` -- plus trailing double spaces
   on lines; those are not data, strip them before parsing or ignore them. If
   the text doesn't end with `]}` followed by a closing `}`, it was cut
   short: retry with `download_file_content`, and if it is still partial say
   which sections you could not see.
3. The file carries its own `meta.howToAnswer` and `meta.fieldGuide` -- follow
   them. In short: lead with how fresh it is (`meta.exportedAt` and
   `meta.dashboardBuilt` in Eastern time; flag it if it is more than about 12
   hours old or any game it covers has already kicked off), call everything a
   research lean with a confidence score and never a lock, treat a bet as live
   only while the current line is still better than its `passAt`, and size
   stakes with "Bet sizing".
4. Answer from `picksIndex` for "best bets", from `games[].leans` /
   `marketNow` / `modelNow` for a spread or total question, from `props` for a
   props question (`conf` 6+ is what the Board section calls a real lean),
   and from `edges`, `propsRecord` and `resultsByWeek` for records and CLV.
   Spread fields in `games[]` are AWAY-relative; use the plain-words
   `marketNow` / `modelNow` text instead of re-deriving signs.
5. If the question needs more than the file holds (a game's full snapshot
   history, older weeks' rows, the raw game files), say that needs the
   computer rather than guessing.

Fallback if Drive isn't connected in that chat or the file isn't there: the
dashboard artifact. `Artifact({action: "list"})`, find the entry titled
"Line Watch", `Artifact({action: "read", url: <that url>})`, and pull the
embedded `const D = {...}` object out of the page (same read-model the
snapshot is cut from: `D.props`, `D.games[].leans`, `D.edges`, freshness in
`D.built`). It is a ~118 KB page, which is the reason the Drive JSON exists.

Why a Drive copy exists now: this section used to say *not* to copy anything
to Drive, because the artifact was already the one copy that didn't depend on
the device. Michael asked for the Drive JSON on 2026-10-23 so chat can read
compact structured data from his phone, and that supersedes the old advice.
The tradeoff to keep in mind is that the snapshot is only as fresh as the
last run that finished step 15, and that run needed the device online, so
always report its age. When Michael is in a session WITH the device connected
and asks for something more granular than either copy surfaces, read
`data/*.json` directly -- the snapshot and the artifact's `D` are flattened
read-models, not the full schema.

## Line fetching -- validated sources, updated after two live runs

The original plan here was "web search or direct fetch of each book's odds
page." That mostly doesn't work -- tested against real DraftKings, ESPN,
FanDuel, VegasInsider, Action Network, SportsBookReview, RotoWire, Covers,
FOX Sports, OddsShopper, and TheLines pages (2026-08-31, against the real
Week 1 slate). What follows is what actually worked, so future runs should
go straight to these sources instead of re-discovering this each time --
that's most of the token savings right there, since a targeted fetch of a
known-good URL beats a web search + fetch of whatever turns up.

**Sources that work, in the order to reach for them:**

1. **Current DraftKings, full slate, one fetch**: `rotowire.com/betting/nfl/odds/week-<N>`.
   Static, parses cleanly, and states a "last verified [date]" freshness
   marker -- check that date against today before trusting it. Doesn't
   include the underdog's moneyline number; log that side `null` rather
   than compute or guess it. **A "last verified" date that matches today
   is not by itself proof the underlying numbers actually moved** -- on
   2026-09-02 this source (and the VegasInsider one below) returned
   numbers byte-identical to the prior day's fetch across nearly the
   whole slate. A fresh freshness-marker date means the page was rendered
   today, not that every number on it was re-priced today; when the whole
   slate is unchanged while the sharp book moved, treat it as a probable
   stale/non-refreshed source (see the systemic-divergence caution in
   "Run behavior" step 4) rather than as 16 independent "no move" facts.
   Also confirm which team the page lists as favored against the
   moneyline -- a Week 5 2026 capture logged the wrong opening favorite
   for CHI@GB, and a DK -3.5 spread didn't match its own -210 moneyline.
2. **Current Caesars (and a DK/FanDuel cross-check), full slate**:
   `vegasinsider.com/nfl/odds/las-vegas/`. This is a genuine multi-book
   comparison table -- confirmed columns for DraftKings, FanDuel,
   Fanatics, BetMGM, bet365, BetRivers, and Caesars (Circa Sports is
   explicitly excluded from this table -- not a fetch failure, it's just
   not one of the books this source carries). Ask for **one book at
   a time** in the fetch prompt (e.g. "list every game's Caesars spread
   only") -- asking for multiple books and both spread+total at once makes
   the extraction drop fields or truncate early. Even asked narrowly, the
   page's content cuts off around the 15th of 16 games -- treat the last
   game as reliably missing and don't force a number for it. Totals don't
   come through reliably from this source; expect spread + juice, log
   totals `null` unless you can confirm them elsewhere. Same freshness
   caveat as DraftKings above applies here.
3. **The true opening line** (the schedule-release-day number, worth
   capturing once for `opening_line` even outside the normal daily flow):
   `covers.com/nfl/week-<N>-odds-<year>`, `foxsports.com/stories/nfl/<year>-nfl-odds-week-<N>-lines-spreads-all-16-games`,
   and `oddsshopper.com/articles/expert-picks/nfl-week-<N>-odds-<year>` all
   independently agree on the same DraftKings numbers -- any one of them
   is reliable for this, cross-checking two is free confirmation. These
   are frozen at the original release date, so only use them for
   `opening_line`, never as a "current" read. (FOX's slug varies with the
   slate size, e.g. `...-all-15-games` on a bye week.)
4. **FanDuel's editorial research page**
   (`fanduel.com/research/nfl-week-<N>-schedule-<year>-odds-for-every-game`)
   parses cleanly and its favorite/underdog attribution checks out against
   DraftKings/Caesars when verified explicitly (ask the fetch "which
   team's number is this, home or away, favorite or underdog" rather than
   trusting the raw table). But a live re-check months after first use
   caught the real issue: **this page is frozen/stale content, not a live
   line** -- it still showed the original May-opening favorite for a game
   whose favorite had since flipped. Treat this source as good for exactly
   one thing: a same-side cross-check of a game's true `opening_line`,
   never as a "current" FanDuel read. If you need FanDuel's actual current
   line, this source can't provide it -- log FanDuel `null` for the
   current snapshot rather than pass off this frozen number as today's.
5. **BookMaker.eu (the sharp/no-vig reference book)**:
   `lines.bookmaker.eu/en/sports/football/nfl/` -- its public odds board,
   fetchable directly, no aggregator needed. This replaced Pinnacle after
   Pinnacle was checked across 15+ sources (including ones built
   specifically as multi-book comparison tables) and confirmed unavailable
   everywhere -- never fetch pinnacle.com directly, it blocks automated
   access, and it isn't worth burning a search on per-game anymore given
   how consistently it came up empty. Circa Sports was also considered and
   rejected for now (VegasInsider explicitly excludes it from its table;
   no other tested source renders its odds) -- revisit only if a direct
   Circa source turns up. If BookMaker.eu itself doesn't post for a game on
   a given day, log it `null` -- that's the normal outcome some days, not a
   failed fetch, and the report already flags when the sharp reference is
   missing. Of the four validated books, this is the one that has
   actually shown real day-to-day movement on both live runs so far --
   weight it accordingly when DK/Caesars look suspiciously frozen.

**General rules that still apply:**

- Check any "as of" / "last verified" / byline date against today. More
  than about a day old isn't today's line -- log `null` for that book
  rather than pass off a stale number as current. But also see the
  freshness-marker caveat above: a today-dated page is necessary, not
  sufficient, evidence that its numbers are actually current.
- If a number can't be confirmed with reasonable confidence, log `null`.
  `update_snapshot.py` handles it cleanly, and an honest gap beats a
  fabricated number every time in a tool whose whole purpose is comparing
  real prices.
- Put a short `--note` on anything sourced indirectly or with any doubt
  attached (e.g. "Caesars via VegasInsider, total unconfirmed") -- it
  lands in the snapshot and the chart's tooltip trail, so a skeptical read
  later is possible.
- **Remember the sign convention** (next section) when transcribing any
  fetched number -- know which team a source says is favored, and which
  team is home, before you write the spread down.
- **Verify which team a news article is actually about before attaching
  it to a game.** A headline or URL slug can be misleading -- e.g. a
  roundup titled "Cowboys Release Milton, Tunsil Goes on IR" bundles
  multiple teams' news under one team's name. Caught live on 2026-09-02:
  the Tunsil item in that piece was actually about the Washington
  Commanders' left tackle, not a Cowboys player, and would have been
  misattributed to the wrong game's research note if the article body
  hadn't been checked, not just the title. Always read enough of the
  actual article text to confirm the team before using an injury/roster
  item as a signal for a specific game.

**Evaluated and rejected for the automated pipeline (2026-09-23): freeodds.oddslogic.com**
-- this is WagerTalk's own live odds board, and it's genuinely bigger than
anything above: 12 books per game (DraftKings, FanDuel, **Circa**, Kalshi,
Caesars, BetMGM, SouthPoint, HardRock, ESPNBet, Fanatics) plus an Open
column, a Consensus line, and live public-betting **Tickets%/Money%**
splits nothing else here tracks -- across NFL and several other leagues.
Circa showing up there directly contradicts this file's own note above
that Circa is unavailable everywhere tested.

The catch: it's entirely WebSocket-driven (`WSclient.js` + a custom "HDF"
protocol) -- a plain fetch only ever returns the loading shell, confirmed
live via `WebFetch`. The Claude-in-app **built-in browser** (`Claude_Browser__*`
tools) CAN render it and read the live table, which is how it was actually
evaluated. But that browser is tied to the Claude desktop app being open on
Michael's machine -- unavailable to an unattended scheduled firing, which is
how this pipeline actually runs most of the time. **Do not add this as a
"Line fetching" source scripts/SKILL.md's automated Run behavior relies
on** -- it would silently do nothing on most scheduled runs. It's fine to
reach for interactively (a session like this one, with the browser tool
live) to spot-check a number or pull public-betting Tickets%/Money% as a
supplementary signal, same as any other one-off research check -- just
don't wire it into the unattended path. Also worth knowing: it's a
"-stage" subdomain (`ol-lite-stage`), possibly a pre-release build, not
guaranteed stable long-term.

Not yet done: the game JSON schema has no field for public betting
Tickets%/Money% at all. `update_snapshot.py` would need a new optional
`--public-betting` flag before this is worth using for real (see the
schema below) -- hold off building that until it's actually verified
against a live NFL slate (no NFL games were up on the board during the
2026-09-23 evaluation, a Tuesday) rather than speculatively wiring up a
field with no real data run through it yet.

## Prop team and name verification (added 2026-10-13 per Michael)

Team confusion is a red flag. A prop on the wrong team or the wrong side produces a wrong grade and a wrong bet. Every prop must pass all of these checks before it is written with apply_props.py.

1. **Full name, never initials.** Write the player's full name as it appears on his team's roster. apply_props.py rejects initials-only names such as "J. Waddle". If a prop source shows only initials, look the player up on a roster source (CBS, Rotowire, or ESPN player page) before writing the pick. If you cannot confirm the full name, do not write the pick.
2. **Confirm the team on a roster source.** The player's team must be the team the roster lists for this season. Do not rely on memory for trades or moves. Known example: Jaylen Waddle was traded from Miami to Denver in 2026, so a Denver-vs-Seattle prop with "J. Waddle" was Jaylen Waddle on Denver.
3. **Map the team to the side.** The game file's matchup is "Away @ Home". `team` must be `home` if the player's team is the home team, and `away` if it is the away team. Read the matchup string, do not guess from the opponent.
4. **Cross-check the prop source's home/away marker.** Linemate shows "vs OPP" when the player's team is at home and "@ OPP" when away. Any source that gives a home/away marker must agree with step 3. A disagreement means stop and resolve it before writing the pick.
5. **Confirm the opponent.** The opponent shown by the prop source must be the other team in the game file. A player whose opponent is not in the game file is on the wrong game.
6. **Confirm the position.** Use the position on the roster. A wide receiver listed as a tight end changes which defense rank applies (CBS position vs. defense).
7. **Record the check.** Note the roster source and the home/away marker used in the pick's reasoning or source note. Leave no unverified name in a game file.
8. **Roster changes are not optional.** Check trades and injury reports when a player's team is in question, and re-verify on game day.

A pick that fails any step is removed, not corrected silently. Use `apply_props.py --status captured --picks '[...]'` without `--merge` to rewrite a game's pick list, and keep the openers of any picks you are keeping (the script does this automatically).

## Approved prop rules (2026-10-13 per Michael, revised same day)

The rules approved by Michael live in `model/prop_rules.json` and are enforced inside `apply_props.py` on every write. Do not override them by hand.

- **Strong markets get +1:** rush_yards and pass_yards get +1 confidence. The base value is kept as `confidence_base`.
- **Weak markets get -1 and a red X:** rec_yards and receptions get -1 confidence, and the script adds an automatic "against" factor labeled "Market history" showing that market's graded hit rate from `results/props_results.json` (for example "rec_yards picks hit 19/41 (46%)"). This replaced the earlier $0 track-only rule, so a strong rec-yards pick can still bet at a reduced size.
- **Order:** the bump or penalty runs before the tough-matchup cap and the net-alignment check, so it cannot push a pick past those limits.
- **Stake follows confidence:** 0-3 is $0, 4 is a half unit ($7.50), 5 and up is the prop stake ($15). The script writes the `stake` field; the reasoning must end with the same stake, e.g. `| Stake $7.50 · pass at 45.5`.
- **Pass-at is the worse number:** higher than the line for an over, lower than the line for an under. `apply_props.py` rejects a pass-at on the wrong side.
- **Confidence 7+ review:** any pick at 7+ is capped at 6 until the 7+ tier shows at least 55% over 15+ graded picks.
- **Opponent-rank gate:** not active. Opponent rank (CBS position vs. defense, not footballdb) is recorded in each pick's `matchup` block for tracking and still drives the tough-matchup cap.
- **Linemate placeholder prices:** rows priced -137 on both sides are placeholders, not book prices. Never use them.
- **Early edges:** log every pick at $7.50+ with `early_edges.py add`. Use `early_edges.py void --id ID --reason R` for a pick pulled before kickoff (never a taken bet); a voided pick can be re-added with `add`.
- **Source of truth:** `model/prop_rules.json`. If Michael changes a rule, update that file and this section together.

## Player props -- validated sources (added 2026-10-01)

RotoWire's props board (below) was the only source used at first, but it
turned out to only cover 2-3 games early in the week (whichever are
closest to kickoff) -- most of a Sunday slate shows nothing there until
Wed/Thu. Live side-by-side testing on 2026-10-01 found a second source
that covers far more of the slate on the same day, so **use both, in this
order, and don't treat either alone as "checked everywhere":**

1. **covers.com/sport/football/nfl/player-props** -- the primary source
   now. On the 2026-10-01 test this one page had real lines (with the
   site's own projection, diff, and EV%) across essentially the whole
   active week's slate at once, while RotoWire that same day only had 3
   games. Covers also shows its own model projection next to the line,
   which is a legitimate single `factor` for `apply_props.py` (see that
   script's section above) -- just one factor, never used alone.
2. **rotowire.com/betting/nfl/player-props.php** -- secondary/cross-check.
   Good for a second opinion on a game covers.com also has a line for, and
   worth checking first for a Tier 1 game since it tends to post there
   earliest, but don't rely on it alone for weekly coverage.
3. **FanDuel Sportsbook player/team pages** (`sportsbook.fanduel.com`) --
   on Tuesday 2026-10-06 these already had full TNF and London-game prop
   boards with each player's 2026 per-game averages, earlier than the
   article sources. Good for early-week openers.

**Both covers.com and RotoWire pages are rendered client-side (JS/React)
and MUST be rendered in a real browser, never fetched with `WebFetch`.**
Confirmed twice live on 2026-10-01: `WebFetch` on either page returns
either an empty "Loading..." shell, or -- worse -- a plausible-looking
prose summary with **corrupted team attribution** (e.g. a Texans WR
labeled "(DAL)", a Lions RB's name attached to Dallas, the same QB
labeled both "(LV)" and "(KC)" in two different lines of the same
summary). The raw numbers in that corrupted summary can be real; the team
tags next to them cannot be trusted. Use
`mcp__remote-devices__chrome-devtools__*` (or the built-in browser, if
that's what's available) to open the page, then `evaluate_script` to pull
the actual DOM content as structured data -- for covers.com this is one
`<table>` (`document.querySelectorAll('table')[0]`, one row per prop,
columns `GAME & PLAYER PROPS | PROJECTIONS | DIFF | +EV | RATING |
ANALYSIS | BEST ODDS`, with the matchup written "AWAY @ HOME" matching
this project's own convention); for RotoWire's board the rows are plain
divs, not a `<table>`, and only render after the page's JS has actually
populated them (`wait_for`/re-check if a first pass still shows
"Loading"), then extracted similarly via `evaluate_script` rather than a
full `take_snapshot` (the full a11y tree is enormous and mostly nav
chrome -- a targeted DOM query is both cheaper and far more reliable than
scanning a giant snapshot dump).

Even with a real DOM render, **a player's team code still isn't gospel --
cross-check an unfamiliar name against known rosters (or this project's
own prior captures) before trusting it**, the same discipline "Line
fetching" above already asks for on injury articles. covers.com's own
matchup field (who's away, who's home) checked out reliably in the
2026-10-01 test; it's the *individual player* team tag worth a second
look on anyone you don't already know.

No props source here carries moneyline-style vig/book odds worth logging
beyond what's already in `book`/the line itself -- don't build out odds
comparison for props the way "Line fetching" does for game lines unless
asked; the point of this section is coverage (finding a clean number),
not price shopping.

### Team defense per-game allowed -- lineups.com (added 2026-10-25 per Michael)

**lineups.com/nfl/team-stats/defense** -- one page, all 32 teams, team-level
defense. This is the source for the "rate stat" the Matchup factor asks for
(pts/g, yds/g, per-carry, per-attempt, TDs allowed per game). It did not
appear anywhere in this skill before 2026-10-25; the only defense sources
were the CBS/footballdb position-vs-defense ranks, which rank teams but
don't give the per-game rate behind the rank.

**What the table is (verified 2026-10-25, "NFL Week 7" page):**
- Columns: `Team, PTS, PLAYS, YDS, YDS/PLAY, RUSH ATT, RUSH YDS, RUSH TD,
  PASS ATT, COMP, PASS YDS, PASS TD, INT, RZ %, TOV, SACKS`.
- These are **allowed** numbers (opponents' offense), and the default view
  is **season TOTALS, not per game**, with **no games-played column**.
  Proof: ATL's PTS read 153, which is exactly the sum of its six opponents'
  scores (20+34+14+24+30+31); its PASS YDS 1,512 / 6 = 252/g matches the
  252 pass yds/g quoted in a separate preview. A page with a "Per Game"
  filter can also be rendered with that toggle on -- so sanity-check one
  number (any team's PTS above ~60 is a total) before dividing.
- Team-level only: **no RB/WR/TE/QB splits.** It supplements the CBS
  position-vs-defense rank and never replaces it; the tough-matchup cap
  still keys off that position rank, not this table.
- `RZ %` and `TOV`/`INT` labels are ambiguous (opponent-offense vs
  defense-forced) -- confirm the direction before using them as a factor,
  or leave them out.
- Static enough for `WebFetch` (returned the full table on 2026-10-25), but
  `WebFetch` can't tell you which toggle state it saw, hence the sanity
  check above. If a number looks off, render it in a browser instead.

**Procedure (once per cycle, covers every player on the slate):**
1. `WebFetch` the page; ask for every team's row, one metric group at a time
   if the extraction truncates (same advice as the VegasInsider fetch).
2. Get each opponent's games played (GP) from the schedule/scores -- count
   its completed games. Never assume GP = week - 1; a bye makes teams differ.
3. Per game = total / GP. Also compute the rate stats, which are less noisy
   than raw yards because opponent pace and garbage time inflate totals:
   `rush yds / rush att`, `pass yds / pass att`, `comp / pass att`.
4. Rank all 32 teams on the metric that matches the market (1 = fewest
   allowed). Use the rank and the rate in the Matchup factor `detail`, and
   label it with its sample, e.g. `"ATL allows 252 pass yds/g (28th), 6.1
   yds/att, 1.7 pass TD/g thru 6 GP (lineups.com)"`.
5. Early season (<4 GP) also name who they faced, same as the existing
   Matchup rule.

**Which columns go with which market:**

| Market | Pull from lineups.com |
|---|---|
| rush_yards | RUSH YDS/g, RUSH ATT/g, yds per carry, RUSH TD/g |
| pass_yards / completions / attempts | PASS YDS/g, PASS ATT/g, COMP/g, yds per att, PASS TD/g, SACKS/g |
| rec_yards / receptions | PASS YDS/g, PASS ATT/g (team pass volume allowed) **plus** the CBS WR/TE position rank |
| Anytime TD (not a supported `apply_props.py` market -- research only) | RUSH TD/g + PASS TD/g allowed, PTS/g allowed |
| Game script | PTS/g allowed, set against this game's total |

**Direction rules:** a per-game number is a `for`/`against`/`neutral` call
like any other factor -- an over into a bottom-third allowed rate is `for`,
into a top-third is `against`. It counts as **one** Matchup-family factor,
not a second independent one next to the CBS position rank (they largely
measure the same weakness; don't double-count them toward the alignment
gate). Worked example (ATL, 6 GP): 153 PTS = 25.5/g; 499 rush yds on 139
carries = 83/g, 3.6 ypc; 1,512 pass yds on 246 att = 252/g, 6.1 yds/att;
10 pass TD = 1.7/g.

### Linemate -- search by player, not the Tools > Props leaderboard (added 2026-10-21 per Michael)

`linemate.io/nfl/tools/props` (what "Line fetching"/the run prompt call
"Linemate's props page") is a **hit-streak leaderboard**, not a props
board: it lists whichever player/market rows currently have the longest
active hit streak, sorted by streak length, across the whole league. Most
rostered players for most games -- including a perfectly good matchup
target, confirmed live on 2026-10-21 with Kyren Williams vs. a soft
Raiders run D -- never appear there at all, simply because they aren't
currently on a hot streak. Scanning that page and concluding "Linemate
doesn't have this player" is the wrong read and was this project's
standing mistake until today -- it silently narrowed every run's prop
coverage to whoever happened to be streaking.

**Use Linemate's own player search instead, for every player you already
have in mind from the CBS posvsdef matchup scan or elsewhere:**

1. Open `linemate.io/nfl/tools/props` (or any Linemate page) in the
   built-in browser, dismiss the cookie banner if it's covering the page
   (Reject all), then use the search box in the top nav (placeholder
   "Search for a team or player...") -- `find`-ing the textbox by its
   placeholder and typing into it directly works; a scripted `form_input`
   value-set on this box did not visibly register with the page's own
   React state in testing, so type via the `computer` tool's `type`
   action after clicking the box, not `form_input`.
2. Pick the player from the dropdown. This opens their full **Profile**
   page (`linemate.io/nfl/trends?...&profile=true`), which is a
   completely different, much richer view than the leaderboard: every
   market Linemate books for that player this week (not just one), each
   with its line, price, and hit-rate splits (last-N games, vs this
   opponent, home/away), **plus that player's own last-5 game log** (date,
   opponent, result, TDs, rec yards, rush yards, red-zone targets/attempts)
   **and a same-page injury table for his team** -- both of which can
   double as the Volume/Trend factor and part of the roster/injury check
   the ACCURACY RULE requires, read fresh this run, no extra page needed.
3. The placeholder-price caution still applies exactly as written above
   (a row priced -137 on *both* sides is a placeholder, never a real book
   price) -- it just applies per-row on the Profile page too, not only on
   the leaderboard.
4. When a player has several rows for the same market at different lines
   (e.g. Kyren Williams showed both "Over 59.5 Rush Yards -114" and "Over
   71.5 Rush Yards -137" the same week), prefer the row whose price is
   closest to standard vig (-105 to -120) as the primary market number --
   a far-from-standard price (-137 and shorter) on an alternate line is
   usually juiced for a bigger per-game sample, not the main line the
   sportsbook is actually posting.

This doesn't replace covers.com or RotoWire above -- it's a third source,
and the one most likely to actually have a specific player you went
looking for on purpose (a CBS posvsdef matchup target, a name Michael
asked about directly) rather than whoever else happened to post a line
first.

## Player prop season game logs -- validated source (added 2026-10-01)

For `apply_prop_backtest.py`'s `--games` input -- a player's own real,
game-by-game stat line this season, not odds. **statmuse.com**, queried
as a plain-English URL slug (`statmuse.com/nfl/ask/<player-slug>-<stat>-
by-game-2026`, e.g. `jerry-jeudy-receiving-yards-by-game-2026`) worked
reliably via a plain `WebFetch` -- no browser render needed, unlike the
props-odds sources above, since this is server-rendered stat-table
content rather than a live odds board. Verified against 29 different
players in one sitting on 2026-10-01: consistently returns a clean
week-by-week table (week, date, opponent, the stat, often supporting
columns like targets/attempts).

Two real failure modes to watch for, both honest gaps, not reasons to
guess a number:
- **Wrong season.** One query (`brock-bowers-receiving-yards-by-game-2026`)
  came back with 2025 season data despite the URL saying 2026. A second,
  differently-worded query against the same player surfaced the correct
  single 2026 game. If a response's own dates don't match the current
  season, re-word the query once rather than trusting a plausible-looking
  but wrong-year table.
- **Incomplete weeks.** Several players (Jaylen Warren, Rashee Rice, Kyle
  Pitts, Marcus Mariota, Drake London, and others on 2026-10-01) only
  returned 1-2 games even though 3 weeks had been played -- sometimes a
  real missed-time/depth-chart reason (Mariota only became the starter in
  Week 2; Kyler Murray and Nico Collins each show exactly one game because
  both actually missed time), sometimes just a gap in the source's own
  data for that query. Either way, pass only the weeks actually returned
  and say so in `apply_prop_backtest.py`'s `--source` (e.g. `"statmuse.com
  (wk3 not available)"`) -- never fabricate a missing week's number.

## Public betting splits -- validated sources

**cleatz.com/public-betting/nfl/** -- the source for `apply_market_signal.py`'s
bet%/handle% splits (see that script's section above, and its RLM note on
when a lopsided split is and isn't a reverse line move). Static enough to
`WebFetch` directly, refreshes roughly every 5 minutes per the page
itself, and attributes each row to a named team (not just "favorite" or
"dog") -- still convert to this project's home-relative convention before
calling the script, same as every other source here. Not every game has a
posted split for every market (spread/total/moneyline) on a given
fetch -- that's normal, pass only the markets you actually got a number
for and omit the rest. Early-week splits (Mon/Tue) are thin and swing to
extremes like 1% or 100% -- treat them as noise until midweek.

## Spread sign convention -- read this before feeding any spread number in

**A spread number is meaningless without knowing which team it belongs to,
and the favorite can flip sides between snapshots.** Store every
`spread` value relative to the **home team** (the second team in
`matchup`, which is always written "Away @ Home"): negative means the home
team is favored, positive means the home team is the underdog. When a
source hands you "Team X -3.5" or "Team X is favored by 3.5," don't just
copy the number -- work out whether Team X is the home or away team first,
and flip the sign if it's the away team.

This isn't a hypothetical: a live test run (2026-08-31, Week 1 slate)
caught it for real. Miami was a -3.0 favorite at the open; by the current
snapshot, Las Vegas (the home team) had become a -3.5 favorite -- an actual
6.5-point swing from a full favorite flip. Storing the raw "favorite's own
line" number both times (-3.0, then -3.5) would have shown up as a boring
half-point move and hidden the real story. Normalized to home-team sign,
it correctly reads +3.0 -> -3.5, and `update_snapshot.py`'s trigger logic
catches it as a real key-number-crossing move because it actually is one
-- though only the number 3 was actually crossed (3.0 to 3.5), not 7; a
full favorite flip means the sign changed, not that every key number in
between was necessarily passed. `build_dashboard_v2.py`'s key-number note
derives its wording from the actual crossed magnitudes for exactly this
reason, not from the flip alone. The moneyline charts on the per-game page
(`generate_chart.py`) have the analogous limitation -- see that section
above.

## JSON schema (one file per game)

```json
{
  "game_id": "bills-jets-2026-wk3",
  "matchup": "Buffalo Bills @ New York Jets",
  "week": "Week 3",
  "kickoff": "2026-09-21T17:00:00Z",
  "opening_line": {
    "captured_at": "2026-09-16",
    "bookmaker_eu": { "spread": -3.0, "total": 44.5, "ml_fav": -160, "ml_dog": 138 },
    "draftkings": { "spread": -3.0, "total": 44.5, "ml_fav": -158, "ml_dog": 135 },
    "fanduel": { "spread": -2.5, "total": 44.0, "ml_fav": -152, "ml_dog": 130 },
    "caesars": { "spread": -3.0, "total": 44.5, "ml_fav": -160, "ml_dog": 138 }
  },
  "snapshots": [
    {
      "captured_at": "2026-09-19T18:00:00Z",
      "bookmaker_eu": { "spread": -6.5, "total": 42.0, "ml_fav": -320, "ml_dog": 260 },
      "draftkings": { "spread": -7.0, "total": 42.0, "ml_fav": -340, "ml_dog": 275 },
      "fanduel": { "spread": -6.5, "total": 42.0, "ml_fav": -320, "ml_dog": 260 },
      "caesars": null,
      "note": "Caesars hasn't re-posted since the QB news broke"
    }
  ],
  "research": {
    "last_run": "2026-09-19",
    "spread_lean": "Jets +6.5 or better",
    "total_lean": "Under 42.0",
    "moneyline_lean": "No edge",
    "confidence": "Medium",
    "top_signals": [
      "Bills starting QB confirmed OUT (Fri), backup making first start",
      "Jets defense top-5 vs the pass last 3 weeks"
    ]
  },
  "injury_adjustments": {
    "last_updated": "2026-09-19T18:00:00Z",
    "home": { "items": [], "items_total_pts": 0.0, "stacking_notes": [], "total_pts": 0.0 },
    "away": {
      "items": [
        { "position": "QB", "player": "Backup QB", "status": "out", "pts": -5.0, "note": "Starter confirmed OUT Fri, clearly worse backup" }
      ],
      "items_total_pts": -5.0, "stacking_notes": [], "total_pts": -5.0
    }
  },
  "game_evaluation": {
    "last_updated": "2026-09-19T19:00:00Z",
    "analysis": [
      "First paragraph -- what the sim and market are saying and why (injuries, home/away, Elo/recent-scoring form).",
      "Second paragraph -- what that means for reading the game."
    ],
    "leans": [
      { "market": "spread", "pick": "NYJ +6.5", "confidence": 7, "reasoning": "Starter QB out flips the model's own read past the market number. | Stake $15 · pass at +5.5", "best_bet": true }
    ]
  },
  "triggers": {
    "book_vs_sharp_gap": true,
    "key_number_crossed": true,
    "steam_no_news": false
  },
  "props": {
    "last_checked": "2026-09-19T15:05:42Z",
    "status": "captured",
    "source_note": "covers.com player-props board, rendered via browser",
    "picks": [
      {
        "player": "Josh Jacobs", "team": "home", "position": "RB", "market": "rush_yards",
        "book": "DraftKings", "lean": "over", "confidence": 6,
        "reasoning": "One sentence on why. | Stake $15 · pass at 78.5",
        "opening_line": 74.5, "opening_captured_at": "2026-09-19T15:05:42Z",
        "current_line": 74.5, "current_captured_at": "2026-09-19T15:05:42Z",
        "matchup": { "opp_rank_vs_pos": 27, "rank_basis": "RB fantasy pts allowed, Wks 1-2" },
        "factors": [
          { "label": "Matchup", "dir": "for", "detail": "Opp 27th vs RB, 118 RB rush yds/g allowed" },
          { "label": "Volume", "dir": "for", "detail": "20+ carries both games" },
          { "label": "Script", "dir": "for", "detail": "-6.5 home fav" }
        ],
        "alignment": { "for": 3, "against": 0, "neutral": 0, "net": 3 }
      }
    ]
  },
  "market_signal": {
    "captured_at": "2026-09-19T15:10:00Z",
    "source": "cleatz.com",
    "spread": { "bets_pct_home": 48, "handle_pct_home": 38 },
    "total": { "bets_pct_over": 55, "handle_pct_over": 54 },
    "moneyline": { "bets_pct_home": 28, "handle_pct_home": 51 },
    "reverse_line_move": false,
    "rlm_note": "62% of handle is on the away side but the spread hasn't moved yet -- too early to call RLM, not evidence of one."
  },
  "status": "active"
}
```

Notes on what changed from the original draft, and why:
- `snapshots[].captured_at` is a full timestamp, not just a date. Tier 1's
  whole reason to exist is catching movement inside the last 48 hours --
  a date-only snapshot would collapse 4 same-day checks into one point and
  throw away the exact thing that cadence is for. `update_snapshot.py`
  handles this: Tier 1 calls always append a new point, Tier 2/3 calls
  (with `--skip-if-same-day`) collapse to one point per day automatically.
- `confidence` is one of `null` (research not yet run -- shows as
  "watching" in the report), `"Low"` (research run, no edge found --
  shows as "no edge"), `"Medium"`, or `"High"` (shows in the filtered
  leans). Setting it to `"Low"` explicitly, rather than leaving it `null`,
  is what tells the report a game was actually looked at and came up
  empty, versus not looked at yet.
- The book key is `bookmaker_eu`, not `pinnacle` -- see "Line fetching"
  above for why. `SHARP_BOOK` in `scripts/lib.py` is the single place that
  constant lives; every script reads it from there rather than hardcoding
  the book name, so a future sharp-book swap (if BookMaker.eu ever becomes
  unreliable too) only touches one line.
- `injury_adjustments`, `game_evaluation`, `props`, and `market_signal`
  are all optional and absent until their respective script has been
  called for that game at least once -- neither `simulate_game.py` nor
  the dashboard build errors on a missing block, they just treat it as
  "nothing to add yet."

## Research signal categories (run per game, Tier 1 every time)

Weighted roughly in this order:

1. **Injury report** -- official Wed/Thu/Fri designations, confirmed via
   team site or beat reporter, not aggregator speculation. Weight starters
   and O-line/secondary clusters heaviest. Confirm which team the report
   is actually about by reading the article body, not just the headline
   or URL slug -- see the general rule under "Line fetching" above. When a
   starter's absence looks real (out, or clearly trending that way) and
   would plausibly move the number by a point or more, don't stop at
   writing it into `top_signals` as prose -- call
   `apply_injury_adjustment.py` with a documented pts value and re-run
   `simulate_game.py`, so the model's own spread/total lean reflects it
   too, not just the human-written research note. See
   "scripts/apply_injury_adjustment.py" above for point-value guidance and
   the depth-stacking rule (2+ starters out at the same position).
   **Read the injury-adjusted numbers, not the raw ones**: the sim stores
   both `projTotal` (injury-blind) and `projTotalAdjusted`; a lean must be
   built on the adjusted figure. Caught live 2026-10-05: a TNF Over 47.5
   was briefly recommended off the injury-blind 50.5 when the rookie-QB-
   adjusted projection was 45.0. Known model limitation: injury pts lower
   the injured team's OWN scoring, which is right for a QB/offense injury
   but wrong-direction for a defensive cluster (it should raise the
   opponent's scoring) -- for defensive clusters, judge the total from the
   injury-blind total plus the research, and say so.
   **Prior-season weighting caveat**: early in the season most of each
   team's rolling scoring window is still regressed 2025 placeholder
   games, and Elo still carries 2025 strength. Before calling a model-vs-
   market gap an edge, compare it with a real-2026-games-only projection;
   if the gap disappears on 2026 data alone, it's an artifact, not an
   edge (caught live Week 4-5 2026 on MIA, NE and several totals).
2. **Situational trend** -- home/away, favorite/dog role, division game,
   short week, coming off a bye, matched to this specific team's
   historical ATS performance in that situation, not a league-wide stat.
   Every week, check each game against `scripts/situational_systems.py`
   -- in particular a team off a loss returning home against a team off
   a win (`off_loss_vs_off_win`, 57.5%), road dogs off a road loss, and a
   backup QB's first start (markets tend to overreact to the injury
   itself) -- but treat these as secondary support only, never the
   primary reason for a lean.
3. **Rest & travel** -- short week (Thu game), cross-country travel,
   back-to-back road games, a team returning from London without a bye,
   and the rest edge for a team coming off a Thursday game.
4. **Weather** (outdoor stadiums only) -- wind/precipitation forecast
   close to kickoff; heavy wind is the strongest total-suppressing signal.
5. **Line movement vs. the sharp book (BookMaker.eu)** -- from the
   `triggers` block. A US book moving off the sharp book without matching
   news is a timing/public-money signal, not a talent signal -- use it to
   decide when to act, not to form the lean itself. Remember
   `steam_no_news` from the script is a raw movement heuristic, not a
   verified "no news" judgment -- confirm there really isn't matching news
   before calling it steam in the report. Also rule out a **systemic**
   explanation before treating it as a per-game signal at all: if the
   divergence shows up across most of the tracked slate at once rather
   than on this one game, it's more likely a source-freshness issue or a
   book-wide reprice than real per-game news -- see the systemic-divergence
   caution in "Run behavior" step 4 and "Line fetching" item 1.
6. **Recent form context** -- last 3 games, adjusted for opponent quality,
   not just win/loss.
7. **Key number awareness** -- 3 and 7 are the NFL's key numbers. A line
   crossing either (flagged in `triggers.key_number_crossed`) is a bigger
   deal than the raw point value suggests.

Rules:
- Never call confidence above Medium unless at least 2 independent signal
  categories agree (line movement itself doesn't count toward this --
  it's a timing signal, not a talent signal).
- Moneyline lean only fires on a genuine price discrepancy across books on
  the same game state -- not just "the favorite is good."
- If BookMaker.eu is unavailable for a game on a given day, still report
  US book movement, but flag that the sharp/no-vig reference is missing
  that day.
- When a slate-wide divergence turns out to be systemic (see above), don't
  write it up as several independently-argued per-game leans -- attach one
  shared, honestly-hedged caveat (Low confidence, explicitly named as a
  probable source-freshness issue) to every affected game instead.

## Bet sizing

Retired 2026-11-04. Sizing lives only in the `line-watch-slate-bets` skill ($600 bankroll, 1-5%: $6/$12/$18/$24/$30). Any $15 / $7.50 / $22.50 figure elsewhere in this file is historical.

## Output format -- single consolidated report

```
NFL LINE REPORT — [date] — [time]

This is research, not a guaranteed outcome.

TIER 1 — IMMINENT (<=48 hrs to kickoff)
FILTERED LEANS (Medium+ confidence only)

[Team A @ Team B] (Week N) — kickoff in [X]h ([kickoff timestamp])
  SPREAD: [lean] | BookMaker.eu [X]
  TOTAL: [lean] | BookMaker.eu [X]
  MONEYLINE: [lean or "no edge"]
  CONFIDENCE: [Medium/High]
  STAKE: [$ per "Bet sizing" above, plus the "pass at" number]
  WHY: [signal 1] / [signal 2]
  LINE MOVEMENT: [game_id]-graph.html

[repeat per qualifying game]

WATCHING, NOT YET ACTIONABLE
  [Team A @ Team B] — kickoff in [X]h — research not yet run this cycle

NO EDGE
  [count] game(s) researched with no Medium+ lean — full slate available in data/ if you want to review manually.

TIER 2/3 — TRACKING (not yet imminent)
  [Team A @ Team B] (Week N) — [days] days out — [opening line logged / early confidence if Medium+]

EARLY EDGES / CLV
  [n logged] — beat the line on [x] ([y]%), avg [+z] pts — by day recommended: Mon [..] / Tue [..]
  Results at our number: [W-L-P] — paper P/L [$] (taken: [n], real P/L [$])
```

`generate_report.py` produces the sections above the EARLY EDGES block.
Present its output to the user; don't hand-reformat it, and add the EARLY
EDGES lines from `early_edges.py report` (and any missing STAKE lines) in
the message that presents it. The dashboard (see "The dashboard" above) is
a separate, complementary deliverable -- it doesn't replace this report,
it gives the user something to reopen and browse between reports, and its
Bet Now tab specifically answers "is there anything to bet right now"
without reading the report at all. The per-game chart page (see
`generate_chart.py` above), linked from each Tier 1 entry, is the third
leg -- full multi-book, multi-market history for one game.

## The scheduler

Tier 1's fast cadence needs to run without anyone triggering it by hand.
**Use the `create_trigger`/`update_trigger` scheduled-task tools for this
(`mcp__claude-code-remote__*`), never a local cron/launchd tool** --
anything scheduled with a local scheduler lives only inside this session
and is gone the moment the session ends, silently breaking the automation.

Because a static cron expression can't itself know which games are
currently Tier 1, the trigger fires on a fixed cadence that safely covers
the fastest tier, and it's the skill's own "Run behavior" logic above --
re-tiering from scratch every firing -- that makes the actual work adapt:
a firing during a quiet stretch mostly no-ops for the fetching (Tier 2/3
games skip via `--skip-if-same-day` once already logged today, and a week
with nothing within 10 days does effectively nothing there), while a
firing during a live Tier 1 window does the real work. This is what makes
"recomputed every run, not a fixed day-of-week schedule" actually true --
the schedule is constant, the workload isn't.

**The cadence is entirely the user's to set and change** -- nothing in
this skill hardcodes it. Adjust it any time with `update_trigger` (cron
expression and/or enabled state); no script or schema change is ever
needed to change how often data gets collected, and the dashboard rebuild
in step 14 picks up whatever cadence is currently running automatically,
since it just reads whatever's in `data/` at firing time.

**The recurring trigger as of 2026-10-23** is `trig_01MidQxEBKkFdm4UheJ3nMiz`,
named "NFL Line Report — 8:30am & 4:30pm ET" (it replaced the earlier
every-2-hours trigger `trig_01ARzYDz3RFgM7vh79Jesz2H` from 2026-09-23; confirm
the live id with `list_triggers` rather than trusting this text):
```
cron_expression: "CRON_TZ=America/New_York 30 8,16 * * *"
```
It runs with automatic approval and has the Google_Drive connector attached,
which step 15 (Drive JSON snapshot) needs. The cadence was tightened over
time to shrink the window a cached/stale browser tab can show a number that's
already moved (caught live 2026-09-23: a "wildly inaccurate" SEA -1.5 turned
out to be a stale tab -- the live dashboard already had the correct DK
-6.5/Caesars -7 from that morning's run). The trigger's own
`prompt` recaps the Run behavior steps for readability, but it says to
follow the skill's "Run behavior" section exactly -- **if a future skill
update adds or reorders steps, update the trigger's prompt to match via
`update_trigger`** rather than letting the two drift out of sync (they
drifted once already: the prompt didn't mention the injury-check or
analyst-evaluation steps until this same 2026-09-23 update; the
2026-10-06 update added step 11, early edges / CLV, and stakes on every
bet; the 2026-10-23 update added step 15, the Drive JSON snapshot, which the
trigger's prompt must also name or the scheduled firing will skip it).
Confirm the trigger is still enabled with `list_triggers` before
treating a gap in reports as a bug rather than a paused/disabled schedule.

## Reporting on a scheduled run

A scheduled firing has no one watching it happen. Message the user
directly with the report (or the one-line "nothing new" summary) rather
than just leaving it in the reports/ folder -- the report is the actual
deliverable of this skill, not a side effect. Mention the dashboard
refresh in the same message (see "The dashboard" above) rather than as a
separate message, include the stake for every bet it names, and include
the early-edge / CLV summary line. Add the one-line Drive snapshot status
from step 15 too (refreshed, or why it was skipped); if the report message
has already gone out by the time step 15 finishes, send that line as a short
follow-up instead of leaving it out.