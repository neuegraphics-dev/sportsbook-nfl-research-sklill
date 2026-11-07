---
name: "nfl-daily-line-report"
description: "Player props and game-line tracking with phantom-entry prevention and weekly performance tiers"
---

# NFL Daily Line + Research Report (Line Watch)

Personal research tooling only. Every output is a lean with a confidence
level, never a guaranteed outcome. Say so in every report.

Slimmed 2026-11-04. The long reference (every script's full docs, the
validated sources, the JSON schema, the dated lessons) now lives in the data
folder at `docs/reference.md`. Each step below names the reference section to
read when you need it. Read only that section (`grep -n "^## \|^### "
docs/reference.md` to find it), never the whole file in a run.

**Sizing lives only in the `line-watch-slate-bets` skill** ($600 bankroll,
1-5%: $6/$12/$18/$24/$30, betting windows, minimum-action picks, the prop
team-weakness filter, exposure caps). Load it every run. Any $15 / $7.50 /
$22.50 figure in `docs/reference.md` is historical.

## Where everything lives

- **Source of truth:** the Mac folder
  `/Users/mchamberlin/Local Sites/@claude_cowork/Sportsbook Research Sklill/`,
  reached with the `mcp__remote-devices__*` tools. Run scripts with
  `device_bash` from `cd "$HOME/mnt/Sportsbook Research Sklill"`.
- **Version history / backup:** private repo
  `github.com/neuegraphics-dev/sportsbook-nfl-research-sklill`, pushed from
  the cloud at the end of each run (step 16). The Mac cannot reach GitHub, so
  the push always goes Mac -> cloud -> GitHub. Never push it to any
  RangeMarketing account.
- **Read-only copies:** the published "Line Watch" dashboard artifact and
  `line-watch-snapshot.json` in the Drive folder "Line Watch"
  (`12iXLUAVb39miB05PcEAyaI1qqMIiY0qv`). Nothing is read back from them to
  build a run.
- A scheduled run starts with an empty cloud filesystem. Anything not written
  to the Mac folder is lost. If the folder can't be reached, say so and stop.
  Never fall back to writing data in the cloud workspace.

## Hard rules

- **Scripts are the only write path** for `data/`, `results/` and `model/`.
  Never hand-edit those JSON files or the dashboard HTML.
- Never loosen a filter to produce more picks.
- Spreads are stored **home-relative** (negative = home favored). Work out
  which team a source's number belongs to before writing it
  (reference: "Spread sign convention").
- A today-dated source is not proof its numbers moved. If a whole slate is
  unchanged while the sharp book moved, treat it as a stale source, not N
  separate stories (reference: "Line fetching").
- Verify which team an article or prop is about before using it
  (reference: "Prop team and name verification").
- Names: `model/aliases.json` maps every spelling of a player, team or book to
  one name; the scripts match through it. When a source spells someone a new
  way, add the spelling there (canonical name -> list of other spellings).

## Scripts (one line each; full docs in reference "Why the scripts exist")

| Script | Job |
|---|---|
| `compute_tier.py data` | Run first. Tier per game by hours to kickoff |
| `setup_week.py` | New week's game skeletons (safe to re-run) |
| `update_snapshot.py` | Only write path for lines; Tier 2/3 use `--skip-if-same-day` |
| `generate_chart.py` | Per-game line chart after each non-skipped snapshot |
| `apply_injury_adjustment.py` | Injury pts. Defensive positions now raise the OPPONENT's projection and write `def_clusters` |
| `simulate_game.py` | 10,000-run sim; re-run after any injury change |
| `apply_game_evaluation.py` | Analyst read + leans (end reasoning with `\| Stake $X · pass at Y`) |
| `apply_props.py` / `apply_prop_backtest.py` | Props (strict filter) / season backtest |
| `apply_market_signal.py` | Bet%/handle% + reverse line move |
| `apply_weather.py list\|apply` | Outdoor-game forecasts (new) |
| `log_result.py`, `capture_result.py`, `capture_prop_result.py` | Grading |
| `early_edges.py add\|prop-line\|refresh\|report\|taken\|void` | CLV tracker |
| `report_props.py`, `generate_report.py` | Hit rates, consolidated report |
| `build_dashboard_v2.py` | Dashboard (never `build_dashboard.py`) |
| `export_snapshot.py` | Drive JSON snapshot (moved out of this skill) |
| `repo_pack.sh` | Packs the versioned files for the GitHub push |

## Run behavior, in order

0. **Folder preflight.** `cd "$HOME/mnt/Sportsbook Research Sklill" && ls
   scripts data results model`. If no folder is connected, call
   `get_device_info`, then `device_request_folder_access` once with the path
   above. If it still fails, message the user (and push) and STOP
   (reference: "Folder connection preflight").
1. `python3 scripts/compute_tier.py data`.
2. **Setup run** if there's no active week or nothing within ~10 days:
   web-search the schedule, `setup_week.py`, capture openers as soon as they
   post (the early number is the edge), then stop.
3. **Tier 1 games** (<=48h): fresh lines every run + full research
   (reference: "Line fetching" and "Research signal categories").
4. **Tier 2/3 games:** lines once a day with `--skip-if-same-day`; research
   only if triggers flipped or research was never run. Rule out a systemic
   (source-wide) divergence first.
5. `generate_chart.py` after each non-skipped snapshot.
6. **Injuries + re-sim.** Record real changes with
   `apply_injury_adjustment.py`, then `simulate_game.py`. Defensive injuries
   (CB, S, DB, DE, DT, EDGE, LB...) are entered the same way (negative pts),
   and the sim now adds them to the opponent. When the script prints a
   `clusters` entry (2+ starters out in one unit), check the opponent's
   matching props (secondary -> passing/receiving, front -> rushing, OL ->
   own QB/RB unders) and the total. A cluster is a reason to look, not a
   pick by itself. Games logged before 2026-11-04 use the old own-side
   behavior until their injuries are re-applied.
7. **Weather (outdoor games, Tue-Wed and Tier 1).** `apply_weather.py list
   data` prints each outdoor game's NWS URL (domes and retractable roofs are
   skipped). WebFetch that URL (api.weather.gov itself is blocked), read the
   period covering kickoff (upper sustained wind, gusts, temp, precip %), and
   record it with `apply_weather.py apply`. `impact: strong` is one
   Environment factor for totals and passing/kicking props, never for a
   spread, never an independent signal category. `mild` = mention only.
8. **Analyst evaluation** for every active game with `apply_game_evaluation.py`,
   built on the injury-adjusted sim numbers.
9. **Results + prop grading** for newly final games (two sources for scores
   and box scores).
10. **Props (mandatory every run, not just Tier 1)** for every game kicking
    off within 96 hours with no `props` block or `pending`: covers.com
    (browser-rendered), RotoWire, Linemate player search, lineups.com team
    defense once per cycle. Never re-check a captured game. Apply the
    team-weakness filter from `line-watch-slate-bets`
    (reference: "Player props -- validated sources"). A game screened with
    no qualifier is still written (`apply_props.py --status captured --picks
    '[]' --source-note ...`) so the next run knows it was done. If the board
    hasn't posted a game yet, write `--status pending` with that note.
11. **Market signal** with `apply_market_signal.py` (Tier 1 every run,
    Tier 2/3 daily). Only call it a reverse line move if the script says so.
12. **CLV.**
    - `early_edges.py add` for every new recommendation at **$6 or more**,
      minimum-action picks included (pass `--min-action` for those).
    - **Prop closes:** for every logged prop whose game kicks off within 30
      hours, read the currently posted line from the same props source and
      record it: `early_edges.py prop-line --id "<id>" --line X [--odds N]`.
      Captured props are never re-checked in the game file, so this is the
      only way a prop gets a real closing line. The last value recorded
      before kickoff is its close.
    - `early_edges.py refresh` (after step 9), then report overall
      beat-the-close %, average CLV, **by window**, **by pick type**
      (edge vs min-action), and **vs the sharp close** (BookMaker.eu).
      Benchmark: 55%+ beat the close with a positive average.
13. **Props record** with `report_props.py`, plus the game-lean record by
    confidence tier.
14. `generate_report.py`, save under `reports/`, present it with a stake and
    pass-at on every bet (format from `line-watch-slate-bets`). Read its
    **RUN HEALTH** section: every "PROPS MISSING" or "STALE LINES" row is a
    skipped step. Go back and do it, then regenerate. Report any row you
    could not clear.
15. **Dashboard:** `build_dashboard_v2.py data
    scripts/dashboard_template_v2.html dashboard/line-watch-build.html` (it
    also writes `dashboard/index.html`). Check the printed `propsGraded`.
    Stage the build file in a separate call, `Artifact` list -> find "Line
    Watch" -> publish to that url with a `label` (never a `note` parameter)
    (reference: "The dashboard").
16. **Snapshot (artifact file, replaces the Drive upload 2026-11-07):** run
    `python3 -I scripts/export_snapshot.py dashboard/line-watch-build.html
    _sync/line-watch-snapshot.json` on the Mac BEFORE step 15's publish, stage
    the JSON with the build file, `cp` both from `/mnt/user-data/uploads/...`
    into the scratchpad (the Artifact tool only publishes files from the
    working dir or scratchpad), then do step 15's publish with
    `files: {"line-watch-snapshot.json": "<scratchpad copy>"}`. Verify with
    `Artifact read` + `path: "line-watch-snapshot.json"`: the byte count must
    equal `wc -c` on the Mac. No retyping, so it never runs out of room. The
    game-day checks read this file first. Drive is no longer written.
17. **GitHub sync (backup only, not a fallback):** needs the repo attached
    as a source on the scheduled task. Without it the push gets a 403 ("not
    in this session's authorized repository set"); report that in one line and
    move on. On the Mac run `scripts/repo_pack.sh`, stage
    `_sync/linewatch-sync.tar.gz`, then in the cloud clone
    (`git clone --depth 1`, long timeout), extract the tarball over the clone
    (`tar xzf ... -C <clone>`), `git add -A`, commit "Line Watch run
    <date time ET>" and push to `main`. If any part fails, finish the run and
    report it. The Mac folder is still the source of truth, so a missed push
    loses nothing.

## The scheduler

- Research runs: scheduled task "NFL Line Report — 8:30am & 4:30pm ET (Mac
  folder)" (`CRON_TZ=America/New_York 30 8,16 * * *`). Its prompt recaps
  these steps. If you add or reorder steps here, update its prompt with
  `update_trigger`.
- Game-day checks (cloud only, no Mac): "Line Watch game-day check" tasks at
  Thu/Mon 6:57pm, Sun 11:42am, Sun 3:03pm and 7:03pm ET. They read the Drive
  snapshot, check inactives for the carded picks only, and push "all clear"
  or "re-check". They never make or change picks.
- Use `create_trigger`/`update_trigger`, never a local cron.

## Reporting on a scheduled run

Message the user with `SendUserMessage`: the card by window with stakes,
qualifying props, the CLV line (overall, by window, by pick type, vs sharp),
the props record, the dashboard note, one line each for the snapshot file
and the GitHub sync, and any gaps. If a message already went out before
steps 16-17 finish, send those lines as a short follow-up.

## Quick question with no device access

Read `line-watch-snapshot.json` from the "Line Watch" artifact (`Artifact read`, `path: "line-watch-snapshot.json"`); fall back to the newest Drive copy by title
(never reuse an old file id), follow its own `meta.howToAnswer`, lead with
how fresh it is, and treat a bet as live only while the line beats its
`passAt`. Fallback: the "Line Watch" artifact's embedded `const D`
(reference: "Answering a quick question with no device access").
