"""NPC event engine: suppliers, sanitary inspector, mafia, rival burger place.

Each event type owns an independent RNG stream derived from (seed, event id), so
disabling one event type (for impact analysis) does not shift the random draws of
the others.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import numpy as np

from . import formulas as F
from .config import NpcEvent, Response
from .model import Economy, GameState, Modifier


@dataclass
class EventStats:
    fired: int = 0
    responses: dict[str, int] = field(default_factory=dict)
    spent: float = 0.0  # coins paid for responses
    cash_lost: float = 0.0  # coins lost to bad outcomes
    cash_gained: float = 0.0
    income_delta: float = 0.0  # integrated (mult - 1) * income: <0 lost, >0 gained

    @property
    def net(self) -> float:
        return self.income_delta + self.cash_gained - self.spent - self.cash_lost


@dataclass
class EventRecord:
    t: float
    event: str
    response: str
    cost: float
    cash_lost: float
    cash_gained: float
    modifiers: str


def event_rng(seed: int, event_id: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(event_id.encode())])


class EventEngine:
    def __init__(self, econ: Economy, seed: int, disabled: set[str] | frozenset[str] = frozenset()):
        self.econ = econ
        self.events = [e for e in econ.cfg.events if e.enabled and e.id not in disabled]
        self.rngs = {e.id: event_rng(seed, e.id) for e in self.events}
        self.last_fired: dict[str, float] = {}
        self.pending: dict[str, float] = {}
        self.stats: dict[str, EventStats] = {e.id: EventStats() for e in econ.cfg.events}
        self.log: list[EventRecord] = []

    def eligible(self, ev: NpcEvent, st: GameState, t: float) -> bool:
        if ev.id in self.pending:
            return False
        if st.tiers_owned <= self.econ.cfg.tier_index(ev.min_tier):
            return False
        if t < ev.min_hours_since_start * 3600.0:
            return False
        last = self.last_fired.get(ev.id)
        if last is not None and t - last < ev.cooldown_hours * 3600.0:
            return False
        return True

    def roll(self, st: GameState, t: float, dt: float) -> None:
        """Poisson arrivals over ``dt`` seconds; fired events become pending."""
        for ev in self.events:
            if ev.rate_per_hour <= 0 or not self.eligible(ev, st, t):
                continue
            p = 1.0 - math.exp(-ev.rate_per_hour * dt / 3600.0)
            if self.rngs[ev.id].random() < p:
                self.pending[ev.id] = t
                self.last_fired[ev.id] = t
                self.stats[ev.id].fired += 1

    def pop_pending(self) -> list[NpcEvent]:
        out = [e for e in self.events if e.id in self.pending]
        self.pending.clear()
        return out

    # ------------------------------------------------------------------ resolution
    def affordable(self, ev: NpcEvent, st: GameState, income: float) -> list[Response]:
        ok = [r for r in ev.responses if F.amount_value(r.cost, st.cash, income) <= st.cash + 1e-9]
        return ok

    def resolve(self, ev: NpcEvent, resp: Response, st: GameState, t: float, income: float) -> EventRecord:
        """Apply ``resp`` to the state. ``income`` is the coins/sec used by income_seconds amounts."""
        rng = self.rngs[ev.id]
        stats = self.stats[ev.id]
        stats.responses[resp.id] = stats.responses.get(resp.id, 0) + 1
        cost = min(F.amount_value(resp.cost, st.cash, income), max(st.cash, 0.0))
        st.cash -= cost
        stats.spent += cost
        lost = gained = 0.0
        mods: list[str] = []
        for idx, eff in enumerate(resp.effects):
            # always draw, so the stream stays aligned regardless of probability
            hit = rng.random() < eff.probability
            if not hit:
                continue
            if eff.cash_loss is not None:
                loss = min(F.amount_value(eff.cash_loss, st.cash, income), max(st.cash, 0.0))
                st.cash -= loss
                lost += loss
            if eff.cash_gain is not None:
                g = F.amount_value(eff.cash_gain, st.cash, income)
                st.earn(g)
                gained += g
            if eff.income_mult is not None:
                key = f"{ev.id}.{resp.id}.{idx}"
                if eff.duration_hours is None:
                    stacks = sum(1 for m in st.modifiers if m.key == key)
                    if stacks >= eff.max_stacks:
                        continue
                    st.modifiers.append(Modifier(ev.id, key, eff.income_mult, None))
                else:
                    # a repeated temporary effect refreshes instead of stacking
                    st.modifiers = [m for m in st.modifiers if m.key != key]
                    st.modifiers.append(Modifier(ev.id, key, eff.income_mult, t + eff.duration_hours * 3600.0))
                mods.append(f"x{eff.income_mult:g}" + (f"/{eff.duration_hours:g}h" if eff.duration_hours else "/perm"))
        stats.cash_lost += lost
        stats.cash_gained += gained
        rec = EventRecord(t, ev.id, resp.id, cost, lost, gained, " ".join(mods))
        self.log.append(rec)
        return rec

    def account_income(self, st: GameState, base_income: float, dt: float) -> None:
        """Attribute the income effect of active modifiers to their source event."""
        if not st.modifiers or base_income <= 0:
            return
        for m in st.modifiers:
            if m.source in self.stats:
                self.stats[m.source].income_delta += base_income * (m.mult - 1.0) * dt


def expire_modifiers(st: GameState, t: float) -> bool:
    """Drop expired modifiers; returns True if anything changed."""
    before = len(st.modifiers)
    st.modifiers = [m for m in st.modifiers if m.expires_at is None or m.expires_at > t]
    return len(st.modifiers) != before
