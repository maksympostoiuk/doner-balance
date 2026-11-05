"""Pure economy formulas. Mirrored 1:1 in the Godot export (see docs/GODOT_EXPORT.md)."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .config import Amount, Milestone


def level_cost(base_cost: float, growth: float, level: int) -> float:
    """Price of buying the next level when ``level`` levels are already owned.

    cost(level) = base_cost * growth ** level
    """
    return base_cost * growth**level


def bulk_cost(base_cost: float, growth: float, level: int, count: int) -> float:
    """Total price of buying ``count`` levels starting from ``level`` (geometric series)."""
    if count <= 0:
        return 0.0
    return base_cost * growth**level * (growth**count - 1.0) / (growth - 1.0)


def max_affordable(base_cost: float, growth: float, level: int, cash: float) -> int:
    """How many levels can be bought with ``cash`` starting at ``level``."""
    first = level_cost(base_cost, growth, level)
    if cash < first:
        return 0
    n = math.floor(math.log(cash * (growth - 1.0) / first + 1.0, growth))
    # guard floating point at the boundary
    while n > 0 and bulk_cost(base_cost, growth, level, n) > cash * (1 + 1e-12):
        n -= 1
    return n


def milestone_multiplier(level: int, milestones: Iterable[Milestone]) -> float:
    """Product of every milestone multiplier whose level has been reached."""
    mult = 1.0
    for m in milestones:
        if level >= m.level:
            mult *= m.mult
    return mult


def staff_income(level: int, income_per_level: float, milestones: Iterable[Milestone]) -> float:
    """Automated coins/sec of one staff item: level * income * milestone multiplier."""
    if level <= 0:
        return 0.0
    return level * income_per_level * milestone_multiplier(level, milestones)


def amount_value(amount: Amount | None, cash: float, income_per_sec: float) -> float:
    """Resolve a scaling :class:`Amount` to coins."""
    if amount is None:
        return 0.0
    return amount.flat + amount.cash_fraction * max(cash, 0.0) + amount.income_seconds * income_per_sec


def reputation_total(lifetime_earnings: float, base_requirement: float, exponent: float, scale: float) -> int:
    """Total reputation earned for all-time lifetime earnings.

    reputation = floor(scale * (lifetime / base_requirement) ** exponent)
    """
    if lifetime_earnings <= 0:
        return 0
    return int(math.floor(scale * (lifetime_earnings / base_requirement) ** exponent))


def reputation_multiplier(points: float, bonus_per_point: float) -> float:
    """Global income multiplier from reputation: 1 + points * bonus_per_point."""
    return 1.0 + points * bonus_per_point


def offline_earnings(rate_per_sec: float, offline_seconds: float, efficiency: float, cap_hours: float) -> float:
    """Coins credited when returning: rate * efficiency * min(offline time, cap)."""
    return rate_per_sec * efficiency * min(max(offline_seconds, 0.0), cap_hours * 3600.0)
