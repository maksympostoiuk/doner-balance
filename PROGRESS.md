# PROGRESS

Plan and status for the Doner Lamar economy simulator. Updated after every milestone.

## Plan

| # | Milestone | Status |
|---|-----------|--------|
| M0 | Project scaffolding: CLAUDE.md, PROGRESS.md, pyproject, venv | done |
| M1 | Config model (pydantic) + default `config/economy.yaml` with comments; cost/income formulas | done |
| M2 | Simulator: time-step loop, session model, tapping, offline earnings, NPC events (seeded) | done |
| M3 | Player strategies: best ROI, cheapest first, saver, casual | done |
| M4 | Analysis: milestones, dead zones, income/s, tap vs automation share, event impact; charts + CSV | in progress |
| M5 | Target pacing in config + tuning helper (distance to targets, suggested parameter changes) | done |
| M6 | Godot JSON export + format docs + GDScript example | done |
| M7 | REPORT.md generator with concrete recommendations; one CLI command for everything | todo |
| M8 | Tests (formulas, income, events, offline, determinism), README, final balance pass + summary | todo |

## Log

- **M0** — scaffolding created. Python 3.12 venv via `uv`.
- **M1** — `doner_balance/config.py` (pydantic models, cross-reference validation, dotted-path
  get/set for the tuning helper), `formulas.py` (cost, bulk cost, milestones, reputation,
  offline), `model.py` (GameState, income engine, purchase options), commented
  `config/economy.yaml` with 4 tiers, tap upgrades, staff, equipment, managers, 4 NPC events,
  offline rules, prestige, profiles, strategies, pacing targets. Formula tests pass.
- **M2/M3** — `sessions.py` (evenly spread sessions in a waking window), `events.py` (Poisson NPC
  events, one RNG stream per event type, pending-while-offline, responses with scaling costs,
  probabilistic outcomes, timed/permanent income modifiers, per-event accounting),
  `strategies.py` (best ROI / cheapest / saver policies on the *effective* income rate, smart vs
  default NPC answers, prestige rule), `simulator.py` (1 s active / 60 s offline steps, offline
  credit at session start, purchase + milestone logs, timeseries).
  First balance pass: initial numbers exploded (income 1e8/s after 10 minutes) because low-level
  milestone doublings + cheap equipment multipliers made marginal payback *fall* with level.
  Fixed by sparser milestones (25/50/100/200), steeper growth (1.12-1.15) and pricier, smaller
  multipliers. Remaining pacing gap is handled by the tuning helper (M5).
- **M5** (done before M4 so the default config could be calibrated with it) — `tuning.py`:
  target checks (calendar targets judged on the equivalent play time), elasticity-based
  suggestions per lever, sequential log-log secant `auto_tune`, `tier:<id>` levers that scale a
  whole tier, overrides. `simulate(..., stop_when=milestone)` for fast measurements.
  Default config rewritten with tuned, rounded numbers; managers now cost ~1.5x their tier unlock
  (they used to cost up to 40x, leaving new tiers without offline income for days).
- **M6** — `export.py` writes deterministic `export/economy.json` (format v1) with formula strings
  and test vectors (costs, staff income at milestone boundaries, reputation, a full sample state);
  `docs/GODOT_EXPORT.md` documents it; `godot/economy.gd` is a Godot 4 loader implementing the
  formulas with `self_test()` against the test vectors.
- **M4 (in progress)** — `analysis.py` (milestone table, dead zones in play time, daily income by
  source, ablation-based NPC event impact over 5 seeds, response mix) and `charts.py`.
