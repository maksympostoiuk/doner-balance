"""Turn simulation results into tables: milestones, dead zones, income sources, event impact."""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EconomyConfig
from .simulator import MILESTONES, SimResult, simulate

# --------------------------------------------------------------------------- milestones


def milestone_table(cfg: EconomyConfig, results: dict[str, SimResult]) -> pd.DataFrame:
    """One row per (strategy, milestone) with calendar and play time."""
    order = ["first_upgrade", "first_manager", *[t.id for t in cfg.tiers[1:]], "first_prestige"]
    rows = []
    for name, r in results.items():
        for m in order:
            v = r.milestones.get(m)
            rows.append({
                "strategy": name, "milestone": m,
                "calendar_h": v[0] if v else np.nan,
                "active_min": v[1] * 60 if v else np.nan,
                "day": v[0] / 24 if v else np.nan,
                "reached": v is not None,
            })
    return pd.DataFrame(rows)


def milestone_pivot(table: pd.DataFrame, value: str = "calendar_h") -> pd.DataFrame:
    order = list(dict.fromkeys(table["milestone"]))
    p = table.pivot(index="strategy", columns="milestone", values=value)
    return p[order]


# --------------------------------------------------------------------------- dead zones


def meaningful_purchases(cfg: EconomyConfig, purchases: pd.DataFrame) -> pd.DataFrame:
    thr = cfg.analysis.meaningful_gain_pct
    if purchases.empty:
        return purchases
    m = purchases["kind"].isin(["tier", "prestige"]) | (purchases["gain_pct"] >= thr)
    return purchases[m]


def dead_zones(cfg: EconomyConfig, r: SimResult) -> pd.DataFrame:
    """Gaps (in play time) between meaningful purchases longer than the threshold.

    A purchase is meaningful if it raises the effective income by at least
    ``analysis.meaningful_gain_pct`` percent, unlocks a tier or is a prestige.
    The open gap at the end of the run is included (``ended_by`` = "(end of run)").
    """
    thr_min = cfg.analysis.dead_zone_threshold_active_minutes
    p = r.purchases
    mp = meaningful_purchases(cfg, p)
    end_active_h = r.final["active_hours"]
    end_cal_h = r.timeseries["t_h"].iloc[-1] if not r.timeseries.empty else 0.0
    points = [(0.0, 0.0, "(start)", 1)]
    for row in mp.itertuples():
        points.append((row.active_h, row.t_h, row.item, None))
    points.append((end_active_h, end_cal_h, "(end of run)", None))
    tiers_at = _tier_lookup(r)
    rows = []
    for (a0, c0, _, _), (a1, c1, item, _) in zip(points[:-1], points[1:]):
        gap = (a1 - a0) * 60.0
        if gap <= thr_min:
            continue
        inside = p[(p["active_h"] > a0) & (p["active_h"] < a1)]
        top = inside["item"].value_counts()
        rows.append({
            "strategy": r.strategy,
            "start_calendar_h": c0, "end_calendar_h": c1,
            "start_day": c0 / 24.0,
            "start_active_h": a0,
            "gap_active_min": gap,
            "gap_calendar_h": c1 - c0,
            "stage": tiers_at(c0),
            "ended_by": item,
            "small_buys": int(len(inside)),
            "most_bought": top.index[0] if len(top) else "",
        })
    return pd.DataFrame(rows, columns=[
        "strategy", "start_calendar_h", "end_calendar_h", "start_day", "start_active_h", "gap_active_min",
        "gap_calendar_h", "stage", "ended_by", "small_buys", "most_bought"])


def _tier_lookup(r: SimResult):
    ts = r.timeseries[["t_h", "tiers_owned", "prestiges"]].to_numpy()

    def f(t_h: float) -> str:
        idx = int(np.searchsorted(ts[:, 0], t_h, side="right")) - 1
        idx = max(idx, 0)
        return f"{int(ts[idx, 1])}|{int(ts[idx, 2])}"

    return f


def dead_zone_summary(cfg: EconomyConfig, zones: pd.DataFrame, results: dict[str, SimResult]) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        z = zones[zones["strategy"] == name]
        total_play = r.final["active_hours"] * 60.0
        rows.append({
            "strategy": name,
            "dead_zones": len(z),
            "total_dead_min": z["gap_active_min"].sum(),
            "share_of_play_pct": 100.0 * z["gap_active_min"].sum() / total_play if total_play else 0.0,
            "longest_min": z["gap_active_min"].max() if len(z) else 0.0,
            "longest_starts_day": z.loc[z["gap_active_min"].idxmax(), "start_day"] if len(z) else np.nan,
        })
    return pd.DataFrame(rows)


def stage_name(cfg: EconomyConfig, code: str) -> str:
    """'3|0' -> 'restaurant (run 1)'."""
    tiers, prest = (int(x) for x in code.split("|"))
    return f"{cfg.tiers[tiers - 1].id} (run {prest + 1})"


# --------------------------------------------------------------------------- income sources


def income_sources(results: dict[str, SimResult]) -> pd.DataFrame:
    """Coins earned per calendar day by source (tap / automation while playing / offline)."""
    rows = []
    for name, r in results.items():
        ts = r.timeseries
        day = np.floor(ts["day"]).astype(int)
        last = ts.groupby(day)[["earned_tap", "earned_auto", "earned_offline"]].last()
        inc = last.diff()
        inc.iloc[0] = last.iloc[0]
        for d, row in inc.iterrows():
            tot = row.sum()
            if tot <= 0:
                continue
            rows.append({
                "strategy": name, "day": int(d) + 1,
                "tap": row["earned_tap"], "automation": row["earned_auto"], "offline": row["earned_offline"],
                "tap_pct": 100 * row["earned_tap"] / tot if tot > 0 else np.nan,
                "automation_pct": 100 * row["earned_auto"] / tot if tot > 0 else np.nan,
                "offline_pct": 100 * row["earned_offline"] / tot if tot > 0 else np.nan,
            })
    return pd.DataFrame(rows)


def tap_share_of_active(r: SimResult, after_h: float = 0.0) -> float:
    """Share of *active-play* income that came from taps after ``after_h`` calendar hours (%)."""
    ts = r.timeseries
    a = ts[ts["t_h"] >= after_h]
    if a.empty:
        return float("nan")
    tap = a["earned_tap"].iloc[-1] - a["earned_tap"].iloc[0]
    auto = a["earned_auto"].iloc[-1] - a["earned_auto"].iloc[0]
    return 100.0 * tap / (tap + auto) if tap + auto > 0 else float("nan")


# --------------------------------------------------------------------------- event impact


@dataclass(frozen=True)
class _Job:
    cfg_json: str
    strategy: str
    seed: int
    disabled: tuple[str, ...]


def _run_job(job: _Job) -> dict:
    cfg = EconomyConfig.model_validate_json(job.cfg_json)
    r = simulate(cfg, job.strategy, seed=job.seed, disabled_events=job.disabled)
    out = {"strategy": job.strategy, "seed": job.seed, "variant": _variant(job.disabled, cfg),
           "log10_lifetime": math.log10(max(r.final["lifetime_total"], 1.0)),
           "reputation": r.final["reputation"]}
    for m in MILESTONES:
        out[m] = r.milestone_hours(m)
        out[f"{m}__active"] = r.milestone_hours(m, "active")
    ts = r.timeseries
    chk = ts[ts["t_h"] <= cfg.analysis.event_checkpoint_hours]
    out["log10_lifetime_at_check"] = math.log10(max(chk["lifetime_total"].iloc[-1], 1.0)) if len(chk) else 0.0
    for eid, s in r.event_stats.items():
        out[f"{eid}__fired"] = s.fired
        out[f"{eid}__net"] = s.net
        out[f"{eid}__spent"] = s.spent
        out[f"{eid}__lost"] = s.cash_lost
        out[f"{eid}__income_delta"] = s.income_delta
    out["lifetime"] = r.final["lifetime_total"]
    return out


def _variant(disabled: tuple[str, ...], cfg: EconomyConfig) -> str:
    all_ids = tuple(sorted(e.id for e in cfg.events if e.enabled))
    if not disabled:
        return "all events"
    if tuple(sorted(disabled)) == all_ids:
        return "no events"
    return "without " + ",".join(disabled)


def event_impact_runs(cfg: EconomyConfig, strategies: list[str], jobs: int = 1) -> pd.DataFrame:
    """Run baseline / no-events / each-event-disabled for every seed (ablation study)."""
    seeds = [cfg.simulation.seed, *cfg.simulation.extra_seeds]
    ids = [e.id for e in cfg.events if e.enabled]
    variants: list[tuple[str, ...]] = [(), tuple(ids)] + [(i,) for i in ids]
    cfg_json = cfg.model_dump_json()
    work = [_Job(cfg_json, s, seed, v) for s in strategies for seed in seeds for v in variants]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            rows = list(ex.map(_run_job, work))
    else:
        rows = [_run_job(j) for j in work]
    return pd.DataFrame(rows)


def event_impact_table(cfg: EconomyConfig, runs: pd.DataFrame, milestone: str = "franchise") -> pd.DataFrame:
    """Per strategy and event: how much the event changes progress (median over seeds).

    Ablation: compare runs with all events against runs with that one event disabled
    (same seeds; every event has its own RNG stream so the others are unaffected).

    * ``delay_play_min``: play time to ``milestone`` with the event minus without it
      (positive = the event slows you down). Play time is used because calendar time is
      quantized by sessions.
    * ``delay_pct``: the same, relative to the time without the event.
    * ``earnings_change_pct``: change of all-time earnings at the checkpoint
      (``analysis.event_checkpoint_hours``) caused by the event.
    * ``net_pct_of_lifetime``: direct accounting of the event (income gained/lost through
      its modifiers + cash gained - response costs - cash lost) relative to final earnings.
    """
    rows = []
    ids = [e.id for e in cfg.events if e.enabled]
    for strat, g in runs.groupby("strategy"):
        base = g[g["variant"] == "all events"].set_index("seed")
        for eid in ids + ["ALL"]:
            name = "no events" if eid == "ALL" else f"without {eid}"
            alt = g[g["variant"] == name].set_index("seed")
            seeds = base.index.intersection(alt.index)
            col = f"{milestone}__active"
            with_ = base.loc[seeds, col].astype(float)
            without = alt.loc[seeds, col].astype(float)
            delay = with_ - without
            life = 100.0 * (10 ** (base.loc[seeds, "log10_lifetime_at_check"]
                                   - alt.loc[seeds, "log10_lifetime_at_check"]) - 1.0)
            row = {
                "strategy": strat, "event": eid if eid != "ALL" else "all events",
                f"{milestone}_day_with": float(base.loc[seeds, milestone].median() / 24),
                f"{milestone}_day_without": float(alt.loc[seeds, milestone].median() / 24),
                "delay_play_min": float((60 * delay).median()),
                "delay_pct": float((100 * delay / without).median()),
                "earnings_change_pct": float(life.median()),
            }
            if eid != "ALL":
                row["fired_per_run"] = float(base[f"{eid}__fired"].mean())
                row["net_pct_of_lifetime"] = float((100 * base[f"{eid}__net"] / base["lifetime"]).median())
            rows.append(row)
    return pd.DataFrame(rows)


def response_mix(results: dict[str, SimResult]) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        if r.events.empty:
            continue
        c = r.events.groupby(["event", "response"]).size().reset_index(name="count")
        c.insert(0, "strategy", name)
        rows.append(c)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["strategy", "event", "response", "count"])
