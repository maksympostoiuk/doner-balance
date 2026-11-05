"""Command line interface: ``python -m doner_balance <command>``.

Commands
--------
report     run everything: simulations, analysis, charts, CSV, REPORT.md, Godot export
simulate   run one strategy and print milestones (optionally save CSVs)
tune       compare against pacing targets and suggest lever changes (--apply: auto-tune)
export     write the Godot JSON export only
validate   validate the config and print a short summary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .config import EconomyConfig, config_from_dict
from .recommend import fmt_h, fmt_num


def load(path: str, overrides: str | None) -> EconomyConfig:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if overrides:
        from .tuning import apply_overrides

        with open(overrides, encoding="utf-8") as fh:
            data = apply_overrides(data, yaml.safe_load(fh) or {})
    return config_from_dict(data)


def cmd_report(a: argparse.Namespace) -> int:
    from .report import build

    cfg = load(a.config, a.overrides)
    if a.days:
        cfg.simulation.days = a.days
    label = a.config + (f" + {a.overrides}" if a.overrides else "")
    build(cfg, Path(a.out), None if a.no_export else Path(a.export), jobs=a.jobs, events=not a.no_events,
          with_suggestions=not a.no_suggest, config_label=label)
    return 0


def cmd_simulate(a: argparse.Namespace) -> int:
    from .simulator import simulate

    cfg = load(a.config, a.overrides)
    names = a.strategy or list(cfg.strategies)
    for n in names:
        if n not in cfg.strategies:
            print(f"unknown strategy '{n}', choose from {list(cfg.strategies)}", file=sys.stderr)
            return 2
        r = simulate(cfg, n, seed=a.seed, days=a.days, disabled_events=a.disable or ())
        print(f"\n== {n} (seed {r.seed}, {len(r.purchases)} purchases, {len(r.events)} NPC events)")
        for m, (cal, act) in r.milestones.items():
            print(f"  {m:16s} {fmt_h(cal):>9s} calendar  {fmt_h(act):>9s} play")
        print(f"  final: lifetime {fmt_num(r.final['lifetime_total'])}, income {fmt_num(r.final['active_income'])}/s, "
              f"reputation {int(r.final['reputation'])}, prestiges {int(r.final['prestiges'])}")
        if a.csv:
            out = Path(a.csv)
            out.mkdir(parents=True, exist_ok=True)
            r.timeseries.to_csv(out / f"timeseries_{n}.csv", index=False)
            r.purchases.to_csv(out / f"purchases_{n}.csv", index=False)
            r.events.to_csv(out / f"events_{n}.csv", index=False)
    return 0


def cmd_tune(a: argparse.Namespace) -> int:
    from .simulator import simulate
    from .tuning import auto_tune, check_targets, suggest

    cfg = load(a.config, a.overrides)
    ref = cfg.pacing.reference_strategy
    if not a.apply:
        checks = suggest(cfg, check_targets(cfg, simulate(cfg, ref)))
        print(f"Pacing check for reference strategy '{ref}':\n")
        print(f"{'milestone':16s} {'target':>14s} {'actual':>10s} {'play':>9s} {'ratio':>6s}  {'status':12s} suggestion")
        for c in checks:
            ratio = f"{c.ratio:.2f}" if c.ratio is not None else "—"
            sugg = ""
            if c.suggested_factor not in (None, 1.0):
                sugg = f"{c.lever}: {fmt_num(c.lever_value)} -> {fmt_num(c.suggested_value)} (x{c.suggested_factor:.2g})"
            elif c.note:
                sugg = c.note
            print(f"{c.milestone:16s} {fmt_h(c.target_h) + ' ' + c.clock[:3]:>14s} {fmt_h(c.actual_h):>10s} "
                  f"{fmt_h(c.actual_active_h):>9s} {ratio:>6s}  {c.status:12s} {sugg}")
        print("\nSuggestions treat each milestone separately; run `tune --apply` for a sequential search.")
        return 0
    res = auto_tune(cfg)
    print("Auto-tune steps (play hours):")
    for s in res.steps:
        print(f"  {s.milestone:16s} lever {s.lever:32s} x{s.factor:.3g}  {fmt_h(s.before_active_h)} -> "
              f"{fmt_h(s.after_active_h)} (target {fmt_h(s.target_active_h)})")
    out = Path(a.apply)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# Overrides written by `python -m doner_balance tune --apply`.\n"
                 "# Use with: python -m doner_balance report --overrides " + str(out) + "\n"
                 "# or copy the values into config/economy.yaml.\n")
        yaml.safe_dump(res.overrides, fh, sort_keys=False)
    if a.write_config:
        from .tuning import nice, patch_yaml_text

        text = Path(a.config).read_text(encoding="utf-8")
        rounded = {k: nice(v, 2) for k, v in res.overrides.items()}
        new_text = patch_yaml_text(text, rounded)
        config_from_dict(yaml.safe_load(new_text))  # must still validate
        Path(a.config).write_text(new_text, encoding="utf-8")
        print(f"Updated {len(rounded)} values in {a.config} (rounded to 2 significant digits, comments kept).")
        res.data = yaml.safe_load(new_text)
    tuned = config_from_dict(res.data)
    checks = check_targets(tuned, simulate(tuned, ref))
    print(f"\nWrote {len(res.overrides)} overrides to {out}. Result:")
    for c in checks:
        print(f"  {c.milestone:16s} {c.status:12s} actual {fmt_h(c.actual_h)} (play {fmt_h(c.actual_active_h)})")
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    from .export import export_json

    cfg = load(a.config, a.overrides)
    print(f"wrote {export_json(cfg, a.out)}")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    cfg = load(a.config, a.overrides)
    n_items = len(cfg.tap.upgrades) + sum(len(t.staff) + len(t.upgrades) + len(t.managers) for t in cfg.tiers)
    print(f"OK: {cfg.name} v{cfg.version}: {len(cfg.tiers)} tiers, {n_items} items, {len(cfg.events)} NPC events, "
          f"{len(cfg.strategies)} strategies, {len(cfg.pacing.targets)} pacing targets")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="doner_balance", description="Doner Lamar economy balance simulator")
    p.add_argument("--config", default="config/economy.yaml", help="economy YAML (default: %(default)s)")
    p.add_argument("--overrides", default=None, help="YAML of dotted-path overrides (e.g. from tune --apply)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="run everything and write reports/ + export/")
    r.add_argument("--out", default="reports")
    r.add_argument("--export", default="export/economy.json")
    r.add_argument("--no-export", action="store_true")
    r.add_argument("--no-events", action="store_true", help="skip the NPC ablation study (faster)")
    r.add_argument("--no-suggest", action="store_true", help="skip tuning sensitivity runs (faster)")
    r.add_argument("--days", type=float, default=None, help="override simulation.days")
    r.add_argument("--jobs", type=int, default=0, help="worker processes for the ablation (0 = auto)")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("simulate", help="run strategies and print milestones")
    s.add_argument("strategy", nargs="*", help="strategy names (default: all)")
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--days", type=float, default=None)
    s.add_argument("--disable", nargs="*", help="NPC event ids to switch off")
    s.add_argument("--csv", default=None, help="directory to write CSVs to")
    s.set_defaults(func=cmd_simulate)

    t = sub.add_parser("tune", help="distance to pacing targets + suggestions")
    t.add_argument("--apply", nargs="?", const="reports/tuned_overrides.yaml", default=None,
                   help="auto-tune and write overrides YAML (default path: %(const)s)")
    t.add_argument("--write-config", action="store_true",
                   help="with --apply: also write the tuned values into the config file (keeps comments)")
    t.set_defaults(func=cmd_tune)

    e = sub.add_parser("export", help="write the Godot JSON export")
    e.add_argument("--out", default="export/economy.json")
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("validate", help="validate the config")
    v.set_defaults(func=cmd_validate)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
