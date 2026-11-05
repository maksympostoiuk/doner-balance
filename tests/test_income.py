import pytest

from doner_balance.model import Economy, Option, profile_weights


def test_tier_income_combines_staff_upgrades_and_managers(tiny):
    e = Economy(tiny)
    st = e.new_state()
    assert e.tier_auto_income(st, 0) == 0
    st.levels.update(helper=10, sauce=2, grill=1)
    # 10 levels * 1 coin/s * milestone x2 = 20; * (1 + 0.1*2) * (1 + 0.5*1) = 36
    assert e.tier_auto_income(st, 0) == pytest.approx(36)
    st.managers.add("boss")
    assert e.tier_auto_income(st, 0) == pytest.approx(72)
    assert e.auto_income(st) == pytest.approx(72)


def test_unowned_tier_produces_nothing(tiny):
    e = Economy(tiny)
    st = e.new_state()
    st.levels["waiter"] = 5
    assert e.tier_auto_income(st, 1) == 0


def test_global_multiplier_tiers_and_reputation(tiny):
    e = Economy(tiny)
    st = e.new_state()
    assert e.global_mult(st) == pytest.approx(1)
    st.tiers_owned = 2
    st.reputation = 5  # +10% each
    assert e.global_mult(st) == pytest.approx(1.5 * 1.5)


def test_tap_value(tiny):
    e = Economy(tiny)
    st = e.new_state()
    assert e.tap_value(st) == pytest.approx(1)
    st.levels.update(knife=2, wrap=2)  # (1+2) * (1+1.0) = 6
    assert e.tap_value(st) == pytest.approx(6)
    st.tiers_owned = 2
    st.levels["waiter"] = 1  # 20 coins/s automated
    # (3*2) * tap_mult 3 * global 1.5 = 27, plus income share 0 so far
    assert e.tap_value(st) == pytest.approx(27)
    st.levels["show"] = 1  # + 0.1 * auto (20*1.5 = 30) = 3
    assert e.tap_value(st) == pytest.approx(30)


def test_active_income_uses_profile_tapping(tiny):
    e = Economy(tiny)
    st = e.new_state()
    prof = tiny.profiles["regular"]
    auto, tap = e.active_income(st, prof)
    assert auto == 0
    assert tap == pytest.approx(prof.taps_per_second * prof.tapping_share * 1)


def test_options_and_purchases(tiny):
    e = Economy(tiny)
    st = e.new_state()
    opts = {o.id: o for o in e.options(st)}
    assert opts["helper"].cost == pytest.approx(10)
    assert opts["cafe"].kind == "tier"
    assert "waiter" not in opts  # cafe items appear only after unlocking
    st.cash = 15
    e.apply(st, opts["helper"])
    assert st.cash == pytest.approx(5) and st.level("helper") == 1
    assert {o.id: o for o in e.options(st)}["helper"].cost == pytest.approx(11)
    with pytest.raises(ValueError):
        e.apply(st, opts["cafe"])
    st.cash = 1000
    e.apply(st, opts["cafe"])
    assert st.tiers_owned == 2 and st.level("waiter") == 1  # free staff level granted


def test_max_level_removes_option(tiny_data):
    from doner_balance.config import config_from_dict

    tiny_data["tap"]["upgrades"][0]["max_level"] = 1
    e = Economy(config_from_dict(tiny_data))
    st = e.new_state()
    st.levels["knife"] = 1
    assert "knife" not in {o.id for o in e.options(st)}


def test_gain_is_effective_rate_delta(tiny):
    e = Economy(tiny)
    st = e.new_state()
    prof = tiny.profiles["regular"]
    w = profile_weights(prof)
    g = e.gain(st, Option("staff", "helper", 10, 0), prof, w)
    # one helper level = 1 coin/s while playing, no offline without manager
    assert g == pytest.approx(w.active_fraction * 1)


def test_profile_weights_regular(default_cfg):
    w = profile_weights(default_cfg.profiles["regular"])
    assert w.active_fraction == pytest.approx(30 / 1440)
    # 3 sessions at 08:00, 13:00, 18:00 of 10 min: gaps 4h50, 4h50, 13h50
    assert w.offline_seconds_per_day == pytest.approx((17400, 17400, 49800))
    assert w.offline_fraction(2) == pytest.approx(6 / 24)


def test_prestige_gain_and_reset(tiny):
    e = Economy(tiny)
    st = e.new_state()
    st.lifetime_total = 100_000  # sqrt(100000/1000) = 10 points
    assert e.prestige_gain(st) == 0  # cafe (required tier) not owned yet
    st.tiers_owned = 2
    st.levels["helper"] = 50
    st.cash = 123
    assert e.prestige_gain(st) == 10
    new = e.prestige(st)
    assert new.reputation == 10 and new.prestiges == 1
    assert new.tiers_owned == 1 and new.cash == 0 and new.level("helper") == 0
    assert new.lifetime_total == 100_000
    new.tiers_owned = 2
    assert e.prestige_gain(new) == 0  # already cashed in
