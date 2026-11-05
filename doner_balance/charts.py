"""PNG charts for the report (matplotlib, Agg backend)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .config import EconomyConfig  # noqa: E402
from .simulator import SimResult  # noqa: E402

# Reference categorical palette (fixed order, never cycled) + recessive ink.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3de"
SOURCE_COLORS = {"tap": SERIES[1], "automation": SERIES[0], "offline": SERIES[2]}


def _style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.labelsize": 10,
        "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "legend.fontsize": 9, "font.size": 10, "lines.linewidth": 2,
    })


def strategy_colors(names: list[str]) -> dict[str, str]:
    return {n: SERIES[i % len(SERIES)] for i, n in enumerate(names)}


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def income_chart(results: dict[str, SimResult], out: Path) -> Path:
    _style()
    colors = strategy_colors(list(results))
    fig, ax = plt.subplots(figsize=(10, 5.2))
    for name, r in results.items():
        ts = r.timeseries
        y = ts["active_income"].clip(lower=1e-2)
        ax.plot(ts["day"], y, color=colors[name], label=name)
        ax.annotate(name, (ts["day"].iloc[-1], y.iloc[-1]), xytext=(4, 0), textcoords="offset points",
                    color=INK_2, fontsize=9, va="center")
        pr = r.purchases[r.purchases["kind"] == "prestige"]
        if len(pr):
            ax.scatter(pr["t_h"] / 24, np.interp(pr["t_h"] / 24, ts["day"], y),
                       marker="v", s=40, color=colors[name], edgecolor=SURFACE, linewidth=1.5, zorder=3)
    ax.set_yscale("log")
    ax.set_xlabel("day since install")
    ax.set_ylabel("coins / second while playing (log)")
    ax.set_title("Income per second over time (▼ = prestige reset)")
    ax.legend(loc="upper left")
    return _save(fig, out)


def income_sources_chart(sources: pd.DataFrame, out: Path) -> Path:
    _style()
    names = list(dict.fromkeys(sources["strategy"]))
    n = len(names)
    cols = 2
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(11, 3.4 * rows), sharex=True, sharey=True, squeeze=False)
    for ax, name in zip(axes.flat, names):
        d = sources[sources["strategy"] == name]
        ys = [d["tap_pct"].fillna(0), d["automation_pct"].fillna(0), d["offline_pct"].fillna(0)]
        ax.stackplot(d["day"], *ys, colors=[SOURCE_COLORS["tap"], SOURCE_COLORS["automation"],
                                            SOURCE_COLORS["offline"]],
                     labels=["taps", "automation while playing", "offline earnings"],
                     edgecolor=SURFACE, linewidth=1)
        ax.set_title(name, fontsize=11)
        ax.set_ylim(0, 100)
        ax.set_xlim(d["day"].min(), d["day"].max())
        ax.set_ylabel("% of coins earned that day")
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("day")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.0))
    fig.suptitle("Where the money comes from: taps vs automation vs offline", y=1.04, fontweight="bold")
    return _save(fig, out)


def milestones_chart(cfg: EconomyConfig, table: pd.DataFrame, out: Path) -> Path:
    _style()
    names = list(dict.fromkeys(table["strategy"]))
    colors = strategy_colors(names)
    ms = list(dict.fromkeys(table["milestone"]))
    fig, ax = plt.subplots(figsize=(10, 4.8))
    offsets = np.linspace(-0.22, 0.22, len(names))
    for off, name in zip(offsets, names):
        d = table[table["strategy"] == name].set_index("milestone").reindex(ms)
        y = np.arange(len(ms)) + off
        x = d["calendar_h"].clip(lower=1 / 120)  # 30 s floor for the log axis
        ax.scatter(x, y, s=60, color=colors[name], edgecolor=SURFACE, linewidth=2, label=name, zorder=3)
        missing = d["calendar_h"].isna().to_numpy()
        for yi in y[missing]:
            ax.annotate("not reached", (cfg.simulation.days * 24, yi), xytext=(-4, 0),
                        textcoords="offset points", ha="right", va="center", fontsize=8, color=INK_2)
    for t in cfg.pacing.targets:
        if t.milestone in ms and t.clock == "calendar":
            yi = ms.index(t.milestone)
            lo, hi = t.hours * (1 - t.tolerance), t.hours * (1 + t.tolerance)
            ax.fill_betweenx([yi - 0.35, yi + 0.35], lo, hi, color=GRID, zorder=1)
            ax.plot([t.hours, t.hours], [yi - 0.35, yi + 0.35], color=INK_2, linewidth=1.5, zorder=2)
    ax.set_xscale("log")
    ax.set_yticks(range(len(ms)))
    ax.set_yticklabels(ms)
    ax.invert_yaxis()
    ticks = [1 / 60, 1 / 6, 1, 6, 24, 72, 168, 336, 504]
    labels = ["1 min", "10 min", "1 h", "6 h", "1 d", "3 d", "1 w", "2 w", "3 w"]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_xlim(1 / 120, cfg.simulation.days * 24 * 1.15)
    ax.set_xlabel("calendar time since install (log); grey band = calendar target ± tolerance")
    ax.set_title("Time to reach each milestone")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    return _save(fig, out)


def dead_zones_chart(cfg: EconomyConfig, zones: pd.DataFrame, names: list[str], out: Path) -> Path:
    _style()
    colors = strategy_colors(names)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    for name in names:
        z = zones[zones["strategy"] == name]
        if len(z):
            ax.scatter(z["start_day"], z["gap_active_min"], s=40, color=colors[name], edgecolor=SURFACE,
                       linewidth=1.5, label=name, zorder=3)
    thr = cfg.analysis.dead_zone_threshold_active_minutes
    ax.axhline(thr, color=INK_2, linewidth=1, linestyle="--")
    ax.annotate(f"threshold {thr:g} min", (0, thr), xytext=(2, 4), textcoords="offset points",
                fontsize=8, color=INK_2)
    ax.set_yscale("log")
    ax.set_xlim(-0.3, cfg.simulation.days)
    ax.set_xlabel("day the wait started")
    ax.set_ylabel("minutes of PLAY until next meaningful buy (log)")
    ax.set_title(f"Dead zones: waits > {thr:g} min of play for a purchase worth ≥ "
                 f"{cfg.analysis.meaningful_gain_pct:g}% income")
    if len(zones):
        ax.legend(loc="upper left")
    return _save(fig, out)


def payback_chart(results: dict[str, SimResult], out: Path) -> Path:
    _style()
    colors = strategy_colors(list(results))
    n = len(results)
    rows = int(np.ceil(n / 2))
    fig, axes = plt.subplots(rows, 2, figsize=(11, 3.3 * rows), sharex=True, sharey=True, squeeze=False)
    for ax, (name, r) in zip(axes.flat, results.items()):
        p = r.purchases[(r.purchases["kind"] != "prestige") & np.isfinite(r.purchases["payback_h"])]
        ax.scatter(p["t_h"] / 24, p["payback_h"].clip(lower=1e-3), s=6, color=colors[name], alpha=0.6,
                   linewidth=0)
        ax.set_yscale("log")
        ax.set_title(name, fontsize=11)
        ax.set_ylabel("payback (real hours, log)")
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("day")
    fig.suptitle("Payback time of every purchase (cost / gain in average coins per real hour)",
                 fontweight="bold")
    return _save(fig, out)


def event_impact_chart(impact: pd.DataFrame, out: Path, milestone: str = "franchise") -> Path:
    _style()
    names = list(dict.fromkeys(impact["strategy"]))
    colors = strategy_colors(names)
    events = list(dict.fromkeys(impact["event"]))
    fig, ax = plt.subplots(figsize=(10, 0.6 * len(events) * len(names) + 1.5))
    h = 0.8 / len(names)
    for i, name in enumerate(names):
        d = impact[impact["strategy"] == name].set_index("event").reindex(events)
        y = np.arange(len(events)) + (i - (len(names) - 1) / 2) * h
        vals = d["delay_pct"].fillna(0).to_numpy()
        ax.barh(y, vals, height=h * 0.85, color=colors[name], label=name)
        for yi, v in zip(y, vals):
            ax.annotate(f"{v:+.1f}%", (v, yi), xytext=(4 if v >= 0 else -4, 0), textcoords="offset points",
                        ha="left" if v >= 0 else "right", va="center", fontsize=8, color=INK_2)
    ax.axvline(0, color=INK_2, linewidth=1)
    ax.set_yticks(range(len(events)))
    ax.set_yticklabels(events)
    ax.invert_yaxis()
    ax.set_xlabel(f"change in time to {milestone} caused by the event (+ = slower), median over seeds")
    ax.set_title("Impact of each NPC event type on progress")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    return _save(fig, out)
