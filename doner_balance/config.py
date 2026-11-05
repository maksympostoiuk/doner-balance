"""Pydantic config model for the Doner Lamar economy.

Every balance number lives in ``config/economy.yaml``; this module only describes
its shape and validates it. See the YAML file for comments on each value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# Ids are used in dotted config paths, CSV file names and Godot translation keys:
# letters, digits, "_" and "-" only (no dots, slashes or spaces).
ID_PATTERN = r"^[A-Za-z0-9_-]+$"
Ident = Annotated[str, StringConstraints(pattern=ID_PATTERN, min_length=1, max_length=64)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- items


class Milestone(_Model):
    """Reaching ``level`` of a staff item multiplies its output by ``mult``."""

    level: int = Field(gt=0)
    mult: float = Field(gt=0)


class TapUpgrade(_Model):
    id: Ident
    name: Name
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

    id: Ident
    name: Name
    base_cost: float = Field(gt=0)
    cost_growth: float = Field(gt=1.0)
    income: float = Field(gt=0)
    max_level: int | None = Field(default=None, gt=0)


class TierUpgrade(_Model):
    """Equipment: ``income`` = +value price per level, ``speed`` = +value speed per level.

    Both multiply the tier's automated income: (1 + sum income) * (1 + sum speed).
    """

    id: Ident
    name: Name
    kind: Literal["income", "speed"]
    value: float = Field(gt=0)
    base_cost: float = Field(gt=0)
    cost_growth: float = Field(gt=1.0)
    max_level: int | None = Field(default=None, gt=0)


class Manager(_Model):
    """One-time hire. Lets a tier keep producing while the player is offline."""

    id: Ident
    name: Name
    cost: float = Field(gt=0)
    income_mult: float = Field(default=1.0, gt=0)
    enables_offline: bool = True
    offline_cap_hours_add: float = Field(default=0.0, ge=0)


class Tier(_Model):
    id: Ident
    name: Name
    unlock_cost: float = Field(ge=0)
    tap_mult: float = Field(default=1.0, gt=0)
    global_income_mult: float = Field(default=1.0, gt=0)
    free_staff_levels: dict[str, Annotated[int, Field(ge=1)]] = Field(default_factory=dict)
    staff: list[Staff] = Field(min_length=1)
    upgrades: list[TierUpgrade] = Field(default_factory=list)
    managers: list[Manager] = Field(default_factory=list)

    @model_validator(mode="after")
    def _free_staff_exist(self) -> Tier:
        staff = {s.id: s for s in self.staff}
        for sid, lv in self.free_staff_levels.items():
            if sid not in staff:
                raise ValueError(f"tier {self.id}: free_staff_levels references unknown staff '{sid}'")
            cap = staff[sid].max_level
            if cap is not None and lv > cap:
                raise ValueError(f"tier {self.id}: free_staff_levels[{sid}] = {lv} exceeds max_level {cap}")
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
    def _has_effect(self) -> Effect:
        if self.income_mult is None and self.cash_loss is None and self.cash_gain is None:
            raise ValueError("effect must define income_mult, cash_loss or cash_gain")
        if self.duration_hours is not None and self.income_mult is None:
            raise ValueError("duration_hours only makes sense together with income_mult")
        return self


class Response(_Model):
    id: Ident
    name: Name
    cost: Amount = Field(default_factory=Amount)
    effects: list[Effect] = Field(default_factory=list)


class NpcEvent(_Model):
    id: Ident
    name: Name
    npc: Literal["supplier", "inspector", "mafia", "rival"]
    enabled: bool = True
    min_tier: str
    min_hours_since_start: float = Field(default=0.0, ge=0)
    rate_per_hour: float = Field(ge=0)  # Poisson rate in calendar hours
    cooldown_hours: float = Field(default=0.0, ge=0)
    default_response: str
    responses: list[Response] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> NpcEvent:
        ids = [r.id for r in self.responses]
        if len(set(ids)) != len(ids):
            raise ValueError(f"event {self.id}: response ids must be unique, got {ids}")
        if self.default_response not in ids:
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


class Perk(_Model):
    """Permanent bonus unlocked once total reputation reaches ``reputation`` (never spent)."""

    id: Ident
    name: Name
    reputation: int = Field(ge=1)
    effect: Literal["start_cash", "offline_cap_hours", "offline_efficiency", "tap_mult", "income_mult"]
    # start_cash / offline_cap_hours / offline_efficiency: added; tap_mult / income_mult: multiplied
    value: float = Field(gt=0)


class PrestigeConfig(_Model):
    enabled: bool = True
    currency_name: str = "reputation"
    required_tier: str
    base_requirement: float = Field(gt=0)
    exponent: float = Field(gt=0, le=1)
    scale: float = Field(gt=0)
    bonus_per_point: float = Field(gt=0)
    min_points: int = Field(default=1, ge=1)
    perks: list[Perk] = Field(default_factory=list)


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
    # real players are not clockwork (all 0 = perfectly regular schedule)
    session_jitter_minutes: float = Field(default=0.0, ge=0, le=720)  # std dev of the start time
    session_length_cv: float = Field(default=0.0, ge=0, le=2)  # relative std dev of the length
    skip_session_chance: float = Field(default=0.0, ge=0, le=0.95)  # chance to miss one session
    skip_day_chance: float = Field(default=0.0, ge=0, le=0.95)  # chance to miss a whole day

    @model_validator(mode="after")
    def _window(self) -> SessionProfile:
        if self.day_end_hour <= self.day_start_hour:
            raise ValueError("day_end_hour must be after day_start_hour")
        spacing_min = (self.day_end_hour - self.day_start_hour) * 60.0 / self.sessions_per_day
        longest = max(self.session_minutes, self.first_session_minutes or 0.0)
        if longest > spacing_min:
            raise ValueError(
                f"sessions overlap: {self.sessions_per_day} sessions in {self.day_end_hour - self.day_start_hour:g} h "
                f"start every {spacing_min:g} min, but a session lasts {longest:g} min"
            )
        return self


class StrategyConfig(_Model):
    policy: Literal["best_roi", "cheapest", "saver"]
    profile: str
    # levels bought per click: 1, 10, 100 or "max" (as many as the cash allows)
    buy_amount: Literal[1, 10, 100, "max"] = 1
    description: str = ""


class StrategyParams(_Model):
    saver_patience_hours: float = Field(gt=0)
    saver_pocket_fraction: float = Field(ge=0, le=1)
    prestige_multiplier_threshold: float = Field(gt=1)
    event_value_horizon_hours: float = Field(gt=0)


class SimulationConfig(_Model):
    days: float = Field(gt=0, le=3650)
    seed: int
    extra_seeds: list[int] = Field(default_factory=list)
    dt_active_seconds: float = Field(gt=0)
    dt_offline_seconds: float = Field(gt=0)
    record_every_seconds: float = Field(gt=0)
    max_purchases_per_tick: int = Field(default=500, gt=0)
    replan_every_seconds: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _seeds_unique(self) -> SimulationConfig:
        seeds = [self.seed, *self.extra_seeds]
        if len(set(seeds)) != len(seeds):
            raise ValueError(f"seed and extra_seeds must all be different, got {seeds}")
        return self


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
    tap_share_high_pct: float = Field(gt=0, le=100)
    casual_slowdown_warn: float = Field(gt=1)
    event_impact_warn_pct: float = Field(gt=0)
    event_checkpoint_hours: float = Field(default=168, gt=0)
    dead_zone_share_warn_pct: float = Field(default=25, ge=0, le=100)
    dead_zone_wall_factor: float = Field(default=3, gt=1)
    offline_share_warn_pct: float = Field(default=75, ge=0, le=100)
    stage_share_warn_pct: float = Field(default=55, gt=0, le=100)
    prestige_min_speedup: float = Field(default=1.4, gt=1)
    prestige_min_gap_hours: float = Field(default=24, ge=0)

    @model_validator(mode="after")
    def _bounds(self) -> AnalysisConfig:
        if self.tap_share_low_pct >= self.tap_share_high_pct:
            raise ValueError("tap_share_low_pct must be below tap_share_high_pct")
        return self


LangCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(_[A-Z]{2})?$")]


class LocalizationConfig(_Model):
    default_language: LangCode = "en"  # the language the names in this file are written in
    languages: list[LangCode] = Field(default_factory=lambda: ["en"])
    # folder with <lang>.yaml files (KEY: text), relative to the config file
    translations_dir: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_/-]*$")] = "translations"

    @model_validator(mode="after")
    def _langs(self) -> LocalizationConfig:
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("localization.languages must be unique")
        if self.default_language not in self.languages:
            raise ValueError("localization.default_language must be one of localization.languages")
        return self


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
    profiles: dict[Ident, SessionProfile] = Field(min_length=1)
    strategies: dict[Ident, StrategyConfig] = Field(min_length=1)
    strategy_params: StrategyParams
    simulation: SimulationConfig
    pacing: PacingConfig
    analysis: AnalysisConfig
    localization: LocalizationConfig = Field(default_factory=LocalizationConfig)

    @field_validator("staff_milestones")
    @classmethod
    def _sorted_milestones(cls, v: list[Milestone]) -> list[Milestone]:
        return sorted(v, key=lambda m: m.level)

    @model_validator(mode="after")
    def _cross_refs(self) -> EconomyConfig:
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
        all_ids += [pk.id for pk in self.prestige.perks]
        for t in self.tiers:
            all_ids += [s.id for s in t.staff] + [u.id for u in t.upgrades] + [m.id for m in t.managers]
        dupes = {i for i in all_ids if all_ids.count(i) > 1}
        if dupes:
            raise ValueError(f"item ids must be globally unique, duplicates: {sorted(dupes)}")
        # ids become translation keys (upper case, "-" -> "_"), so they must stay unique after that
        norm = [i.upper().replace("-", "_") for i in all_ids]
        clashes = sorted({i for i, n in zip(all_ids, norm, strict=True) if norm.count(n) > 1})
        if clashes:
            raise ValueError(f"ids must differ by more than letter case or '-' vs '_': {clashes}")
        if self.prestige.required_tier not in tier_ids:
            raise ValueError(f"prestige.required_tier '{self.prestige.required_tier}' is not a tier")
        event_ids = [e.id for e in self.events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError(f"event ids must be unique, got {event_ids}")
        for e in self.events:
            if e.min_tier not in tier_ids:
                raise ValueError(f"event {e.id}: min_tier '{e.min_tier}' is not a tier")
        for name, s in self.strategies.items():
            if s.profile not in self.profiles:
                raise ValueError(f"strategy {name}: unknown profile '{s.profile}'")
        if self.pacing.reference_strategy not in self.strategies:
            raise ValueError("pacing.reference_strategy must be one of the strategies")
        valid_ms = {"first_upgrade", "first_prestige", *tier_ids[1:]}
        for target in self.pacing.targets:
            if target.milestone not in valid_ms:
                raise ValueError(f"target milestone '{target.milestone}' must be one of {sorted(valid_ms)}")
        self._check_levers(tier_ids)
        return self

    def _check_levers(self, tier_ids: list[str]) -> None:
        """Every pacing lever must point at existing numeric values (fail now, not during tuning)."""
        data = self.model_dump()
        for t in self.pacing.targets:
            for lever in [t.lever] if isinstance(t.lever, str) else t.lever:
                if lever.startswith("tier:"):
                    if lever.split(":", 1)[1] not in tier_ids:
                        raise ValueError(f"target {t.milestone}: lever '{lever}' names an unknown tier")
                    continue
                paths = expand_path(data, lever)
                if not paths:
                    raise ValueError(f"target {t.milestone}: lever path '{lever}' matches nothing in the config")
                for p in paths:
                    v = get_path(data, p)
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        raise ValueError(f"target {t.milestone}: lever path '{p}' is not a number")

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
                if isinstance(node, dict):
                    keys = [str(k) for k in node]
                elif isinstance(node, list):
                    keys = [it["id"] if isinstance(it, dict) and "id" in it else str(i) for i, it in enumerate(node)]
                else:
                    continue
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
    """Write a dotted path. Missing intermediate keys raise ``KeyError`` (no silent typos)."""
    parts = path.split(".")
    cur: Any = data
    for part in parts[:-1]:
        cur = _resolve_container(cur, part)
    last = parts[-1]
    if isinstance(cur, list):
        for i, item in enumerate(cur):
            if isinstance(item, dict) and item.get("id") == last:
                cur[i] = value
                return
        if last.isdigit() and int(last) < len(cur):
            cur[int(last)] = value
            return
        raise KeyError(f"no list item with id '{last}'")
    if not isinstance(cur, dict):
        raise KeyError(f"'{path}' does not point into a mapping")
    cur[last] = value
