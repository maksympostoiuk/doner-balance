"""Pydantic config model for the Doner Lamar economy.

Every balance number lives in ``config/economy.yaml``; this module only describes
its shape and validates it. See the YAML file for comments on each value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- items


class Milestone(_Model):
    """Reaching ``level`` of a staff item multiplies its output by ``mult``."""

    level: int = Field(gt=0)
    mult: float = Field(gt=0)


class TapUpgrade(_Model):
    id: str
    name: str
    kind: Literal["flat", "pct", "income_share"]
    # flat: +value coins per tap per level
    # pct: +value (fraction) tap multiplier per level (additive between levels)
    # income_share: each tap also earns +value * automated income/sec per level
    value: float = Field(gt=0)
    base_cost: float = Field(gt=0)
    cost_growth: float = Field(gt=1.0)
    max_level: int | None = Field(default=None, gt=0)


class TapConfig(_Model):
    base_value: float = Field(gt=0)
    upgrades: list[TapUpgrade] = Field(default_factory=list)


class Staff(_Model):
    """Hired staff: each level adds ``income`` coins/sec of automated production."""

    id: str
    name: str
    base_cost: float = Field(gt=0)
    cost_growth: float = Field(gt=1.0)
    income: float = Field(gt=0)
    max_level: int | None = Field(default=None, gt=0)


class TierUpgrade(_Model):
    """Equipment: ``income`` = +value price per level, ``speed`` = +value speed per level.

    Both multiply the tier's automated income: (1 + sum income) * (1 + sum speed).
    """

    id: str
    name: str
    kind: Literal["income", "speed"]
    value: float = Field(gt=0)
    base_cost: float = Field(gt=0)
    cost_growth: float = Field(gt=1.0)
    max_level: int | None = Field(default=None, gt=0)


class Manager(_Model):
    """One-time hire. Lets a tier keep producing while the player is offline."""

    id: str
    name: str
    cost: float = Field(gt=0)
    income_mult: float = Field(default=1.0, gt=0)
    enables_offline: bool = True
    offline_cap_hours_add: float = Field(default=0.0, ge=0)


class Tier(_Model):
    id: str
    name: str
    unlock_cost: float = Field(ge=0)
    tap_mult: float = Field(default=1.0, gt=0)
    global_income_mult: float = Field(default=1.0, gt=0)
    free_staff_levels: dict[str, int] = Field(default_factory=dict)
    staff: list[Staff]
    upgrades: list[TierUpgrade] = Field(default_factory=list)
    managers: list[Manager] = Field(default_factory=list)

    @model_validator(mode="after")
    def _free_staff_exist(self) -> "Tier":
        ids = {s.id for s in self.staff}
        for sid in self.free_staff_levels:
            if sid not in ids:
                raise ValueError(f"tier {self.id}: free_staff_levels references unknown staff '{sid}'")
        return self


# --------------------------------------------------------------------------- events


class Amount(_Model):
    """A coin amount that scales with the economy.

    total = flat + cash_fraction * cash + income_seconds * income_per_sec
    """

    flat: float = Field(default=0.0, ge=0)
    cash_fraction: float = Field(default=0.0, ge=0, le=1)
    income_seconds: float = Field(default=0.0, ge=0)


class Effect(_Model):
    probability: float = Field(default=1.0, ge=0, le=1)
    income_mult: float | None = Field(default=None, gt=0)
    duration_hours: float | None = Field(default=None, gt=0)  # None = permanent
    max_stacks: int = Field(default=1, ge=1)  # only for permanent multipliers
    cash_loss: Amount | None = None
    cash_gain: Amount | None = None

    @model_validator(mode="after")
    def _has_effect(self) -> "Effect":
        if self.income_mult is None and self.cash_loss is None and self.cash_gain is None:
            raise ValueError("effect must define income_mult, cash_loss or cash_gain")
        return self


class Response(_Model):
    id: str
    name: str
    cost: Amount = Field(default_factory=Amount)
    effects: list[Effect] = Field(default_factory=list)


class NpcEvent(_Model):
    id: str
    name: str
    npc: Literal["supplier", "inspector", "mafia", "rival"]
    enabled: bool = True
    min_tier: str
    min_hours_since_start: float = Field(default=0.0, ge=0)
    rate_per_hour: float = Field(ge=0)  # Poisson rate in calendar hours
    cooldown_hours: float = Field(default=0.0, ge=0)
    default_response: str
    responses: list[Response] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> "NpcEvent":
        if self.default_response not in {r.id for r in self.responses}:
            raise ValueError(f"event {self.id}: default_response '{self.default_response}' is not a response id")
        return self

    def response(self, rid: str) -> Response:
        for r in self.responses:
            if r.id == rid:
                return r
        raise KeyError(rid)


# --------------------------------------------------------------------------- meta systems


class OfflineConfig(_Model):
    efficiency: float = Field(gt=0, le=1)
    cap_hours: float = Field(gt=0)
    requires_manager: bool = True


class PrestigeConfig(_Model):
    enabled: bool = True
    currency_name: str = "reputation"
    required_tier: str
    base_requirement: float = Field(gt=0)
    exponent: float = Field(gt=0, le=1)
    scale: float = Field(gt=0)
    bonus_per_point: float = Field(gt=0)
    min_points: int = Field(default=1, ge=1)


# --------------------------------------------------------------------------- simulation


class SessionProfile(_Model):
    sessions_per_day: int = Field(gt=0)
    session_minutes: float = Field(gt=0)
    first_session_minutes: float | None = Field(default=None, gt=0)
    day_start_hour: float = Field(ge=0, lt=24)
    day_end_hour: float = Field(gt=0, le=24)
    taps_per_second: float = Field(ge=0)
    tapping_share: float = Field(ge=0, le=1)
    event_response: Literal["smart", "default"] = "smart"

    @model_validator(mode="after")
    def _window(self) -> "SessionProfile":
        if self.day_end_hour <= self.day_start_hour:
            raise ValueError("day_end_hour must be after day_start_hour")
        return self


class StrategyConfig(_Model):
    policy: Literal["best_roi", "cheapest", "saver"]
    profile: str
    description: str = ""


class StrategyParams(_Model):
    saver_patience_hours: float = Field(gt=0)
    saver_pocket_fraction: float = Field(ge=0, le=1)
    prestige_multiplier_threshold: float = Field(gt=1)
    event_value_horizon_hours: float = Field(gt=0)


class SimulationConfig(_Model):
    days: float = Field(gt=0)
    seed: int
    extra_seeds: list[int] = Field(default_factory=list)
    dt_active_seconds: float = Field(gt=0)
    dt_offline_seconds: float = Field(gt=0)
    record_every_seconds: float = Field(gt=0)
    max_purchases_per_tick: int = Field(default=500, gt=0)
    replan_every_seconds: float = Field(default=60, gt=0)


class Target(_Model):
    milestone: str
    hours: float = Field(gt=0)
    clock: Literal["calendar", "active"] = "calendar"
    tolerance: float = Field(default=0.3, gt=0)  # +-30% counts as on target
    mode: Literal["around", "at_most"] = "around"  # at_most: faster is fine
    # what the tuning helper scales: "tier:<id>" (every money value of a tier) or
    # one or more dotted config paths (list items addressed by id, "*" = all items)
    lever: str | list[str]
    description: str = ""


class PacingConfig(_Model):
    reference_strategy: str
    targets: list[Target]


class AnalysisConfig(_Model):
    dead_zone_threshold_active_minutes: float = Field(gt=0)
    meaningful_gain_pct: float = Field(gt=0)
    tap_share_low_pct: float = Field(ge=0)
    tap_share_high_pct: float = Field(le=100)
    casual_slowdown_warn: float = Field(gt=1)
    event_impact_warn_pct: float = Field(gt=0)
    event_checkpoint_hours: float = Field(default=168, gt=0)


class Currency(_Model):
    id: str = "coins"
    name: str = "Coins"
    start_cash: float = Field(default=0.0, ge=0)


class EconomyConfig(_Model):
    name: str
    version: str
    currency: Currency
    staff_milestones: list[Milestone]
    tap: TapConfig
    tiers: list[Tier] = Field(min_length=1)
    offline: OfflineConfig
    prestige: PrestigeConfig
    events: list[NpcEvent] = Field(default_factory=list)
    profiles: dict[str, SessionProfile]
    strategies: dict[str, StrategyConfig]
    strategy_params: StrategyParams
    simulation: SimulationConfig
    pacing: PacingConfig
    analysis: AnalysisConfig

    @field_validator("staff_milestones")
    @classmethod
    def _sorted_milestones(cls, v: list[Milestone]) -> list[Milestone]:
        return sorted(v, key=lambda m: m.level)

    @model_validator(mode="after")
    def _cross_refs(self) -> "EconomyConfig":
        tier_ids = [t.id for t in self.tiers]
        if len(set(tier_ids)) != len(tier_ids):
            raise ValueError("tier ids must be unique")
        if self.tiers[0].unlock_cost != 0:
            raise ValueError("the first tier must be free (unlock_cost: 0)")
        costs = [t.unlock_cost for t in self.tiers]
        if costs != sorted(costs):
            raise ValueError("tier unlock costs must be non-decreasing")
        all_ids: list[str] = list(tier_ids)
        all_ids += [u.id for u in self.tap.upgrades]
        for t in self.tiers:
            all_ids += [s.id for s in t.staff] + [u.id for u in t.upgrades] + [m.id for m in t.managers]
        dupes = {i for i in all_ids if all_ids.count(i) > 1}
        if dupes:
            raise ValueError(f"item ids must be globally unique, duplicates: {sorted(dupes)}")
        if self.prestige.required_tier not in tier_ids:
            raise ValueError(f"prestige.required_tier '{self.prestige.required_tier}' is not a tier")
        for e in self.events:
            if e.min_tier not in tier_ids:
                raise ValueError(f"event {e.id}: min_tier '{e.min_tier}' is not a tier")
        for name, s in self.strategies.items():
            if s.profile not in self.profiles:
                raise ValueError(f"strategy {name}: unknown profile '{s.profile}'")
        if self.pacing.reference_strategy not in self.strategies:
            raise ValueError("pacing.reference_strategy must be one of the strategies")
        valid_ms = {"first_upgrade", "first_prestige", *tier_ids[1:]}
        for t in self.pacing.targets:
            if t.milestone not in valid_ms:
                raise ValueError(f"target milestone '{t.milestone}' must be one of {sorted(valid_ms)}")
        return self

    # convenience -----------------------------------------------------------
    def tier_index(self, tier_id: str) -> int:
        for i, t in enumerate(self.tiers):
            if t.id == tier_id:
                return i
        raise KeyError(tier_id)


def load_config(path: str | Path) -> EconomyConfig:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return EconomyConfig.model_validate(data)


def config_from_dict(data: dict[str, Any]) -> EconomyConfig:
    return EconomyConfig.model_validate(data)


# --------------------------------------------------------------------------- dotted paths


def _resolve_container(data: Any, key: str) -> Any:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id") == key:
                return item
        if key.isdigit():
            return data[int(key)]
        raise KeyError(f"no list item with id '{key}'")
    return data[key]


def expand_path(data: dict[str, Any], path: str) -> list[str]:
    """Expand ``*`` wildcards (all list items, by id) into concrete dotted paths."""
    parts = path.split(".")
    cur_nodes: list[tuple[str, Any]] = [("", data)]
    for part in parts:
        nxt: list[tuple[str, Any]] = []
        for prefix, node in cur_nodes:
            if part == "*":
                items = node if isinstance(node, list) else list(node.values())
                keys = [it["id"] if isinstance(it, dict) and "id" in it else str(i) for i, it in enumerate(items)]
                for k in keys:
                    nxt.append((f"{prefix}.{k}" if prefix else k, _resolve_container(node, k)))
            else:
                try:
                    child = _resolve_container(node, part)
                except (KeyError, IndexError):
                    continue  # optional field missing on this item
                nxt.append((f"{prefix}.{part}" if prefix else part, child))
        cur_nodes = nxt
    out = [p for p, _ in cur_nodes]
    return out


def get_path(data: dict[str, Any], path: str) -> Any:
    """Read a dotted path like ``tiers.cafe.unlock_cost`` (list items addressed by id)."""
    cur: Any = data
    for part in path.split("."):
        cur = _resolve_container(cur, part)
    return cur


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = data
    for part in parts[:-1]:
        cur = _resolve_container(cur, part)
    cur[parts[-1]] = value
