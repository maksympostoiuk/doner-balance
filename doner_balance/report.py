"""One-shot pipeline: simulate all strategies, analyse, chart, recommend, write REPORT.md + CSVs."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import analysis as A
from . import charts as C
from .config import EconomyConfig
from .export import export_json
from .recommend import Recommendation, all_recommendations, fmt_h, fmt_num
from .simulator import SimResult, simulate
from .tuning import TargetCheck, check_targets, suggest


@dataclass
class ReportData:
    cfg: EconomyConfig
    results: dict[str, SimResult]
    milestones: pd.DataFrame
    zones: pd.DataFrame
    zone_summary: pd.DataFrame
    sources: pd.DataFrame
    impact: pd.DataFrame | None
    mix: pd.DataFrame
    runs: pd.DataFrame
    stages: pd.DataFrame
    checks: list[TargetCheck]
    recs: list[Recommendation]
    charts: dict[str, Path]
    seconds: float


def md_table(df: pd.DataFrame, fmt: dict[str, Callable[[Any], str]] | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if c in fmt:
                cells.append(fmt[c](v))
            elif isinstance(v, float):
                cells.append("—" if math.isnan(v) else f"{v:.3g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _log(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, flush=True)


def build(cfg: EconomyConfig, out_dir: Path, export_path: Path | None, jobs: int = 0,
          events: bool = True, with_suggestions: bool = True, quiet: bool = False,
          config_label: str = "config/economy.yaml") -> ReportData:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs or min(os.cpu_count() or 1, 8)

    _log(f"[1/6] simulating {len(cfg.strategies)} strategies x {cfg.simulation.days:g} days ...", quiet)
    results = {name: simulate(cfg, name) for name in cfg.strategies}

    _log("[2/6] analysing milestones, dead zones, income sources ...", quiet)
    ms = A.milestone_table(cfg, results)
    zones = pd.concat([A.dead_zones(cfg, r) for r in results.values()], ignore_index=True)
    zones["stage"] = zones["stage"].map(lambda c: A.stage_name(cfg, c))
    zsum = A.dead_zone_summary(cfg, zones, results)
    sources = A.income_sources(results)
    mix = A.response_mix(results)
    runs = pd.concat([A.run_table(cfg, r) for r in results.values()], ignore_index=True)
    ref = results[cfg.pacing.reference_strategy]
    stages = pd.concat([A.stage_lengths(cfg, r) for r in results.values()], ignore_index=True)

    impact = None
    if events and any(e.enabled for e in cfg.events):
        strategies = [cfg.pacing.reference_strategy] + [
            n for n, s in cfg.strategies.items()
            if cfg.profiles[s.profile].event_response != cfg.profiles[cfg.strategies[
                cfg.pacing.reference_strategy].profile].event_response][:1]
        n_runs = len(strategies) * (1 + len(cfg.simulation.extra_seeds)) * (2 + sum(e.enabled for e in cfg.events))
        _log(f"[3/6] NPC event ablation: {n_runs} runs on {jobs} workers ...", quiet)
        ev_runs = A.event_impact_runs(cfg, strategies, jobs=jobs)
        ev_runs.drop(columns=["sess_end_h", "sess_log_life"]).to_csv(out_dir / "event_impact_runs.csv", index=False)
        impact = A.event_impact_table(cfg, ev_runs)
    else:
        _log("[3/6] NPC event ablation skipped", quiet)

    _log("[4/6] pacing targets and tuning suggestions ...", quiet)
    checks = check_targets(cfg, ref)
    if with_suggestions:
        checks = suggest(cfg, checks)

    _log("[5/6] charts and tables ...", quiet)
    C.set_strategy_order(list(cfg.strategies))
    charts = {
        "income": C.income_chart(results, out_dir / "income_per_second.png"),
        "sources": C.income_sources_chart(sources, out_dir / "income_sources.png"),
        "milestones": C.milestones_chart(cfg, ms, out_dir / "milestones.png"),
        "dead_zones": C.dead_zones_chart(cfg, zones, list(results), out_dir / "dead_zones.png"),
        "payback": C.payback_chart(results, out_dir / "payback.png"),
    }
    if impact is not None:
        charts["events"] = C.event_impact_chart(impact, out_dir / "event_impact.png",
                                                  cfg.analysis.event_checkpoint_hours)
        impact.to_csv(out_dir / "event_impact.csv", index=False)
    ms.to_csv(out_dir / "milestones.csv", index=False)
    zones.to_csv(out_dir / "dead_zones.csv", index=False)
    zsum.to_csv(out_dir / "dead_zone_summary.csv", index=False)
    sources.to_csv(out_dir / "income_sources.csv", index=False)
    mix.to_csv(out_dir / "event_responses.csv", index=False)
    runs.to_csv(out_dir / "prestige_runs.csv", index=False)
    stages.to_csv(out_dir / "stage_lengths.csv", index=False)
    pd.DataFrame([{
        "milestone": c.milestone, "clock": c.clock, "mode": c.mode, "target_h": c.target_h,
        "actual_h": c.actual_h, "target_play_h": c.target_active_h, "actual_play_h": c.actual_active_h,
        "ratio": c.ratio, "status": c.status, "lever": c.lever, "lever_value": c.lever_value,
        "elasticity": c.elasticity, "suggested_factor": c.suggested_factor, "suggested_value": c.suggested_value,
        "note": c.note} for c in checks]).to_csv(out_dir / "pacing_targets.csv", index=False)
    for name, r in results.items():
        r.purchases.to_csv(out_dir / f"purchases_{name}.csv", index=False)
        r.timeseries.iloc[::6].to_csv(out_dir / f"timeseries_{name}.csv", index=False)
        if not r.events.empty:
            r.events.to_csv(out_dir / f"events_{name}.csv", index=False)

    recs = all_recommendations(cfg, results, checks, zones, zsum, sources, impact, mix, runs,
                               stages[stages["strategy"] == ref.strategy])
    pd.DataFrame([r.__dict__ for r in recs]).to_csv(out_dir / "recommendations.csv", index=False)

    _log("[6/6] writing REPORT.md and Godot export ...", quiet)
    if export_path is not None:
        export_json(cfg, export_path)
    data = ReportData(cfg, results, ms, zones, zsum, sources, impact, mix, runs, stages, checks, recs, charts,
                      time.time() - t0)
    (out_dir / "REPORT.md").write_text(render(data, config_label, export_path), encoding="utf-8")
    _log(f"done in {data.seconds:.0f} s -> {out_dir / 'REPORT.md'}", quiet)
    return data


# --------------------------------------------------------------------------- markdown


def _pct(v: float) -> str:
    return "—" if v is None or not np.isfinite(v) else f"{v:+.1f}%"


def _status_icon(s: str) -> str:
    return {"ok": "✅ ok", "too fast": "⚠️ too fast", "too slow": "⚠️ too slow",
            "not reached": "❌ not reached"}.get(s, s)


def render(d: ReportData, config_label: str, export_path: Path | None) -> str:
    cfg = d.cfg
    ref_name = cfg.pacing.reference_strategy
    ref = d.results[ref_name]
    L: list[str] = []
    w = L.append
    w(f"# {cfg.name} — economy balance report\n")
    w(f"Config `{config_label}` (economy version {cfg.version}) · seed {cfg.simulation.seed} · "
      f"{cfg.simulation.days:g} simulated days per strategy · reference strategy `{ref_name}`.  ")
    w("Regenerate with `python -m doner_balance report`. All numbers below come from the simulation; "
      "nothing in this file is hand-written.\n")

    # ---------------------------------------------------------------- TL;DR
    w("## What to tune first\n")
    actionable = [r for r in d.recs if r.action != "No change needed."]
    for i, r in enumerate(actionable[:3], 1):
        w(f"{i}. **{r.title}** ({r.area}) — {r.evidence}  \n   **Do:** {r.action}\n")
    if not actionable:
        w("Nothing urgent: every check is within its thresholds.\n")

    # ---------------------------------------------------------------- at a glance
    w("## Current balance at a glance\n")
    ts = ref.timeseries
    def income_at(day: float) -> str:
        x = ts[(ts["t_h"] <= day * 24) & ts["is_active"]]
        return fmt_num(x["active_income"].iloc[-1]) if len(x) else "—"
    st = d.stages[d.stages["strategy"] == ref_name]
    stage_txt = ", ".join(f"{r.stage} {fmt_h(r.calendar_h)} ({r.play_min:.0f} min play)" for r in st.itertuples())
    src = d.sources[(d.sources["strategy"] == ref_name) & (d.sources["day"] >= 2)]
    tot = src[["tap", "automation", "offline"]].sum()
    share = (100 * tot / tot.sum()) if tot.sum() > 0 else tot * np.nan
    zs = d.zone_summary.set_index("strategy").loc[ref_name]
    w(f"- **Milestones (`{ref_name}`):** " + ", ".join(
        f"{m} {fmt_h(ref.milestone_hours(m))}" for m in ["first_upgrade", "first_manager",
                                                          *[t.id for t in cfg.tiers[1:]], "first_prestige"]
        if ref.milestone_hours(m) is not None) + " (calendar time since install).")
    w(f"- **Stage lengths (run 1):** {stage_txt}.")
    w(f"- **Income while playing:** end of day 1 {income_at(1)}/s, day 3 {income_at(3)}/s, day 7 {income_at(7)}/s, "
      f"day 14 {income_at(14)}/s (resets after each prestige).")
    w(f"- **Where coins come from (day 2+):** taps {share['tap']:.1f}%, automation while playing "
      f"{share['automation']:.1f}%, offline {share['offline']:.1f}%.")
    w(f"- **Dead zones:** {int(zs['dead_zones'])} waits > {cfg.analysis.dead_zone_threshold_active_minutes:g} min of "
      f"play, {zs['share_of_play_pct']:.0f}% of all play time, longest {zs['longest_min']:.0f} min.")
    n_ok = sum(c.status == "ok" for c in d.checks)
    w(f"- **Pacing targets met:** {n_ok}/{len(d.checks)}.")
    pr = d.runs[d.runs["strategy"] == ref_name]
    w(f"- **Prestiges in {cfg.simulation.days:g} days:** {int(ref.final['prestiges'])}, "
      f"reputation {int(ref.final['reputation'])} (x{1 + ref.final['reputation'] * cfg.prestige.bonus_per_point:.2f} income).\n")

    # ---------------------------------------------------------------- milestones
    w("## 1. Milestones per strategy\n")
    w("Calendar time since install (play time in brackets). Strategies:\n")
    for n, s in cfg.strategies.items():
        p = cfg.profiles[s.profile]
        w(f"- `{n}` — {s.description} Profile `{s.profile}`: {p.sessions_per_day}×{p.session_minutes:g} min/day, "
          f"{p.taps_per_second * p.tapping_share:g} taps/s on average, {p.event_response} NPC answers.")
    w("")
    order = list(dict.fromkeys(d.milestones["milestone"]))
    rows = []
    for n in d.results:
        sub = d.milestones[d.milestones["strategy"] == n].set_index("milestone")
        row = {"strategy": n}
        for m in order:
            v = sub.loc[m]
            row[m] = "—" if not v["reached"] else f"{fmt_h(v['calendar_h'])} ({fmt_h(v['active_min'] / 60)})"
        rows.append(row)
    w(md_table(pd.DataFrame(rows)))
    w(f"\n![milestones]({d.charts['milestones'].name})\n")

    # ---------------------------------------------------------------- pacing
    w("## 2. Pacing vs targets\n")
    w(f"Targets live in `pacing.targets` of the config and are checked against `{ref_name}`. Calendar targets are "
      "judged on the equivalent play time (sessions quantize calendar time). The suggestion column comes from "
      "re-simulating with the lever scaled and measuring the elasticity.\n")
    rows = []
    for c in d.checks:
        sugg = "—"
        if c.suggested_factor is not None and c.suggested_factor != 1.0:
            sugg = f"x{c.suggested_factor:.2g} → {fmt_num(c.suggested_value)}"
        elif c.note:
            sugg = c.note
        rows.append({"milestone": c.milestone, "target": f"{fmt_h(c.target_h)} {c.clock} ({c.mode})",
                     "actual": fmt_h(c.actual_h) + (f" ({fmt_h(c.actual_active_h)} play)" if c.clock == "calendar" else ""),
                     "status": _status_icon(c.status), "lever": f"`{c.lever}`",
                     "lever value": fmt_num(c.lever_value), "suggestion": sugg})
    w(md_table(pd.DataFrame(rows)))
    w("\n`python -m doner_balance tune` prints this table; `tune --apply` searches all levers sequentially and "
      "writes an overrides file.\n")

    # ---------------------------------------------------------------- dead zones
    w("## 3. Dead zones\n")
    w(f"A *dead zone* is a stretch of **play time** longer than "
      f"`analysis.dead_zone_threshold_active_minutes` = {cfg.analysis.dead_zone_threshold_active_minutes:g} min "
      f"in which the player's purchases do not add up to *meaningful progress*: "
      f"+{cfg.analysis.meaningful_gain_pct:g}% income in total, a new tier or a prestige. Offline time does not count.\n")
    w(md_table(d.zone_summary, {"total_dead_min": lambda v: f"{v:.0f}", "share_of_play_pct": lambda v: f"{v:.0f}%",
                                 "longest_min": lambda v: f"{v:.0f}",
                                 "longest_starts_day": lambda v: "—" if not np.isfinite(v) else f"{v:.1f}"}))
    top = d.zones[d.zones["strategy"] == ref_name].sort_values("gap_active_min", ascending=False).head(8)
    if len(top):
        w(f"\nLongest dead zones for `{ref_name}`:\n")
        w(md_table(top[["start_day", "gap_active_min", "gap_calendar_h", "stage", "ended_by", "small_buys",
                        "most_bought"]],
                   {"start_day": lambda v: f"{v:.1f}", "gap_active_min": lambda v: f"{v:.0f} min",
                    "gap_calendar_h": lambda v: fmt_h(v)}))
    w(f"\n![dead zones]({d.charts['dead_zones'].name})\n")
    w(f"![payback]({d.charts['payback'].name})\n")
    w("The payback chart shows how long each purchase takes to pay for itself in real time. A healthy idle curve "
      "rises slowly and steadily; flat clouds far above the rest are walls.\n")

    # ---------------------------------------------------------------- income
    w("## 4. Income over time and its sources\n")
    w(f"![income per second]({d.charts['income'].name})\n")
    w(f"![income sources]({d.charts['sources'].name})\n")
    rows = []
    for n, r in d.results.items():
        s = d.sources[(d.sources["strategy"] == n) & (d.sources["day"] >= 2)]
        t = s[["tap", "automation", "offline"]].sum()
        tt = t.sum() or float("nan")
        rows.append({"strategy": n, "taps": f"{100 * t['tap'] / tt:.1f}%",
                     "automation (playing)": f"{100 * t['automation'] / tt:.1f}%",
                     "offline": f"{100 * t['offline'] / tt:.1f}%",
                     "taps share of active income": f"{A.tap_share_of_active(r, 24):.0f}%",
                     "final active income/s": fmt_num(r.final["active_income"])})
    w("Share of coins earned from day 2 on:\n")
    w(md_table(pd.DataFrame(rows)))
    w("")

    # ---------------------------------------------------------------- events
    w("## 5. NPC events\n")
    if d.impact is not None:
        n_seeds = 1 + len(cfg.simulation.extra_seeds)
        w(f"Ablation study over {n_seeds} seeds: each event type is switched off on its own and the run is compared "
          "with the run where it is on. Every event type has its own random stream, so switching one off does not "
          "change the others.\n")
        chk = cfg.analysis.event_checkpoint_hours
        w(f"- **shift** — hours of progress the event costs by hour {chk:g}: the run without the event reached the "
          "same all-time earnings that much earlier (negative = the event speeds you up). **delay %** = shift / "
          f"{chk:g} h. This is the headline number.")
        last = cfg.tiers[-1].id
        w(f"- **{last} play** — extra play time to reach the {last} (coarse: purchases happen in sessions).")
        w(f"- **earnings** — all-time earnings at hour {chk:g} with vs without (noisy: earnings grow exponentially).")
        w("- **net** — direct accounting (bonus income − lost income − answer costs − cash losses) / final earnings.\n")
        imp = d.impact.rename(columns={"delay_play_min": f"{last} play", "earnings_change_pct": "earnings",
                                       "net_pct_of_lifetime": "net", "delay_pct": "delay %",
                                       "fired_per_run": "fired/run", "shift_h": "shift"})
        cols = ["strategy", "event", "fired/run", "shift", "delay %", f"{last} play", "earnings", "net"]
        w(md_table(imp[cols], {"fired/run": lambda v: "—" if not np.isfinite(v) else f"{v:.1f}",
                               "shift": lambda v: f"{v:+.1f} h" if np.isfinite(v) else "—",
                               f"{last} play": lambda v: f"{v:+.0f} min" if np.isfinite(v) else "—",
                               "delay %": _pct, "earnings": _pct, "net": _pct}))
        w(f"\n![event impact]({d.charts['events'].name})\n")
    w("Answers chosen (main runs):\n")
    if len(d.mix):
        piv = d.mix.pivot_table(index=["event", "response"], columns="strategy", values="count",
                                fill_value=0).reset_index()
        w(md_table(piv, {c: (lambda v: f"{int(v)}") for c in piv.columns if c not in ("event", "response")}))
    w("")

    # ---------------------------------------------------------------- prestige
    w("## 6. Prestige runs\n")
    w(f"Hours after the start of each run (a run starts at install or at a prestige reset). "
      f"Selling the empire needs `{cfg.prestige.required_tier}` and gives "
      f"`floor({cfg.prestige.scale:g} * (all_time / {fmt_num(cfg.prestige.base_requirement)}) ^ "
      f"{cfg.prestige.exponent:g})` reputation, +{100 * cfg.prestige.bonus_per_point:g}% income each.\n")
    rr = d.runs.copy()
    fmts = {c: (lambda v: fmt_h(v)) for c in rr.columns if c.endswith("_h")}
    w(md_table(rr, fmts))
    w("")

    # ---------------------------------------------------------------- all recommendations
    w("## 7. All findings and recommendations\n")
    w("Sorted by severity (0–100). Rules live in `doner_balance/recommend.py`; thresholds in `analysis:` of the config.\n")
    for r in d.recs:
        w(f"- **[{r.severity:.0f}] {r.title}** ({r.area})  \n  {r.evidence}  \n  → {r.action}")
    w("")

    # ---------------------------------------------------------------- summary
    w("## Summary\n")
    w(summary_text(d))
    w("")

    # ---------------------------------------------------------------- files
    w("## Files\n")
    w("| file | content |\n|---|---|")
    files = [
        ("milestones.csv", "time to every milestone per strategy (calendar h, play min)"),
        ("pacing_targets.csv", "targets, actual values, lever, elasticity, suggestion"),
        ("dead_zones.csv / dead_zone_summary.csv", "every dead zone and per-strategy totals"),
        ("income_sources.csv", "coins per day from taps / automation / offline"),
        ("stage_lengths.csv", "time spent per tier in run 1"),
        ("prestige_runs.csv", "tier times inside each prestige run"),
        ("event_impact.csv / event_impact_runs.csv", "NPC ablation summary and raw runs"),
        ("event_responses.csv, events_<strategy>.csv", "NPC answers chosen and full event logs"),
        ("purchases_<strategy>.csv", "every purchase with cost, income gain and payback"),
        ("timeseries_<strategy>.csv", "state every 30 min: cash, incomes, tier, reputation"),
        ("recommendations.csv", "the findings above as a table"),
    ]
    for f, c in files:
        w(f"| `{f}` | {c} |")
    if export_path is not None:
        w(f"| `{export_path}` | Godot export of this config (see `docs/GODOT_EXPORT.md`) |")
    w(f"\n_Generated in {d.seconds:.0f} s._\n")
    return "\n".join(L)


def summary_text(d: ReportData) -> str:
    """Closing prose summary: how the balance looks and what to tune first (generated)."""
    cfg = d.cfg
    ref_name = cfg.pacing.reference_strategy
    ref = d.results[ref_name]
    out: list[str] = []
    ok = [c for c in d.checks if c.status == "ok"]
    off = [c for c in d.checks if c.status != "ok"]
    ms = ", ".join(f"{c.milestone} at {fmt_h(c.actual_h)}" for c in d.checks if c.actual_h is not None)
    out.append(f"**Pacing.** {len(ok)} of {len(d.checks)} pacing targets are met for `{ref_name}` ({ms})."
               + (" Off target: " + ", ".join(f"`{c.milestone}` ({c.status})" for c in off) + "." if off else ""))
    st = d.stages[d.stages["strategy"] == ref_name]
    if len(st):
        longest = st.loc[st["calendar_h"].idxmax()]
        path = " → ".join(t.id for t in cfg.tiers)
        out.append(f"**Shape.** The first run is {path}; the longest stage is "
                   f"**{longest['stage']}** ({fmt_h(longest['calendar_h'])}, {longest['play_min']:.0f} min of play).")
    src = d.sources[(d.sources["strategy"] == ref_name) & (d.sources["day"] >= 2)]
    tot = src[["tap", "automation", "offline"]].sum()
    if tot.sum() > 0:
        out.append(f"**Income.** From day 2 on, {100 * tot['offline'] / tot.sum():.0f}% of coins come from offline "
                   f"earnings, {100 * tot['automation'] / tot.sum():.0f}% from automation while playing and "
                   f"{100 * tot['tap'] / tot.sum():.0f}% from taps (taps are "
                   f"{A.tap_share_of_active(ref, 24):.0f}% of what is earned during a session).")
    zs = d.zone_summary.set_index("strategy").loc[ref_name]
    out.append(f"**Dead zones.** {zs['share_of_play_pct']:.0f}% of play time passes without +"
               f"{cfg.analysis.meaningful_gain_pct:g}% progress; the longest wait is {zs['longest_min']:.0f} min of play.")
    others = []
    for n, r in d.results.items():
        if n == ref_name:
            continue
        f = r.milestone_hours(cfg.tiers[-1].id)
        others.append(f"`{n}` {fmt_h(f) if f is not None else 'never'}")
    out.append(f"**Other players.** Time to {cfg.tiers[-1].id}: `{ref_name}` "
               f"{fmt_h(ref.milestone_hours(cfg.tiers[-1].id))}, " + ", ".join(others) + ".")
    if d.impact is not None:
        imp = d.impact[(d.impact["strategy"] == ref_name) & (d.impact["event"] != "all events")]
        if len(imp):
            big = imp.loc[imp["delay_pct"].abs().idxmax()]
            allrow = d.impact[(d.impact["strategy"] == ref_name) & (d.impact["event"] == "all events")]
            out.append(f"**NPCs.** All events together shift `{ref_name}` by {allrow['shift_h'].iloc[0]:+.1f} h by hour "
                       f"{cfg.analysis.event_checkpoint_hours:g}; the strongest single NPC is `{big['event']}` "
                       f"({big['shift_h']:+.1f} h).")
    actionable = [r for r in d.recs if r.action != "No change needed."][:3]
    if actionable:
        out.append("**Tune first:** " + " ".join(f"({i}) {r.title}." for i, r in enumerate(actionable, 1))
                   + " Details and concrete values are at the top of this report.")
    return "\n\n".join(out)
