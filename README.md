# doner-balance

Economy balance simulator for **Doner Lamar** (working title, theme *Shawarma Empire*), an
idle/tapper mobile game. The player starts with a shawarma kiosk by a metro station and grows into
an international chain: **kiosk → cafe → restaurant → franchise**, with tapping, staff,
managers, NPC events (supplier, sanitary inspector, mafia, rival burger place), offline earnings
and a prestige layer (sell the empire for reputation).

This repo designs and tests the economy only. The game will be built in Godot; it loads the
economy from a JSON export (`export/economy.json`).

```
config/economy.yaml  ──►  simulator (4 player strategies, seeded NPC events)  ──►  reports/REPORT.md + PNG + CSV
                     └─►  export/economy.json  ──►  Godot (godot/economy.gd)
```

## Setup

Python 3.12 with numpy, pandas, matplotlib, pydantic, PyYAML, pytest.

```bash
uv venv --python 3.12 .venv
```

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
```

(Plain `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"` works too.)

## Run everything (one command)

```bash
.venv/bin/python -m doner_balance report
```

This takes ~25 s and writes:

- `reports/REPORT.md` — findings, tables, charts and concrete tuning recommendations
- `reports/*.png` — charts; `reports/*.csv` — every table behind them
- `export/economy.json` — the Godot export of the current config

Useful flags: `--no-events` (skip the NPC ablation study), `--no-suggest` (skip the tuning
sensitivity runs), `--days 30`, `--out other_dir`, `--overrides file.yaml`, `--config other.yaml`
(global flags go before the command: `python -m doner_balance --config x.yaml report`).

## Other commands

Simulate strategies and print milestones (optionally a different seed, fewer days, NPC events off,
CSV output):

```bash
.venv/bin/python -m doner_balance simulate best_roi casual --days 7 --seed 3 --disable mafia --csv out/
```

Check the config against the pacing targets and get a suggested value per lever:

```bash
.venv/bin/python -m doner_balance tune
```

Auto-tune all levers (sequential search) and write the result as an overrides file:

```bash
.venv/bin/python -m doner_balance tune --apply
```

The same, but also write the tuned values straight into `config/economy.yaml`
(rounded to 2 significant digits, comments kept):

```bash
.venv/bin/python -m doner_balance tune --apply --write-config
```

Other commands: `export` (Godot JSON only) and `validate` (check the config).

Run the tests:

```bash
.venv/bin/pytest -q
```

## How the model works

**Time.** The simulator steps 1 s while the player is in the game and 60 s while away, over
`simulation.days` calendar days (default 21). Sessions are spread evenly over a waking window:
the default *regular* player plays 3 × 10 min (08:00, 13:00, 18:00). The game is installed at the
start of the first session.

**Income** (all per second; the full formulas are in `docs/GODOT_EXPORT.md`):

- *Taps*: `taps_per_second × tapping_share × tap_value`. Tap value grows with tap upgrades
  (flat, %, and "income share" = each tap also earns a % of automated income) and the tier's
  `tap_mult`.
- *Automation*: staff produce `level × income × milestone_mult(level)` per item; a tier's equipment
  upgrades multiply it (price and speed); managers multiply it too.
- *Global*: every owned tier's `global_income_mult`, reputation (`+bonus_per_point` each) and active
  NPC modifiers.
- *Offline*: tiers with a manager keep producing at `offline.efficiency`, up to
  `offline.cap_hours` (+ the managers' `offline_cap_hours_add`). Credited when the next session starts.

**Costs.** `cost(level) = base_cost × cost_growth^level` (level = levels already owned).

**NPC events.** Poisson arrivals (`rate_per_hour`) once `min_tier` and `min_hours_since_start`
are reached, with a cooldown. Events that fire while the player is away wait for the next session.
The player picks a response: *smart* players minimise the expected cost (response cost +
probability × losses − gains, valuing income effects over `event_value_horizon_hours`); *default*
players always press the default button. Each event type has its own RNG stream (seeded from the
master seed and the event id), so runs are reproducible and disabling one event does not change
the others.

**Strategies** (in `strategies:`; each one = a buying policy + a session profile):

| strategy | policy | meaning |
|---|---|---|
| `best_roi` | best ROI first | buys the item with the shortest payback = cost / gain in *effective* income (average coins per real second: active income × play share + offline income × credited offline share), saving for it if needed |
| `cheapest` | cheapest first | always buys the cheapest item that adds income |
| `saver` | saver | saves for the next tier when it is ≤ `saver_patience_hours` away (still buying items ≤ 2% of its price), otherwise best ROI |
| `casual` | best ROI, casual profile | 2 × 5 min per day, little tapping, default NPC answers |

All strategies sell the empire when the reputation gain would at least double income
(`prestige_multiplier_threshold`).

## How to change the config

Everything lives in **`config/economy.yaml`**, and every number has a comment. The file is
validated with pydantic on load: typos, unknown keys, duplicate ids and broken references fail with
a readable error (`python -m doner_balance validate`).

Typical edits:

- **Make the cafe cheaper:** `tiers → cafe → unlock_cost`.
- **Make an item last longer:** lower its `cost_growth` (1.07 = long-lived, 1.15 = few levels).
- **Add a staff type / upgrade / manager:** copy a block inside a tier and give it a new, globally
  unique `id`. It shows up in the simulation, the report and the Godot export automatically.
- **Add a tier:** append to `tiers:` (unlock costs must not decrease). Target milestones can then
  use its id.
- **Change offline rules:** `offline.efficiency`, `offline.cap_hours`, `requires_manager`.
- **Add or tune an NPC event:** `events:`. Costs and losses are `Amount`s that scale with the
  economy: `flat + cash_fraction × cash + income_seconds × income/sec`. Effects can be timed
  (`duration_hours`) or permanent (`max_stacks`), and probabilistic (`probability`).
- **Model a different player:** add a profile under `profiles:` and a strategy that uses it.
- **Change the target pacing:** `pacing.targets` (milestone, hours, `clock: calendar|active`,
  tolerance, `mode: around|at_most`, and the `lever` the tuning helper may scale: a dotted path such
  as `prestige.base_requirement`, or `tier:<id>` to scale every money value of a tier together).
- **Change what counts as a problem:** `analysis:` (dead-zone threshold, tap-share bounds, casual
  slowdown warning, NPC impact warning).

Suggested loop: edit → `tune` (are the targets still met?) → `report` → read *What to tune first*.
To try changes without touching the main file, put dotted paths in an overrides YAML and pass
`--overrides my_changes.yaml`, for example:

```yaml
tiers.cafe.unlock_cost: 40000
offline.efficiency: 0.35
events.mafia.rate_per_hour: 0.015
```

## How to read the report

`reports/REPORT.md` is fully generated; regenerate it after every change. Sections:

1. **What to tune first:** the three most severe actionable findings, each with the evidence and a
   concrete change (config path and value).
2. **Current balance at a glance:** milestones, stage lengths, income per second, where coins come
   from, dead zones, targets met, prestiges.
3. **Milestones per strategy:** calendar time since install (play time in brackets), plus
   `milestones.png` (grey bands are the targets ± tolerance).
4. **Pacing vs targets:** each target with status and the suggested lever change. Calendar
   targets are judged on the equivalent *play time*, because calendar time jumps from session to
   session ("restaurant on day 1" means "by the end of the last session of day 1").
5. **Dead zones:** stretches of *play time* longer than the threshold in which purchases do not add
   up to +5% income (or a tier/prestige). *Idle sessions* (offline earnings spent in the first
   seconds, then nothing to do) show up here. `payback.png` shows the payback time of every
   purchase: a steady slow rise is healthy; isolated high clouds are walls.
6. **Income over time and sources:** income per second (log scale, ▼ = prestige) and daily shares of
   taps vs automation vs offline.
7. **NPC events:** ablation study over several seeds (each event switched off alone). *shift* =
   hours of progress the event costs by the checkpoint (day 7 by default); positive = it slows
   you down. Also the answers players chose.
8. **Prestige runs:** how fast each run reaches each tier after a reset.
9. **All findings:** every rule's result sorted by severity, then a prose **Summary**.

## Godot

`export/economy.json` is the whole economy in a simple JSON format, documented in
[`docs/GODOT_EXPORT.md`](docs/GODOT_EXPORT.md). [`godot/economy.gd`](godot/economy.gd) is a
ready-to-use Godot 4 loader that implements the same formulas and verifies itself against test
vectors embedded in the JSON (`self_test()`).

## Layout

```
config/economy.yaml        the economy (commented)
doner_balance/
  config.py                pydantic models, validation, dotted paths
  formulas.py              cost / income / reputation / offline formulas
  model.py                 game state, income engine, purchase options
  sessions.py              session schedule
  events.py                NPC event engine
  strategies.py            buying policies, NPC answers, prestige rule
  simulator.py             time-step simulation
  analysis.py              milestones, dead zones, income sources, NPC ablation, prestige runs
  charts.py                PNG charts
  tuning.py                pacing checks, elasticity suggestions, auto-tune, YAML patching
  recommend.py             rule-based findings and recommendations
  report.py                full pipeline + REPORT.md
  export.py                Godot JSON export
  cli.py                   command line
tests/                     pytest suite
reports/                   generated output
export/economy.json        generated Godot export
docs/GODOT_EXPORT.md       export format
godot/economy.gd           GDScript loader
```
