import pytest

from doner_balance.config import config_from_dict
from doner_balance.model import Economy
from doner_balance.simulator import Simulator


def _managed_state(econ):
    st = econ.new_state()
    st.levels["helper"] = 10  # 20 coins/s
    return st


def test_offline_requires_manager(tiny):
    econ = Economy(tiny)
    st = _managed_state(econ)
    assert econ.offline_rate(st) == 0
    st.managers.add("boss")  # x2 income -> 40/s, efficiency 0.5 -> 20/s
    assert econ.offline_rate(st) == pytest.approx(20)


def test_offline_without_manager_requirement(tiny_data):
    tiny_data["offline"]["requires_manager"] = False
    econ = Economy(config_from_dict(tiny_data))
    assert econ.offline_rate(_managed_state(econ)) == pytest.approx(10)


def test_offline_cap_grows_with_managers(tiny):
    econ = Economy(tiny)
    st = econ.new_state()
    assert econ.offline_cap_hours(st) == 2
    st.managers.add("boss")
    assert econ.offline_cap_hours(st) == 3


def _sim_with_state(tiny, st):
    tiny.events.clear()  # no NPC noise
    sim = Simulator(tiny, "best_roi")
    sim.st = st
    sim._refresh()
    return sim


@pytest.mark.parametrize("away_h, expected_h", [(1, 1), (3, 3), (10, 3)])
def test_simulated_offline_earnings_are_capped(tiny, away_h, expected_h):
    econ = Economy(tiny)
    st = _managed_state(econ)
    st.managers.add("boss")  # cap 3 h, 20 coins/s offline
    sim = _sim_with_state(tiny, st)
    pending = sim._offline(away_h * 3600.0)
    assert pending == pytest.approx(20 * expected_h * 3600)


def test_offline_earnings_credited_at_next_session(tiny):
    tiny.events.clear()
    r = Simulator(tiny, "best_roi", days=1).run()
    # offline earnings exist only once a manager was bought, and they are credited at session starts
    assert r.final["earned_offline"] > 0
    assert r.milestones["first_manager"][0] < 5  # bought during the first session
