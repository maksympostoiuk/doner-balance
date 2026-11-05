# PROGRESS

Plan and status for the Doner Lamar economy simulator. Updated after every milestone.

## Plan

| # | Milestone | Status |
|---|-----------|--------|
| M0 | Project scaffolding: CLAUDE.md, PROGRESS.md, pyproject, venv | done |
| M1 | Config model (pydantic) + default `config/economy.yaml` with comments; cost/income formulas | done |
| M2 | Simulator: time-step loop, session model, tapping, offline earnings, NPC events (seeded) | done |
| M3 | Player strategies: best ROI, cheapest first, saver, casual | done |
| M4 | Analysis: milestones, dead zones, income/s, tap vs automation share, event impact; charts + CSV | done |
| M5 | Target pacing in config + tuning helper (distance to targets, suggested parameter changes) | done |
| M6 | Godot JSON export + format docs + GDScript example | done |
| M7 | REPORT.md generator with concrete recommendations; one CLI command for everything | done |
| M8 | Tests (formulas, income, events, offline, determinism), README, final balance pass + summary | done |

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
- **M4** — `analysis.py` (milestone table, dead zones in play time, daily income by
  source, ablation-based NPC event impact over 5 seeds, response mix) and `charts.py`.
  Dead zones measure *progress velocity*: play time until purchases add up to +5% effective income
  (a single-item threshold unfairly punished incremental designs). Event impact uses play time
  (calendar time is quantized by sessions) and earnings at a fixed checkpoint (prestige timing
  makes end-of-run earnings noisy). Charts use one fixed colour per strategy across all figures.
- **M7** — `recommend.py` (rule-based findings with severity, evidence and a concrete action naming
  config paths/values: pacing, dead zones incl. "idle sessions", stage length, taps, offline share,
  casual gap, naive-buyer stall, NPC events, prestige loop), `report.py` (whole pipeline, CSVs,
  PNGs, REPORT.md), `cli.py` (`report`, `simulate`, `tune [--apply]`, `export`, `validate`;
  `--overrides` layering). `python -m doner_balance report` takes ~25 s (ablation runs in parallel).
- **M8a (tests)** — 78 pytest tests: cost formulas (level/bulk/max-affordable, milestones,
  reputation), income engine on a hand-computable tiny economy (staff, equipment, managers,
  global/tier/reputation multipliers, tap value incl. income share), purchases & prestige reset,
  NPC events (scaling costs, certain/impossible outcomes, cash floor, timed refresh, permanent
  stacking, income accounting, eligibility, Poisson rate, smart vs default answers), offline
  earnings (manager requirement, efficiency, cap, cap from managers, credit at next session),
  determinism (same seed identical, different seed differs, independent event streams), dead-zone
  logic, tuning levers, export test vectors and a CLI smoke test. Runs in ~4 s.
- **Balance pass** — added restaurant content (`night_shift` staff, `delivery_app` upgrade) because
  that stage lasts ~5 days; COO no longer costs more than the franchise unlock; then
  `tune --apply --write-config` re-hit all five pacing targets (new: comment-preserving YAML
  patcher). NPC impact now measured as *progress shift* (hours of progress lost by day 7, sampled
  at session ends) after two noisier metrics proved misleading.
- **M8b** — README (setup, one-command run, CLI, model, how to change the config, how to read the
  report), generated closing summary in REPORT.md. 81 tests pass.

## Summary — where the balance stands (economy v0.1.0, seed 42)

**Pacing: all five targets met** for the reference player (`best_roi`, 3 × 10 min/day):
first purchase 4 s, cafe 8.5 min (end of session 1), restaurant 24 h (last session of day 1),
franchise 6.2 days, first prestige 12.4 days. Casual players (2 × 5 min/day) reach the franchise
on day 11 (×1.8 slower, inside the ×3 warning line). A naive cheapest-first buyer never reaches
the franchise in 21 days.

**What the economy looks like.** Run 1 is kiosk 9 min → cafe 1 day → restaurant 5.2 days →
franchise 6.2 days → prestige (54 reputation, ×2.08 income); run 2 reaches the franchise ×1.6
faster. From day 2 on, ~89% of coins come from offline earnings, ~8% from automation while playing
and ~3% from taps (taps are still ~27% of what is earned *during* a session, so tapping feels
worthwhile while playing). NPCs are mild: all events together cost the smart player ~3 h of
progress by day 7. Supplier deals are the strongest NPC and act as a buff (~9 h ahead); inspector
and rival barely register.

**What to tune first** (also at the top of `reports/REPORT.md`):

1. **Idle sessions / offline dominance.** 42 of 47 dead zones are single sessions in which the
   offline earnings are spent in the first seconds and the remaining ~9 minutes add little. That is
   the core idle-game trade-off. If sessions should matter, lower `offline.efficiency` (0.5 → ~0.35)
   and/or raise `tap.upgrades.showman.value`, then re-run `tune --apply` to restore pacing. It is
   also a game-design hook: in-session goals, NPC events that happen *while* playing, timed boosts.
2. **The wall before the franchise** (~40 min of play over ~4 sessions on days 5-6). Add one more
   restaurant item around 12T cost, or more `interior` levels.
3. **Early franchise stage waits** for `regional_director` / `marketing_dept` (20-30 min of play).
   Slightly cheaper growth, or an intermediate franchise upgrade.
4. Decide what the NPCs should *feel* like: they are currently mostly flavour. To make the
   inspector/mafia/rival memorable, raise their rates or stakes; to keep the supplier from being a
   free buff, lower `accept` to ×1.1 or raise its cost.

**Caveats.** Strategies are scripted bots: they buy optimally (or naively) and never forget to log
in. Prestige timing follows a fixed "double your income" rule. The target pacing is an assumption
in `pacing.targets`; change it and re-run `tune --apply`.

## Next ideas (not done)

- Monte Carlo over many seeds for the main milestone table (currently main runs use one seed;
  the NPC ablation uses 5).
- Session-length variance and missed days in the player model.
- Bulk-buy (x10/x100) behaviour and "buy max" UI in strategies.
- More prestige layers / reputation shop.

## Second pass (review + open items)

- **R1 — simulation/analysis correctness** (fixed, with regression tests in `tests/test_regressions.py`):
  - smart NPC answers kept paying for permanent bonuses that had already hit `max_stacks`
    (e.g. a 6th kitchen renovation that gives nothing) — expected value now ignores maxed stacks;
  - milestone names were hardcoded (`cafe/restaurant/franchise`): renaming or adding a tier silently
    dropped data from the NPC impact table — milestones and the impact milestone now come from the config;
  - with exactly one NPC event the ablation ran a duplicate variant and the impact table crashed;
  - the prestige rule read run 1/run 2 of whichever strategy came first, not the reference player;
  - `casual_rules` divided by `None` when the reference player never left the first tier;
  - NPC events kept firing (and income was attributed to them) after the last session, where nothing
    is ever collected;
  - reputation gained per run was parsed out of a display string (broke with other currency names);
  - the 60 s re-planning interval was hardcoded → `simulation.replan_every_seconds`;
  - cost formulas return `inf` instead of raising `OverflowError` for absurd levels.
- **R2 — validation, security, CLI** (fixed + tested):
  - ids and strategy/profile names must match `[A-Za-z0-9_-]+` — strategy names become CSV file
    names, so `../../x` could previously write outside `reports/`; ids with dots broke dotted paths;
  - new checks: unique event ids and response ids, at least one staff per tier, `free_staff_levels`
    ≥ 1 and ≤ `max_level`, sessions must not overlap (session length ≤ spacing), timed effects need
    an `income_mult`, `tap_share_low_pct < tap_share_high_pct`, unique seeds, `days ≤ 3650`, and every
    pacing lever must resolve to existing numeric fields (was only discovered mid-tuning);
  - `set_path` no longer silently creates keys under list items; `expand_path` supports `*` on maps;
  - CLI: missing/empty/broken YAML, invalid configs and bad override paths print one clear error
    (exit 2) instead of a traceback; `--days`/`--jobs` are range-checked and go through validation;
    unknown `--disable` ids are rejected; `--write-config` requires `--apply` and refuses
    `--overrides` (it would have written values tuned for a different config into the YAML);
  - tuning: levers never scale booleans and keep integers integral; `format_yaml_number` wrote
    `1e-07`, which PyYAML reads back as a *string*; `auto_tune` no longer crashes on a milestone
    reached at t = 0.
- Tooling: `ruff` (lint) and `mypy` (typecheck) configured in `pyproject.toml`, dev extras updated.
- **R3 — report, charts, accessibility, translations, tooling**:
  - REPORT.md: config text is escaped in tables (a `|` in a name broke the table), stale CSV/PNG files
    of renamed strategies are removed before writing, income checkpoints only list days inside the
    horizon (a 3-day run printed a "day 14" value), plural fixes, no hardcoded tier names or
    `showman` id in recommendations, every warning threshold moved to `analysis:` in the config;
  - charts: identity no longer relies on colour alone — per-strategy marker shapes, line styles and
    bar hatching, hatched income-source areas; line-end labels no longer collide; `%` ticks on the
    NPC chart; week ticks follow the horizon; placeholder chart instead of a crash on empty data;
    descriptive alt text for every image in REPORT.md;
  - translations: every player-facing name has a key (`STAFF_NEPHEW_NAME`, …); new `localization:`
    config section; `export/translations.csv` in Godot's CSV format; other languages from
    `config/translations/<lang>.yaml`; `validate` and REPORT.md list missing/stale translations;
    ids that would collide as keys are rejected;
  - Godot export **format v2** (`name_key` everywhere, `localization` section); docs and
    `godot/economy.gd` updated (`display_name`, `reputation_gain`, `can_sell_empire`), GDScript now
    linted/format-checked with gdtoolkit; a test keeps `SUPPORTED_VERSION` in sync;
  - quality gates: `scripts/check.sh` runs ruff (lint + format), mypy, gdlint, pytest and a wheel
    build; codebase formatted with ruff; mypy found loop variables reused with different types and
    a `None` multiplier that could reach `max()` in the NPC rule — fixed.
