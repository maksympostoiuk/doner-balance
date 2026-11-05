import math

import pytest

from doner_balance import formulas as F
from doner_balance.config import Amount, Milestone


def test_level_cost_is_exponential():
    assert F.level_cost(10, 1.15, 0) == pytest.approx(10)
    assert F.level_cost(10, 1.15, 1) == pytest.approx(11.5)
    assert F.level_cost(10, 1.15, 10) == pytest.approx(10 * 1.15**10)


@pytest.mark.parametrize("growth", [1.07, 1.1, 1.15])
@pytest.mark.parametrize("start", [0, 5, 37])
def test_bulk_cost_equals_sum_of_levels(growth, start):
    expected = sum(F.level_cost(25, growth, start + i) for i in range(12))
    assert F.bulk_cost(25, growth, start, 12) == pytest.approx(expected)
    assert F.bulk_cost(25, growth, start, 0) == 0


@pytest.mark.parametrize("cash", [0, 9.99, 10, 50, 1234.5, 1e9])
def test_max_affordable_is_tight(cash):
    n = F.max_affordable(10, 1.1, 3, cash)
    assert F.bulk_cost(10, 1.1, 3, n) <= cash + 1e-9
    assert F.bulk_cost(10, 1.1, 3, n + 1) > cash


def test_milestone_multiplier_stacks():
    ms = [Milestone(level=10, mult=2), Milestone(level=25, mult=2), Milestone(level=100, mult=3)]
    assert F.milestone_multiplier(9, ms) == 1
    assert F.milestone_multiplier(10, ms) == 2
    assert F.milestone_multiplier(30, ms) == 4
    assert F.milestone_multiplier(100, ms) == 12


def test_staff_income_includes_milestones():
    ms = [Milestone(level=10, mult=2)]
    assert F.staff_income(0, 3, ms) == 0
    assert F.staff_income(9, 3, ms) == pytest.approx(27)
    assert F.staff_income(10, 3, ms) == pytest.approx(60)


def test_amount_value_scales_with_economy():
    a = Amount(flat=5, cash_fraction=0.1, income_seconds=60)
    assert F.amount_value(a, cash=1000, income_per_sec=2) == pytest.approx(5 + 100 + 120)
    assert F.amount_value(None, 1000, 2) == 0
    # negative cash never produces a negative fraction
    assert F.amount_value(Amount(cash_fraction=0.5), cash=-10, income_per_sec=0) == 0


def test_reputation_formula():
    assert F.reputation_total(0, 1e12, 0.5, 10) == 0
    assert F.reputation_total(1e12, 1e12, 0.5, 10) == 10
    assert F.reputation_total(4e12, 1e12, 0.5, 10) == 20
    assert F.reputation_total(2e12, 1e12, 0.5, 10) == math.floor(10 * math.sqrt(2))
    assert F.reputation_multiplier(50, 0.02) == pytest.approx(2.0)


def test_offline_earnings_capped_and_scaled():
    assert F.offline_earnings(10, 3600, 0.5, 2) == pytest.approx(18000)
    assert F.offline_earnings(10, 10 * 3600, 0.5, 2) == pytest.approx(10 * 0.5 * 7200)
    assert F.offline_earnings(10, -5, 0.5, 2) == 0
