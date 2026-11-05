"""Player purchase strategies and NPC response policies.

All strategies rank options by their effect on the *effective* income rate: the
average coins per real-world second for the strategy's session profile (active
income weighted by play time + offline income weighted by credited offline time).
That makes managers (offline income) and tap upgrades (active only) comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import formulas as F
from .config import EconomyConfig, NpcEvent, Response, SessionProfile, StrategyConfig, StrategyParams
from .model import Economy, GameState, Option, ProfileWeights, modifier_key, profile_weights


@dataclass(frozen=True)
class Scored:
    option: Option
    gain: float  # increase of effective coins/sec

    @property
    def payback(self) -> float:
        return self.option.cost / self.gain if self.gain > 0 else float("inf")


class Strategy:
    def __init__(self, econ: Economy, name: str, scfg: StrategyConfig, params: StrategyParams):
        self.econ = econ
        self.name = name
        self.policy = scfg.policy
        self.profile: SessionProfile = econ.cfg.profiles[scfg.profile]
        self.params = params
        self.weights: ProfileWeights = profile_weights(self.profile)

    # ------------------------------------------------------------------ evaluation
    def effective_rate(self, st: GameState) -> float:
        return self.econ.effective_rate(st, self.profile, self.weights)

    def scored_options(self, st: GameState) -> list[Scored]:
        base = self.effective_rate(st)
        out = []
        for opt in self.econ.options(st):
            g = self.econ.gain(st, opt, self.profile, self.weights, base)
            if g > base * 1e-9:
                out.append(Scored(opt, g))
        return out

    # ------------------------------------------------------------------ decisions
    def choose(self, st: GameState) -> Option | None:
        """The next item this player is working towards (may not be affordable yet)."""
        scored = self.scored_options(st)
        if not scored:
            return None
        if self.policy == "cheapest":
            return min(scored, key=lambda s: (s.option.cost, s.payback)).option
        best = min(scored, key=lambda s: (s.payback, s.option.cost))
        if self.policy == "best_roi":
            return best.option
        # saver: save for the next tier when it is within reach
        tier = next((s for s in scored if s.option.kind == "tier"), None)
        if tier is None:
            return best.option
        if st.cash >= tier.option.cost:
            return tier.option
        rate = self.effective_rate(st)
        eta_h = (tier.option.cost - st.cash) / rate / 3600.0 if rate > 0 else float("inf")
        if eta_h <= self.params.saver_patience_hours:
            pocket = [s for s in scored if s.option.kind != "tier"
                      and s.option.cost <= self.params.saver_pocket_fraction * tier.option.cost]
            if pocket:
                return min(pocket, key=lambda s: (s.payback, s.option.cost)).option
            return tier.option
        return best.option

    def should_prestige(self, st: GameState) -> bool:
        p = self.econ.cfg.prestige
        if not p.enabled:
            return False
        gain = self.econ.prestige_gain(st)
        if gain < p.min_points:
            return False
        now = F.reputation_multiplier(st.reputation, p.bonus_per_point)
        after = F.reputation_multiplier(st.reputation + gain, p.bonus_per_point)
        return after / now >= self.params.prestige_multiplier_threshold

    # ------------------------------------------------------------------ NPC responses
    def expected_cost(self, resp: Response, st: GameState, income: float, eff_rate: float,
                      ev: NpcEvent | None = None) -> float:
        """Expected coin cost of a response (negative = profitable).

        With ``ev`` given, permanent income effects that already reached ``max_stacks`` are
        worth nothing (paying for a 6th "+3% forever" that cannot stack is a waste).
        """
        horizon = self.params.event_value_horizon_hours * 3600.0
        total = F.amount_value(resp.cost, st.cash, income)
        for idx, eff in enumerate(resp.effects):
            exp = 0.0
            exp += F.amount_value(eff.cash_loss, st.cash, income)
            exp -= F.amount_value(eff.cash_gain, st.cash, income)
            maxed = (ev is not None and eff.duration_hours is None
                     and st.stacks(modifier_key(ev.id, resp.id, idx)) >= eff.max_stacks)
            if eff.income_mult is not None and not maxed:
                dur = horizon if eff.duration_hours is None else min(eff.duration_hours * 3600.0, horizon)
                exp += (1.0 - eff.income_mult) * eff_rate * dur
            total += eff.probability * exp
        return total

    def choose_response(self, ev: NpcEvent, st: GameState, affordable: list[Response], income: float) -> Response:
        if not affordable:
            # nothing affordable: fall back to the cheapest response (cost gets clamped to cash)
            return min(ev.responses, key=lambda r: F.amount_value(r.cost, st.cash, income))
        if self.profile.event_response == "default":
            default = ev.response(ev.default_response)
            if default in affordable:
                return default
            return min(affordable, key=lambda r: F.amount_value(r.cost, st.cash, income))
        eff_rate = self.effective_rate(st)
        return min(affordable, key=lambda r: self.expected_cost(r, st, income, eff_rate, ev))


def make_strategy(econ: Economy, name: str) -> Strategy:
    cfg: EconomyConfig = econ.cfg
    return Strategy(econ, name, cfg.strategies[name], cfg.strategy_params)
