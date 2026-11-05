# PROGRESS

Plan and status for the Doner Lamar economy simulator. Updated after every milestone.

## Plan

| # | Milestone | Status |
|---|-----------|--------|
| M0 | Project scaffolding: CLAUDE.md, PROGRESS.md, pyproject, venv | done |
| M1 | Config model (pydantic) + default `config/economy.yaml` with comments; cost/income formulas | todo |
| M2 | Simulator: time-step loop, session model, tapping, offline earnings, NPC events (seeded) | todo |
| M3 | Player strategies: best ROI, cheapest first, saver, casual | todo |
| M4 | Analysis: milestones, dead zones, income/s, tap vs automation share, event impact; charts + CSV | todo |
| M5 | Target pacing in config + tuning helper (distance to targets, suggested parameter changes) | todo |
| M6 | Godot JSON export + format docs + GDScript example | todo |
| M7 | REPORT.md generator with concrete recommendations; one CLI command for everything | todo |
| M8 | Tests (formulas, income, events, offline, determinism), README, final balance pass + summary | todo |

## Log

- **M0** — scaffolding created. Python 3.12 venv via `uv`.
