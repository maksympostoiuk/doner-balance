"""Turn simulation results into tables: milestones, dead zones, income sources, event impact."""

from __future__ import annotations

import itertools
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EconomyConfig
from .simulator import SimResult, milestone_names, simulate

# --------------------------------------------------------------------------- milestones


def milestone_table(cfg: EconomyConfig, results: dict[str, SimResult]) -> pd.DataFrame:
    """One row per (strategy, milestone) with calendar and play time."""
    order = milestone_names(cfg)
    rows = []
    for name, r in results.items():
        for m in order:
            v = r.milestones.get(m)
            rows.append(
                {
                    "strategy": name,
                    "milestone": m,
                    "calendar_h": v[0] if v else np.nan,
                    "active_min": v[1] * 60 if v else np.nan,
                    "day": v[0] / 24 if v else np.nan,
                    "reached": v is not None,
                }
            )
    return pd.DataFrame(rows)


def milestone_pivot(table: pd.DataFrame, value: str = "calendar_h") -> pd.DataFrame:
    order = list(dict.fromkeys(table["milestone"]))
    p = table.pivot(index="strategy", columns="milestone", values=value)
    return p[order]


# --------------------------------------------------------------------------- dead zones


def meaningful_purchases(cfg: EconomyConfig, purchases: pd.DataFrame) -> pd.DataFrame:
    """Purchases that complete a step of real progress.

    Purchase gains accumulate (multiplicatively) until together they raise the effective
    income by ``analysis.meaningful_gain_pct`` percent; the purchase that crosses the
    threshold is "meaningful" and the counter resets. Tier unlocks and prestiges always
    count. So ten +0.5% purchases equal one +5% purchase: what matters is progress
    velocity, not item size.
    """
    if purchases.empty:
        return purchases
    thr = math.log1p(cfg.analysis.meaningful_gain_pct / 100.0)
    acc = 0.0
    keep = []
    for row in purchases.itertuples():
        if row.kind in ("tier", "prestige"):
            keep.append(True)
            acc = 0.0
            continue
        if row.rate_before > 0 and row.rate_after > row.rate_before:
            acc += math.log(row.rate_after / row.rate_before)
        if acc >= thr:
            keep.append(True)
            acc = 0.0
        else:
            keep.append(False)
    return purchases[np.array(keep)]


def dead_zones(cfg: EconomyConfig, r: SimResult) -> pd.DataFrame:
    """Gaps (in play time) between meaningful purchases longer than the threshold.

    See :func:`meaningful_purchases`: a step of progress is +``meaningful_gain_pct``%
    effective income (cumulative over purchases), a tier unlock or a prestige.
    The open gap at the end of the run is included (``ended_by`` = "(end of run)").
    """
    thr_min = cfg.analysis.dead_zone_threshold_active_minutes
    p = r.purchases
    mp = meaningful_purchases(cfg, p)
    end_active_h = r.final["active_hours"]
    end_cal_h = r.timeseries["t_h"].iloc[-1] if not r.timeseries.empty else 0.0
    points: list[tuple[float, float, str]] = [(0.0, 0.0, "(start)")]
    for row in mp.itertuples():
        points.append((float(row.active_h), float(row.t_h), str(row.item)))
    points.append((float(end_active_h), float(end_cal_h), "(end of run)"))
    tiers_at = _tier_lookup(r)
    rows = []
    for (a0, c0, _), (a1, c1, item) in itertools.pairwise(points):
        gap = (a1 - a0) * 60.0
        if gap <= thr_min:
            continue
        inside = p[(p["active_h"] > a0) & (p["active_h"] < a1)]
        top = inside["item"].value_counts()
        rows.append(
            {
                "strategy": r.strategy,
                "start_calendar_h": c0,
                "end_calendar_h": c1,
                "start_day": c0 / 24.0,
                "start_active_h": a0,
                "gap_active_min": gap,
                "gap_calendar_h": c1 - c0,
                "stage": tiers_at(c0),
                "ended_by": item,
                "small_buys": len(inside),
                "most_bought": top.index[0] if len(top) else "",
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "strategy",
            "start_calendar_h",
            "end_calendar_h",
            "start_day",
            "start_active_h",
            "gap_active_min",
            "gap_calendar_h",
            "stage",
            "ended_by",
            "small_buys",
            "most_bought",
        ],
    )


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
        rows.append(
            {
                "strategy": name,
                "dead_zones": len(z),
                "total_dead_min": z["gap_active_min"].sum(),
                "share_of_play_pct": 100.0 * z["gap_active_min"].sum() / total_play if total_play else 0.0,
                "longest_min": z["gap_active_min"].max() if len(z) else 0.0,
                "longest_starts_day": z.loc[z["gap_active_min"].idxmax(), "start_day"] if len(z) else np.nan,
            }
        )
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
            rows.append(
                {
                    "strategy": name,
                    "day": int(d) + 1,
                    "tap": row["earned_tap"],
                    "automation": row["earned_auto"],
                    "offline": row["earned_offline"],
                    "tap_pct": 100 * row["earned_tap"] / tot if tot > 0 else np.nan,
                    "automation_pct": 100 * row["earned_auto"] / tot if tot > 0 else np.nan,
                    "offline_pct": 100 * row["earned_offline"] / tot if tot > 0 else np.nan,
                }
            )
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
    out = {
        "strategy": job.strategy,
        "seed": job.seed,
        "variant": _variant(job.disabled, cfg),
        "log10_lifetime": math.log10(max(r.final["lifetime_total"], 1.0)),
        "reputation": r.final["reputation"],
    }
    for m in milestone_names(cfg):
        out[m] = r.milestone_hours(m)
        out[f"{m}__active"] = r.milestone_hours(m, "active")
    ts = r.timeseries
    chk = ts[ts["t_h"] <= cfg.analysis.event_checkpoint_hours]
    out["log10_lifetime_at_check"] = math.log10(max(chk["lifetime_total"].iloc[-1], 1.0)) if len(chk) else 0.0
    sess = r.sessions
    out["sess_end_h"] = sess["end_h"].to_numpy()
    out["sess_log_life"] = np.log10(np.maximum(sess["lifetime_total"].to_numpy(), 1.0))
    out["hours_per_session"] = 24.0 / cfg.profiles[cfg.strategies[job.strategy].profile].sessions_per_day
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
    variants: list[tuple[str, ...]] = list(dict.fromkeys([(), tuple(ids), *[(i,) for i in ids]]))
    cfg_json = cfg.model_dump_json()
    work = [_Job(cfg_json, s, seed, v) for s in strategies for seed in seeds for v in variants]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            rows = list(ex.map(_run_job, work))
    else:
        rows = [_run_job(j) for j in work]
    return pd.DataFrame(rows)


def progress_shift_h(with_row: pd.Series, without_row: pd.Series, checkpoint_h: float) -> float:
    """Hours of progress an event costs by the checkpoint (positive = the event slows you down).

    Progress is sampled at the end of every session (offline earnings arrive as a lump at
    session start, so sampling inside the day would just measure session timing). Take the
    all-time earnings of the run *with* the event after the last session before the checkpoint,
    find (log-linear interpolation between sessions) at which fractional session the run
    *without* the event had the same earnings, and convert the session difference to hours.
    """
    ends = with_row["sess_end_h"]
    k = int(np.searchsorted(ends, checkpoint_h, side="right")) - 1
    if k < 0:
        return float("nan")
    target = with_row["sess_log_life"][k]
    y = np.maximum.accumulate(without_row["sess_log_life"])
    if target > y[-1] or len(y) < 2:
        return float("nan")
    k_without = float(np.interp(target, y, np.arange(len(y))))
    return (k - k_without) * with_row["hours_per_session"]


def event_impact_table(cfg: EconomyConfig, runs: pd.DataFrame, milestone: str | None = None) -> pd.DataFrame:
    """Per strategy and event: how much the event changes progress (median over seeds).

    Ablation: compare runs with all events against runs with that one event disabled
    (same seeds; every event has its own RNG stream so the others are unaffected).

    * ``shift_h``: hours of progress the event costs at the checkpoint
      (``analysis.event_checkpoint_hours``), see :func:`progress_shift_h`; positive = slower.
    * ``delay_pct``: ``shift_h`` relative to the checkpoint (the headline number).
    * ``delay_play_min``: extra play time to reach ``milestone`` (default: the last tier;
      coarse, because purchases happen in sessions).
    * ``earnings_change_pct``: change of all-time earnings at the checkpoint (noisy, because
      earnings grow exponentially).
    * ``net_pct_of_lifetime``: direct accounting of the event (income gained/lost through
      its modifiers + cash gained - response costs - cash lost) relative to final earnings.
    """
    milestone = milestone or cfg.tiers[-1].id  # default: the last tier (franchise)
    rows = []
    ids = [e.id for e in cfg.events if e.enabled]
    for strat, g in runs.groupby("strategy"):
        base = g[g["variant"] == "all events"].set_index("seed")
        for eid in [*ids, "ALL"]:
            # with a single event type, "without it" and "no events" are the same runs
            name = "no events" if eid == "ALL" or len(ids) == 1 else f"without {eid}"
            alt = g[g["variant"] == name].set_index("seed")
            seeds = base.index.intersection(alt.index)
            col = f"{milestone}__active"
            with_ = base.loc[seeds, col].astype(float)
            without = alt.loc[seeds, col].astype(float)
            delay = with_ - without
            life = 100.0 * (
                10 ** (base.loc[seeds, "log10_lifetime_at_check"] - alt.loc[seeds, "log10_lifetime_at_check"]) - 1.0
            )
            chk = cfg.analysis.event_checkpoint_hours
            shifts = pd.Series([progress_shift_h(base.loc[sd], alt.loc[sd], chk) for sd in seeds], dtype=float)
            row = {
                "strategy": strat,
                "event": eid if eid != "ALL" else "all events",
                "shift_h": float(shifts.median()),
                "delay_pct": float((100 * shifts / chk).median()),
                "milestone": milestone,
                "milestone_day_with": float(base.loc[seeds, milestone].astype(float).median() / 24),
                "milestone_day_without": float(alt.loc[seeds, milestone].astype(float).median() / 24),
                "delay_play_min": float((60 * delay).median()),
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
    return (
        pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["strategy", "event", "response", "count"])
    )


# --------------------------------------------------------------------------- stages & prestige


def run_table(cfg: EconomyConfig, r: SimResult) -> pd.DataFrame:
    """One row per prestige run: when each tier was reached (hours after the run started)."""
    p = r.purchases
    prest = p[p["kind"] == "prestige"]
    # the prestige row's "level" is the reputation owned after the reset
    gained = prest["level"].astype(float).diff().fillna(prest["level"].astype(float))
    starts = [0.0, *prest["t_h"].tolist()]
    rows = []
    for k, start in enumerate(starts):
        row = {"strategy": r.strategy, "run": k + 1, "start_h": start}
        tiers = p[(p["kind"] == "tier") & (p["run"] == k)]
        for t in cfg.tiers[1:]:
            hit = tiers[tiers["item"] == t.id]
            row[f"{t.id}_after_h"] = float(hit["t_h"].iloc[0] - start) if len(hit) else np.nan
        end = prest[prest["run"] == k + 1]
        row["prestige_after_h"] = float(end["t_h"].iloc[0] - start) if len(end) else np.nan
        row["reputation_gained"] = int(gained.loc[end.index[0]]) if len(end) else 0
        rows.append(row)
    return pd.DataFrame(rows)


def stage_lengths(cfg: EconomyConfig, r: SimResult) -> pd.DataFrame:
    """Calendar and play time spent in each tier during the first run."""
    names = [t.id for t in cfg.tiers]
    marks = [(0.0, 0.0)]
    for t in cfg.tiers[1:]:
        m = r.milestones.get(t.id)
        if m is None:
            break
        marks.append(m)
    end = r.milestones.get("first_prestige") or (r.timeseries["t_h"].iloc[-1], r.final["active_hours"])
    marks.append(end)
    rows = []
    for i in range(len(marks) - 1):
        rows.append(
            {
                "strategy": r.strategy,
                "stage": names[i],
                "calendar_h": marks[i + 1][0] - marks[i][0],
                "play_min": 60 * (marks[i + 1][1] - marks[i][1]),
                "ends_with": names[i + 1]
                if i + 1 < len(names) and cfg.tiers[i + 1].id in r.milestones
                else ("first_prestige" if "first_prestige" in r.milestones else "(end of run)"),
            }
        )
    return pd.DataFrame(rows)
