"""Rule-based tuning recommendations derived from the analysis tables.

Every rule produces a :class:`Recommendation` with a severity (0-100), the evidence and
a concrete action naming config paths and values. The report sorts them by severity;
the top ones become "what to tune first".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EconomyConfig
from .simulator import SimResult
from .tuning import TargetCheck, nice


@dataclass
class Recommendation:
    severity: float
    area: str
    title: str
    evidence: str
    action: str


def fmt_h(h: float | None) -> str:
    """Human-friendly duration from hours."""
    if h is None or (isinstance(h, float) and math.isnan(h)):
        return "—"
    if h < 1 / 60:
        return f"{h * 3600:.0f} s"
    if h < 1:
        return f"{h * 60:.1f} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} d"


def fmt_num(x: float) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    ax = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if ax >= div and ax < div * 1000:
            return f"{x / div:.3g}{suf}"
    if ax >= 1e15:
        return f"{x:.2e}"
    return f"{x:.3g}"


# --------------------------------------------------------------------------- rules


def pacing_rules(cfg: EconomyConfig, checks: list[TargetCheck]) -> list[Recommendation]:
    out = []
    for c in checks:
        if c.status == "ok":
            continue
        if c.ratio is not None:
            dev = abs(math.log(c.ratio))
            sev = 35 + 45 * min(dev / math.log(3), 1.0)
            cal = c.clock == "calendar"
            ev = (f"`{c.milestone}` ({c.description}): reached after {fmt_h(c.actual_active_h)} of play"
                  + (f" / {fmt_h(c.actual_h)} calendar" if cal else "")
                  + f"; target {fmt_h(c.target_active_h)} of play"
                  + (f" (= {fmt_h(c.target_h)} calendar)" if cal else "")
                  + f". Status: **{c.status}** (x{c.ratio:.2f}).")
        else:
            sev = 85
            ev = f"`{c.milestone}` ({c.description}) is **not reached** within {cfg.simulation.days:g} days."
        if c.suggested_factor is not None and c.suggested_factor != 1.0:
            f = c.suggested_factor
            if c.lever.startswith("tier:"):
                tid = c.lever.split(":", 1)[1]
                act = (f"Scale every money value of the **{tid}** tier by **x{nice(f)}** "
                       f"(`tiers.{tid}.unlock_cost`: {fmt_num(c.lever_value)} → {fmt_num(c.suggested_value)}, "
                       f"and its staff costs/incomes, upgrade and manager costs by the same factor). "
                       f"`python -m doner_balance tune --apply` does this for you.")
            else:
                act = f"Change `{c.lever}`: {fmt_num(c.lever_value)} → **{fmt_num(c.suggested_value)}** (x{nice(f)})."
            if c.elasticity is not None:
                act += f" (measured elasticity of play time to this lever: {c.elasticity:.2f})"
        else:
            act = c.note or "Lever has no measurable effect; adjust the previous stage instead."
        out.append(Recommendation(sev, "pacing", f"{c.milestone} is {c.status}", ev, act))
    return out


def dead_zone_rules(cfg: EconomyConfig, ref: SimResult, zones: pd.DataFrame,
                    summary: pd.DataFrame) -> list[Recommendation]:
    out = []
    thr = cfg.analysis.dead_zone_threshold_active_minutes
    z = zones[zones["strategy"] == ref.strategy]
    if z.empty:
        return out
    srow = summary[summary["strategy"] == ref.strategy].iloc[0]
    share = srow["share_of_play_pct"]
    if share > 25:
        sess = ref_session_minutes(cfg, ref.strategy)
        idle = z[z["gap_active_min"] <= 1.05 * sess]  # the wait fits inside one session
        ev = (f"{share:.0f}% of all play time of `{ref.strategy}` is spent in stretches longer than {thr:g} min "
              f"before purchases add up to +{cfg.analysis.meaningful_gain_pct:g}% income "
              f"({int(srow['dead_zones'])} dead zones, {srow['total_dead_min']:.0f} min in total).")
        if len(idle) >= 0.4 * len(z):
            ev += (f" {len(idle)} of them fit inside a single session (*idle sessions*): the offline earnings are spent in the first seconds "
                   f"of a {sess:g}-minute session and the rest of the session adds almost nothing.")
            act = ("Give the session itself something to do: raise active income relative to offline "
                   f"(lower `offline.efficiency` {cfg.offline.efficiency:g} → {nice(cfg.offline.efficiency * 0.7)}, "
                   "or raise `tap.upgrades.showman.value`), and/or add short in-session goals in the game design "
                   "(NPC events while playing, timed boosts). In the economy, more mid-sized purchases per tier "
                   "(a 4th staff type, more equipment levels) also break up the waits.")
        else:
            act = ("Add more mid-sized purchases (a 4th staff type or more equipment levels per tier) or lower "
                   "`staff_milestones` levels slightly so milestone purchases come more often.")
        out.append(Recommendation(40 + min(share - 25, 40), "dead zones",
                                  "Too much play time without meaningful progress", ev, act))
    worst = z.sort_values("gap_active_min", ascending=False).head(3)
    tiers = {t.id: t for t in cfg.tiers}
    seen: set[str] = set()
    for row in worst.itertuples():
        if row.gap_active_min < 3 * thr or row.ended_by in seen:
            continue
        seen.add(row.ended_by)
        sessions = row.gap_active_min / ref_session_minutes(cfg, ref.strategy)
        ev = (f"On day {row.start_day:.1f} (stage: {row.stage}) the player waits **{row.gap_active_min:.0f} min of play** "
              f"(≈ {sessions:.0f} sessions, {fmt_h(row.gap_calendar_h)} calendar) until `{row.ended_by}`; "
              f"meanwhile only {row.small_buys} small purchases"
              f"{f' (mostly `{row.most_bought}`)' if row.most_bought else ''}.")
        sev = 45 + min(row.gap_active_min / thr, 30)
        p = ref.purchases
        if row.ended_by in tiers:
            t = tiers[row.ended_by]
            prev = p[(p["t_h"] <= row.start_calendar_h) & (p["kind"] != "prestige")]
            last_cost = float(prev["cost"].iloc[-1]) if len(prev) else t.unlock_cost / 100
            fill = nice(math.sqrt(max(last_cost, 1.0) * t.unlock_cost))
            prev_tier = cfg.tiers[cfg.tier_index(t.id) - 1]
            payback = prev_tier.staff[-1].base_cost / prev_tier.staff[-1].income
            act = (f"Wall before **{t.id}**. Keep the pacing target and fill the gap with content in the "
                   f"`{prev_tier.id}` tier: e.g. a 4th staff item with `base_cost` ≈ {fmt_num(fill)} and "
                   f"`income` ≈ {fmt_num(nice(fill / (payback * 1.6)))} (base payback ~1.6x the last staff), "
                   f"or more levels on `{prev_tier.upgrades[0].id if prev_tier.upgrades else 'its upgrades'}` "
                   f"(`max_level` +5). Lowering `tiers.{t.id}.unlock_cost` also helps but moves the "
                   f"{t.id} milestone earlier.")
        elif row.ended_by in {m.id for t in cfg.tiers for m in t.managers}:
            mcost = next(m.cost for t in cfg.tiers for m in t.managers if m.id == row.ended_by)
            tid = next(t.id for t in cfg.tiers for m in t.managers if m.id == row.ended_by)
            new = nice(mcost * max(min(2 * thr / row.gap_active_min, 1.0), 0.1))
            act = (f"The manager `{row.ended_by}` is too expensive right after its tier opens (the tier earns "
                   f"nothing offline until then). Lower `tiers.{tid}.managers.{row.ended_by}.cost`: "
                   f"{fmt_num(mcost)} → **{fmt_num(new)}**.")
        else:
            act = (f"Reduce `cost_growth` of `{row.ended_by}` by 0.01, or add a cheaper intermediate upgrade "
                   f"in stage {row.stage}.")
        out.append(Recommendation(sev, "dead zones", f"Long wait for `{row.ended_by}`", ev, act))
    return out


def ref_session_minutes(cfg: EconomyConfig, strategy: str) -> float:
    return cfg.profiles[cfg.strategies[strategy].profile].session_minutes


def income_rules(cfg: EconomyConfig, ref: SimResult, results: dict[str, SimResult],
                 sources: pd.DataFrame) -> list[Recommendation]:
    from .analysis import tap_share_of_active

    out = []
    a = cfg.analysis
    tap_active = tap_share_of_active(ref, after_h=24)
    s = sources[(sources["strategy"] == ref.strategy) & (sources["day"] >= 2)]
    tot = s[["tap", "automation", "offline"]].sum()
    offline_pct = 100 * tot["offline"] / tot.sum() if tot.sum() > 0 else float("nan")
    tap_all = 100 * tot["tap"] / tot.sum() if tot.sum() > 0 else float("nan")
    if tap_active < a.tap_share_low_pct:
        out.append(Recommendation(
            55, "taps", "Tapping becomes pointless",
            f"After day 1 taps are only {tap_active:.1f}% of the income earned while playing.",
            "Raise `tap.upgrades.showman.value` (e.g. 0.01 → 0.02) or the `tap_mult` of later tiers so tapping "
            "keeps up with automation."))
    elif tap_active > a.tap_share_high_pct:
        out.append(Recommendation(
            50, "taps", "Automation is too weak compared with tapping",
            f"After day 1 taps still make {tap_active:.0f}% of the income earned while playing.",
            "Lower `tap_mult` of later tiers or `showman.value`, or raise staff `income`."))
    else:
        out.append(Recommendation(
            10, "taps", "Tap share is healthy",
            f"After day 1 taps make {tap_active:.0f}% of the income earned while playing "
            f"({tap_all:.1f}% of all income incl. offline).", "No change needed."))
    if offline_pct > 75:
        out.append(Recommendation(
            30 + min(offline_pct - 75, 25), "offline", "Offline earnings dominate",
            f"From day 2 on, {offline_pct:.0f}% of `{ref.strategy}`'s coins come from offline earnings; "
            f"the {cfg.profiles[cfg.strategies[ref.strategy].profile].session_minutes:g}-minute sessions add "
            f"little beyond spending.",
            f"Fine for a relaxed idle game. If sessions should matter more, lower `offline.efficiency` "
            f"({cfg.offline.efficiency:g} → {nice(cfg.offline.efficiency * 0.7)}) or reduce manager "
            f"`offline_cap_hours_add`, and re-run `tune` (pacing will slow down)."))
    return out


def casual_rules(cfg: EconomyConfig, results: dict[str, SimResult]) -> list[Recommendation]:
    out = []
    ref = results.get(cfg.pacing.reference_strategy)
    cas = [n for n, s in cfg.strategies.items() if cfg.profiles[s.profile].sessions_per_day
           < cfg.profiles[cfg.strategies[cfg.pacing.reference_strategy].profile].sessions_per_day]
    if ref is None or not cas:
        return out
    # the furthest tier the reference player reaches
    reached = [t.id for t in cfg.tiers[1:] if ref.milestone_hours(t.id) is not None]
    if not reached:
        return out
    m = reached[-1]
    a = ref.milestone_hours(m)
    assert a is not None
    for name in cas:
        b = results[name].milestone_hours(m)
        if b is None:
            out.append(Recommendation(
                70, "casual", f"`{name}` never reaches {m}",
                f"`{name}` does not reach `{m}` in {cfg.simulation.days:g} days (reference: {fmt_h(a)}).",
                f"Raise `offline.cap_hours` ({cfg.offline.cap_hours:g} → {cfg.offline.cap_hours * 2:g}) so "
                f"long gaps between few sessions still pay."))
            continue
        ratio = b / a
        if ratio > cfg.analysis.casual_slowdown_warn:
            out.append(Recommendation(
                55 + min(10 * (ratio - cfg.analysis.casual_slowdown_warn), 25), "casual",
                f"`{name}` players fall far behind",
                f"`{name}` reaches `{m}` after {fmt_h(b)} vs {fmt_h(a)} for `{ref.strategy}` (x{ratio:.1f}).",
                f"Raise `offline.cap_hours` ({cfg.offline.cap_hours:g} → {nice(cfg.offline.cap_hours * 1.5)}) "
                f"or make the first manager cheaper; casual players live on offline income."))
        else:
            out.append(Recommendation(
                8, "casual", f"`{name}` pace is acceptable",
                f"`{name}` reaches `{m}` after {fmt_h(b)} vs {fmt_h(a)} (x{ratio:.1f}, warning above "
                f"x{cfg.analysis.casual_slowdown_warn:g}).", "No change needed."))
    return out


def strategy_rules(cfg: EconomyConfig, results: dict[str, SimResult]) -> list[Recommendation]:
    out = []
    ref = results.get(cfg.pacing.reference_strategy)
    if ref is None:
        return out
    ref_prof = cfg.strategies[cfg.pacing.reference_strategy].profile
    for name, r in results.items():
        if name == ref.strategy or cfg.strategies[name].profile != ref_prof:
            continue
        for t in cfg.tiers[1:]:
            a, b = ref.milestone_hours(t.id), r.milestone_hours(t.id)
            if a is None:
                break
            if b is None or b > 2 * a:
                cheap = min((s for tt in cfg.tiers for s in tt.staff), key=lambda s: s.base_cost)
                out.append(Recommendation(
                    35 if cfg.strategies[name].policy == "cheapest" else 45, "strategies",
                    f"`{name}` stalls before {t.id}",
                    f"`{name}` reaches `{t.id}` {'never' if b is None else 'after ' + fmt_h(b)} vs "
                    f"{fmt_h(a)} for `{ref.strategy}` (same sessions, different buying).",
                    "Players who buy naively get stuck: show a 'best buy' hint in the UI, and/or steepen "
                    f"`cost_growth` of the cheapest generators (e.g. `{cheap.id}` {cheap.cost_growth:g} → "
                    f"{cheap.cost_growth + 0.02:g}) so cheap items stop soaking up cash."))
                break
            if b < 0.8 * a and cfg.strategies[name].policy == "saver":
                out.append(Recommendation(
                    25, "strategies", f"Saving for {t.id} beats greedy ROI",
                    f"`{name}` reaches `{t.id}` after {fmt_h(b)} vs {fmt_h(a)} for `{ref.strategy}`.",
                    f"The {t.id} unlock is worth more than its immediate income gain suggests (its global "
                    f"multiplier and new items). Fine as a skill expression; if unwanted, lower "
                    f"`tiers.{t.id}.global_income_mult`."))
                break
    return out


def event_rules(cfg: EconomyConfig, impact: pd.DataFrame | None, mix: pd.DataFrame) -> list[Recommendation]:
    out = []
    if impact is None or impact.empty:
        return out
    warn = cfg.analysis.event_impact_warn_pct
    evs = {e.id: e for e in cfg.events}
    for row in impact[impact["event"] != "all events"].itertuples():
        e = evs[row.event]
        d = row.delay_pct
        life = row.earnings_change_pct
        if not np.isfinite(d):
            continue
        shift = row.shift_h
        if d > warn:
            used = mix[(mix["strategy"] == row.strategy) & (mix["event"] == row.event)]
            top = used.sort_values("count", ascending=False)["response"].iloc[0] if len(used) else e.default_response
            out.append(Recommendation(
                40 + min(d, 40), "events", f"{e.name} hurts `{row.strategy}` too much",
                f"With this event `{row.strategy}` is {shift:+.1f} h behind by hour "
                f"{cfg.analysis.event_checkpoint_hours:g} ({d:+.1f}%, median of "
                f"{1 + len(cfg.simulation.extra_seeds)} seeds); most used answer: `{top}`.",
                f"Halve the cost/losses of response `{top}` or lower `rate_per_hour` "
                f"({e.rate_per_hour:g} → {nice(e.rate_per_hour / 2)})."))
        elif d < -warn:
            best = max(((r, eff) for r in e.responses for eff in r.effects if eff.income_mult and eff.income_mult > 1),
                       key=lambda x: x[1].income_mult, default=None)
            act = ("Make the bonus smaller or pricier: "
                   + (f"`{best[0].id}` income_mult {best[1].income_mult:g} → {nice(1 + (best[1].income_mult - 1) / 2)}"
                      f" or raise its cost." if best else "raise response costs."))
            out.append(Recommendation(
                30 + min(-d, 30), "events", f"{e.name} is a big buff for `{row.strategy}`",
                f"With this event `{row.strategy}` is {-shift:.1f} h *ahead* by hour "
                f"{cfg.analysis.event_checkpoint_hours:g} ({d:+.1f}%), earnings {life:+.0f}%.",
                act + " Good events are fine, but at this size the economy is balanced around them."))
        elif abs(d) < 1:
            out.append(Recommendation(
                12, "events", f"{e.name} barely matters for `{row.strategy}`",
                f"Effect on progress: {shift:+.1f} h by hour {cfg.analysis.event_checkpoint_hours:g} ({d:+.1f}%).",
                f"If the {e.npc} should be a memorable NPC, raise stakes (rate {e.rate_per_hour:g}/h or "
                f"effect size); otherwise it is flavour."))
    return out


def prestige_rules(cfg: EconomyConfig, ref: SimResult, runs: pd.DataFrame) -> list[Recommendation]:
    out = []
    if not cfg.prestige.enabled:
        return out
    f_id = cfg.tiers[-1].id
    fr, pr = ref.milestone_hours(f_id), ref.milestone_hours("first_prestige")
    if fr is not None and pr is not None:
        gap = pr - fr
        if gap < 24:
            out.append(Recommendation(
                45, "prestige", f"Prestige right after the {f_id}",
                f"First prestige {fmt_h(gap)} after the {f_id} opens: the last tier is barely played.",
                f"Raise `prestige.base_requirement` ({fmt_num(cfg.prestige.base_requirement)} → "
                f"{fmt_num(nice(cfg.prestige.base_requirement * 3))}) or `strategy_params.prestige_multiplier_threshold`."))
    mine = runs[runs["strategy"] == ref.strategy]
    r1 = mine[mine["run"] == 1]
    r2 = mine[mine["run"] == 2]
    col = f"{f_id}_after_h"
    if len(r1) and len(r2) and np.isfinite(r1[col].iloc[0]) and np.isfinite(r2[col].iloc[0]):
        speed = r1[col].iloc[0] / r2[col].iloc[0]
        rep = int(r1["reputation_gained"].iloc[0])
        bonus = 1 + rep * cfg.prestige.bonus_per_point
        if speed < 1.4:
            out.append(Recommendation(
                45, "prestige", "Second run is not much faster",
                f"Run 2 reaches {f_id} after {fmt_h(r2[col].iloc[0])} vs {fmt_h(r1[col].iloc[0])} in run 1 "
                f"(x{speed:.2f} faster) with x{bonus:.2f} income from {rep} reputation.",
                f"Raise `prestige.bonus_per_point` ({cfg.prestige.bonus_per_point:g} → "
                f"{nice(cfg.prestige.bonus_per_point * 1.5)}) so a reset feels rewarding."))
        else:
            out.append(Recommendation(
                10, "prestige", "Prestige loop works",
                f"Run 2 reaches {f_id} x{speed:.1f} faster than run 1 (x{bonus:.2f} income from {rep} reputation).",
                "No change needed."))
    return out


def stage_rules(cfg: EconomyConfig, stages: pd.DataFrame) -> list[Recommendation]:
    out = []
    if stages.empty:
        return out
    total = stages["calendar_h"].sum()
    top = stages.loc[stages["calendar_h"].idxmax()]
    share = 100 * top["calendar_h"] / total if total else 0
    tier = next(t for t in cfg.tiers if t.id == top["stage"])
    if share > 55:
        out.append(Recommendation(
            50 + min(share - 55, 25), "content", f"The {top['stage']} stage carries {share:.0f}% of the journey",
            f"In run 1 the player spends {fmt_h(top['calendar_h'])} ({top['play_min']:.0f} min of play) in "
            f"{top['stage']} with {len(tier.staff)} staff, {len(tier.upgrades)} upgrades and "
            f"{len(tier.managers)} manager(s) to buy.",
            f"Either add content to `{top['stage']}` (another staff type and 1-2 upgrades), or move the stage "
            f"boundary: lower the next tier's cost and raise the target accordingly."))
    return out


def all_recommendations(cfg: EconomyConfig, results: dict[str, SimResult], checks: list[TargetCheck],
                        zones: pd.DataFrame, zone_summary: pd.DataFrame, sources: pd.DataFrame,
                        impact: pd.DataFrame | None, mix: pd.DataFrame, runs: pd.DataFrame,
                        stages: pd.DataFrame) -> list[Recommendation]:
    ref = results[cfg.pacing.reference_strategy]
    recs: list[Recommendation] = []
    recs += pacing_rules(cfg, checks)
    recs += dead_zone_rules(cfg, ref, zones, zone_summary)
    recs += stage_rules(cfg, stages)
    recs += income_rules(cfg, ref, results, sources)
    recs += casual_rules(cfg, results)
    recs += strategy_rules(cfg, results)
    recs += event_rules(cfg, impact, mix)
    recs += prestige_rules(cfg, ref, runs)
    return sorted(recs, key=lambda r: -r.severity)
