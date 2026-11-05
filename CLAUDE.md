# CLAUDE.md — rules for working in this repo

This repo is the **economy balance simulator** for the idle/tapper mobile game
**"Doner Lamar"** (working title, theme "Shawarma Empire"). The game itself will be
built later in Godot; this repo only designs and tests the economy.

## Game concept (source of truth for theming)

- The player starts with one shawarma kiosk by a metro station and grows into an
  international chain: **kiosk -> cafe -> restaurant -> franchise**.
- Tapping makes shawarma by hand; later the player hires **staff** and **managers**
  for automation.
- NPCs affect the economy:
  - **suppliers** — cheaper ingredients via deals,
  - **sanitary inspector** — fines or required upgrades,
  - **mafia** — protection payments or risk,
  - **rival burger place** — competition that lowers demand unless countered.
- Feel: "ordinary guy rising to the top", deep progression.
- Optional prestige layer: sell the empire for **reputation** multipliers.

## Hard rules

1. **Stack:** Python 3.12, numpy, pandas, matplotlib, pydantic (config validation),
   pytest. Do not add other runtime dependencies without a good reason.
2. **The economy lives in YAML (`config/economy.yaml`), never hardcoded.** Every
   balance number (costs, growth, income, events, offline rules, prestige, targets,
   player profiles, analysis thresholds) must come from the config. Code may only
   contain formulas and structural logic. Comment every number in the YAML.
3. **Validate config with pydantic.** Invalid configs must fail loudly with a
   readable error.
4. **Determinism:** every random draw goes through a seeded `numpy.random.Generator`.
   Same config + same seed => identical results. Each NPC event type uses its own
   RNG stream so disabling one event does not change the draws of the others.
5. **One CLI command regenerates everything** in `reports/`
   (`python -m doner_balance report`): PNG charts, CSV tables, `REPORT.md`, and the
   Godot JSON export.
6. **Godot export format** (`export/economy.json`) is documented in
   `docs/GODOT_EXPORT.md`. If you change the format, bump `format_version` and
   update the docs and the GDScript example in `godot/`.
7. **Tests:** pytest must pass before every commit (`.venv/bin/pytest -q`). Cover cost
   formulas, income calculations, event effects, offline earnings and determinism.
8. **Workflow:** work in milestones; after each milestone update `PROGRESS.md`,
   commit with a clear message and push to the working branch.
9. Keep `README.md` accurate: how to run a simulation, how to change the config,
   how to read the report.

## Layout

```
config/economy.yaml        default economy (commented)
doner_balance/             the package (config, formulas, model, sim, analysis, ...)
tests/                     pytest suite
reports/                   generated output (charts, CSV, REPORT.md)
export/economy.json        generated Godot export
docs/GODOT_EXPORT.md       export format spec
godot/                     GDScript example loader
```

## Environment

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/pytest -q
.venv/bin/python -m doner_balance report                      # everything -> reports/, export/
.venv/bin/python -m doner_balance tune                        # pacing vs targets + suggestions
.venv/bin/python -m doner_balance tune --apply --write-config # auto-tune into the YAML (keeps comments)
```
