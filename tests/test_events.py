import math

import pytest

from doner_balance.config import Amount, Effect, NpcEvent, Response, config_from_dict
from doner_balance.events import EventEngine, event_rng, expire_modifiers
from doner_balance.model import Economy
from doner_balance.strategies import make_strategy


def _event(responses, **kw) -> NpcEvent:
    base = dict(
        id="test",
        name="Test",
        npc="mafia",
        min_tier="kiosk",
        rate_per_hour=1,
        default_response=responses[0].id,
        responses=responses,
    )
    base.update(kw)
    return NpcEvent(**base)


@pytest.fixture
def engine(tiny):
    return EventEngine(Economy(tiny), seed=1)


def test_response_cost_scales_with_cash_and_income(engine, tiny):
    st = Economy(tiny).new_state()
    st.cash = 1000
    ev = _event([Response(id="pay", name="Pay", cost=Amount(flat=10, cash_fraction=0.1, income_seconds=60))])
    engine.stats["test"] = engine.stats[tiny.events[0].id].__class__()
    engine.rngs["test"] = event_rng(1, "test")
    rec = engine.resolve(ev, ev.responses[0], st, t=0, income=2)
    assert rec.cost == pytest.approx(10 + 100 + 120)
    assert st.cash == pytest.approx(1000 - 230)
    assert engine.stats["test"].spent == pytest.approx(230)


def _prep(engine, ev):
    engine.stats[ev.id] = engine.stats[next(iter(engine.stats))].__class__()
    engine.rngs[ev.id] = event_rng(1, ev.id)


def test_certain_and_impossible_outcomes(engine, tiny):
    econ = Economy(tiny)
    sure = _event(
        [
            Response(
                id="r",
                name="R",
                effects=[Effect(probability=1, cash_loss=Amount(cash_fraction=0.5), income_mult=0.5, duration_hours=2)],
            )
        ]
    )
    never = _event(
        [Response(id="r", name="R", effects=[Effect(probability=0, cash_loss=Amount(cash_fraction=0.5))])], id="never"
    )
    _prep(engine, sure)
    _prep(engine, never)
    st = econ.new_state()
    st.cash = 100
    engine.resolve(never, never.responses[0], st, 0, 0)
    assert st.cash == 100 and not st.modifiers
    engine.resolve(sure, sure.responses[0], st, 0, 0)
    assert st.cash == pytest.approx(50)
    assert st.event_mult() == pytest.approx(0.5)
    assert engine.stats["test"].cash_lost == pytest.approx(50)


def test_cash_never_goes_negative(engine, tiny):
    st = Economy(tiny).new_state()
    st.cash = 5
    ev = _event([Response(id="r", name="R", cost=Amount(flat=100), effects=[Effect(cash_loss=Amount(flat=100))])])
    _prep(engine, ev)
    engine.resolve(ev, ev.responses[0], st, 0, 0)
    assert st.cash == 0


def test_timed_modifier_expires_and_refreshes(engine, tiny):
    st = Economy(tiny).new_state()
    ev = _event([Response(id="r", name="R", effects=[Effect(income_mult=1.2, duration_hours=1)])])
    _prep(engine, ev)
    engine.resolve(ev, ev.responses[0], st, 0, 0)
    engine.resolve(ev, ev.responses[0], st, 1800, 0)  # refresh, not stack
    assert st.event_mult() == pytest.approx(1.2)
    assert not expire_modifiers(st, 3600)  # refreshed: now lasts until 5400
    assert expire_modifiers(st, 5400)
    assert st.event_mult() == 1


def test_permanent_modifiers_stack_up_to_max(engine, tiny):
    st = Economy(tiny).new_state()
    ev = _event([Response(id="r", name="R", effects=[Effect(income_mult=1.1, max_stacks=2)])])
    _prep(engine, ev)
    for _ in range(4):
        engine.resolve(ev, ev.responses[0], st, 0, 0)
    assert st.event_mult() == pytest.approx(1.21)
    assert not expire_modifiers(st, 10**9)


def test_income_accounting_attributes_modifiers(engine, tiny):
    st = Economy(tiny).new_state()
    ev = _event([Response(id="r", name="R", effects=[Effect(income_mult=0.8, duration_hours=1)])])
    _prep(engine, ev)
    engine.resolve(ev, ev.responses[0], st, 0, 0)
    engine.account_income(st, base_income=100, dt=10)
    assert engine.stats["test"].income_delta == pytest.approx(-200)


def test_eligibility_rules(tiny_data):
    tiny_data["events"][2].update(min_tier="cafe", min_hours_since_start=1, cooldown_hours=5)
    cfg = config_from_dict(tiny_data)
    econ = Economy(cfg)
    eng = EventEngine(econ, seed=3)
    ev = cfg.events[2]
    st = econ.new_state()
    assert not eng.eligible(ev, st, 7200)  # cafe not owned
    st.tiers_owned = 2
    assert not eng.eligible(ev, st, 1800)  # too early
    assert eng.eligible(ev, st, 7200)
    eng.last_fired[ev.id] = 7200
    assert not eng.eligible(ev, st, 7200 + 4 * 3600)  # cooldown
    assert eng.eligible(ev, st, 7200 + 5 * 3600)


def test_poisson_rate(tiny_data):
    tiny_data["events"] = [tiny_data["events"][0]]
    tiny_data["events"][0].update(rate_per_hour=2, cooldown_hours=0, min_hours_since_start=0)
    cfg = config_from_dict(tiny_data)
    econ = Economy(cfg)
    eng = EventEngine(econ, seed=7)
    st = econ.new_state()
    fired = 0
    for i in range(2000 * 60):  # 2000 hours in 1-minute steps
        eng.roll(st, i * 60.0, 60.0)
        if eng.pending:
            fired += 1
            eng.pending.clear()
    # at most one arrival per 1-minute step: expected 60 * (1 - exp(-2/60)) = 1.967 per hour;
    # 4000 expected arrivals -> sd ~1.6%, allow 4 sd
    expected = 60 * (1 - math.exp(-2 / 60))
    assert fired / 2000 == pytest.approx(expected, rel=0.065)


def test_smart_vs_default_answers(default_cfg):
    econ = Economy(default_cfg)
    st = econ.new_state()
    st.cash = 1e6
    st.levels.update(nephew=50, grill_cook=40)
    st.managers.add("aunt_zina")
    rival = next(e for e in default_cfg.events if e.id == "rival_burger")
    smart = make_strategy(econ, "best_roi")
    casual = make_strategy(econ, "casual")
    inc = sum(econ.active_income(st, smart.profile))
    aff = EventEngine(econ, 1).affordable(rival, st, inc)
    assert casual.choose_response(rival, st, aff, inc).id == rival.default_response == "ignore"
    pick = smart.choose_response(rival, st, aff, inc)
    costs = {r.id: smart.expected_cost(r, st, inc, smart.effective_rate(st)) for r in aff}
    assert costs[pick.id] == min(costs.values())


def test_unaffordable_responses_are_excluded(default_cfg):
    econ = Economy(default_cfg)
    st = econ.new_state()
    st.cash = 0
    supplier = next(e for e in default_cfg.events if e.id == "supplier_deal")
    aff = EventEngine(econ, 1).affordable(supplier, st, income=10)
    assert [r.id for r in aff] == ["decline"]
