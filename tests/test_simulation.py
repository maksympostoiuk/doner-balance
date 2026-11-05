import math

import pandas as pd
import pytest

from doner_balance.analysis import dead_zones, meaningful_purchases
from doner_balance.events import EventEngine
from doner_balance.model import Economy
from doner_balance.sessions import session_schedule
from doner_balance.simulator import simulate
from doner_balance.strategies import make_strategy


def test_same_seed_is_deterministic(default_cfg):
    a = simulate(default_cfg, "best_roi", days=3)
    b = simulate(default_cfg, "best_roi", days=3)
    pd.testing.assert_frame_equal(a.timeseries, b.timeseries)
    pd.testing.assert_frame_equal(a.purchases, b.purchases)
    pd.testing.assert_frame_equal(a.events, b.events)
    assert a.milestones == b.milestones


def test_different_seed_changes_events(default_cfg):
    a = simulate(default_cfg, "best_roi", days=3, seed=1)
    b = simulate(default_cfg, "best_roi", days=3, seed=2)
    assert not a.events.equals(b.events)


def test_event_streams_are_independent(default_cfg):
    """Disabling one event type must not change the random draws of another."""
    econ = Economy(default_cfg)
    st = econ.new_state()
    st.tiers_owned = 4
    full = EventEngine(econ, seed=5)
    part = EventEngine(econ, seed=5, disabled={"mafia"})
    fired_full, fired_part = [], []
    for i in range(30 * 24 * 60):
        t = i * 60.0
        full.roll(st, t, 60.0)
        part.roll(st, t, 60.0)
        fired_full += [(t, e) for e in full.pending if e != "mafia"]
        fired_part += [(t, e) for e in part.pending]
        full.pending.clear()
        part.pending.clear()
    assert fired_full and fired_full == fired_part


def test_disabled_events_never_fire(default_cfg):
    r = simulate(default_cfg, "best_roi", days=3, disabled_events=[e.id for e in default_cfg.events])
    assert r.events.empty


def test_session_schedule(default_cfg):
    prof = default_cfg.profiles["regular"]
    s = session_schedule(prof, 2)
    assert len(s) == 6
    assert s[0] == (0.0, 600.0)
    assert s[1][0] == pytest.approx(5 * 3600)
    assert s[3][0] == pytest.approx(24 * 3600)


def test_playtime_matches_sessions(default_cfg):
    r = simulate(default_cfg, "casual", days=2)
    prof = default_cfg.profiles["casual"]
    assert r.final["active_hours"] == pytest.approx(2 * prof.sessions_per_day * prof.session_minutes / 60)


def test_progress_is_monotonic_and_milestones_ordered(default_cfg):
    r = simulate(default_cfg, "best_roi", days=2)
    assert r.timeseries["lifetime_total"].is_monotonic_increasing
    ms = r.milestones
    assert ms["first_upgrade"][0] <= ms["cafe"][0] <= ms["restaurant"][0]


def test_stop_when(default_cfg):
    r = simulate(default_cfg, "best_roi", stop_when="cafe")
    assert "cafe" in r.milestones and "restaurant" not in r.milestones


def test_strategy_choices(default_cfg):
    econ = Economy(default_cfg)
    st = econ.new_state()
    st.cash = 0
    best = make_strategy(econ, "best_roi")
    cheap = make_strategy(econ, "cheapest")
    scored = best.scored_options(st)
    assert best.choose(st) == min(scored, key=lambda s: (s.payback, s.option.cost)).option
    assert cheap.choose(st) == min(scored, key=lambda s: (s.option.cost, s.payback)).option


def test_saver_waits_for_close_tier(default_cfg):
    econ = Economy(default_cfg)
    saver = make_strategy(econ, "saver")
    st = econ.new_state()
    st.levels.update(nephew=40, grill_cook=30, cashier=20)
    st.cash = default_cfg.tiers[1].unlock_cost * 0.99  # almost there
    choice = saver.choose(st)
    assert choice.kind == "tier" or choice.cost <= default_cfg.strategy_params.saver_pocket_fraction * 30000


def test_meaningful_progress_is_cumulative(default_cfg):
    rows = []
    for i in range(12):  # twelve +1% purchases, one per minute of play
        rows.append({"t_h": i / 60, "active_h": i / 60, "kind": "staff", "item": "x", "rate_before": 1.0,
                     "rate_after": 1.01, "cost": 1.0})
    df = pd.DataFrame(rows)
    mp = meaningful_purchases(default_cfg, df)
    # +5% needs 5 compounding +1% steps (1.01^5 = 1.051)
    assert list(mp.index) == [4, 9]


def test_dead_zone_detection(default_cfg):
    r = simulate(default_cfg, "best_roi", days=2)
    z = dead_zones(default_cfg, r)
    thr = default_cfg.analysis.dead_zone_threshold_active_minutes
    assert (z["gap_active_min"] > thr).all()
    assert math.isclose(z["gap_active_min"].sum() <= r.final["active_hours"] * 60, True)


def test_progress_shift_metric():
    import numpy as np

    from doner_balance.analysis import progress_shift_h

    ends = np.array([1.0, 5.0, 10.0, 25.0, 29.0, 34.0])
    without = pd.Series({"sess_end_h": ends, "sess_log_life": np.arange(6.0), "hours_per_session": 8.0})
    same = pd.Series({"sess_end_h": ends, "sess_log_life": np.arange(6.0), "hours_per_session": 8.0})
    behind = pd.Series({"sess_end_h": ends, "sess_log_life": np.arange(6.0) - 1.5, "hours_per_session": 8.0})
    assert progress_shift_h(same, without, 30) == pytest.approx(0)
    # one and a half sessions behind = 12 hours of progress lost
    assert progress_shift_h(behind, without, 30) == pytest.approx(12)


def test_session_log(default_cfg):
    r = simulate(default_cfg, "best_roi", days=2)
    assert len(r.sessions) == 6
    assert r.sessions["lifetime_total"].is_monotonic_increasing
