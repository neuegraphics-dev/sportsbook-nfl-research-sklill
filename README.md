# Line Watch — NFL line + prop research

Michael's personal NFL betting research system. Research leans with a confidence score, never guaranteed outcomes.

## How it runs
- **8:30am + 4:30pm ET** — full research run (scheduled task, runs against the Mac folder). Card by window, dashboard, Drive snapshot, then pushes these files here.
- **Game days, after inactives** — cloud-only "game-day check" reads the Drive snapshot and pushes ALL CLEAR / RE-CHECK for carded picks.
- The Mac folder is the source of truth. This repo is version history and backup, synced Mac → cloud → GitHub, because the Mac can't reach GitHub directly.

## Layout
| Path | What |
|---|---|
| `scripts/` | The only write path for data (see `docs/reference.md`) |
| `model/` | Ratings, prop rules, `aliases.json` (names), `stadiums.json` (weather) |
| `data/<season>-nfl-wk<N>/` | One JSON file per game |
| `results/` | Game results, graded props, CLV tracker (`early_edges.json`) |
| `docs/reference.md` | Full script docs, sources, schema |
| `docs/skills/` | Copies of the two Claude skills that drive the runs |
| `dashboard/index.html` | Latest dashboard build |

## Upgrade log
- 2026-11-04: CLV by window / pick type / sharp close, prop closing lines, moneyline grading + no-vig price CLV; alias map; NWS weather; defensive injury clusters credit the opponent; slim skill + reference doc; game-day checks; this repo.
