---
name: "line-watch-slate-bets"
description: "Use for Line Watch bet cards by betting window (Thursday night, Sunday 1pm, Sunday 4pm, Sunday night, Monday night): sizes bets on the 1-5% bankroll scale ($600), guarantees game-bet action per window, and limits props to team-weakness picks. Overrides stake amounts in nfl-daily-line-report."
---

# Line Watch slate bets

This skill sets how Line Watch bets are sized, how many game bets each
betting window gets, and which player props qualify. It overrides the stake
amounts and "Bet sizing" section in `nfl-daily-line-report` wherever they
differ. Research, line fetching, and the script pipeline stay in that skill.

## Bankroll and sizing

Bankroll: $600. Each bet is a percentage of it.

| Tier | % of bankroll | Stake | Confidence (0-10) |
|---|---|---|---|
| 0 | 0% | $0 | 0-3, context only (except a labeled minimum-action pick, below) |
| 1 | 1% | $6 | 4, or a minimum-action pick |
| 2 | 2% | $12 | 5 |
| 3 | 3% | $18 | 6 |
| 4 | 4% | $24 | 7 |
| 5 | 5% | $30 | 8-10; 8 needs market confirmation (a reverse line move or steam) plus 2+ independent signal categories; 9-10 are rare and need the same |

- Props are capped at Tier 3 ($18) until the 7-8 tier in `report_props.py`
  has 30+ graded picks hitting 57% or better.
- `scripts/apply_props.py` and `model/prop_rules.json` already compute prop
  stakes on this scale (4 = $6, 5 = $12, 6+ = $18). Never override the
  script's stake by hand.
- Game leans: end every lean's `reasoning` in `apply_game_evaluation.py`
  with ` | Stake $X · pass at Y`, using this table.

## Betting windows

Windows: Thursday night, Sunday 1pm ET, Sunday 4pm ET, Sunday night,
Monday night. Assign each game to its window by kickoff time in Eastern time.

### Game bets: guaranteed action

- Every window with a game still to kick off gets **at least 1** game bet
  (spread, total or moneyline), and **ideally 3** when 3 leans reach
  confidence 5+. Rank by confidence.
- If no lean in a window reaches confidence 5, the window's single best lean
  still goes on the card at Tier 1 ($6), labeled **minimum-action**. Michael
  wants action on every main slate, and the label lets CLV tracking grade
  these picks apart from real edges.
- Only one minimum-action pick per window. When more than 3 leans reach 5+,
  stake the top 3 and list the rest as $0 swap-ins (used only if a top-3
  price moves past its pass-at number).
- A window that has already kicked off (e.g. Thursday by Sunday morning) is
  listed as done, with no card.

### Player props: team-weakness picks only

Props have **no minimum count**, and **at most 5 per window** (best by
confidence). A prop qualifies only if it exploits a real team weakness:

- **OVER:** the opponent ranks **23-32** (the 10 softest) against that
  position in the market's own stat (yards allowed per game to the position:
  CBS position-vs-defense for QB/RB/TE, footballdb 2026 for WR), **and** that
  per-game number is worse than the league average.
- **UNDER:** the opponent ranks **1-10** (the 10 toughest) in that stat, and
  its per-game number is better than the league average.
- Record the rank in the pick's `matchup` block with a `rank_basis` naming
  the stat and source.
- The pick must also pass every existing check in `nfl-daily-line-report`:
  3+ stat-backed factors, the alignment gate, the tough-matchup cap, team and
  name verification, and the `model/prop_rules.json` market rules.
- Final confidence must be 5 or higher to go on the card. A $6 prop is not a
  best bet.
- In the props table, show the weakness itself: opponent rank against the
  position and the per-game allowed number.

Never loosen this filter to reach a count. A window with zero qualifying
props says so plainly. Track record to keep in view: props are 81/139
(58.3%) through Week 8, and the 5-6 tier (56.8%) has not beaten the 1-4 tier
(66.0%), so confidence calibration is still unproven.

## Data sources that work (learned 2026-11-01)

- Live game lines: render `covers.com/sport/football/nfl/odds` in a real
  browser and read the DraftKings, FanDuel and Caesars columns. RotoWire's
  odds page, VegasInsider and the BookMaker.eu board were all days stale on
  2026-11-01.
- Props: render `covers.com/sport/football/nfl/player-props` in a real
  browser. A WebFetch of it garbles team tags.
- Starters and injury tags for every game: render
  `rotowire.com/football/lineups.php` in a browser. News searches often
  return articles from past seasons, so check every article's date.
- Defense vs position: WebFetch of footballdb returns old seasons. Render
  it in a browser with `&yr=2026`, or use CBS position-vs-defense pages.

## Line freshness

- A bet is live only against a line checked within the last 6 hours. Check
  each source's own "last verified" or "updated" date against today. A fresh
  date alone doesn't prove the numbers moved (see the stale-source caution in
  `nfl-daily-line-report`).
- If every fetchable source is stale, render a live odds page in the browser.
  Older numbers are reference only and are labeled "re-check before betting".
- When two sources disagree on a spread, log the fresher one and note the
  gap. If neither is fresh, the game's bets stay reference-only.
- Monday night bets made on Sunday must be re-checked on Monday.
- Past the pass-at number, the stake is $0.

## Exposure

- **Correlation cap:** total stake on one game stays at or under 15% of
  bankroll ($90). Later correlated legs drop a tier or are cut.
- **Daily total:** Michael's rule is to stay under $300 on any one day,
  except Sunday, which has no cap. Flag any non-Sunday day over $300 and name
  the bet to drop first (thinnest edge, then most correlated). On Sunday,
  report the total without a cap.

## Closing line value tracking

- Log every recommendation with a stake of $6 or more through
  `early_edges.py add`. Pass `--min-action` on minimum-action picks so they
  are graded apart from real edges (`pickType`).
- Before each window, record every logged prop's posted line with
  `early_edges.py prop-line` (see `nfl-daily-line-report` step 12), so props
  get a real closing line instead of freezing at capture.
- `early_edges.py refresh` reports CLV by window, by pick type, and against
  the sharp close (BookMaker.eu). After ~30 minimum-action picks, compare
  their CLV and record with the `edge` picks: if they trail clearly, raise it
  with Michael rather than changing the rule yourself.

## Output

For each window still to kick off:

- **Game bets:** bet | market | conf | tier | stake | pass at | why (one line)
- **Props:** player | market | line | lean | opponent rank vs position |
  per-game allowed | conf | stake | pass at
- Window total in dollars

Then the day total (checked against the $300 rule), the CLV lines (overall,
this window's row from `byWindow`, edge vs min-action from `byPickType`, vs
sharp close), and the dashboard, Drive snapshot and GitHub sync status.

On game day, the "Line Watch game-day check" push (sent after each window's
inactives) is the go signal: bet a window only after it says ALL CLEAR, or
after re-checking anything it flags. Every pick is a research lean
with a confidence score. Never call one a lock.