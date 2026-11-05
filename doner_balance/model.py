"""Game state and income calculations (the economy "rules engine").

Income formulas (all per second):

    tier_auto(t)   = sum_staff(level * income * milestone_mult(level))
                     * (1 + sum income-upgrade bonuses) * (1 + sum speed-upgrade bonuses)
                     * product(manager income_mult)
    global_mult    = product(global_income_mult of unlocked tiers)
                     * (1 + reputation * bonus_per_point)
    auto_income    = sum_t tier_auto(t) * global_mult
    tap_value      = (base + sum flat) * (1 + sum pct) * tap_mult(highest tier) * global_mult
                     + income_share * auto_income
    active_income  = auto_income + taps_per_sec * tap_value
    offline_rate   = sum_{t with manager} tier_auto(t) * global_mult * efficiency

NPC event multipliers are applied on top of these by the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import formulas as F
from .config import EconomyConfig, Manager, SessionProfile, Staff, TapUpgrade, Tier, TierUpgrade

OptionKind = Literal["tier", "staff", "upgrade", "manager", "tap"]


@dataclass(frozen=True)
class Option:
    """A purchase the player can make right now (maybe not affordable yet)."""

    kind: OptionKind
    id: str
    cost: float
    tier: int  # tier index the item belongs to (-1 for tap upgrades)
    count: int = 1  # levels bought at once (bulk buy); always 1 for tiers and managers


def modifier_key(event_id: str, response_id: str, effect_index: int) -> str:
    """Stacking key of an income modifier created by an NPC event effect."""
    return f"{event_id}.{response_id}.{effect_index}"


@dataclass
class Modifier:
    """Temporary or permanent income multiplier created by an NPC event."""

    source: str  # event id
    key: str  # stacking key (event.response.effect)
    mult: float
    expires_at: float | None  # calendar seconds, None = permanent


@dataclass
class GameState:
    cash: float = 0.0
    tiers_owned: int = 1
    levels: dict[str, int] = field(default_factory=dict)
    managers: set[str] = field(default_factory=set)
    reputation: int = 0
    lifetime_run: float = 0.0
    lifetime_total: float = 0.0
    prestiges: int = 0
    modifiers: list[Modifier] = field(default_factory=list)

    def clone(self) -> GameState:
        return GameState(
            cash=self.cash,
            tiers_owned=self.tiers_owned,
            levels=dict(self.levels),
            managers=set(self.managers),
            reputation=self.reputation,
            lifetime_run=self.lifetime_run,
            lifetime_total=self.lifetime_total,
            prestiges=self.prestiges,
            modifiers=list(self.modifiers),
        )

    def level(self, item_id: str) -> int:
        return self.levels.get(item_id, 0)

    def earn(self, amount: float) -> None:
        self.cash += amount
        if amount > 0:
            self.lifetime_run += amount
            self.lifetime_total += amount

    def stacks(self, key: str) -> int:
        return sum(1 for m in self.modifiers if m.key == key)

    def event_mult(self) -> float:
        m = 1.0
        for mod in self.modifiers:
            m *= mod.mult
        return m


@dataclass(frozen=True)
class ProfileWeights:
    """Fractions of real time that count as active play / credited offline time."""

    active_fraction: float
    offline_seconds_per_day: tuple[float, ...]  # raw gap lengths between sessions

    def offline_fraction(self, cap_hours: float) -> float:
        cap = cap_hours * 3600.0
        return sum(min(g, cap) for g in self.offline_seconds_per_day) / 86400.0


def profile_weights(profile: SessionProfile) -> ProfileWeights:
    n = profile.sessions_per_day
    window = (profile.day_end_hour - profile.day_start_hour) * 3600.0
    spacing = window / n
    active = n * profile.session_minutes * 60.0
    gaps = []
    for i in range(n):
        start = profile.day_start_hour * 3600.0 + i * spacing
        end = start + profile.session_minutes * 60.0
        nxt = (
            profile.day_start_hour * 3600.0 + (i + 1) * spacing
            if i + 1 < n
            else 86400.0 + profile.day_start_hour * 3600.0
        )
        gaps.append(max(nxt - end, 0.0))
    # missed sessions/days: less play time on average (gaps are kept nominal: an approximation)
    attendance = (1.0 - profile.skip_session_chance) * (1.0 - profile.skip_day_chance)
    return ProfileWeights(active_fraction=attendance * active / 86400.0, offline_seconds_per_day=tuple(gaps))


class Economy:
    """Stateless calculator over an :class:`EconomyConfig`."""

    def __init__(self, cfg: EconomyConfig):
        self.cfg = cfg
        self.staff: dict[str, tuple[int, Staff]] = {}
        self.upgrades: dict[str, tuple[int, TierUpgrade]] = {}
        self.managers: dict[str, tuple[int, Manager]] = {}
        self.tap_upgrades: dict[str, TapUpgrade] = {u.id: u for u in cfg.tap.upgrades}
        for ti, tier in enumerate(cfg.tiers):
            for s in tier.staff:
                self.staff[s.id] = (ti, s)
            for u in tier.upgrades:
                self.upgrades[u.id] = (ti, u)
            for m in tier.managers:
                self.managers[m.id] = (ti, m)
        self.milestones = cfg.staff_milestones

    # ------------------------------------------------------------------ state
    def new_state(self, reputation: int = 0, lifetime_total: float = 0.0, prestiges: int = 0) -> GameState:
        st = GameState(
            cash=self.cfg.currency.start_cash, reputation=reputation, lifetime_total=lifetime_total, prestiges=prestiges
        )
        self._grant_free_levels(st, self.cfg.tiers[0])
        return st

    def _grant_free_levels(self, st: GameState, tier: Tier) -> None:
        for sid, lv in tier.free_staff_levels.items():
            st.levels[sid] = max(st.level(sid), lv)

    # ------------------------------------------------------------------ income
    def tier_auto_income(self, st: GameState, ti: int) -> float:
        """Automated coins/sec of tier ``ti`` before global multipliers."""
        if ti >= st.tiers_owned:
            return 0.0
        tier = self.cfg.tiers[ti]
        base = 0.0
        for s in tier.staff:
            base += F.staff_income(st.level(s.id), s.income, self.milestones)
        if base == 0.0:
            return 0.0
        inc = 1.0
        spd = 1.0
        for u in tier.upgrades:
            lv = st.level(u.id)
            if lv:
                if u.kind == "income":
                    inc += u.value * lv
                else:
                    spd += u.value * lv
        mgr = 1.0
        for m in tier.managers:
            if m.id in st.managers:
                mgr *= m.income_mult
        return base * inc * spd * mgr

    def global_mult(self, st: GameState) -> float:
        m = 1.0
        for ti in range(st.tiers_owned):
            m *= self.cfg.tiers[ti].global_income_mult
        if self.cfg.prestige.enabled:
            m *= F.reputation_multiplier(st.reputation, self.cfg.prestige.bonus_per_point)
        return m

    def auto_income(self, st: GameState) -> float:
        return sum(self.tier_auto_income(st, ti) for ti in range(st.tiers_owned)) * self.global_mult(st)

    def income_share(self, st: GameState) -> float:
        return sum(u.value * st.level(u.id) for u in self.cfg.tap.upgrades if u.kind == "income_share")

    def tap_value(self, st: GameState, auto: float | None = None) -> float:
        flat = self.cfg.tap.base_value
        pct = 1.0
        for u in self.cfg.tap.upgrades:
            lv = st.level(u.id)
            if not lv:
                continue
            if u.kind == "flat":
                flat += u.value * lv
            elif u.kind == "pct":
                pct += u.value * lv
        tier_mult = self.cfg.tiers[st.tiers_owned - 1].tap_mult
        if auto is None:
            auto = self.auto_income(st)
        return flat * pct * tier_mult * self.global_mult(st) + self.income_share(st) * auto

    def tap_income(self, st: GameState, profile: SessionProfile, auto: float | None = None) -> float:
        return profile.taps_per_second * profile.tapping_share * self.tap_value(st, auto)

    def active_income(self, st: GameState, profile: SessionProfile) -> tuple[float, float]:
        """(automated, tap) coins/sec while the player is in the game, without event modifiers."""
        auto = self.auto_income(st)
        return auto, self.tap_income(st, profile, auto)

    def managed(self, st: GameState, ti: int) -> bool:
        if not self.cfg.offline.requires_manager:
            return True
        tier = self.cfg.tiers[ti]
        return any(m.id in st.managers and m.enables_offline for m in tier.managers)

    def offline_rate(self, st: GameState) -> float:
        """Coins/sec credited while offline (already includes efficiency)."""
        base = sum(self.tier_auto_income(st, ti) for ti in range(st.tiers_owned) if self.managed(st, ti))
        return base * self.global_mult(st) * self.cfg.offline.efficiency

    def offline_cap_hours(self, st: GameState) -> float:
        cap = self.cfg.offline.cap_hours
        for mid in st.managers:
            cap += self.managers[mid][1].offline_cap_hours_add
        return cap

    def effective_rate(self, st: GameState, profile: SessionProfile, weights: ProfileWeights) -> float:
        """Average coins per real second for this player profile (used for ROI decisions)."""
        auto, tap = self.active_income(st, profile)
        off = self.offline_rate(st) * weights.offline_fraction(self.offline_cap_hours(st))
        return weights.active_fraction * (auto + tap) + off

    # ------------------------------------------------------------------ purchases
    def options(self, st: GameState) -> list[Option]:
        opts: list[Option] = []
        cfg = self.cfg
        if st.tiers_owned < len(cfg.tiers):
            nxt = cfg.tiers[st.tiers_owned]
            opts.append(Option("tier", nxt.id, nxt.unlock_cost, st.tiers_owned))
        for u in cfg.tap.upgrades:
            lv = st.level(u.id)
            if u.max_level is None or lv < u.max_level:
                opts.append(Option("tap", u.id, F.level_cost(u.base_cost, u.cost_growth, lv), -1))
        for ti in range(st.tiers_owned):
            tier = cfg.tiers[ti]
            for s in tier.staff:
                lv = st.level(s.id)
                if s.max_level is None or lv < s.max_level:
                    opts.append(Option("staff", s.id, F.level_cost(s.base_cost, s.cost_growth, lv), ti))
            for up in tier.upgrades:
                lv = st.level(up.id)
                if up.max_level is None or lv < up.max_level:
                    opts.append(Option("upgrade", up.id, F.level_cost(up.base_cost, up.cost_growth, lv), ti))
            for m in tier.managers:
                if m.id not in st.managers:
                    opts.append(Option("manager", m.id, m.cost, ti))
        return opts

    def apply(self, st: GameState, opt: Option, pay: bool = True) -> None:
        """Apply a purchase to ``st`` (mutates). Raises if not affordable when ``pay``."""
        if pay:
            if st.cash + 1e-9 < opt.cost:
                raise ValueError(f"cannot afford {opt.id}: cash {st.cash:.2f} < cost {opt.cost:.2f}")
            st.cash -= opt.cost
        if opt.kind == "tier":
            st.tiers_owned += 1
            self._grant_free_levels(st, self.cfg.tiers[st.tiers_owned - 1])
        elif opt.kind == "manager":
            st.managers.add(opt.id)
        else:
            st.levels[opt.id] = st.level(opt.id) + opt.count

    def leveled_item(self, opt: Option) -> TapUpgrade | Staff | TierUpgrade | None:
        if opt.kind == "tap":
            return self.tap_upgrades[opt.id]
        if opt.kind == "staff":
            return self.staff[opt.id][1]
        if opt.kind == "upgrade":
            return self.upgrades[opt.id][1]
        return None

    def bulk(self, st: GameState, opt: Option, amount: int | str) -> Option:
        """Turn a single-level option into a bulk purchase (x10, x100 or "max").

        ``max`` buys as many levels as the cash allows (at least one); a fixed amount needs the
        whole bundle to be affordable, like the x10 button in the game. Bundles never exceed
        ``max_level``. Tiers and managers are always bought one at a time.
        """
        it = self.leveled_item(opt)
        if it is None or amount == 1:
            return opt
        level = st.level(opt.id)
        remaining = (it.max_level - level) if it.max_level is not None else 10**9
        if amount == "max":
            n = min(F.max_affordable(it.base_cost, it.cost_growth, level, st.cash), remaining)
            if n < 1:
                return opt
        else:
            n = min(int(amount), remaining)
        if n <= 1:
            return opt
        return Option(opt.kind, opt.id, F.bulk_cost(it.base_cost, it.cost_growth, level, n), opt.tier, n)

    def gain(
        self,
        st: GameState,
        opt: Option,
        profile: SessionProfile,
        weights: ProfileWeights,
        base_rate: float | None = None,
    ) -> float:
        """Increase of the effective income rate if ``opt`` were bought (cost ignored)."""
        if base_rate is None:
            base_rate = self.effective_rate(st, profile, weights)
        tmp = st.clone()
        self.apply(tmp, opt, pay=False)
        return self.effective_rate(tmp, profile, weights) - base_rate

    # ------------------------------------------------------------------ prestige
    def prestige_gain(self, st: GameState) -> int:
        p = self.cfg.prestige
        if not p.enabled:
            return 0
        if st.tiers_owned <= self.cfg.tier_index(p.required_tier):
            return 0
        total = F.reputation_total(st.lifetime_total, p.base_requirement, p.exponent, p.scale)
        return max(total - st.reputation, 0)

    def prestige(self, st: GameState) -> GameState:
        """Sell the empire: returns a fresh state that keeps reputation and all-time earnings."""
        gain = self.prestige_gain(st)
        return self.new_state(
            reputation=st.reputation + gain, lifetime_total=st.lifetime_total, prestiges=st.prestiges + 1
        )
