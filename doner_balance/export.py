"""Export the economy to a simple JSON file for the Godot project.

The format is documented in docs/GODOT_EXPORT.md. Output is deterministic (no
timestamps, stable key order) so diffs between balance versions stay readable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import formulas as F
from .config import EconomyConfig
from .model import Economy

FORMAT = "doner-lamar-economy"
FORMAT_VERSION = 1

FORMULAS = {
    "level_cost": "base_cost * pow(cost_growth, level)   # level = levels already owned",
    "bulk_cost": "base_cost * pow(cost_growth, level) * (pow(cost_growth, n) - 1) / (cost_growth - 1)",
    "milestone_mult": "product of mult for every staff_milestones entry with level >= entry.level",
    "staff_income": "level * income * milestone_mult(level)",
    "tier_income": "sum(staff_income) * (1 + sum(value*level of 'income' upgrades))"
                   " * (1 + sum(value*level of 'speed' upgrades)) * product(owned manager income_mult)",
    "global_mult": "product(global_income_mult of owned tiers) * (1 + reputation * prestige.bonus_per_point)"
                   " * product(active event income_mult)",
    "auto_income": "sum(tier_income of owned tiers) * global_mult",
    "tap_value": "(tap.base_value + sum(value*level of 'flat' tap upgrades))"
                 " * (1 + sum(value*level of 'pct' tap upgrades)) * tap_mult(highest owned tier) * global_mult"
                 " + sum(value*level of 'income_share' tap upgrades) * auto_income",
    "offline_income": "sum(tier_income of tiers with an enables_offline manager) * global_mult"
                      " * offline.efficiency * min(seconds_away, offline_cap_hours * 3600)",
    "offline_cap_hours": "offline.cap_hours + sum(offline_cap_hours_add of owned managers)",
    "amount": "flat + cash_fraction * cash + income_seconds * (auto_income + tap income per second)",
    "reputation_total": "floor(prestige.scale * pow(all_time_earnings / prestige.base_requirement,"
                        " prestige.exponent))",
    "reputation_gain": "reputation_total - reputation_already_owned (only with prestige.required_tier owned)",
    "event_chance_per_second": "1 - exp(-rate_per_hour / 3600)",
}


def _item(model: Any, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    return {k: v for k, v in model.model_dump().items() if k not in drop}


def _test_vectors(cfg: EconomyConfig) -> dict[str, Any]:
    econ = Economy(cfg)
    costs = []
    leveled = [*cfg.tap.upgrades]
    for t in cfg.tiers:
        leveled += [*t.staff, *t.upgrades]
    for it in leveled:
        for lv in (0, 1, 10, 25):
            if it.max_level is not None and lv >= it.max_level:
                continue
            costs.append({"item": it.id, "level": lv, "cost": F.level_cost(it.base_cost, it.cost_growth, lv)})
    staff = []
    for t in cfg.tiers:
        s = t.staff[0]
        for lv in (1, 24, 25, 50, 100):
            staff.append({"item": s.id, "level": lv, "income": F.staff_income(lv, s.income, cfg.staff_milestones)})
    rep = []
    p = cfg.prestige
    for mult in (0.5, 1, 10, 1000):
        life = p.base_requirement * mult
        rep.append({"all_time_earnings": life,
                    "reputation_total": F.reputation_total(life, p.base_requirement, p.exponent, p.scale)})
    # a sample mid-game state, so the Godot port can check its income engine end to end
    st = econ.new_state()
    st.tiers_owned = min(2, len(cfg.tiers))
    levels: dict[str, int] = {}
    for ti in range(st.tiers_owned):
        for i, s in enumerate(cfg.tiers[ti].staff):
            levels[s.id] = 30 - 10 * i if 30 - 10 * i > 0 else 1
        for u in cfg.tiers[ti].upgrades:
            levels[u.id] = 2
        if cfg.tiers[ti].managers:
            st.managers.add(cfg.tiers[ti].managers[0].id)
    for u in cfg.tap.upgrades:
        levels[u.id] = 3 if (u.max_level is None or u.max_level >= 3) else u.max_level
    st.levels.update(levels)
    st.reputation = 10
    auto = econ.auto_income(st)
    sample = {
        "tiers_owned": st.tiers_owned, "levels": dict(sorted(st.levels.items())),
        "managers": sorted(st.managers), "reputation": st.reputation,
        "expected": {
            "global_mult": econ.global_mult(st), "auto_income": auto,
            "tap_value": econ.tap_value(st, auto), "offline_income_per_sec": econ.offline_rate(st),
            "offline_cap_hours": econ.offline_cap_hours(st),
        },
    }
    return {"level_cost": costs, "staff_income": staff, "reputation": rep, "sample_state": sample}


def build_export(cfg: EconomyConfig) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "economy_name": cfg.name,
        "economy_version": cfg.version,
        "currency": _item(cfg.currency),
        "formulas": FORMULAS,
        "staff_milestones": [_item(m) for m in cfg.staff_milestones],
        "tap": {"base_value": cfg.tap.base_value, "upgrades": [_item(u) for u in cfg.tap.upgrades]},
        "tiers": [
            {
                "id": t.id, "name": t.name, "index": i, "unlock_cost": t.unlock_cost, "tap_mult": t.tap_mult,
                "global_income_mult": t.global_income_mult, "free_staff_levels": dict(t.free_staff_levels),
                "staff": [_item(s) for s in t.staff],
                "upgrades": [_item(u) for u in t.upgrades],
                "managers": [_item(m) for m in t.managers],
            }
            for i, t in enumerate(cfg.tiers)
        ],
        "offline": _item(cfg.offline),
        "prestige": _item(cfg.prestige),
        "events": [_item(e, drop=("enabled",)) for e in cfg.events if e.enabled],
        "test_vectors": _test_vectors(cfg),
    }


def export_json(cfg: EconomyConfig, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_export(cfg), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path
