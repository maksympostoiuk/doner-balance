"""Time-step simulation of one player over N calendar days.

Loop structure:

* offline gap  -> step ``dt_offline_seconds``; managed tiers accumulate offline earnings
  (capped), NPC events can fire and wait for the next session.
* session start -> offline earnings are credited, pending NPC events are answered.
* active play  -> step ``dt_active_seconds``; automated + tap income, NPC events,
  purchases chosen by the strategy, prestige decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import EconomyConfig
from .events import EventEngine, EventStats, expire_modifiers
from .model import Economy, GameState, Option
from .sessions import session_rng, session_schedule
from .strategies import Strategy, make_strategy


def milestone_names(cfg: EconomyConfig) -> list[str]:
    """All milestones the simulator records, in progression order (tier ids come from the config)."""
    return ["first_upgrade", "first_manager", *[t.id for t in cfg.tiers[1:]], "first_prestige"]


@dataclass
class SimResult:
    strategy: str
    seed: int
    disabled_events: tuple[str, ...]
    timeseries: pd.DataFrame
    purchases: pd.DataFrame
    events: pd.DataFrame
    milestones: dict[str, tuple[float, float]]  # name -> (calendar_h, active_h)
    event_stats: dict[str, EventStats]
    final: dict[str, float] = field(default_factory=dict)
    sessions: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per finished session

    def milestone_hours(self, name: str, clock: str = "calendar") -> float | None:
        m = self.milestones.get(name)
        if m is None:
            return None
        return m[0] if clock == "calendar" else m[1]


class Simulator:
    def __init__(
        self,
        cfg: EconomyConfig,
        strategy: str,
        *,
        seed: int | None = None,
        disabled_events: tuple[str, ...] | list[str] = (),
        days: float | None = None,
        stop_when: str | None = None,
    ):
        self.cfg = cfg
        self.stop_when = stop_when  # end the run as soon as this milestone is reached
        self.stopped = False
        self.econ = Economy(cfg)
        self.strategy: Strategy = make_strategy(self.econ, strategy)
        self.profile = self.strategy.profile
        self.seed = cfg.simulation.seed if seed is None else seed
        self.disabled = tuple(sorted(disabled_events))
        self.days = cfg.simulation.days if days is None else days
        self.events = EventEngine(self.econ, self.seed, set(self.disabled))

        self.st: GameState = self.econ.new_state()
        self.t = 0.0
        self.active_t = 0.0
        self.target: Option | None = None
        self.earned = {"tap": 0.0, "auto": 0.0, "offline": 0.0}
        self.milestones: dict[str, tuple[float, float]] = {}
        self.purchases: list[dict] = []
        self.session_rows: list[dict] = []
        self.rows: list[dict] = []
        self._next_record = 0.0
        self._refresh()

    # ------------------------------------------------------------------ helpers
    def _refresh(self) -> None:
        """Recompute cached incomes after a state change."""
        self.auto, self.tap = self.econ.active_income(self.st, self.profile)
        self.offline_rate = self.econ.offline_rate(self.st)
        self.cap_s = self.econ.offline_cap_hours(self.st) * 3600.0
        self.emult = self.st.event_mult()

    def _mark(self, name: str) -> None:
        if name not in self.milestones:
            self.milestones[name] = (self.t / 3600.0, self.active_t / 3600.0)
            if name == self.stop_when:
                self.stopped = True

    def _record(self, active: bool, force: bool = False) -> None:
        if not force and self.t < self._next_record:
            return
        self._next_record = self.t + self.cfg.simulation.record_every_seconds
        st = self.st
        self.rows.append(
            {
                "t_h": self.t / 3600.0,
                "day": self.t / 86400.0,
                "active_h": self.active_t / 3600.0,
                "is_active": active,
                "cash": st.cash,
                "auto_income": self.auto * self.emult,
                "tap_income": self.tap * self.emult,
                "active_income": (self.auto + self.tap) * self.emult,
                "offline_rate": self.offline_rate * self.emult,
                "effective_rate": self.strategy.effective_rate(st),
                "event_mult": self.emult,
                "tiers_owned": st.tiers_owned,
                "reputation": st.reputation,
                "prestiges": st.prestiges,
                "lifetime_run": st.lifetime_run,
                "lifetime_total": st.lifetime_total,
                "earned_tap": self.earned["tap"],
                "earned_auto": self.earned["auto"],
                "earned_offline": self.earned["offline"],
            }
        )

    # ------------------------------------------------------------------ actions
    def _buy(self, opt: Option) -> None:
        before = self.strategy.effective_rate(self.st)
        self.econ.apply(self.st, opt)
        after = self.strategy.effective_rate(self.st)
        level = (
            len(self.st.managers)
            if opt.kind == "manager"
            else (self.st.tiers_owned if opt.kind == "tier" else self.st.level(opt.id))
        )
        self.purchases.append(
            {
                "t_h": self.t / 3600.0,
                "active_h": self.active_t / 3600.0,
                "run": self.st.prestiges,
                "kind": opt.kind,
                "item": opt.id,
                "level": level,
                "cost": opt.cost,
                "tier": self.cfg.tiers[opt.tier].id if opt.tier >= 0 else "tap",
                "rate_before": before,
                "rate_after": after,
                "gain_pct": 100.0 * (after - before) / before if before > 0 else float("inf"),
                "payback_h": opt.cost / (after - before) / 3600.0 if after > before else float("inf"),
            }
        )
        self._mark("first_upgrade")
        if opt.kind == "tier":
            self._mark(opt.id)
        if opt.kind == "manager":
            self._mark("first_manager")
        self._refresh()

    def _prestige(self) -> None:
        gain = self.econ.prestige_gain(self.st)
        before = self.strategy.effective_rate(self.st)
        self.st = self.econ.prestige(self.st)
        self._refresh()
        after = self.strategy.effective_rate(self.st)
        self.purchases.append(
            {
                "t_h": self.t / 3600.0,
                "active_h": self.active_t / 3600.0,
                "run": self.st.prestiges,
                "kind": "prestige",
                "item": f"+{gain} {self.cfg.prestige.currency_name}",
                "level": self.st.reputation,
                "cost": 0.0,
                "tier": "prestige",
                "rate_before": before,
                "rate_after": after,
                "gain_pct": float("nan"),
                "payback_h": 0.0,
            }
        )
        self._mark("first_prestige")
        self.target = None

    def _shop(self) -> None:
        for _ in range(self.cfg.simulation.max_purchases_per_tick):
            if self.stopped:
                return
            if self.strategy.should_prestige(self.st):
                self._prestige()
                continue
            if self.target is None:
                self.target = self.strategy.choose(self.st)
            if self.target is None or self.st.cash + 1e-9 < self.target.cost:
                return
            self._buy(self.target)
            self.target = None

    def _handle_events(self) -> None:
        for ev in self.events.pop_pending():
            income = self.auto + self.tap
            options = self.events.affordable(ev, self.st, income)
            resp = self.strategy.choose_response(ev, self.st, options, income)
            self.events.resolve(ev, resp, self.st, self.t, income)
            self.target = None  # cash changed: re-plan
        self._refresh()

    # ------------------------------------------------------------------ phases
    def _offline(self, until: float, collect: bool = True) -> float:
        """Advance through an offline gap; returns the earnings to credit at the next session.

        ``collect=False`` is used for the time after the last session: nothing is credited
        there, so no NPC events fire and no income is attributed to them.
        """
        dt0 = self.cfg.simulation.dt_offline_seconds
        pending = 0.0
        elapsed = 0.0
        while self.t < until:
            dt = min(dt0, until - self.t)
            if expire_modifiers(self.st, self.t):
                self.emult = self.st.event_mult()
            credit_dt = max(min(dt, self.cap_s - elapsed), 0.0)
            if collect and credit_dt > 0 and self.offline_rate > 0:
                pending += self.offline_rate * self.emult * credit_dt
                self.events.account_income(self.st, self.offline_rate, credit_dt)
            if collect:
                self.events.roll(self.st, self.t, dt)
            elapsed += dt
            self.t += dt
            self._record(active=False)
        return pending

    def _active(self, until: float) -> None:
        dt0 = self.cfg.simulation.dt_active_seconds
        replan_every = self.cfg.simulation.replan_every_seconds
        next_replan = self.t + replan_every
        while self.t < until and not self.stopped:
            dt = min(dt0, until - self.t)
            if expire_modifiers(self.st, self.t):
                self.emult = self.st.event_mult()
            a, tp = self.auto * self.emult * dt, self.tap * self.emult * dt
            self.st.earn(a + tp)
            self.earned["auto"] += a
            self.earned["tap"] += tp
            self.events.account_income(self.st, self.auto + self.tap, dt)
            self.events.roll(self.st, self.t, dt)
            self.t += dt
            self.active_t += dt
            if self.events.pending:
                self._handle_events()
            if self.t >= next_replan:
                self.target = None
                next_replan = self.t + replan_every
            self._shop()
            self._record(active=True)

    def run(self) -> SimResult:
        horizon = self.days * 86400.0
        self._record(active=True, force=True)
        for start, end in session_schedule(self.profile, self.days, session_rng(self.seed)):
            if self.stopped:
                break
            if start > self.t:
                credited = self._offline(start)
                self.st.earn(credited)
                self.earned["offline"] += credited
            self._refresh()
            self._handle_events()
            self.target = None
            self._shop()
            self._active(end)
            self.session_rows.append(
                {
                    "session": len(self.session_rows),
                    "end_h": self.t / 3600.0,
                    "active_h": self.active_t / 3600.0,
                    "lifetime_total": self.st.lifetime_total,
                    "effective_rate": self.strategy.effective_rate(self.st),
                    "tiers_owned": self.st.tiers_owned,
                    "prestiges": self.st.prestiges,
                }
            )
        if self.t < horizon and not self.stopped:
            self._offline(horizon, collect=False)  # trailing offline time is never collected
        self._record(active=False, force=True)
        return self._result()

    def _result(self) -> SimResult:
        ev = pd.DataFrame(
            [r.__dict__ for r in self.events.log],
            columns=["t", "event", "response", "cost", "cash_lost", "cash_gained", "modifiers"],
        )
        if not ev.empty:
            ev["t_h"] = ev.pop("t") / 3600.0
        purchases = pd.DataFrame(
            self.purchases,
            columns=[
                "t_h",
                "active_h",
                "run",
                "kind",
                "item",
                "level",
                "cost",
                "tier",
                "rate_before",
                "rate_after",
                "gain_pct",
                "payback_h",
            ],
        )
        st = self.st
        final = {
            "lifetime_total": st.lifetime_total,
            "reputation": float(st.reputation),
            "prestiges": float(st.prestiges),
            "tiers_owned": float(st.tiers_owned),
            "active_income": (self.auto + self.tap),
            "effective_rate": self.strategy.effective_rate(st),
            "active_hours": self.active_t / 3600.0,
            **{f"earned_{k}": v for k, v in self.earned.items()},
        }
        return SimResult(
            self.strategy.name,
            self.seed,
            self.disabled,
            pd.DataFrame(self.rows),
            purchases,
            ev,
            dict(self.milestones),
            self.events.stats,
            final,
            pd.DataFrame(self.session_rows),
        )


def simulate(
    cfg: EconomyConfig,
    strategy: str,
    *,
    seed: int | None = None,
    disabled_events: tuple[str, ...] | list[str] = (),
    days: float | None = None,
    stop_when: str | None = None,
) -> SimResult:
    return Simulator(cfg, strategy, seed=seed, disabled_events=disabled_events, days=days, stop_when=stop_when).run()
